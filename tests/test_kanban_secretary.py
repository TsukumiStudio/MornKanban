#!/usr/bin/env python3
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SECRETARY = REPO / "kanban-secretary.sh"


class SecretaryScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
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
                    printf '%s\n' '{"result":{"layout":{"panes":[{"pane_id":"w1:p1","rect":{"width":160,"height":40}}]}}}'
                    ;;
                  "pane split")
                    printf '%s\n' '{"result":{"pane":{"pane_id":"w1:p2"}}}'
                    ;;
                  *) printf '%s\n' '{"result":{}}' ;;
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

    def test_bootstrap_initializes_board_and_registers_secretary(self):
        result = self.run_secretary("bootstrap", self.project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / ".kanban" / "KANBAN.md").is_file())
        self.assertIn("execution=visible-herdr", result.stdout)
        self.assertIn("agent rename w1:p1 secretary", self.log.read_text(encoding="utf-8"))

    def test_dispatch_binds_visible_worker_reviewer_and_notification(self):
        bootstrap = self.run_secretary("bootstrap", self.project)
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        self.log.write_text("", encoding="utf-8")

        result = self.run_secretary("dispatch", self.project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pane=w1:p2", result.stdout)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("pane split --current --direction right", log)
        self.assertIn("KANBAN_WORKER_CMD=", log)
        self.assertIn("herdr-agent-worker.sh", log)
        self.assertIn("KANBAN_REVIEW_CMD=", log)
        self.assertIn("KANBAN_NOTIFY_CMD=", log)
        self.assertIn("herdr-notify-secretary.sh", log)
        self.assertIn("kanban.sh run; exit", log)

    def test_bootstrap_refuses_hidden_headless_fallback(self):
        env = self.env.copy()
        env.pop("HERDR_ENV")

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing a hidden headless fallback", result.stderr)
        self.assertFalse((self.project / ".kanban").exists())


class SkillInstallerTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO / "gui"))
        self.setup_core = importlib.import_module("setup_core")
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.targets = {
            "Claude Code": str(root / "claude" / "kanban-dispatch"),
            "Codex": str(root / "codex" / "kanban-dispatch"),
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_installs_rendered_skill_for_claude_and_codex(self):
        with mock.patch.object(self.setup_core, "SKILL_TARGETS", self.targets):
            messages = self.setup_core.install_skills(force=True)
            status = self.setup_core.skill_status()

        self.assertEqual(status, {"Claude Code": True, "Codex": True})
        self.assertEqual(len(messages), 2)
        for directory in self.targets.values():
            skill = Path(directory) / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            self.assertNotIn("__MORNKANBAN_REPO__", content)
            self.assertIn(str(REPO), content)
            self.assertTrue((Path(directory) / "agents" / "openai.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
