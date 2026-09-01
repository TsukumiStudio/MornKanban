#!/usr/bin/env python3
"""Tests for gui/dashboard.py (the terminal setup dashboard) and its wiring
into gui/setup_cli.py.

Includes status collection under fixture HOME/config, terminal capability
fallback (non-TTY, NO_COLOR, TERM=dumb, narrow COLUMNS), display-width-aware
wrapping for long paths/Japanese text, and the install/uninstall preview +
confirm + summary flow end to end (via a real pty so sys.stdin.isatty() is
true, matching real interactive use).
"""
import importlib
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
DIST_FILES = ["kanban.sh", "kanban-setup.sh", "dispatcher_tui.py", "VERSION", "gui", "skills", "registry", "guard", ".gitignore"]


def _copy_dist(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in DIST_FILES:
        src = REPO / name
        target = dest / name
        if src.is_dir():
            shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, target)
    return dest


class DashboardModuleTests(unittest.TestCase):
    """Pure-function tests: no filesystem/env fixture needed."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "gui"))
        self.dashboard = importlib.import_module("dashboard")

    def test_terminal_caps_non_tty_forces_ascii_no_color(self):
        stdout = mock.Mock()
        stdout.isatty.return_value = False
        caps = self.dashboard.terminal_caps(env={}, stdout=stdout)
        self.assertFalse(caps["color"])
        self.assertFalse(caps["unicode"])
        self.assertFalse(caps["isatty"])

    def test_terminal_caps_no_color_env_disables_color_but_keeps_tty_unicode(self):
        stdout = mock.Mock()
        stdout.isatty.return_value = True
        caps = self.dashboard.terminal_caps(env={"NO_COLOR": "1", "LANG": "en_US.UTF-8"}, stdout=stdout)
        self.assertFalse(caps["color"])
        self.assertTrue(caps["unicode"])

    def test_terminal_caps_term_dumb_forces_ascii_no_color(self):
        stdout = mock.Mock()
        stdout.isatty.return_value = True
        caps = self.dashboard.terminal_caps(env={"TERM": "dumb"}, stdout=stdout)
        self.assertFalse(caps["color"])
        self.assertFalse(caps["unicode"])

    def test_terminal_caps_width_clamped_to_minimum(self):
        caps = self.dashboard.terminal_caps(env={}, stdout=mock.Mock(isatty=lambda: False), columns_override=5)
        self.assertGreaterEqual(caps["width"], 40)

    def test_display_width_counts_japanese_as_two_columns(self):
        self.assertEqual(self.dashboard.display_width("ab"), 2)
        self.assertEqual(self.dashboard.display_width("あい"), 4)
        self.assertEqual(self.dashboard.display_width("a あ b"), 6)

    def test_wrap_path_never_exceeds_width_for_long_path(self):
        long_path = "/Users/someone/very/long/nested/path/that/keeps/going/and/going/leaf"
        for line in self.dashboard.wrap_path(long_path, 20):
            self.assertLessEqual(self.dashboard.display_width(line), 20)
        self.assertEqual("".join(self.dashboard.wrap_path(long_path, 20)), long_path)

    def test_wrap_path_never_exceeds_width_for_long_japanese_text(self):
        text = "日本語の非常に長い説明文がここに続きます" * 3
        for line in self.dashboard.wrap_path(text, 24):
            self.assertLessEqual(self.dashboard.display_width(line), 24)
        self.assertEqual("".join(self.dashboard.wrap_path(text, 24)), text)

    def test_sanitize_strips_control_and_escape_characters(self):
        injected = "\x1b[31mFAKE\x1b[0m/path\x07"
        cleaned = self.dashboard.sanitize(injected)
        self.assertNotIn("\x1b", cleaned)
        self.assertNotIn("\x07", cleaned)
        self.assertIn("FAKE", cleaned)

    def test_render_status_never_exceeds_configured_width_ascii(self):
        stdout = mock.Mock(isatty=lambda: False)
        caps = self.dashboard.terminal_caps(env={}, stdout=stdout, columns_override=60)
        status = _fake_status()
        rendered = self.dashboard.render_status(status, caps)
        for line in rendered.splitlines():
            self.assertLessEqual(self.dashboard.display_width(line), 60)

    def test_render_status_colored_unicode_boxes_stay_aligned(self):
        caps = self.dashboard.terminal_caps(
            env={"TERM": "xterm-256color", "LANG": "ja_JP.UTF-8"},
            stdout=mock.Mock(isatty=lambda: True),
            columns_override=60,
        )
        rendered = self.dashboard.render_status(_fake_status(), caps)
        self.assertIn("\x1b[", rendered)
        for line in rendered.splitlines():
            if line.startswith(("│", "┌", "├", "└")):
                self.assertEqual(self.dashboard.display_width(line), 60)

    def test_render_status_has_boxed_sections_and_version(self):
        caps = self.dashboard.terminal_caps(
            env={}, stdout=mock.Mock(isatty=lambda: False), columns_override=60
        )
        rendered = self.dashboard.render_status(_fake_status(), caps)
        self.assertIn("+", rendered)
        self.assertIn("VERSION: 1.0.0", rendered)
        self.assertIn("秘書ガード: claude=enforced, codex=partial", rendered)

    def test_render_status_wraps_long_japanese_and_paths_without_overflow(self):
        stdout = mock.Mock(isatty=lambda: False)
        caps = self.dashboard.terminal_caps(env={}, stdout=stdout, columns_override=40)
        status = _fake_status()
        status["repo"] = "/very/長い/日本語を含む/とても長いパスの例/leaf-directory-name"
        rendered = self.dashboard.render_status(status, caps)
        for line in rendered.splitlines():
            self.assertLessEqual(self.dashboard.display_width(line), 40)

    def test_state_badge_uses_label_text_not_color_alone(self):
        stdout = mock.Mock(isatty=lambda: False)
        caps = self.dashboard.terminal_caps(env={}, stdout=stdout)
        for state in (
            self.dashboard.STATE_INSTALLED,
            self.dashboard.STATE_NOT_INSTALLED,
            self.dashboard.STATE_UPDATE,
            self.dashboard.STATE_REGISTERED,
            self.dashboard.STATE_EMPTY,
            self.dashboard.STATE_NEEDS_CHECK,
        ):
            badge = self.dashboard.state_badge(caps, state)
            self.assertIn(state, badge)  # label text present even with color off

    def test_build_uninstall_preview_distinguishes_kept_vs_removed(self):
        status = _fake_status()
        status["cli"]["state"] = self.dashboard.STATE_INSTALLED
        lines = self.dashboard.build_uninstall_preview(status)
        text = "\n".join(lines)
        self.assertIn("削除しない", text)
        self.assertIn(status["repo"], text)

    def test_build_update_preview_describes_reinstall_without_git(self):
        status = _fake_status()
        lines = self.dashboard.build_update_preview(status)
        text = "\n".join(lines)
        self.assertIn("Git操作: なし", text)
        self.assertNotIn("git pull", text.lower())
        self.assertIn(status["repo"], text)
        self.assertIn("変更しない", text)

    def test_guide_covers_required_flows(self):
        titles = [t for t, *_ in self.dashboard.GUIDE_FLOWS]
        for expected in (
            "初回 install", "update", "uninstall", "project で init",
            "秘書として開始", "秘書のboard管理", "projects add/list/remove",
            "send による別 project への投函",
        ):
            self.assertIn(expected, titles)


def _fake_status():
    return {
        "repo": "/tmp/repo",
        "local_version": "1.0.0",
        "cli": {"state": "未導入", "link": "/tmp/home/.local/bin/kanban", "target": None},
        "skills": {
            "Claude Code / kanban-dispatch": {"installed": False, "version": None, "repo": None, "state": "未導入"},
            "Claude Code / kanban-report": {"installed": False, "version": None, "repo": None, "state": "未導入"},
            "Codex / kanban-dispatch": {"installed": False, "version": None, "repo": None, "state": "未導入"},
            "Codex / kanban-report": {"installed": False, "version": None, "repo": None, "state": "未導入"},
        },
        "version": {"current": "1.0.0", "latest": "1.0.0", "state": "up-to-date", "error": None, "badge_state": "導入済み"},
        "registry": {"state": "登録なし", "path": "/tmp/home/.config/mornkanban/projects.json", "count": 0, "error": None},
        "project": {"state": "未導入", "root": None},
        "deps": {"herdr": False, "claude": False, "codex": False},
        "guard": {"claude": "enforced", "codex": "partial"},
    }


class CollectStatusFixtureTests(unittest.TestCase):
    """Exercises dashboard.collect_status() against a fixture HOME/config,
    covering: all-uninstalled, installed, update-available, in/out of a
    project, broken CLI symlink, and an
    unreachable latest-version source."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "gui"))
        self.dashboard = importlib.reload(importlib.import_module("dashboard"))
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "KANBAN_CONFIG_DIR": str(self.home / "cfg"),
                "KANBAN_VERSION_URL": "file://%s" % (REPO / "VERSION"),
            },
        )
        self.env_patch.start()
        # setup_core module-level constants were computed against the real
        # HOME at import time; repoint them at the fixture for this test.
        self.setup_core = importlib.import_module("setup_core")
        self.local_bin_patch = mock.patch.object(
            self.setup_core, "LOCAL_BIN", str(self.home / ".local" / "bin")
        )
        self.kanban_link_patch = mock.patch.object(
            self.setup_core, "KANBAN_LINK", str(self.home / ".local" / "bin" / "kanban")
        )
        self.claude_settings_patch = mock.patch.object(
            self.setup_core, "CLAUDE_SETTINGS_PATH", str(self.home / ".claude" / "settings.json")
        )
        self.skill_targets_patch = mock.patch.object(
            self.setup_core, "SKILL_TARGETS",
            {
                "Claude Code": str(self.home / "claude" / "kanban-dispatch"),
                "Codex": str(self.home / "agents" / "kanban-dispatch"),
            },
        )
        self.local_bin_patch.start()
        self.kanban_link_patch.start()
        self.claude_settings_patch.start()
        self.skill_targets_patch.start()
    def tearDown(self):
        self.skill_targets_patch.stop()
        self.claude_settings_patch.stop()
        self.kanban_link_patch.stop()
        self.local_bin_patch.stop()
        self.env_patch.stop()
        self.temp.cleanup()

    def test_empty_registry_is_not_reported_as_uninstalled(self):
        status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["cli"]["state"], self.dashboard.STATE_NOT_INSTALLED)
        self.assertEqual(status["skills"]["Claude Code"]["state"], self.dashboard.STATE_NOT_INSTALLED)
        self.assertEqual(status["registry"]["state"], self.dashboard.STATE_EMPTY)
        self.assertEqual(status["project"]["state"], self.dashboard.STATE_NOT_INSTALLED)

    def test_cli_installed_state(self):
        link = self.home / ".local" / "bin" / "kanban"
        link.parent.mkdir(parents=True)
        link.symlink_to(REPO / "kanban.sh")
        status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["cli"]["state"], self.dashboard.STATE_INSTALLED)
        self.assertEqual(status["cli"]["target"], str(REPO / "kanban.sh"))

    def test_cli_broken_symlink_needs_check(self):
        link = self.home / ".local" / "bin" / "kanban"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.home / "nonexistent-target")
        status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["cli"]["state"], self.dashboard.STATE_NEEDS_CHECK)

    def test_skill_update_available_when_installed_version_differs(self):
        directory = self.home / "claude" / "kanban-dispatch"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "The authoritative MornKanban checkout is `%s` (installed\nversion `0.0.1`).\n" % REPO,
            encoding="utf-8",
        )
        status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["skills"]["Claude Code"]["state"], self.dashboard.STATE_UPDATE)

    def test_skill_installed_when_version_matches(self):
        local = self.setup_core.local_version()
        directory = self.home / "claude" / "kanban-dispatch"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "The authoritative MornKanban checkout is `%s` (installed\nversion `%s`).\n" % (REPO, local),
            encoding="utf-8",
        )
        status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["skills"]["Claude Code"]["state"], self.dashboard.STATE_INSTALLED)

    def test_project_detected_when_inside_kanban_project(self):
        project = self.root / "proj"
        (project / ".kanban" / "todo").mkdir(parents=True)
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        status = self.dashboard.collect_status(cwd=str(nested))
        self.assertEqual(status["project"]["state"], self.dashboard.STATE_INSTALLED)
        self.assertEqual(status["project"]["root"], str(project.resolve()))

    def test_project_not_detected_outside_any_kanban_project(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        status = self.dashboard.collect_status(cwd=str(outside))
        self.assertEqual(status["project"]["state"], self.dashboard.STATE_NOT_INSTALLED)

    def test_latest_version_unreachable_is_needs_check_not_fatal(self):
        with mock.patch.dict(os.environ, {"KANBAN_VERSION_URL": "file:///nonexistent/VERSION"}):
            status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["version"]["badge_state"], self.dashboard.STATE_NEEDS_CHECK)
        self.assertIsNone(status["version"]["latest"])

    def test_registry_count_reflects_registered_projects(self):
        registry = importlib.import_module("registry.store")
        importlib.reload(registry)
        project = self.root / "regproj"
        (project / ".kanban").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"KANBAN_PROJECTS_FILE": str(self.home / "cfg" / "projects.json")}):
            registry.add("regproj", str(project))
            status = self.dashboard.collect_status(cwd=str(self.root))
        self.assertEqual(status["registry"]["count"], 1)
        self.assertEqual(status["registry"]["state"], self.dashboard.STATE_REGISTERED)


