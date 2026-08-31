#!/usr/bin/env python3
import importlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SECRETARY = REPO / "kanban-secretary.sh"
KANBAN_SH = REPO / "kanban.sh"
KANBAN_SETUP_SH = REPO / "kanban-setup.sh"

# Distribution files copied to build a standalone repo outside this worktree
# (setup_core.install_cli/install_skills/run_update all refuse to run from a
# .kanban/wt/<id> worktree by design).
DIST_FILES = ["kanban.sh", "kanban-secretary.sh", "kanban-setup.sh", "VERSION", "gui", "skills", "registry", "guard", ".gitignore"]


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


def _init_git_repo(path, origin=None):
    run = lambda *a: subprocess.run(
        ["git", "-C", str(path), *a], check=True, capture_output=True, text=True
    )
    run("init", "-q")
    run("checkout", "-q", "-b", "main")
    run("add", "-A")
    run("-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "init")
    if origin is not None:
        run("remote", "add", "origin", str(origin))
        run("push", "-q", "origin", "main")
        run("branch", "-q", "--set-upstream-to=origin/main", "main")
    return run


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
        self.assertIn("KANBAN_RESOLVE_CMD=", log)
        self.assertIn("KANBAN_HERDR_ROLE=resolver", log)
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
        with mock.patch.object(self.setup_core, "SKILL_TARGETS", self.targets), \
             mock.patch.object(self.setup_core, "in_worktree", return_value=False):
            messages = self.setup_core.install_skills(force=True)
            status = self.setup_core.skill_status()

        self.assertEqual(status, {"Claude Code": True, "Codex": True})
        self.assertEqual(len(messages), 2)
        for directory in self.targets.values():
            skill = Path(directory) / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            self.assertNotIn("__MORNKANBAN_REPO__", content)
            self.assertNotIn("__MORNKANBAN_VERSION__", content)
            self.assertIn(str(REPO), content)
            self.assertIn(self.setup_core.local_version(), content)
            self.assertTrue((Path(directory) / "agents" / "openai.yaml").is_file())


class VersionComparisonTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO / "gui"))
        self.setup_core = importlib.import_module("setup_core")

    def test_parse_version_rejects_non_semantic_strings(self):
        with self.assertRaises(ValueError):
            self.setup_core.parse_version("1.2")
        with self.assertRaises(ValueError):
            self.setup_core.parse_version("1.2.x")

    def test_compare_versions_orders_semantically_not_lexically(self):
        # lexical comparison would put "1.9.0" after "1.10.0"; semantic must not.
        self.assertEqual(self.setup_core.compare_versions("1.9.0", "1.10.0"), -1)
        self.assertEqual(self.setup_core.compare_versions("1.10.0", "1.9.0"), 1)
        self.assertEqual(self.setup_core.compare_versions("0.1.0", "0.1.0"), 0)

    def test_version_report_uses_file_url_override_no_network(self):
        with tempfile.TemporaryDirectory() as td:
            latest_file = Path(td) / "VERSION"
            latest_file.write_text("9.9.9\n", encoding="utf-8")
            env = {"KANBAN_VERSION_URL": "file://%s" % latest_file}
            with mock.patch.dict(os.environ, env):
                report = self.setup_core.version_report()
        self.assertEqual(report["latest"], "9.9.9")
        self.assertEqual(report["state"], "update-available")

    def test_version_report_reports_local_ahead(self):
        with tempfile.TemporaryDirectory() as td:
            latest_file = Path(td) / "VERSION"
            latest_file.write_text("0.0.1\n", encoding="utf-8")
            env = {"KANBAN_VERSION_URL": "file://%s" % latest_file}
            with mock.patch.dict(os.environ, env):
                report = self.setup_core.version_report()
        self.assertEqual(report["state"], "local-ahead")

    def test_version_report_unreachable_source_is_unknown_not_fatal(self):
        env = {"KANBAN_VERSION_URL": "file:///nonexistent/VERSION"}
        with mock.patch.dict(os.environ, env):
            report = self.setup_core.version_report()
        self.assertIsNone(report["latest"])
        self.assertEqual(report["state"], "unknown")
        self.assertIsNotNone(report["error"])


