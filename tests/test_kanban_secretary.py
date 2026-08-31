#!/usr/bin/env python3
import importlib
import os
from pathlib import Path
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
DIST_FILES = ["kanban.sh", "kanban-setup.sh", "VERSION", "gui", "skills", "registry", "guard", ".gitignore"]


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
                # Isolate the PC-wide project registry so these tests never
                # read/write the real machine's ~/.config/mornkanban.
                "KANBAN_CONFIG_DIR": str(self.root / "registry-config"),
            }
        )
        self.env.pop("KANBAN_HERDR_SECRETARY", None)

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

    def test_bootstrap_initializes_board_and_registers_project_specific_secretary(self):
        result = self.run_secretary("bootstrap", self.project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / ".kanban" / "KANBAN.md").is_file())
        self.assertIn("execution=visible-herdr", result.stdout)
        # No project-wide fixed "secretary" default any more: the basename
        # of self.project is "project", so the generated default is
        # "secretary-project", not the bare "secretary" every project used
        # to collide on.
        self.assertIn("secretary=secretary-project", result.stdout)
        self.assertIn("agent rename w1:p1 secretary-project", self.log.read_text(encoding="utf-8"))

    def test_bootstrap_resolved_name_is_stable_from_a_subdirectory(self):
        sub = self.project / "sub" / "dir"
        sub.mkdir(parents=True)

        root_result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(root_result.returncode, 0, root_result.stderr)
        self.log.write_text("", encoding="utf-8")
        sub_result = self.run_secretary("bootstrap", sub)

        self.assertEqual(sub_result.returncode, 0, sub_result.stderr)
        self.assertIn("secretary=secretary-project", sub_result.stdout)

    def test_bootstrap_resolved_name_is_stable_from_inside_a_card_worktree(self):
        # .kanban/wt/<id> checkouts carry their own tracked .kanban/ subtree
        # (todo/doing/... are committed); bootstrapping from inside one must
        # still resolve the outer project's identity, not the worktree's own.
        wt = self.project / ".kanban" / "wt" / "20260101-000000-1"
        wt.mkdir(parents=True)
        (wt / ".kanban").mkdir()

        root_result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(root_result.returncode, 0, root_result.stderr)
        self.log.write_text("", encoding="utf-8")
        wt_result = self.run_secretary("bootstrap", wt)

        self.assertEqual(wt_result.returncode, 0, wt_result.stderr)
        self.assertIn("secretary=secretary-project", wt_result.stdout)

    def test_bootstrap_honors_kanban_md_secretary_agent_override(self):
        result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        kanban_md = self.project / ".kanban" / "KANBAN.md"
        content = kanban_md.read_text(encoding="utf-8")
        kanban_md.write_text(
            content.replace("codex_sandbox: workspace-write", "codex_sandbox: workspace-write\nsecretary_agent: secretary-override"),
            encoding="utf-8",
        )
        self.log.write_text("", encoding="utf-8")

        result = self.run_secretary("bootstrap", self.project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secretary=secretary-override", result.stdout)
        self.assertIn("agent rename w1:p1 secretary-override", self.log.read_text(encoding="utf-8"))

    def test_bootstrap_environment_override_beats_kanban_md(self):
        result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        kanban_md = self.project / ".kanban" / "KANBAN.md"
        content = kanban_md.read_text(encoding="utf-8")
        kanban_md.write_text(
            content.replace("codex_sandbox: workspace-write", "codex_sandbox: workspace-write\nsecretary_agent: secretary-from-md"),
            encoding="utf-8",
        )
        env = self.env.copy()
        env["KANBAN_HERDR_SECRETARY"] = "secretary-from-env"

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secretary=secretary-from-env", result.stdout)

    def test_bootstrap_accepts_legacy_explicit_secretary_env_value(self):
        # Users who already set KANBAN_HERDR_SECRETARY=secretary explicitly
        # (the old project-wide fixed default) keep working unchanged - only
        # the *unset* case gets the new project-specific default.
        env = self.env.copy()
        env["KANBAN_HERDR_SECRETARY"] = "secretary"

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secretary=secretary", result.stdout)
        self.assertIn("agent rename w1:p1 secretary", self.log.read_text(encoding="utf-8"))

    def test_bootstrap_rejects_invalid_override_with_clear_guidance(self):
        env = self.env.copy()
        env["KANBAN_HERDR_SECRETARY"] = "Not Valid!"

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid", result.stderr)
        self.assertIn("Not Valid!", result.stderr)
        # Must not silently pick a different name and proceed.
        self.assertNotIn("secretary ready", result.stdout)

    def test_bootstrap_rename_failure_reports_identity_and_override_guidance_without_stdout_success(self):
        conflict_bin = self.root / "conflict-bin"
        conflict_bin.mkdir()
        herdr = conflict_bin / "herdr"
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
                  "agent rename")
                    exit 1
                    ;;
                  *) printf '%s\n' '{"result":{}}' ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        herdr.chmod(0o755)
        env = self.env.copy()
        env["PATH"] = str(conflict_bin) + os.pathsep + env["PATH"]

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secretary-project", result.stderr)
        self.assertIn("secretary_agent", result.stderr)
        self.assertIn("KANBAN_HERDR_SECRETARY", result.stderr)
        self.assertIn("not renamed", result.stderr.lower())
        self.assertNotIn("secretary ready", result.stdout)

    def test_dispatch_binds_visible_worker_reviewer_and_notification(self):
        bootstrap = self.run_secretary("bootstrap", self.project)
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        self.log.write_text("", encoding="utf-8")

        result = self.run_secretary("dispatch", self.project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pane=w1:p2", result.stdout)
        self.assertIn("secretary=secretary-project", result.stdout)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("pane split --current --direction right", log)
        self.assertIn("KANBAN_WORKER_CMD=", log)
        self.assertIn("herdr-agent-worker.sh", log)
        self.assertIn("KANBAN_REVIEW_CMD=", log)
        self.assertIn("KANBAN_NOTIFY_CMD=", log)
        self.assertIn("herdr-notify-secretary.sh", log)
        self.assertIn("KANBAN_HERDR_SECRETARY=secretary-project", log)
        self.assertIn("kanban.sh run; exit", log)

    def test_dispatch_and_bootstrap_resolve_the_same_name_across_separate_invocations(self):
        # bootstrap and dispatch never share process/state; the name must
        # come out identical purely from deterministic resolution.
        bootstrap = self.run_secretary("bootstrap", self.project)
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        self.log.write_text("", encoding="utf-8")

        dispatch = self.run_secretary("dispatch", self.project)

        self.assertEqual(dispatch.returncode, 0, dispatch.stderr)
        self.assertIn("secretary=secretary-project", bootstrap.stdout)
        self.assertIn("secretary=secretary-project", dispatch.stdout)

    def test_bootstrap_refuses_hidden_headless_fallback(self):
        env = self.env.copy()
        env.pop("HERDR_ENV")

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing a hidden headless fallback", result.stderr)
        self.assertFalse((self.project / ".kanban").exists())


class SecretaryNameResolutionTests(unittest.TestCase):
    """Unit coverage for registry/secretary.py's pure name-resolution logic
    (slugging, hashing, override precedence, validation) independent of the
    Herdr-facing shell scripts."""

    def setUp(self):
        sys.path.insert(0, str(REPO))
        self.secretary = importlib.import_module("registry.secretary")
        importlib.reload(self.secretary)
        self.store = importlib.import_module("registry.store")
        importlib.reload(self.store)
        self.temp = tempfile.TemporaryDirectory()
        self.env_patch = mock.patch.dict(
            os.environ, {"KANBAN_CONFIG_DIR": str(Path(self.temp.name) / "config")}
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.temp.cleanup()

    def _project(self, name):
        root = Path(self.temp.name) / name
        (root / ".kanban").mkdir(parents=True)
        return root

    def test_default_name_is_project_specific_not_the_old_fixed_secretary(self):
        root = self._project("my-app")
        name, source = self.secretary.resolve(str(root))
        self.assertEqual(name, "secretary-my-app")
        self.assertEqual(source, "generated")

    def test_registry_alias_is_preferred_over_basename(self):
        root = self._project("checkout-dir-name")
        self.store.add("kimekyawa", str(root))
        name, source = self.secretary.resolve(str(root))
        self.assertEqual(name, "secretary-kimekyawa")
        self.assertEqual(source, "generated")

    def test_same_basename_different_path_collides_by_documented_design(self):
        # Both a/app and b/app get the identical default; disambiguation is
        # documented to happen at Herdr registration time (see
        # kanban-secretary.sh bootstrap), not here.
        root_a = self._project("group-a/app")
        root_b = self._project("group-b/app")
        name_a, _ = self.secretary.resolve(str(root_a))
        name_b, _ = self.secretary.resolve(str(root_b))
        self.assertEqual(name_a, name_b)
        self.assertEqual(name_a, "secretary-app")

    def test_unicode_basename_gets_a_stable_hash_suffix(self):
        root = self._project("カフェ")
        name1, _ = self.secretary.resolve(str(root))
        name2, _ = self.secretary.resolve(str(root))
        self.assertEqual(name1, name2)
        self.assertTrue(self.secretary._NAME_RE.match(name1))
        self.assertNotEqual(name1, "secretary-project")  # not a bare placeholder collision

    def test_symbol_only_basename_falls_back_to_hashed_placeholder(self):
        root = self._project("___")
        name, _ = self.secretary.resolve(str(root))
        self.assertTrue(self.secretary._NAME_RE.match(name))
        self.assertTrue(name.startswith("secretary-project-"))

    def test_two_unicode_only_projects_get_distinct_hashed_names(self):
        root1 = self._project("あ" * 3)
        root2 = self._project("い" * 3)
        name1, _ = self.secretary.resolve(str(root1))
        name2, _ = self.secretary.resolve(str(root2))
        self.assertNotEqual(name1, name2)

    def test_long_basename_is_truncated_and_hashed(self):
        root = self._project("a" * 100)
        name, _ = self.secretary.resolve(str(root))
        self.assertLessEqual(len(name), 48)
        self.assertTrue(self.secretary._NAME_RE.match(name))

    def test_env_override_wins_over_kanban_md_override(self):
        root = self._project("app")
        (root / ".kanban" / "KANBAN.md").write_text(
            "---\nsecretary_agent: secretary-from-md\n---\n", encoding="utf-8"
        )
        name, source = self.secretary.resolve(str(root), env_override="secretary-from-env")
        self.assertEqual(name, "secretary-from-env")
        self.assertEqual(source, "environment")

    def test_kanban_md_override_wins_over_generated_default(self):
        root = self._project("app")
        (root / ".kanban" / "KANBAN.md").write_text(
            "---\nsecretary_agent: secretary-from-md\n---\n", encoding="utf-8"
        )
        name, source = self.secretary.resolve(str(root))
        self.assertEqual(name, "secretary-from-md")
        self.assertEqual(source, "kanban_md")

    def test_legacy_explicit_secretary_env_value_is_accepted_as_override(self):
        root = self._project("app")
        name, source = self.secretary.resolve(str(root), env_override="secretary")
        self.assertEqual(name, "secretary")
        self.assertEqual(source, "environment")

    def test_invalid_env_override_raises_instead_of_silently_substituting(self):
        root = self._project("app")
        with self.assertRaises(self.secretary.SecretaryNameError):
            self.secretary.resolve(str(root), env_override="Not Valid!")

    def test_invalid_kanban_md_override_raises_instead_of_silently_substituting(self):
        root = self._project("app")
        (root / ".kanban" / "KANBAN.md").write_text(
            "---\nsecretary_agent: Not Valid!\n---\n", encoding="utf-8"
        )
        with self.assertRaises(self.secretary.SecretaryNameError):
            self.secretary.resolve(str(root))

    def test_resolve_rejects_a_root_without_dot_kanban(self):
        bare = Path(self.temp.name) / "no-kanban-here"
        bare.mkdir()
        with self.assertRaises(self.secretary.SecretaryNameError):
            self.secretary.resolve(str(bare))


class NotifySecretaryRoutingTests(unittest.TestCase):
    """herdr-notify-secretary.sh must prompt the correct project's secretary,
    never a different project's (whether that project uses the fixed legacy
    'secretary' name or a generated project-specific one), including when
    two projects' dispatchers notify concurrently."""

    NOTIFY = REPO / "herdr-notify-secretary.sh"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
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
                printf '%s\n' '{"result":{}}'
                """
            ),
            encoding="utf-8",
        )
        herdr.chmod(0o755)
        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + self.base_env.get("PATH", ""),
                "HERDR_ENV": "1",
                "HERDR_TEST_LOG": str(self.log),
                "KANBAN_CONFIG_DIR": str(self.root / "registry-config"),
            }
        )
        self.base_env.pop("KANBAN_HERDR_SECRETARY", None)

    def tearDown(self):
        self.temp.cleanup()

    def _project(self, name):
        root = self.root / name
        (root / ".kanban").mkdir(parents=True)
        return root

    def run_notify(self, state, title, cwd=None, env=None):
        return subprocess.run(
            [str(self.NOTIFY), state, title],
            text=True,
            capture_output=True,
            cwd=str(cwd) if cwd else None,
            env=env or self.base_env,
            check=False,
        )

    def test_uses_env_secretary_name_verbatim(self):
        env = self.base_env.copy()
        env["KANBAN_HERDR_SECRETARY"] = "secretary-project-a"

        result = self.run_notify("done", "card title", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agent prompt secretary-project-a", self.log.read_text(encoding="utf-8"))

    def test_falls_back_to_resolved_name_from_cwd_when_env_unset(self):
        project = self._project("standalone-app")

        result = self.run_notify("done", "card title", cwd=project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "agent prompt secretary-standalone-app", self.log.read_text(encoding="utf-8")
        )

    def test_two_projects_notify_distinct_secretaries_without_cross_talk(self):
        env_a = self.base_env.copy()
        env_a["KANBAN_HERDR_SECRETARY"] = "secretary-project-a"
        env_b = self.base_env.copy()
        env_b["KANBAN_HERDR_SECRETARY"] = "secretary-project-b"

        result_a = self.run_notify("done", "card A", env=env_a)
        result_b = self.run_notify("failed", "card B", env=env_b)

        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("agent prompt secretary-project-a", log)
        self.assertIn("agent prompt secretary-project-b", log)
        # Neither call must have addressed the other project's name.
        for line in log.splitlines():
            if "card A" in line or "A」" in line:
                self.assertNotIn("secretary-project-b", line)
            if "card B" in line or "B」" in line:
                self.assertNotIn("secretary-project-a", line)


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
                      "KANBAN_CODEX_SANDBOX", "KANBAN_ALLOWED_TOOLS"):
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


if __name__ == "__main__":
    unittest.main()