class InteractiveWizardPtyTests(unittest.TestCase):
    """End-to-end preview -> confirm -> summary flow through a real pty, so
    sys.stdin.isatty() is true (matching real terminal use). Also confirms
    that declining at the confirm step makes no changes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = _copy_dist(Path(self.temp.name) / "repo")
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env["KANBAN_VERSION_URL"] = "file://%s" % (self.dist / "VERSION")

    def tearDown(self):
        self.temp.cleanup()

    def _run_wizard(self, inputs, wait=1.0):
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            ["python3", "gui/setup_cli.py"],
            cwd=str(self.dist), stdin=slave, stdout=slave, stderr=slave, env=self.env,
        )
        os.close(slave)
        out = b""
        try:
            out += self._read_available(master, wait)
            for line in inputs:
                os.write(master, (line + "\n").encode())
                out += self._read_available(master, wait)
            proc.wait(timeout=10)
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            os.close(master)
        return out.decode(errors="replace")

    @staticmethod
    def _read_available(fd, timeout):
        data = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.1)
            if fd in r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                data += chunk
            elif data:
                break
        return data

    def test_install_declined_at_confirm_makes_no_changes(self):
        output = self._run_wizard(["y", "N"])
        self.assertIn("これから行う変更", output)
        self.assertIn("中止しました", output)
        self.assertFalse((self.home / ".local" / "bin" / "kanban").exists())

    def test_install_confirmed_creates_symlink_and_skills(self):
        output = self._run_wizard(["y", "y"], wait=2.0)
        self.assertIn("install 結果", output)
        link = self.home / ".local" / "bin" / "kanban"
        self.assertTrue(link.is_symlink())
        skill = self.home / ".claude" / "skills" / "kanban-dispatch" / "SKILL.md"
        self.assertTrue(skill.is_file())

    def test_uninstall_preview_lists_what_is_kept(self):
        self._run_wizard(["y", "y"], wait=2.0)
        output = self._run_wizard(["u", "N"])
        self.assertIn("削除しない", output)
        self.assertIn(str(self.dist), output)
        # declined: still installed
        self.assertTrue((self.home / ".local" / "bin" / "kanban").is_symlink())

    def test_update_preview_shown_and_declined_makes_no_changes(self):
        output = self._run_wizard(["s", "N"])
        self.assertIn("これから行う変更 (update)", output)
        self.assertIn("Git操作: なし", output)
        self.assertIn("中止しました", output)

    def test_plain_n_does_nothing(self):
        output = self._run_wizard(["N"])
        self.assertNotIn("これから行う変更", output)
        self.assertNotIn("どこで・何をすると", output)
        self.assertFalse((self.home / ".local" / "bin" / "kanban").exists())

    def test_help_is_on_demand_and_returns_to_menu(self):
        output = self._run_wizard(["h", "N"])
        self.assertIn("どこで・何をすると・何が起こるか", output)
        self.assertEqual(output.count("h=ヘルプ"), 2)
        self.assertFalse((self.home / ".local" / "bin" / "kanban").exists())


class NonInteractiveCompatTests(unittest.TestCase):
    """Regression: existing non-interactive subcommands and non-tty status
    display must be unaffected by the new dashboard."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = _copy_dist(Path(self.temp.name) / "repo")
        self.env = os.environ.copy()
        self.env["HOME"] = str(Path(self.temp.name) / "home")
        self.env["KANBAN_VERSION_URL"] = "file://%s" % (self.dist / "VERSION")

    def tearDown(self):
        self.temp.cleanup()

    def test_non_tty_status_display_exits_zero_without_prompting(self):
        result = subprocess.run(
            ["python3", "gui/setup_cli.py"],
            cwd=str(self.dist), input="", capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MornKanban", result.stdout)
        self.assertNotIn("どこで・何をすると", result.stdout)
        # no changes made
        self.assertFalse((Path(self.env["HOME"]) / ".local" / "bin" / "kanban").exists())

    def test_version_subcommand_still_bypasses_dashboard(self):
        result = subprocess.run(
            ["python3", "gui/setup_cli.py", "version"],
            cwd=str(self.dist), capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("current:", result.stdout)
        self.assertNotIn("どこで・何をすると", result.stdout)

    def test_update_subcommand_prints_preview_and_summary_without_confirmation(self):
        result = subprocess.run(
            ["python3", "gui/setup_cli.py", "update"],
            cwd=str(self.dist), input="", capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("これから行う変更 (update)", result.stdout)
        self.assertIn("update 結果", result.stdout)
        self.assertIn("Git操作: なし", result.stdout)
        self.assertNotIn("[y/N]", result.stdout)

    def test_install_subcommand_runs_without_confirmation_prompt(self):
        result = subprocess.run(
            ["python3", "gui/setup_cli.py", "install"],
            cwd=str(self.dist), capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        link = Path(self.env["HOME"]) / ".local" / "bin" / "kanban"
        self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
