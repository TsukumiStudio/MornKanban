#!/usr/bin/env python3
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
KANBAN = REPO / "kanban.sh"


class KanbanBoardTestCase(unittest.TestCase):
    """Shared board fixture; holds no test_* methods of its own."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.project)], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "--allow-empty", "-qm", "init"], check=True,
        )
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
        return list((self.project / ".git" / "kanban" / state).glob("*.md"))

    def _find_by_id(self, card_id):
        for state in ("backlog", "todo", "doing", "review", "resolving", "blocked", "done", "failed"):
            for f in (self.project / ".git" / "kanban" / state).glob("*.md"):
                if self._id(f) == card_id:
                    return f
        return None


class DependencyWorkflowTests(KanbanBoardTestCase):
    def test_declared_dependency_waits_without_attempt_then_auto_resumes_on_done(self):
        upstream = self._add("upstream")
        downstream = self._add("downstream", "--depends-on", self._id(upstream))

        first = self._run("run", "--once")

        self.assertEqual(first.returncode, 0, first.stderr)
        waiting = self._cards("todo")
        self.assertEqual(len(waiting), 1, first.stdout)
        waiting_text = waiting[0].read_text(encoding="utf-8")
        self.assertIn("dependency ", waiting_text)
        self.assertIn("reached done; card returned to todo", waiting_text)
        self.assertIn("attempts: 0", waiting_text)
        self.assertEqual(self.worker_log.read_text(encoding="utf-8").splitlines(), ["upstream"])

        second = self._run("run", "--once")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self._cards("done")), 2, second.stdout)
        self.assertEqual(self._cards("blocked"), [])
        self.assertEqual(self.worker_log.read_text(encoding="utf-8").splitlines(), ["upstream", "downstream"])
        done_downstream = self.project / ".git" / "kanban" / "done" / downstream.name
        self.assertIn("dependency ready", done_downstream.read_text(encoding="utf-8"))

    def test_failed_dependency_stays_blocked_until_that_card_reaches_done(self):
        upstream = self._add("upstream")
        downstream = self._add("downstream", "--depends-on", self._id(upstream))
        failed_upstream = self.project / ".git" / "kanban" / "failed" / upstream.name
        upstream.rename(failed_upstream)

        self.assertEqual(self._run("run", "--once").returncode, 0)
        self.assertEqual(self._run("run", "--once").returncode, 0)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1)
        self.assertIn("dependency_state: failed", blocked[0].read_text(encoding="utf-8"))
        self.assertIn("attempts: 0", blocked[0].read_text(encoding="utf-8"))
        self.assertFalse(self.worker_log.exists())

        failed_upstream.rename(self.project / ".git" / "kanban" / "done" / upstream.name)
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
        notifications = Path(self.temp.name) / "notifications"
        worker = self._script("blocked-worker.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: waiting for upstream\\n'\n")
        reviewer = self._script("forbidden-reviewer.sh", f"#!/usr/bin/env bash\ntouch '{review_log}'\n")
        notify = self._script("notify.sh", "#!/usr/bin/env bash\nprintf '%s %s\\n' \"$1\" \"$2\" >> \"$NOTIFICATIONS\"\n")
        env = {
            **self.env, "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer),
            "KANBAN_NOTIFY_CMD": str(notify), "NOTIFICATIONS": str(notifications),
        }
        self._add("discovered dependency")

        result = self._run("run", "--once", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1)
        text = blocked[0].read_text(encoding="utf-8")
        self.assertIn("blocked_kind: ordering", text)
        self.assertIn("attempts: 0", text)
        self.assertFalse(review_log.exists())
        self.assertIn("blocked discovered dependency", notifications.read_text(encoding="utf-8"))

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


class OrderingStallTests(KanbanBoardTestCase):
    """Ordering blocks that never resolve must stop being re-dispatched, without
    catching legitimate ordering waits that clear up on their own (see
    kanban.sh record_ordering_block / DEFAULT_ORDERING_STALL_REPEATS)."""

    def setUp(self):
        super().setUp()
        self.env["KANBAN_ORDERING_STALL_REPEATS"] = "3"

    def test_same_reason_repeated_to_threshold_stalls_and_stops_being_reclaimed(self):
        worker = self._script("always-blocked.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: no diagnosis target specified\\n'\n")
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker)}
        card = self._add("probe with no target")
        card_id = self._id(card)

        for _ in range(3):
            result = self._run("run", "--once", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)

        blocked = self._find_by_id(card_id)
        self.assertIsNotNone(blocked)
        text = blocked.read_text(encoding="utf-8")
        self.assertIn("blocked_kind: ordering_stalled", text)
        self.assertIn("ordering_block_repeat: 3", text)
        self.assertEqual(self.worker_log.exists(), False)

        # A further dispatch must not reclaim or re-run the stalled card.
        before_mtime = blocked.stat().st_mtime_ns
        result = self._run("run", "--once", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        still_blocked = self._find_by_id(card_id)
        self.assertIsNotNone(still_blocked)
        self.assertEqual(still_blocked, blocked)
        self.assertEqual(still_blocked.stat().st_mtime_ns, before_mtime)

    def test_changed_reason_resets_repeat_count_and_never_stalls(self):
        counter = Path(self.temp.name) / "reason.count"
        worker = self._script(
            "changing-reason.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\n"
            "n=$(($(cat \"$COUNTER_FILE\" 2>/dev/null || echo 0) + 1))\n"
            "echo \"$n\" > \"$COUNTER_FILE\"\n"
            "if [ \"$n\" -le 2 ]; then printf 'BLOCKED: reason-a\\n'; else printf 'BLOCKED: reason-b\\n'; fi\n",
        )
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker), "COUNTER_FILE": str(counter)}
        card = self._add("flip-flopping reason")
        card_id = self._id(card)

        for _ in range(4):
            result = self._run("run", "--once", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)

        blocked = self._find_by_id(card_id)
        self.assertIsNotNone(blocked)
        text = blocked.read_text(encoding="utf-8")
        self.assertIn("blocked_kind: ordering\n", text)
        self.assertIn("ordering_block_repeat: 2", text)

    def test_legit_ordering_wait_resolves_before_stall_threshold(self):
        counter = Path(self.temp.name) / "attempt.count"
        worker = self._script(
            "resolves-on-third.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\n"
            "n=$(($(cat \"$COUNTER_FILE\" 2>/dev/null || echo 0) + 1))\n"
            "echo \"$n\" > \"$COUNTER_FILE\"\n"
            "if [ \"$n\" -le 2 ]; then printf 'BLOCKED: waiting for upstream to merge\\n'; "
            "else printf '%s\\n' \"$KANBAN_CARD_TITLE\" >> \"$WORKER_LOG\"; fi\n",
        )
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker), "COUNTER_FILE": str(counter)}
        card = self._add("downstream waiting on a real merge")
        card_id = self._id(card)

        for _ in range(3):
            result = self._run("run", "--once", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)

        done = self._find_by_id(card_id)
        self.assertIsNotNone(done)
        self.assertTrue(str(done).endswith(f"done/{done.name}") or done.parent.name == "done")
        self.assertEqual(self.worker_log.read_text(encoding="utf-8").strip(), "downstream waiting on a real merge")

    def test_resume_clears_stalled_ordering_block_and_repeat_count(self):
        worker = self._script("always-blocked.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: no diagnosis target specified\\n'\n")
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker)}
        card = self._add("stuck probe")
        card_id = self._id(card)
        for _ in range(3):
            self.assertEqual(self._run("run", "--once", env=env).returncode, 0)
        self.assertIn("blocked_kind: ordering_stalled", self._find_by_id(card_id).read_text(encoding="utf-8"))

        result = self._run("resume", card_id)

        self.assertEqual(result.returncode, 0, result.stderr)
        card_after = self._find_by_id(card_id)
        self.assertEqual(card_after.parent.name, "todo")
        text = card_after.read_text(encoding="utf-8")
        self.assertIn("blocked_kind: \n", text)
        self.assertIn("ordering_block_repeat: 0", text)

    def test_remove_deletes_an_ordering_blocked_card(self):
        worker = self._script("always-blocked.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: waiting for upstream\\n'\n")
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker)}
        card = self._add("disposable ordering block")
        card_id = self._id(card)
        self.assertEqual(self._run("run", "--once", env=env).returncode, 0)
        blocked = self._find_by_id(card_id)
        self.assertEqual(blocked.parent.name, "blocked")

        result = self._run("remove", card_id)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self._find_by_id(card_id))

    def test_list_marks_stalled_ordering_card_as_needing_user_judgment(self):
        worker = self._script("always-blocked.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: no diagnosis target specified\\n'\n")
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker)}
        self._add("stalled and visible")
        for _ in range(3):
            self.assertEqual(self._run("run", "--once", env=env).returncode, 0)

        result = self._run("list")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ordering_stalled", result.stdout)
        self.assertIn("user judgment needed", result.stdout)

    def test_ordering_blocked_card_with_no_repeats_yet_is_not_stalled_and_stays_dispatchable(self):
        worker = self._script("always-blocked.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'BLOCKED: waiting for upstream\\n'\n")
        env = {**self.env, "KANBAN_WORKER_CMD": str(worker)}
        self._add("first block only")

        result = self._run("run", "--once", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        blocked = self._cards("blocked")
        self.assertEqual(len(blocked), 1)
        text = blocked[0].read_text(encoding="utf-8")
        self.assertIn("blocked_kind: ordering\n", text)
        self.assertIn("ordering_block_repeat: 1", text)


if __name__ == "__main__":
    unittest.main()
