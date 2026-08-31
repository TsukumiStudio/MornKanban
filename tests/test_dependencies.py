#!/usr/bin/env python3
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
KANBAN = REPO / "kanban.sh"


class DependencyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        subprocess.run([str(KANBAN), "init"], cwd=self.project, check=True, capture_output=True, text=True)
        self.worker_log = Path(self.temp.name) / "worker.log"
        self.worker = self._script(
            "worker.sh",
            '#!/usr/bin/env bash\ncat >/dev/null\nprintf "%s\\n" "$KANBAN_CARD_TITLE" >> "$WORKER_LOG"\n',
        )
        self.reviewer = self._script(
            "reviewer.sh",
            '#!/usr/bin/env bash\ncat >/dev/null\nprintf \'{"score":95,"feedback":"ok"}\\n\'\n',
        )
        self.env = {
            **os.environ,
            "KANBAN_WORKER_CMD": str(self.worker),
            "KANBAN_REVIEW_CMD": str(self.reviewer),
            "KANBAN_JOBS": "1",
            "WORKER_LOG": str(self.worker_log),
        }

    def tearDown(self):
        self.temp.cleanup()

    def _script(self, name, body):
        path = Path(self.temp.name) / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run(self, *args, input_text=None, env=None):
        return subprocess.run(
            [str(KANBAN), *args], cwd=self.project, env=env or self.env,
            input=input_text, text=True, capture_output=True, check=False,
        )

    def _add(self, title, *args):
        result = self._run("add", title, *args, input_text="task")
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(result.stdout.strip())

    @staticmethod
    def _id(card):
        return re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)

    def _cards(self, state):
        return list((self.project / ".kanban" / state).glob("*.md"))

    def test_declared_dependency_waits_without_attempt_then_auto_resumes_on_done(self):
        upstream = self._add("upstream")
        downstream = self._add("downstream", "--depends-on", self._id(upstream))

        first = self._run("run", "--once")

        self.assertEqual(first.returncode, 0, first.stderr)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1, first.stdout)
        blocked_text = blocked[0].read_text(encoding="utf-8")
        self.assertIn("blocked_kind: dependency", blocked_text)
        self.assertIn("dependency_state: todo", blocked_text)
        self.assertIn("attempts: 0", blocked_text)
        self.assertEqual(self.worker_log.read_text(encoding="utf-8").splitlines(), ["upstream"])

        second = self._run("run", "--once")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self._cards("done")), 2, second.stdout)
        self.assertEqual(self._cards("blocked"), [])
        self.assertEqual(self.worker_log.read_text(encoding="utf-8").splitlines(), ["upstream", "downstream"])
        done_downstream = self.project / ".kanban" / "done" / downstream.name
        self.assertIn("dependency ready", done_downstream.read_text(encoding="utf-8"))

    def test_failed_dependency_stays_blocked_until_that_card_reaches_done(self):
        upstream = self._add("upstream")
        downstream = self._add("downstream", "--depends-on", self._id(upstream))
        failed_upstream = self.project / ".kanban" / "failed" / upstream.name
        upstream.rename(failed_upstream)

        self.assertEqual(self._run("run", "--once").returncode, 0)
        self.assertEqual(self._run("run", "--once").returncode, 0)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1)
        self.assertIn("dependency_state: failed", blocked[0].read_text(encoding="utf-8"))
        self.assertIn("attempts: 0", blocked[0].read_text(encoding="utf-8"))
        self.assertFalse(self.worker_log.exists())

        failed_upstream.rename(self.project / ".kanban" / "done" / upstream.name)
        resumed = self._run("run", "--once")

        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self._cards("blocked"), [])
        self.assertEqual(self.worker_log.read_text(encoding="utf-8").splitlines(), ["downstream"])

    def test_add_rejects_missing_dependency(self):
        result = self._run("add", "orphan", "--depends-on", "20990101-000000-99999", input_text="task")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency card not found", result.stderr)
        self.assertEqual(self._cards("todo"), [])

    def test_discovered_dependency_uses_ordering_block_without_review_or_attempt(self):
        review_log = Path(self.temp.name) / "review.log"
        worker = self._script("blocked-worker.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: waiting for upstream\\n'\n")
        reviewer = self._script("forbidden-reviewer.sh", f"#!/usr/bin/env bash\ntouch '{review_log}'\n")
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer)}
        self._add("discovered dependency")

        result = self._run("run", "--once", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1)
        text = blocked[0].read_text(encoding="utf-8")
        self.assertIn("blocked_kind: ordering", text)
        self.assertIn("attempts: 0", text)
        self.assertFalse(review_log.exists())

    def test_model_start_error_is_unverified_block_without_attempt_or_review(self):
        review_log = Path(self.temp.name) / "review.log"
        worker = self._script(
            "broken-model-worker.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'KANBAN_INFRA_ERROR: model_start: model failed to start\\n'\nexit 1\n",
        )
        reviewer = self._script("forbidden-reviewer.sh", f"#!/usr/bin/env bash\ntouch '{review_log}'\n")
        env = {
            **self.env,
            "KANBAN_WORKER_CMD": str(worker),
            "KANBAN_REVIEW_CMD": str(reviewer),
            "KANBAN_REVIEW_INFRA_MAX_RETRIES": "0",
        }
        self._add("browser verification")

        result = self._run("run", "--once", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1)
        text = blocked[0].read_text(encoding="utf-8")
        self.assertIn("blocked_kind: review_infra", text)
        self.assertIn("attempts: 0", text)
        self.assertIn("not a code failure", text)
        self.assertEqual(self._cards("failed"), [])
        self.assertFalse(review_log.exists())

    def test_failed_card_records_work_process_failure_kind(self):
        reviewer = self._script(
            "rejecting-reviewer.sh",
            '#!/usr/bin/env bash\ncat >/dev/null\nprintf \'{"score":20,"feedback":"not complete"}\\n\'\n',
        )
        env = {**self.env, "KANBAN_REVIEW_CMD": str(reviewer)}
        card = self._add("review failure")
        card.write_text(card.read_text(encoding="utf-8").replace("max_attempts: 3", "max_attempts: 1"), encoding="utf-8")

        result = self._run("run", "--once", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        failed = self._cards("failed")
        self.assertEqual(len(failed), 1)
        self.assertIn("failure_kind: review", failed[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
