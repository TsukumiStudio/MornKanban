#!/usr/bin/env python3
"""Agent permission policy: unrestricted-by-default argv, and the override
precedence (env > KANBAN.md > built-in default) for worker, reviewer, and
resolver in the headless `kanban run` path and visible Herdr wrapper.

All backends are fakes (argv/stdin captured to files); no real Claude/Codex
call is made, no real HOME/credentials/remote/tag/LaunchAgent is touched.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[1]
KANBAN_SH = REPO / "kanban.sh"
HERDR_WORKER_SH = REPO / "herdr-agent-worker.sh"


def _init_git_repo(path):
    run = lambda *a: subprocess.run(
        ["git", "-C", str(path), *a], check=True, capture_output=True, text=True
    )
    run("init", "-q")
    run("checkout", "-q", "-b", "main")
    run("add", "-A")
    run("-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "init")


class HeadlessPermissionPolicyTests(unittest.TestCase):
    """kanban run --once -j1, fake claude/codex capture their argv+stdin."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text("placeholder\n", encoding="utf-8")
        _init_git_repo(self.project)

        self.calls = self.root / "calls"
        self.calls.mkdir()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        for name in ("claude", "codex"):
            script = fake_bin / name
            script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    out="{self.calls}/{name}.$$.argv"
                    printf '%s\\n' "$@" >"$out"
                    cat >/dev/null
                    echo '{{"score": 90, "feedback": "ok"}}'
                    """
                ),
                encoding="utf-8",
            )
            script.chmod(0o755)

        self.env = os.environ.copy()
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env.get("PATH", "")
        self.env["KANBAN_DISPATCH_POLL_INTERVAL"] = "0.05"
        for k in ("KANBAN_CLAUDE_PERMS", "KANBAN_CODEX_SANDBOX", "KANBAN_CODEX_FULL_BYPASS", "KANBAN_CODEX_APPROVAL"):
            self.env.pop(k, None)

    def tearDown(self):
        self.temp.cleanup()

    def _run_kanban(self, *args, env=None, cwd=None):
        return subprocess.run(
            [str(KANBAN_SH), *args],
            cwd=str(cwd or self.project),
            env=env or self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _argv_lines(self, backend):
        files = sorted((self.calls).glob(f"{backend}.*.argv"))
        return [f.read_text(encoding="utf-8").splitlines() for f in files]

    def test_init_template_defaults_to_unrestricted(self):
        self._run_kanban("init")
        content = (self.project / ".kanban" / "KANBAN.md").read_text(encoding="utf-8")
        self.assertIn("claude_perms: bypassPermissions", content)
        self.assertIn("codex_sandbox: danger-full-access", content)
        self.assertIn("codex_full_bypass: true", content)
        self.assertIn("codex_approval: never", content)

    def test_claude_worker_and_reviewer_default_unrestricted(self):
        self._run_kanban("init")
        env = self.env.copy()
        env["KANBAN_BACKEND_ORDER"] = "claude"
        env["KANBAN_REVIEWER"] = "claude"
        self._run_kanban("add", "task", "-b", "claude", env=env)
        result = self._run_kanban("run", "--once", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        argvs = self._argv_lines("claude")
        self.assertEqual(len(argvs), 2, argvs)  # worker + reviewer
        for argv in argvs:
            self.assertIn("--dangerously-skip-permissions", argv)
            self.assertNotIn("--permission-mode", argv)

    def test_codex_worker_and_reviewer_default_unrestricted(self):
        self._run_kanban("init")
        env = self.env.copy()
        env["KANBAN_BACKEND_ORDER"] = "codex"
        env["KANBAN_REVIEWER"] = "codex"
        self._run_kanban("add", "task", "-b", "codex", env=env)
        result = self._run_kanban("run", "--once", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        argvs = self._argv_lines("codex")
        self.assertEqual(len(argvs), 2, argvs)  # worker + reviewer
        for argv in argvs:
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
            self.assertNotIn("-s", argv)
            self.assertNotIn("read-only", argv)
            self.assertNotIn("workspace-write", argv)

    def test_kanban_md_can_dial_back_to_safe_mode(self):
        self._run_kanban("init")
        kanban_md = self.project / ".kanban" / "KANBAN.md"
        content = kanban_md.read_text(encoding="utf-8")
        content = content.replace("claude_perms: bypassPermissions", "claude_perms: acceptEdits")
        content = content.replace("codex_full_bypass: true", "codex_full_bypass: false")
        content = content.replace("codex_sandbox: danger-full-access", "codex_sandbox: workspace-write")
        content = content.replace("codex_approval: never", "codex_approval: on-request")
        kanban_md.write_text(content, encoding="utf-8")

        env = self.env.copy()
        env["KANBAN_BACKEND_ORDER"] = "claude codex"
        env["KANBAN_REVIEWER"] = "codex"
        self._run_kanban("add", "task", "-b", "claude", env=env)
        result = self._run_kanban("run", "--once", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        claude_argv = self._argv_lines("claude")[0]
        self.assertNotIn("--dangerously-skip-permissions", claude_argv)
        self.assertIn("--permission-mode", claude_argv)
        self.assertIn("acceptEdits", claude_argv)

        codex_argv = self._argv_lines("codex")[0]
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_argv)
        self.assertIn("-s", codex_argv)
        self.assertIn("workspace-write", codex_argv)
        self.assertIn("-a", codex_argv)
        self.assertIn("on-request", codex_argv)

    def test_env_override_beats_kanban_md(self):
        self._run_kanban("init")  # KANBAN.md defaults to bypassPermissions/full bypass
        env = self.env.copy()
        env["KANBAN_BACKEND_ORDER"] = "claude codex"
        env["KANBAN_REVIEWER"] = "codex"
        env["KANBAN_CLAUDE_PERMS"] = "manual"
        env["KANBAN_CODEX_FULL_BYPASS"] = "false"
        env["KANBAN_CODEX_SANDBOX"] = "read-only"
        env["KANBAN_CODEX_APPROVAL"] = "untrusted"
        self._run_kanban("add", "task", "-b", "claude", env=env)
        result = self._run_kanban("run", "--once", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        claude_argv = self._argv_lines("claude")[0]
        self.assertIn("--permission-mode", claude_argv)
        self.assertIn("manual", claude_argv)
        self.assertNotIn("--dangerously-skip-permissions", claude_argv)

        codex_argv = self._argv_lines("codex")[0]
        self.assertIn("read-only", codex_argv)
        self.assertIn("untrusted", codex_argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_argv)

    def test_custom_worker_cmd_receives_resolved_policy_via_env(self):
        self._run_kanban("init")
        probe = self.root / "bin" / "probe-worker"
        probe.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                {{
                  echo "CLAUDE_PERMS=$KANBAN_CLAUDE_PERMS"
                  echo "CODEX_FULL_BYPASS=$KANBAN_CODEX_FULL_BYPASS"
                  echo "CODEX_SANDBOX=$KANBAN_CODEX_SANDBOX"
                  echo "CODEX_APPROVAL=$KANBAN_CODEX_APPROVAL"
                }} >"{self.calls}/probe.env"
                cat >/dev/null
                """
            ),
            encoding="utf-8",
        )
        probe.chmod(0o755)
        env = self.env.copy()
        env["KANBAN_WORKER_CMD"] = str(probe)
        env["KANBAN_REVIEW_CMD"] = str(probe)
        self._run_kanban("add", "task", env=env)
        result = self._run_kanban("run", "--once", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        probe_env = (self.calls / "probe.env").read_text(encoding="utf-8")
        self.assertIn("CLAUDE_PERMS=bypassPermissions", probe_env)
        self.assertIn("CODEX_FULL_BYPASS=true", probe_env)
        self.assertIn("CODEX_SANDBOX=danger-full-access", probe_env)
        self.assertIn("CODEX_APPROVAL=never", probe_env)


class HerdrWorkerPermissionPolicyTests(unittest.TestCase):
    """Direct herdr-agent-worker.sh invocations: assert the argv passed to
    `herdr agent start ... -- <kind_args>` for worker AND reviewer roles."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worktree = self.root / "wt"
        self.worktree.mkdir()
        self.log = self.root / "herdr.log"
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        herdr = fake_bin / "herdr"
        herdr.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                echo "$*" >>"$HERDR_TEST_LOG"
                case "$1 $2" in
                  "pane layout")
                    printf '%s\\n' '{"result":{"layout":{"panes":[{"pane_id":"w1:p1","rect":{"width":160,"height":40}}]}}}'
                    ;;
                  "pane split")
                    printf '%s\\n' '{"result":{"pane":{"pane_id":"w1:p2"}}}'
                    ;;
                  "pane rename"|"pane close")
                    printf '%s\\n' '{"result":{}}'
                    ;;
                  "agent start")
                    printf '%s\\n' '{"result":{}}'
                    ;;
                  "agent get")
                    printf '%s\\n' '{"result":{"agent":{"agent_status":"idle"}}}'
                    ;;
                  "agent prompt")
                    printf 'KANBAN_ANSWER_ID: test-card|wt|%s|attempt-1\\n{"score":90,"feedback":"ok"}\\n' \
                      "$KANBAN_HERDR_ROLE" > "$PWD/.kanban-answer.md"
                    printf '%s\\n' '{"result":{}}'
                    ;;
                  "agent read")
                    printf 'fake transcript\\n'
                    ;;
                  *) printf '%s\\n' '{"result":{}}' ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        herdr.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + self.env.get("PATH", ""),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w1:p1",
                "HERDR_TEST_LOG": str(self.log),
                "KANBAN_CARD_ID": "test-card",
                "KANBAN_CARD_ATTEMPT": "attempt-1",
                "KANBAN_HERDR_POLL_INTERVAL": "0.1",
                "KANBAN_HERDR_STABLE_SLEEP": "0.05",
                "KANBAN_HERDR_ANSWER_WAIT_SECS": "3",
            }
        )
        for k in ("KANBAN_CLAUDE_PERMS", "KANBAN_CODEX_SANDBOX", "KANBAN_CODEX_FULL_BYPASS", "KANBAN_CODEX_APPROVAL"):
            self.env.pop(k, None)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, role, backend, env_extra=None):
        env = self.env.copy()
        env["KANBAN_HERDR_ROLE"] = role
        if role == "reviewer":
            env["KANBAN_REVIEWER"] = backend
        elif role == "resolver":
            env["KANBAN_RESOLVER"] = backend
        else:
            env["KANBAN_CARD_BACKEND"] = backend
        env.update(env_extra or {})
        self.log.write_text("", encoding="utf-8")
        result = subprocess.run(
            [str(HERDR_WORKER_SH)],
            cwd=str(self.worktree),
            env=env,
            input="dummy card body\n",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in self.log.read_text(encoding="utf-8").splitlines():
            if line.startswith("agent start "):
                return line
        self.fail(f"no 'agent start' call logged; log=\n{self.log.read_text()}")

    def test_claude_worker_unrestricted(self):
        line = self._run("worker", "claude")
        self.assertIn("--dangerously-skip-permissions", line)
        self.assertNotIn("--permission-mode acceptEdits", line)

    def test_claude_reviewer_unrestricted(self):
        line = self._run("reviewer", "claude")
        self.assertIn("--dangerously-skip-permissions", line)

    def test_codex_worker_unrestricted(self):
        line = self._run("worker", "codex")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", line)
        self.assertNotIn("workspace-write", line)

    def test_codex_reviewer_unrestricted_not_read_only(self):
        line = self._run("reviewer", "codex")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", line)
        self.assertNotIn("read-only", line)

    def test_codex_reviewer_safe_mode_override(self):
        line = self._run(
            "reviewer",
            "codex",
            env_extra={
                "KANBAN_CODEX_FULL_BYPASS": "false",
                "KANBAN_CODEX_SANDBOX": "read-only",
                "KANBAN_CODEX_APPROVAL": "on-request",
            },
        )
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", line)
        self.assertIn("-s read-only", line)
        self.assertIn("-a on-request", line)

    def test_claude_resolver_unrestricted(self):
        line = self._run("resolver", "claude")
        self.assertIn("--dangerously-skip-permissions", line)

    def test_codex_resolver_unrestricted(self):
        line = self._run("resolver", "codex")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", line)


if __name__ == "__main__":
    unittest.main()