class SymlinkEntryPointTests(unittest.TestCase):
    """kanban.sh must resolve its own real location when invoked through a
    symlink (the ~/.local/bin/kanban entry point), not through $PWD."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        bindir = Path(self.temp.name) / "bin"
        bindir.mkdir()
        self.link = bindir / "kanban"
        self.link.symlink_to(KANBAN_SH)

    def tearDown(self):
        self.temp.cleanup()

    def test_version_flag_resolves_through_symlink_without_network(self):
        result = subprocess.run(
            [str(self.link), "--version"], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), (REPO / "VERSION").read_text(encoding="utf-8").strip())

    def test_version_subcommand_resolves_gui_through_symlink(self):
        env = os.environ.copy()
        env["KANBAN_VERSION_URL"] = "file://%s" % (REPO / "VERSION")
        result = subprocess.run(
            [str(self.link), "version"], capture_output=True, text=True, env=env, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("current: %s" % (REPO / "VERSION").read_text(encoding="utf-8").strip(), result.stdout)
        self.assertIn("state: up-to-date", result.stdout)


class InstallUninstallTests(unittest.TestCase):
    """End-to-end install/uninstall via kanban.sh, run against a copy of the
    distribution outside this worktree (install/uninstall refuse to run
    from inside a .kanban/wt/<id> checkout by design)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = _copy_dist(Path(self.temp.name) / "repo")
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [str(self.dist / "kanban.sh"), *args],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_install_creates_symlink_and_versioned_skills(self):
        result = self._run("install")
        self.assertEqual(result.returncode, 0, result.stderr)

        link = self.home / ".local" / "bin" / "kanban"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(str(link)), os.path.realpath(str(self.dist / "kanban.sh")))

        skill = self.home / ".claude" / "skills" / "kanban-dispatch" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        self.assertIn(str(self.dist), content)
        self.assertIn((self.dist / "VERSION").read_text(encoding="utf-8").strip(), content)

    def test_install_is_idempotent_repair(self):
        first = self._run("install")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self._run("install")
        self.assertEqual(second.returncode, 0, second.stderr)
        link = self.home / ".local" / "bin" / "kanban"
        self.assertTrue(link.is_symlink())

    def test_uninstall_removes_cli_and_skills_only(self):
        self._run("install")
        result = self._run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertFalse((self.home / ".local" / "bin" / "kanban").exists())
        self.assertFalse((self.home / ".claude" / "skills" / "kanban-dispatch").exists())
        self.assertFalse((self.home / ".codex" / "skills" / "kanban-dispatch").exists())
        # the repository checkout itself is untouched by uninstall
        self.assertTrue((self.dist / "kanban.sh").exists())


