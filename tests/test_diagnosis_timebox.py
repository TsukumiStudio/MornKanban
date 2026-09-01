import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]
KANBAN = REPO / "kanban.sh"


class DiagnosisTimeboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.project, check=True)
        (self.project / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
            cwd=self.project, check=True,
        )
        subprocess.run([str(KANBAN), "init"], cwd=self.project, check=True, capture_output=True, text=True)
        self.worker = Path(self.temp.name) / "worker.sh"
        self.prompt = Path(self.temp.name) / "prompt.txt"

    def tearDown(self):
        self.temp.cleanup()

    def _add(self, title="diagnose slow thing"):
        result = subprocess.run(
            [str(KANBAN), "add", title, "--diagnose"], cwd=self.project,
            input="Find the bottleneck and report evidence.", text=True,
            check=True, capture_output=True,
        )
        return Path(result.stdout.strip())

    def _run(self, worker_body):
        self.worker.write_text("#!/usr/bin/env bash\nset -eu\n" + worker_body, encoding="utf-8")
        self.worker.chmod(0o755)
        env = os.environ.copy()
        env.update({
            "KANBAN_WORKER_CMD": str(self.worker), "KANBAN_JOBS": "1",
            "KANBAN_DISPATCH_POLL_INTERVAL": "0.05",
        })
        return subprocess.run(
            [str(KANBAN), "run", "--once"], cwd=self.project, env=env,
            text=True, capture_output=True, timeout=15,
        )

    def test_diagnose_card_freezes_5_10_minute_read_only_policy(self):
        card = self._add()
        text = card.read_text(encoding="utf-8")
        self.assertIn("task_kind: diagnose", text)
        self.assertIn("diagnosis_target_minutes: 5", text)
        self.assertIn("diagnosis_max_minutes: 10", text)
        self.assertIn("review_enabled: false", text)
        self.assertIn("review_source: diagnose", text)

    def test_worker_receives_deadline_and_expected_output_contract(self):
        self._add()
        result = self._run(
            'cat > "%s"\nprintf "evidence and cause\\n"\n' % self.prompt
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prompt = self.prompt.read_text(encoding="utf-8")
        self.assertIn("DIAGNOSIS-ONLY TIMEBOX CONTRACT", prompt)
        self.assertIn("within 5 minutes", prompt)
        self.assertIn("Hard maximum: 10 minutes", prompt)
        self.assertIn("Expected output: observed evidence", prompt)
        self.assertIn("BLOCKED: scope/timebox", prompt)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "done").glob("*.md"))), 1)

    def test_diagnose_file_changes_are_discarded_and_never_merged(self):
        self._add()
        result = self._run('cat >/dev/null\nprintf "must not merge\\n" > unwanted.txt\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.project / "unwanted.txt").exists())
        failed = list((self.project / ".git" / "kanban" / "failed").glob("*.md"))
        self.assertEqual(len(failed), 1)
        self.assertIn("diagnosis read-only violation", failed[0].read_text(encoding="utf-8"))

    def test_hard_timebox_blocks_once_without_infrastructure_retries(self):
        self._add()
        count = Path(self.temp.name) / "count.txt"
        result = self._run(
            'cat >/dev/null\n'
            'n=0; [[ -f "{0}" ]] && n=$(cat "{0}"); echo $((n + 1)) > "{0}"\n'
            'echo "KANBAN_INFRA_ERROR: scope_timebox: hard maximum" >&2\n'
            'exit 1\n'.format(count)
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(count.read_text(encoding="utf-8").strip(), "1")
        blocked = list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertEqual(len(blocked), 1)
        self.assertIn("scope/timebox", blocked[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
