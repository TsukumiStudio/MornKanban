#!/usr/bin/env python3
"""Tests for the secretary in-process-delegation guard.

Covers: guard/secretary_marker.py (project/pane-scoped marker), the
kanban-secretary.sh bootstrap/end lifecycle that writes/clears it,
guard/claude_secretary_guard.py's PreToolUse decision, and setup_core's
Claude Code settings.json install/uninstall of the guard hook.

All state lives under temp directories; no real HOME, ~/.claude, or ~/.codex
is ever touched.
"""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SECRETARY = REPO / "kanban-secretary.sh"
GUARD_SCRIPT = REPO / "guard" / "claude_secretary_guard.py"

sys.path.insert(0, str(REPO / "guard"))
sys.path.insert(0, str(REPO / "gui"))
import secretary_marker as marker  # noqa: E402


def _run_guard(payload, env):
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_write_read_roundtrip(self):
        data = marker.write_marker(str(self.root), "w1:p1", "secretary")
        self.assertEqual(data["pane_id"], "w1:p1")
        got = marker.read_marker(str(self.root))
        self.assertEqual(got["pane_id"], "w1:p1")
        self.assertEqual(got["secretary_name"], "secretary")

    def test_is_secretary_pane_matches_only_recorded_pane(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        self.assertTrue(marker.is_secretary_pane(str(self.root), "w1:p1"))
        self.assertFalse(marker.is_secretary_pane(str(self.root), "w1:p2"))

    def test_missing_marker_is_not_secretary(self):
        self.assertFalse(marker.is_secretary_pane(str(self.root), "w1:p1"))

    def test_rebootstrap_supersedes_stale_marker(self):
        marker.write_marker(str(self.root), "w1:old", "secretary")
        marker.write_marker(str(self.root), "w1:new", "secretary")
        self.assertFalse(marker.is_secretary_pane(str(self.root), "w1:old"))
        self.assertTrue(marker.is_secretary_pane(str(self.root), "w1:new"))

    def test_clear_marker(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        self.assertTrue(marker.clear_marker(str(self.root)))
        self.assertIsNone(marker.read_marker(str(self.root)))
        self.assertFalse(marker.is_secretary_pane(str(self.root), "w1:p1"))

    def test_projects_are_isolated(self):
        other = Path(self.temp.name) / "other"
        other.mkdir()
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        self.assertFalse(marker.is_secretary_pane(str(other), "w1:p1"))

    def test_audit_log_is_capped(self):
        for i in range(marker.AUDIT_MAX_LINES + 50):
            marker.append_audit(str(self.root), "line %d" % i)
        lines = marker.audit_path(str(self.root))
        content = Path(lines).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(content), marker.AUDIT_MAX_LINES)
        self.assertIn("line %d" % (marker.AUDIT_MAX_LINES + 49), content[-1])


class ClaudeGuardHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / ".kanban").mkdir()
        self.env = {"PATH": "/usr/bin:/bin"}

    def tearDown(self):
        self.temp.cleanup()

    def _env(self, pane_id=None, herdr_env="1"):
        env = dict(self.env)
        if herdr_env is not None:
            env["HERDR_ENV"] = herdr_env
        if pane_id is not None:
            env["HERDR_PANE_ID"] = pane_id
        return env

    def test_denies_task_from_recorded_secretary_pane(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        r = _run_guard(
            {"tool_name": "Task", "cwd": str(self.root)}, self._env(pane_id="w1:p1")
        )
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("kanban add", out["reason"])

    def test_allows_task_from_other_pane_worker(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        r = _run_guard(
            {"tool_name": "Task", "cwd": str(self.root)}, self._env(pane_id="w1:p2")
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_allows_non_delegation_tool_from_secretary_pane(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        r = _run_guard(
            {"tool_name": "Bash", "cwd": str(self.root)}, self._env(pane_id="w1:p1")
        )
        self.assertEqual(r.stdout.strip(), "")

    def test_allows_when_no_marker(self):
        r = _run_guard(
            {"tool_name": "Task", "cwd": str(self.root)}, self._env(pane_id="w1:p1")
        )
        self.assertEqual(r.stdout.strip(), "")

    def test_allows_when_not_in_herdr(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        r = _run_guard({"tool_name": "Task", "cwd": str(self.root)}, self._env(pane_id=None, herdr_env=None))
        self.assertEqual(r.stdout.strip(), "")

    def test_allows_different_project(self):
        other = Path(self.temp.name) / "other"
        other.mkdir()
        (other / ".kanban").mkdir()
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        r = _run_guard(
            {"tool_name": "Task", "cwd": str(other)}, self._env(pane_id="w1:p1")
        )
        self.assertEqual(r.stdout.strip(), "")

    def test_deny_appends_audit_log_without_conversation_text(self):
        marker.write_marker(str(self.root), "w1:p1", "secretary")
        _run_guard({"tool_name": "Task", "cwd": str(self.root)}, self._env(pane_id="w1:p1"))
        audit = Path(marker.audit_path(str(self.root)))
        self.assertTrue(audit.is_file())
        line = audit.read_text(encoding="utf-8").strip()
        self.assertIn("tool=Task", line)
        self.assertIn("pane=w1:p1", line)


class SecretaryLifecycleTests(unittest.TestCase):
    """kanban-secretary.sh bootstrap/end marker lifecycle via the real script."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        fake_bin = root / "bin"
        fake_bin.mkdir()
        herdr = fake_bin / "herdr"
        herdr.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                case "$1 $2" in
                  "pane layout")
                    printf '%s\n' '{"result":{"layout":{"panes":[{"pane_id":"'"$HERDR_PANE_ID"'","rect":{"width":160,"height":40}}]}}}'
                    ;;
                  *) printf '%s\n' '{"result":{}}' ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        herdr.chmod(0o755)
        import os

        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + self.env.get("PATH", ""),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w1:p1",
                "KANBAN_BIN": str(REPO / "kanban.sh"),
            }
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_secretary(self, *args, env=None):
        return subprocess.run(
            [str(SECRETARY), *map(str, args)],
            text=True,
            capture_output=True,
            env=env or self.env,
            check=False,
        )

    def test_bootstrap_writes_marker_for_this_pane(self):
        result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        m = marker.read_marker(str(self.project))
        self.assertEqual(m["pane_id"], "w1:p1")
        self.assertEqual(m["secretary_name"], "secretary")

    def test_end_clears_marker(self):
        self.run_secretary("bootstrap", self.project)
        result = self.run_secretary("end", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(marker.read_marker(str(self.project)))

    def test_rebootstrap_in_new_pane_supersedes_old(self):
        self.run_secretary("bootstrap", self.project)
        env2 = dict(self.env, HERDR_PANE_ID="w1:p9")
        self.run_secretary("bootstrap", self.project, env=env2)
        self.assertFalse(marker.is_secretary_pane(str(self.project), "w1:p1"))
        self.assertTrue(marker.is_secretary_pane(str(self.project), "w1:p9"))

    def test_guard_denies_only_the_bootstrapped_pane(self):
        self.run_secretary("bootstrap", self.project)
        r_secretary = _run_guard(
            {"tool_name": "Task", "cwd": str(self.project)},
            dict(self.env, HERDR_ENV="1", HERDR_PANE_ID="w1:p1"),
        )
        self.assertEqual(json.loads(r_secretary.stdout)["decision"], "block")

        r_worker = _run_guard(
            {"tool_name": "Task", "cwd": str(self.project)},
            dict(self.env, HERDR_ENV="1", HERDR_PANE_ID="w1:p2"),
        )
        self.assertEqual(r_worker.stdout.strip(), "")


class ClaudeGuardInstallerTests(unittest.TestCase):
    def setUp(self):
        self.setup_core = importlib.import_module("setup_core")
        self.temp = tempfile.TemporaryDirectory()
        self.settings = str(Path(self.temp.name) / "settings.json")

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, data):
        Path(self.settings).write_text(json.dumps(data), encoding="utf-8")

    def test_install_on_missing_file_creates_it(self):
        msg = self.setup_core.install_claude_guard(self.settings)
        self.assertIn("導入しました", msg)
        data = json.loads(Path(self.settings).read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        self.assertTrue(any(e["matcher"] == "Task" for e in entries))

    def test_install_preserves_unrelated_hooks_and_keys(self):
        self._write(
            {
                "permissions": {"allow": ["Bash(npx astro:*)"]},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
                    ]
                },
            }
        )
        self.setup_core.install_claude_guard(self.settings)
        data = json.loads(Path(self.settings).read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"]["allow"], ["Bash(npx astro:*)"])
        matchers = {e["matcher"] for e in data["hooks"]["PreToolUse"]}
        self.assertEqual(matchers, {"Bash", "Task"})
        bash_entry = next(e for e in data["hooks"]["PreToolUse"] if e["matcher"] == "Bash")
        self.assertEqual(bash_entry["hooks"], [{"type": "command", "command": "echo hi"}])

    def test_install_is_idempotent(self):
        self.setup_core.install_claude_guard(self.settings)
        before = Path(self.settings).read_text(encoding="utf-8")
        msg = self.setup_core.install_claude_guard(self.settings)
        self.assertIn("導入済み", msg)
        after = Path(self.settings).read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_install_backs_up_existing_file_once(self):
        self._write({"hooks": {}})
        self.setup_core.install_claude_guard(self.settings)
        backup = Path(self.settings + ".mornkanban-guard.bak")
        self.assertTrue(backup.is_file())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"hooks": {}})

    def test_uninstall_removes_only_our_entry(self):
        self._write(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
                    ]
                }
            }
        )
        self.setup_core.install_claude_guard(self.settings)
        msg = self.setup_core.uninstall_claude_guard(self.settings)
        self.assertIn("削除しました", msg)
        data = json.loads(Path(self.settings).read_text(encoding="utf-8"))
        matchers = [e["matcher"] for e in data["hooks"]["PreToolUse"]]
        self.assertEqual(matchers, ["Bash"])

    def test_uninstall_on_never_installed_is_noop(self):
        msg = self.setup_core.uninstall_claude_guard(self.settings)
        self.assertIn("未導入", msg)

    def test_guard_status_reports_enforced_and_not_installed(self):
        self.assertEqual(self.setup_core.guard_status(self.settings)["claude"], "not_installed")
        self.setup_core.install_claude_guard(self.settings)
        self.assertEqual(self.setup_core.guard_status(self.settings)["claude"], "enforced")

    def test_guard_status_codex_is_documented_as_prompt_only(self):
        status = self.setup_core.guard_status(self.settings)
        self.assertIn("not_supported", status["codex"])


if __name__ == "__main__":
    unittest.main()