class GitUpdateTests(unittest.TestCase):
    """Real temporary git remote + clone: exercises the actual
    `git pull --ff-only origin main` path, not a mock."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)

        self.clone = _copy_dist(root / "clone")
        self.run_git = _init_git_repo(self.clone, origin=self.origin)
        subprocess.run(
            ["git", "-C", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )

        self.home = root / "home"
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [str(self.clone / "kanban.sh"), *args],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def _push_upstream_version_bump(self, new_version):
        work = Path(self.temp.name) / "upstream_work"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(work)], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
        (work / "VERSION").write_text(new_version + "\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(work), "-c", "user.email=test@example.com",
             "-c", "user.name=test", "commit", "-q", "-am", "bump version"],
            check=True,
        )
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)

    def test_update_refuses_dirty_checkout(self):
        (self.clone / "VERSION").write_text("9.9.9\n", encoding="utf-8")  # uncommitted local edit

        result = self._run("update")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty", result.stdout + result.stderr)
        # local edit must be untouched -- update never discards user changes
        self.assertEqual((self.clone / "VERSION").read_text(encoding="utf-8").strip(), "9.9.9")

    def test_update_refuses_detached_head(self):
        sha = subprocess.run(
            ["git", "-C", str(self.clone), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(self.clone), "checkout", "-q", sha], check=True)

        result = self._run("update")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("detached", result.stdout + result.stderr)

    def test_update_fast_forwards_and_reinstalls_versioned_skills(self):
        self._push_upstream_version_bump("9.9.1")

        result = self._run("update")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.clone / "VERSION").read_text(encoding="utf-8").strip(), "9.9.1")

        skill = self.home / ".claude" / "skills" / "kanban-dispatch" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        self.assertIn("9.9.1", content)
        self.assertIn(str(self.clone), content)

        link = self.home / ".local" / "bin" / "kanban"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(str(link)), os.path.realpath(str(self.clone / "kanban.sh")))


class ArgumentForwardingTests(unittest.TestCase):
    """kanban-setup.sh must forward its argv to gui/setup_cli.py -- documented
    as a known silent-failure risk (a missing "$@" makes every explicit
    subcommand silently fall back to the interactive wizard)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = _copy_dist(Path(self.temp.name) / "repo")
        self.env = os.environ.copy()
        self.env["HOME"] = str(Path(self.temp.name) / "home")
        self.env["KANBAN_VERSION_URL"] = "file://%s" % (self.dist / "VERSION")

    def tearDown(self):
        self.temp.cleanup()

    def test_version_command_is_forwarded_and_not_swallowed_by_wizard(self):
        result = subprocess.run(
            [str(self.dist / "kanban-setup.sh"), "version"],
            capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("current:", result.stdout)
        # the interactive wizard's status_summary()/prompt banner must not appear
        self.assertNotIn("kanban CLI:", result.stdout)

    def test_unknown_command_is_forwarded_and_rejected_not_ignored(self):
        result = subprocess.run(
            [str(self.dist / "kanban-setup.sh"), "bogus-command"],
            capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bogus-command", result.stdout + result.stderr)


class HerdrAgentWorkerBackendTests(unittest.TestCase):
    """herdr-agent-worker.sh must pick --kind claude|codex from the card's own
    routing (KANBAN_CARD_BACKEND / KANBAN_REVIEWER), never a hardcoded
    --kind claude, and must never mix Claude-only args (--permission-mode,
    --model) with Codex-only args (-s, -a, -m) or vice versa. Regression for
    a visible codex/gpt-5.6-terra card being started as Claude and failing
    immediately on the unknown model name."""

    WORKER = REPO / "herdr-agent-worker.sh"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / "herdr.log"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._write_fake_herdr()
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                # Minimal, real-CLI-free PATH: a test asserting "no backend
                # CLI available" must not accidentally see this machine's own
                # installed claude/codex from the inherited PATH.
                "PATH": str(self.bin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w1:p1",
                "HERDR_TEST_LOG": str(self.log),
            }
        )
        # KANBAN_HERDR_ROLE etc. must come only from each test's overrides.
        for stray in ("KANBAN_HERDR_ROLE", "KANBAN_CARD_BACKEND", "KANBAN_REVIEWER",
                      "KANBAN_CARD_MODEL", "KANBAN_REVIEW_MODEL", "KANBAN_BACKEND_ORDER",
                      "KANBAN_CODEX_SANDBOX", "KANBAN_ALLOWED_TOOLS",
                      "KANBAN_RESOLVER", "KANBAN_RESOLVE_MODEL"):
            self.base_env.pop(stray, None)

    def tearDown(self):
        self.temp.cleanup()

    def _write_fake_herdr(self):
        herdr = self.bin / "herdr"
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
                  "agent get")
                    printf '%s\n' '{"result":{"agent":{"agent_status":"idle"}}}'
                    ;;
                  "agent read")
                    echo "mock agent transcript"
                    ;;
                  *) printf '%s\n' '{"result":{}}' ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        herdr.chmod(0o755)

    def _write_fake_cli(self, *names):
        for n in names:
            p = self.bin / n
            p.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            p.chmod(0o755)

    def _run_worker(self, env_overrides, stdin_text="task body"):
        env = self.base_env.copy()
        env.update(env_overrides)
        return subprocess.run(
            [str(self.WORKER)],
            input=stdin_text,
            text=True,
            capture_output=True,
            env=env,
            cwd=str(self.worktree),
            check=False,
        )

    def _start_call(self):
        log = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        starts = [ln for ln in log.splitlines() if ln.startswith("agent start ")]
        self.assertEqual(len(starts), 1, log)
        return starts[0]

    def test_claude_worker_gets_claude_only_args(self):
        self._write_fake_cli("claude")
        result = self._run_worker({"KANBAN_CARD_BACKEND": "claude", "KANBAN_CARD_MODEL": "sonnet"})
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind claude", start)
        self.assertIn("--permission-mode acceptEdits", start)
        self.assertIn("--model sonnet", start)
        self.assertNotIn("-s ", start)
        self.assertNotIn("-a never", start)

    def test_codex_worker_gets_codex_only_args(self):
        self._write_fake_cli("codex")
        result = self._run_worker({"KANBAN_CARD_BACKEND": "codex", "KANBAN_CARD_MODEL": "gpt-5.6-terra"})
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind codex", start)
        self.assertIn("-s workspace-write", start)
        self.assertIn("-a never", start)
        self.assertIn("-m gpt-5.6-terra", start)
        self.assertNotIn("--permission-mode", start)
        self.assertNotIn("--model", start)
        self.assertNotIn("sonnet", start)

    def test_codex_reviewer_is_read_only_with_review_model(self):
        self._write_fake_cli("codex")
        result = self._run_worker(
            {
                "KANBAN_HERDR_ROLE": "reviewer",
                "KANBAN_REVIEWER": "codex",
                "KANBAN_REVIEW_MODEL": "gpt-5.6-terra",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind codex", start)
        self.assertIn("-s read-only", start)
        self.assertIn("-a never", start)
        self.assertIn("-m gpt-5.6-terra", start)
        self.assertNotIn("workspace-write", start)

    def test_codex_worker_without_model_omits_dash_m_and_sonnet(self):
        self._write_fake_cli("codex")
        result = self._run_worker({"KANBAN_CARD_BACKEND": "codex"})
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertNotIn(" -m ", start)
        self.assertNotIn("sonnet", start)

    def test_auto_backend_resolves_via_backend_order(self):
        self._write_fake_cli("codex")  # claude intentionally absent from PATH
        result = self._run_worker(
            {"KANBAN_CARD_BACKEND": "auto", "KANBAN_BACKEND_ORDER": "codex claude"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind codex", start)

    def test_unsupported_backend_fails_before_creating_a_pane(self):
        self._write_fake_cli("claude", "codex")
        result = self._run_worker({"KANBAN_CARD_BACKEND": "gpt-5.6-terra"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gpt-5.6-terra", result.stderr)
        log = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        self.assertNotIn("pane split", log)

    def test_auto_backend_with_no_cli_available_fails_clearly(self):
        result = self._run_worker({"KANBAN_CARD_BACKEND": "auto", "KANBAN_BACKEND_ORDER": "claude codex"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no agent CLI found", result.stderr)

    def test_resolver_role_gets_editing_claude_args_from_resolver_env(self):
        self._write_fake_cli("claude")
        result = self._run_worker(
            {
                "KANBAN_HERDR_ROLE": "resolver",
                "KANBAN_RESOLVER": "claude",
                "KANBAN_RESOLVE_MODEL": "sonnet",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind claude", start)
        self.assertIn("--permission-mode acceptEdits", start)
        self.assertIn("--model sonnet", start)

    def test_resolver_role_gets_workspace_write_codex_args(self):
        self._write_fake_cli("codex")
        result = self._run_worker(
            {
                "KANBAN_HERDR_ROLE": "resolver",
                "KANBAN_RESOLVER": "codex",
                "KANBAN_RESOLVE_MODEL": "gpt-5.6-terra",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind codex", start)
        self.assertIn("-s workspace-write", start)
        self.assertIn("-a never", start)
        self.assertIn("-m gpt-5.6-terra", start)
        self.assertNotIn("read-only", start)


class DispatcherWorkflowTests(unittest.TestCase):
    """kanban.sh `run` end-to-end via KANBAN_WORKER_CMD / KANBAN_REVIEW_CMD /
    KANBAN_RESOLVE_CMD mock scripts -- no real agent CLI is spent, but the
    actual git worktree/branch/merge/conflict machinery runs for real."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self._git("init", "-q")
        self._git("checkout", "-q", "-b", "main")
        (self.project / "seed.txt").write_text("seed\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        subprocess.run(
            [str(KANBAN_SH), "init"], cwd=self.project, check=True, capture_output=True, text=True
        )
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["KANBAN_TEST_MAIN_ROOT"] = str(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(self.project), *args], check=check, capture_output=True, text=True
        )

    def _write_script(self, name, content):
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _add_card(self, title, body="task body", backend=None, model=None):
        args = ["add", title]
        if backend:
            args += ["-b", backend]
        if model:
            args += ["-m", model]
        r = subprocess.run(
            [str(KANBAN_SH), *args],
            input=body, cwd=self.project, env=self.env, check=True, capture_output=True, text=True,
        )
        return Path(r.stdout.strip())

    def _run(self, *args, env_overrides=None, timeout=90):
        env = self.env.copy()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [str(KANBAN_SH), *args], cwd=self.project, env=env,
            check=False, capture_output=True, text=True, timeout=timeout,
        )

    OK_REVIEW = textwrap.dedent(
        """\
        #!/usr/bin/env bash
        cat >/dev/null
        printf '{"score": 95, "feedback": "ok"}\\n'
        """
    )

    def test_no_conflict_merge_still_works(self):
        worker = self._write_script(
            "worker.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'from worker\\n' > new_file.txt
                """
            ),
        )
        review = self._write_script("review.sh", self.OK_REVIEW)
        self._add_card("regression card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = list((self.project / ".kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        self.assertEqual((self.project / "new_file.txt").read_text(encoding="utf-8"), "from worker\n")

    def _seed_conflict(self):
        (self.project / "file.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add file")

    def _conflicting_worker(self):
        # Simulates another card merging into base WHILE this worker is
        # running (a real ordering race), by committing straight to the base
        # checkout (KANBAN_TEST_MAIN_ROOT) before touching its own worktree
        # copy of the same line -- the two edits then genuinely conflict at
        # merge time instead of just being a stale worktree base.
        return self._write_script(
            "worker.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                ( cd "$KANBAN_TEST_MAIN_ROOT" && printf 'main change\\n' > file.txt && \\
                  git add file.txt && \\
                  git -c user.email=t@t -c user.name=t commit -q -m "main edit" )
                printf 'card change\\n' > file.txt
                """
            ),
        )

    def test_merge_conflict_after_review_goes_to_resolver_then_done(self):
        self._seed_conflict()
        worker = self._conflicting_worker()
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                input=$(cat)
                if printf '%s' "$input" | grep -q conflict; then
                  printf '{"score": 90, "feedback": "resolve ok"}\\n'
                else
                  printf '{"score": 95, "feedback": "ok"}\\n'
                fi
                """
            ),
        )
        resolve = self._write_script(
            "resolve.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'merged change\\n' > file.txt
                """
            ),
        )
        self._add_card("conflicting card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_RESOLVE_CMD": str(resolve),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = list((self.project / ".kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        card_text = done[0].read_text(encoding="utf-8")
        self.assertIn("merge conflict", card_text)
        self.assertIn("resolver output", card_text)
        self.assertEqual((self.project / "file.txt").read_text(encoding="utf-8"), "merged change\n")
        # no double-merge: the original card branch must be gone, only the
        # resolve branch's commit landed on main.
        branches = self._git("branch", "--list").stdout
        self.assertNotIn("kanban/", branches)
        self.assertNotIn("kanban-resolve/", branches)

    def test_resolver_retries_on_low_review_score_then_passes(self):
        self._seed_conflict()
        worker = self._conflicting_worker()
        count_file = Path(self.temp.name) / "resolve_review_count"
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                input=$(cat)
                if printf '%s' "$input" | grep -q conflict; then
                  n=0
                  [[ -f "{count_file}" ]] && n=$(cat "{count_file}")
                  n=$((n + 1))
                  echo "$n" > "{count_file}"
                  if [[ $n -lt 2 ]]; then
                    printf '{{"score": 30, "feedback": "still conflicted"}}\\n'
                  else
                    printf '{{"score": 90, "feedback": "resolve ok"}}\\n'
                  fi
                else
                  printf '{{"score": 95, "feedback": "ok"}}\\n'
                fi
                """
            ),
        )
        resolve = self._write_script(
            "resolve.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'merged change\\n' > file.txt
                """
            ),
        )
        self._add_card("retry-then-pass card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_RESOLVE_CMD": str(resolve),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = list((self.project / ".kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        card_text = done[0].read_text(encoding="utf-8")
        self.assertIn("still conflicted", card_text)
        self.assertEqual(count_file.read_text(encoding="utf-8").strip(), "2")

    def test_resolve_max_attempts_exceeded_moves_to_failed_with_history(self):
        cfg = self.project / ".kanban" / "KANBAN.md"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("resolve_max_attempts: 2", text)
        cfg.write_text(text.replace("resolve_max_attempts: 2", "resolve_max_attempts: 1"), encoding="utf-8")

        self._seed_conflict()
        worker = self._conflicting_worker()
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                input=$(cat)
                if printf '%s' "$input" | grep -q conflict; then
                  printf '{"score": 10, "feedback": "never good enough"}\\n'
                else
                  printf '{"score": 95, "feedback": "ok"}\\n'
                fi
                """
            ),
        )
        resolve = self._write_script(
            "resolve.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'still bad\\n' > file.txt
                """
            ),
        )
        self._add_card("unresolvable card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_RESOLVE_CMD": str(resolve),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        failed = list((self.project / ".kanban" / "failed").glob("*.md"))
        self.assertEqual(len(failed), 1, result.stdout + result.stderr)
        card_text = failed[0].read_text(encoding="utf-8")
        self.assertIn("conflict files: file.txt", card_text)
        self.assertIn("never good enough", card_text)
        self.assertIn("kept for manual inspection", card_text)
        branches = self._git("branch", "--list").stdout
        self.assertIn("kanban-resolve/", branches)
        self.assertIn("kanban/", branches)
        # branches must actually still exist on disk, not just claimed
        self.assertTrue((self.project / ".git").exists())

    def test_resolve_cmd_receives_card_routing_and_conflict_context(self):
        self._seed_conflict()
        worker = self._conflicting_worker()
        review = self._write_script("review.sh", self.OK_REVIEW)
        dump_file = Path(self.temp.name) / "resolve_env.txt"
        resolve = self._write_script(
            "resolve.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                {{
                  echo "backend=$KANBAN_CARD_BACKEND"
                  echo "model=$KANBAN_CARD_MODEL"
                  echo "conflict=$KANBAN_CONFLICT_FILES"
                  echo "base=$KANBAN_BASE_BRANCH"
                  echo "card=$KANBAN_CARD_BRANCH"
                }} > "{dump_file}"
                printf 'merged change\\n' > file.txt
                """
            ),
        )
        card = self._add_card("routed card", backend="claude", model="opus")
        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_RESOLVE_CMD": str(resolve),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(dump_file.exists(), result.stdout + result.stderr)
        dumped = dump_file.read_text(encoding="utf-8")
        self.assertIn("backend=claude", dumped)
        self.assertIn("model=opus", dumped)
        self.assertIn("conflict=file.txt", dumped)
        self.assertIn("base=main", dumped)
        self.assertIn(f"card=kanban/{card_id}", dumped)

    def test_dispatcher_refuses_second_run_while_lock_is_live(self):
        lock = self.project / ".kanban" / ".lock"
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")

        result = self._run("run", "--once")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already running", result.stdout + result.stderr)

    def test_resolving_orphan_is_reclaimed_and_not_double_processed(self):
        card = self._add_card("orphan card")
        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)

        # Simulate a dispatcher that crashed mid-resolve: the card is stuck in
        # resolving/ with leftover worktrees/branches from the interrupted
        # attempt.
        wt = self.project / ".kanban" / "wt"
        self._git("worktree", "add", "-q", "-b", f"kanban/{card_id}", str(wt / card_id), "main")
        self._git(
            "worktree", "add", "-q", "-b", f"kanban-resolve/{card_id}",
            str(wt / f"{card_id}-resolve"), "main",
        )
        resolving_dir = self.project / ".kanban" / "resolving"
        resolving_dir.mkdir(exist_ok=True)
        card.rename(resolving_dir / card.name)

        worker = self._write_script(
            "worker.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'ok\\n' > out.txt
                """
            ),
        )
        review = self._write_script("review.sh", self.OK_REVIEW)

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = list((self.project / ".kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        self.assertEqual(len(list(resolving_dir.glob("*.md"))), 0)
        branches = self._git("branch", "--list").stdout
        self.assertNotIn(f"kanban/{card_id}\n", branches.replace(" ", "\n"))
        self.assertNotIn(f"kanban-resolve/{card_id}", branches)


class SecretaryDoesNotHoldCardsBackContractTests(unittest.TestCase):
    """Locks the "秘書は競合判断で起票を止めない" contract into the docs so a
    future edit cannot silently reintroduce a hold-back-on-conflict rule."""

    def test_readme_forbids_holding_cards_back_over_conflict_or_order(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("Never hold a card back over file overlap, dependency order", text)
        self.assertIn("resolving", text)
        self.assertIn("blocked", text)

    def test_skill_forbids_holding_cards_back_over_conflict_or_order(self):
        text = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("Never hold a card back over file overlap, dependency order", normalized)

    def test_kanban_md_template_forbids_holding_cards_back(self):
        text = (REPO / "kanban.sh").read_text(encoding="utf-8")
        self.assertIn("秘書はファイル重複・依存順序・実行中カードとの競合を理由に起票を保留しない", text)
        self.assertIn("resolve_max_attempts", text)


class SecretaryForbidsInProcessDelegationContractTests(unittest.TestCase):
    """Locks the "カードなしの in-process delegation / visible pane なしの自己実装
    禁止" contract into README, SKILL.md, and the generated KANBAN.md template so
    a future doc edit cannot silently drop the ban or the required
    `kanban add` -> `kanban-secretary.sh dispatch` escape hatch."""

    def test_readme_forbids_in_process_delegation_and_names_forbidden_tools(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("No in-process delegation from the secretary pane", text)
        self.assertIn("Agent", text)
        self.assertIn("Task", text)
        self.assertIn("collaboration/subagent-spawning feature", text)
        self.assertIn("kanban add", text)
        self.assertIn("kanban-secretary.sh dispatch", text)

    def test_skill_forbids_in_process_delegation_and_names_forbidden_tools(self):
        text = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("Forbidden: in-process delegation from this pane", normalized)
        self.assertIn("never launch this CLI's own built-in subagent/delegation tool", normalized)
        self.assertIn("Agent`/`Task`", normalized)
        self.assertIn("collaboration or", normalized)
        self.assertIn("subagent spawning", normalized)
        self.assertIn("visible Herdr pane", normalized)
        self.assertIn("kanban add", normalized)
        self.assertIn("kanban-secretary.sh dispatch", normalized)

    def test_kanban_md_template_forbids_in_process_delegation_and_names_forbidden_tools(self):
        text = (REPO / "kanban.sh").read_text(encoding="utf-8")
        self.assertIn("in-process delegation 禁止", text)
        self.assertIn("Agent`/`Task` (Claude Code)", text)
        self.assertIn("collaboration/subagent 起動 (Codex)", text)
        self.assertIn("kanban add", text)
        self.assertIn("kanban-secretary.sh dispatch", text)


if __name__ == "__main__":
    unittest.main()
