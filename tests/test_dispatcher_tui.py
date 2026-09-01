import datetime
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import dispatcher_tui


class DispatcherTuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for state in dispatcher_tui.STATES:
            (self.root / ".kanban" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def card(self, state, card_id, title, attempts=1, max_attempts=3):
        path = self.root / ".kanban" / state / (card_id + ".md")
        path.write_text(
            "---\nid: %s\ntitle: %s\nattempts: %s\nmax_attempts: %s\n---\n"
            % (card_id, title, attempts, max_attempts),
            encoding="utf-8",
        )
        return path

    def test_fixed_header_fits_narrow_pane_and_reports_hidden_active_rows(self):
        for n in range(6):
            self.card("doing", "c%s" % n, "日本語の長いタスク名" * 5)
        snapshot = dispatcher_tui.scan_board(str(self.root))
        lines = dispatcher_tui.render_header(
            snapshot,
            [],
            width=76,
            max_lines=10,
            status="RUNNING",
            now=datetime.datetime(2026, 9, 1, 15, 10, 49),
        )

        self.assertEqual(len(lines), 10)
        self.assertTrue(any("ACTIVE 4/6" in line for line in lines))
        self.assertEqual(lines[-1].strip("─- "), "LIVE LOG")
        for line in lines:
            self.assertLessEqual(dispatcher_tui.display_width(line), 76)

    def test_active_row_shows_live_agent_routing_and_drops_it_after_exit(self):
        self.card("doing", "c1", "表示を修正")
        activity = self.root / ".kanban" / "activity.jsonl"
        started = {
            "event": "agent_started",
            "card_id": "c1",
            "backend": "claude",
            "model": "sonnet",
            "effort": "high",
            "agent_name": "worker-1",
        }
        activity.write_text(json.dumps(started) + "\n", encoding="utf-8")

        lines = dispatcher_tui.render_header(
            dispatcher_tui.scan_board(str(self.root)), [], width=100
        )
        self.assertTrue(
            any("ACK:…  AI:claude  MODEL:sonnet  EFFORT:high" in line for line in lines)
        )

        acknowledged = dict(started, event="agent_acknowledged")
        with activity.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(acknowledged) + "\n")
        lines = dispatcher_tui.render_header(
            dispatcher_tui.scan_board(str(self.root)), [], width=100
        )
        self.assertTrue(any("ACK:✓" in line for line in lines))

        finished = dict(started, event="answer_accepted")
        with activity.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(finished) + "\n")
        lines = dispatcher_tui.render_header(
            dispatcher_tui.scan_board(str(self.root)), [], width=100
        )
        self.assertTrue(
            any("AI:unknown  MODEL:unknown  EFFORT:unknown" in line for line in lines)
        )
        self.assertTrue(any("ACK:-" in line for line in lines))

    def test_board_move_becomes_a_timestamped_transition(self):
        card = self.card("todo", "c1", "表示を修正")
        before = dispatcher_tui.scan_board(str(self.root))
        card.rename(self.root / ".kanban" / "doing" / card.name)
        after = dispatcher_tui.scan_board(str(self.root))

        moves = dispatcher_tui.diff_board(
            before,
            after,
            now=datetime.datetime(2026, 9, 1, 15, 11, 7),
        )

        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["from"], "todo")
        self.assertEqual(moves[0]["to"], "doing")
        self.assertEqual(moves[0]["at"], "15:11:07")
        self.assertEqual(moves[0]["title"], "表示を修正")

    def test_non_tty_mode_preserves_output_log_and_exit_status(self):
        log = self.root / ".kanban" / "wt" / "dispatcher.log"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO / "dispatcher_tui.py"),
                "--root",
                str(self.root),
                "--log",
                str(log),
                "--",
                sys.executable,
                "-c",
                "import sys; print('dispatcher child output'); sys.exit(7)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("dispatcher child output", result.stdout)
        self.assertIn("dispatcher child output", log.read_text(encoding="utf-8"))

    def test_terminating_plain_tui_also_terminates_dispatcher_child(self):
        log = self.root / ".kanban" / "wt" / "dispatcher.log"
        child_pid = self.root / "child.pid"
        process = subprocess.Popen(
            [
                sys.executable, str(REPO / "dispatcher_tui.py"),
                "--root", str(self.root), "--log", str(log), "--",
                sys.executable, "-c",
                "import os,time; open(%r,'w').write(str(os.getpid())); time.sleep(30)" % str(child_pid),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            for _ in range(100):
                if child_pid.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(child_pid.exists())
            pid = int(child_pid.read_text())
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=5)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if process.poll() is None:
                process.kill()

    def test_retired_monitor_command_is_absent_from_cli(self):
        result = subprocess.run(
            ["bash", str(REPO / "kanban.sh"), "monitor"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("monitor", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
