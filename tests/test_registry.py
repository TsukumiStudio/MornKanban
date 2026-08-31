#!/usr/bin/env python3
"""Integration tests for the PC-wide project registry (`kanban projects`)
and the cross-project card sender (`kanban send`).

Uses a temp HOME so `~/.config/mornkanban/projects.json` never touches the
real machine's registry, and every subprocess it starts (dispatcher lock
sleeps included) is torn down before the test ends.
"""
import concurrent.futures
import glob
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
KANBAN_SH = REPO / "kanban.sh"


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = dict(os.environ)
        self.env["HOME"] = str(self.root / "home")
        (self.root / "home").mkdir()
        self.a = self.root / "A"
        self.b = self.root / "B"
        self.unrelated = self.root / "unrelated"
        for d in (self.a, self.b, self.unrelated):
            d.mkdir()
        self._init(self.a)
        self._init(self.b)
        self._procs = []

    def tearDown(self):
        for p in self._procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
        self.temp.cleanup()

    def _run(self, *args, cwd=None, input_text=None, check=True):
        result = subprocess.run(
            ["bash", str(KANBAN_SH), *args],
            cwd=str(cwd) if cwd else None,
            input=input_text,
            capture_output=True,
            text=True,
            env=self.env,
        )
        if check and result.returncode != 0:
            self.fail("kanban %s failed: %s\n%s" % (" ".join(args), result.stdout, result.stderr))
        return result

    def _init(self, path):
        r = self._run("init", cwd=path)
        self.assertIn("initialized", r.stdout)

    def _card_files(self, project, state="todo"):
        return sorted(glob.glob(str(project / ".kanban" / state / "*.md")))

    # --- registry CRUD -----------------------------------------------------

    def test_add_list_show_remove(self):
        self._run("projects", "add", "project-a", str(self.a))
        self._run("projects", "add", "project-b", str(self.b))

        listed = self._run("projects", "list").stdout
        self.assertIn("project-a", listed)
        self.assertIn("project-b", listed)

        shown = self._run("projects", "show", "project-a").stdout
        self.assertIn("alias: project-a", shown)
        self.assertIn(str(self.a.resolve()), shown)
        self.assertIn("dispatcher: not running", shown)

        self._run("projects", "remove", "project-a")
        r = self._run("projects", "show", "project-a", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("project-a", self._run("projects", "list").stdout)

    def test_update_repoints_alias(self):
        self._run("projects", "add", "project-a", str(self.a))
        c = self.root / "C"
        c.mkdir()
        self._init(c)
        self._run("projects", "update", "project-a", str(c))
        shown = self._run("projects", "show", "project-a").stdout
        self.assertIn(str(c.resolve()), shown)

    def test_rejects_invalid_alias(self):
        r = self._run("projects", "add", "Bad Alias!", str(self.a), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid alias", r.stderr)

    def test_rejects_nonexistent_path(self):
        r = self._run("projects", "add", "ghost", str(self.root / "nope"), check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_rejects_path_without_kanban(self):
        plain = self.root / "plain"
        plain.mkdir()
        r = self._run("projects", "add", "plain", str(plain), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".kanban", r.stderr)

    def test_rejects_duplicate_alias(self):
        self._run("projects", "add", "project-a", str(self.a))
        r = self._run("projects", "add", "project-a", str(self.b), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already registered", r.stderr)

    def test_rejects_duplicate_path_via_symlink(self):
        self._run("projects", "add", "project-a", str(self.a))
        link = self.root / "A-link"
        link.symlink_to(self.a)
        r = self._run("projects", "add", "project-a-2", str(link), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already registered", r.stderr)

    def test_rejects_removed_alias_reuse_error_message(self):
        self._run("projects", "add", "project-a", str(self.a))
        self._run("projects", "remove", "project-a")
        r = self._run("send", "project-a", "x", input_text="body", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not registered", r.stderr)

    # --- send ---------------------------------------------------------------

    def test_send_a_to_b_creates_card_only_in_b(self):
        self._run("projects", "add", "project-a", str(self.a))
        self._run("projects", "add", "project-b", str(self.b))
        r = self._run("send", "project-b", "AからB", cwd=self.a, input_text="本文A")
        dest = Path(r.stdout.strip())
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.parent, (self.b / ".kanban" / "todo").resolve())
        self.assertEqual(self._card_files(self.a), [])

        content = dest.read_text(encoding="utf-8")
        self.assertIn("source_alias: project-a", content)
        self.assertIn("source_path: %s" % self.a.resolve(), content)
        self.assertIn("dispatched_via: send", content)
        self.assertIn("本文A", content)

    def test_send_b_to_a_symmetric(self):
        self._run("projects", "add", "project-a", str(self.a))
        self._run("projects", "add", "project-b", str(self.b))
        r = self._run("send", "project-a", "BからA", cwd=self.b, input_text="本文B")
        dest = Path(r.stdout.strip())
        self.assertEqual(dest.parent, (self.a / ".kanban" / "todo").resolve())
        self.assertIn("source_alias: project-b", dest.read_text(encoding="utf-8"))

    def test_send_from_unrelated_cwd_has_no_source_alias(self):
        self._run("projects", "add", "project-a", str(self.a))
        r = self._run("send", "project-a", "無関係から", cwd=self.unrelated, input_text="本文U")
        dest = Path(r.stdout.strip())
        content = dest.read_text(encoding="utf-8")
        self.assertIn("source_alias: \n", content)
        self.assertIn("source_path: %s" % self.unrelated.resolve(), content)

    def test_send_applies_target_kanban_md_defaults(self):
        self._run("projects", "add", "project-b", str(self.b))
        (self.b / ".kanban" / "KANBAN.md").write_text(
            "---\ndefault_backend: codex\ndefault_model: gpt-5.3-codex-spark\nthreshold: 55\n---\n",
            encoding="utf-8",
        )
        r = self._run("send", "project-b", "t", cwd=self.unrelated, input_text="b")
        content = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("backend: codex", content)
        self.assertIn("model: gpt-5.3-codex-spark", content)
        self.assertIn("threshold: 55", content)

    def test_send_cli_overrides_win_over_defaults(self):
        self._run("projects", "add", "project-b", str(self.b))
        r = self._run(
            "send", "project-b", "t", "-b", "claude", "-m", "opus", "-e", "high", "-t", "90",
            cwd=self.unrelated, input_text="b",
        )
        content = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("backend: claude", content)
        self.assertIn("model: opus", content)
        self.assertIn("effort: high", content)
        self.assertIn("threshold: 90", content)

    def test_send_rejects_unknown_effort(self):
        self._run("projects", "add", "project-b", str(self.b))
        result = self._run("send", "project-b", "t", "-e", "extreme", input_text="b", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid effort", result.stderr)
        self.assertEqual(self._card_files(self.b), [])

    def test_send_diagnose_preserves_read_only_timebox_metadata(self):
        self._run("projects", "add", "project-b", str(self.b))
        r = self._run("send", "project-b", "why slow", "--diagnose", input_text="evidence only")
        content = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("task_kind: diagnose", content)
        self.assertIn("review_enabled: false", content)
        self.assertIn("diagnosis_target_minutes: 5", content)
        self.assertIn("diagnosis_max_minutes: 10", content)

    def test_send_to_unregistered_alias_fails(self):
        r = self._run("send", "no-such-alias", "t", input_text="b", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not registered", r.stderr)

    def test_send_reports_dispatcher_not_running(self):
        self._run("projects", "add", "project-b", str(self.b))
        r = self._run("send", "project-b", "t", input_text="b")
        self.assertIn("not running", r.stderr)

    def test_send_reports_dispatcher_running(self):
        self._run("projects", "add", "project-b", str(self.b))
        sleeper = subprocess.Popen(["sleep", "30"])
        self._procs.append(sleeper)
        (self.b / ".kanban" / ".lock").write_text(str(sleeper.pid), encoding="utf-8")
        try:
            r = self._run("send", "project-b", "t", input_text="b")
            self.assertIn("running (pid %d)" % sleeper.pid, r.stderr)
        finally:
            (self.b / ".kanban" / ".lock").unlink(missing_ok=True)

    # --- concurrency ----------------------------------------------------

    def test_concurrent_send_no_id_collisions_or_partial_writes(self):
        self._run("projects", "add", "project-b", str(self.b))
        n = 24

        def one(i):
            return self._run("send", "project-b", "concurrent-%d" % i, input_text="body %d" % i)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(one, range(n)))

        dests = [Path(r.stdout.strip()) for r in results]
        self.assertEqual(len(dests), len(set(dests)), "duplicate destination paths")
        for d in dests:
            self.assertTrue(d.is_file())

        todo_files = self._card_files(self.b)
        self.assertEqual(len(todo_files), n)
        self.assertEqual([f for f in todo_files if ".tmp" in f], [])

        ids = []
        for f in todo_files:
            text = Path(f).read_text(encoding="utf-8")
            m = re.search(r"^id: (.+)$", text, re.M)
            ids.append(m.group(1))
        self.assertEqual(len(ids), len(set(ids)), "duplicate card ids")


if __name__ == "__main__":
    unittest.main()
