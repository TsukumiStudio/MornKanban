import json
import os
import tempfile
import unittest

import activity_log


class ActivityLogTests(unittest.TestCase):
    def test_append_is_bounded_and_drops_unapproved_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "activity.jsonl")
            for n in range(5):
                activity_log.append_event(
                    path,
                    {
                        "event": "agent_started",
                        "card_id": "card-%d" % n,
                        "role": "worker",
                        "attempt": "1",
                        "backend": "codex",
                        "model": "gpt-test",
                        "effort": "high",
                        "agent_name": "worker-1",
                        "pane_id": "w1:p2",
                        "status": "running",
                        "duration_secs": n,
                        "prompt": "must never be persisted",
                        "answer": "must never be persisted",
                    },
                    max_events=3,
                )
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh]
            self.assertEqual([row["card_id"] for row in rows], ["card-2", "card-3", "card-4"])
            self.assertNotIn("prompt", rows[0])
            self.assertNotIn("answer", rows[0])
            self.assertEqual(rows[-1]["effort"], "high")
            self.assertEqual(rows[-1]["duration_secs"], 4)


if __name__ == "__main__":
    unittest.main()
