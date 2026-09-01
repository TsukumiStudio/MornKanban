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
import time
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SECRETARY = REPO / "kanban-secretary.sh"
KANBAN_SH = REPO / "kanban.sh"
KANBAN_SETUP_SH = REPO / "kanban-setup.sh"

# Distribution files copied to build a standalone repo outside this worktree
# (setup_core.install_cli/install_skills/run_update all refuse to run from a
# .git/kanban/wt/<id> worktree by design).
DIST_FILES = ["kanban.sh", "kanban-root.sh", "kanban-secretary.sh", "kanban-setup.sh", "dispatcher_tui.py", "VERSION", "gui", "skills", "registry", "guard", ".gitignore"]

# `KANBAN_TEST_TIER=fast` skips the handful of tests that drive a real
# `kanban.sh run --once` end to end (real git worktree/branch/merge
# subprocesses, ~2s each). They stay in the default/full run; fast is for
# tight worker/rework iteration where the rest of the suite already covers
# the touched unit. See gui/VERIFY.md "テストの段階 (fast / full)".
FULL_ONLY = unittest.skipIf(
    os.environ.get("KANBAN_TEST_TIER") == "fast",
    "full tier only: real kanban.sh run --once git worktree/merge integration",
)


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
    run("-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "--allow-empty", "-q", "-m", "init")
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
        _init_git_repo(self.project)
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
                  "agent get")
                    printf '%s\n' '{"result":{"agent":{"pane_id":"w1:p1","name":"secretary"}}}'
                    ;;
                  "agent list")
                    printf '%s\n' '{"result":{"agents":[{"pane_id":"w1:p1","name":"secretary"}]}}'
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
        self.assertTrue((self.project / ".git" / "kanban" / "KANBAN.md").is_file())
        self.assertNotIn("execution=", result.stdout)
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
        root_result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(root_result.returncode, 0, root_result.stderr)
        wt = self.project / ".git" / "kanban" / "wt" / "20260101-000000-1"
        subprocess.run(
            ["git", "-C", str(self.project), "worktree", "add", "-q", "-b", "test-card", str(wt)],
            check=True,
        )
        self.log.write_text("", encoding="utf-8")
        wt_result = self.run_secretary("bootstrap", wt)

        self.assertEqual(wt_result.returncode, 0, wt_result.stderr)
        self.assertIn("secretary=secretary-project", wt_result.stdout)

    def test_bootstrap_honors_kanban_md_secretary_agent_override(self):
        result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        kanban_md = self.project / ".git" / "kanban" / "KANBAN.md"
        content = kanban_md.read_text(encoding="utf-8")
        kanban_md.write_text(
            content.replace("codex_sandbox: danger-full-access", "codex_sandbox: danger-full-access\nsecretary_agent: secretary-override"),
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
        kanban_md = self.project / ".git" / "kanban" / "KANBAN.md"
        content = kanban_md.read_text(encoding="utf-8")
        kanban_md.write_text(
            content.replace("codex_sandbox: danger-full-access", "codex_sandbox: danger-full-access\nsecretary_agent: secretary-from-md"),
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
        self.assertNotIn("execution=", result.stdout)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("pane split --current --direction down", log)
        self.assertIn("KANBAN_WORKER_CMD=", log)
        self.assertIn("herdr-agent-worker.sh", log)
        self.assertIn("KANBAN_REVIEW_CMD=", log)
        self.assertIn("KANBAN_RESOLVE_CMD=", log)
        self.assertIn("KANBAN_HERDR_ROLE=resolver", log)
        self.assertIn("KANBAN_OPERATION_CMD=", log)
        self.assertIn("KANBAN_HERDR_ROLE=operator", log)
        self.assertIn("KANBAN_NOTIFY_CMD=", log)
        self.assertIn("herdr-notify-secretary.sh", log)
        self.assertIn("KANBAN_HERDR_SECRETARY=secretary-project", log)
        self.assertIn("kanban-secretary.sh __run-dispatcher-pane", log)
        self.assertIn("dispatcher_tui.py", (REPO / "kanban-secretary.sh").read_text(encoding="utf-8"))
        self.assertIn("agent get w1:p1", log)
        self.assertIn("agent rename w1:p1 secretary-project", log)

    def test_dispatcher_runtime_failure_is_logged_and_notifies_secretary(self):
        bootstrap = self.run_secretary("bootstrap", self.project)
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        failing_kanban = self.root / "failing-kanban.sh"
        failing_kanban.write_text(
            "#!/usr/bin/env bash\necho 'parallel mode requires a git repository (worktrees)' >&2\nexit 42\n",
            encoding="utf-8",
        )
        failing_kanban.chmod(0o755)
        self.log.write_text("", encoding="utf-8")
        env = self.env.copy()
        env["KANBAN_BIN"] = str(failing_kanban)
        env["KANBAN_HERDR_SECRETARY"] = "secretary-project"

        result = self.run_secretary("__run-dispatcher-pane", self.project, env=env)

        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        dispatcher_log = self.project / ".git" / "kanban" / "wt" / "dispatcher.log"
        logged = dispatcher_log.read_text(encoding="utf-8")
        self.assertIn("parallel mode requires", logged)
        self.assertIn("dispatcher exited with status 42", logged)
        notification = self.log.read_text(encoding="utf-8")
        self.assertIn("agent prompt secretary-project", notification)
        self.assertIn("dispatcher が終了コード 42", notification)
        self.assertIn(str(dispatcher_log), notification)

    def test_dispatcher_uses_one_validated_worker_snapshot_for_its_lifetime(self):
        bootstrap = self.run_secretary("bootstrap", self.project)
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        probe = self.root / "probe-kanban.sh"
        probe.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "case \"$KANBAN_WORKER_CMD\" in\n"
            "  \"$EXPECTED_ROOT\"/.git/kanban/wt/runtime.*/herdr-agent-worker.sh) ;;\n"
            "  *) echo \"not a runtime snapshot: $KANBAN_WORKER_CMD\" >&2; exit 9 ;;\n"
            "esac\n"
            "bash -n \"$KANBAN_WORKER_CMD\"\n"
            "test -f \"$(dirname \"$KANBAN_WORKER_CMD\")/activity_log.py\"\n"
            "printf 'runtime=%s\\n' \"$(dirname \"$KANBAN_WORKER_CMD\")\"\n",
            encoding="utf-8",
        )
        probe.chmod(0o755)
        env = self.env.copy()
        env.update({"KANBAN_BIN": str(probe), "EXPECTED_ROOT": str(self.project.resolve())})

        result = self.run_secretary("__run-dispatcher-pane", self.project, env=env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        runtime_line = next(line for line in result.stdout.splitlines() if line.startswith("runtime="))
        self.assertFalse(Path(runtime_line.removeprefix("runtime=")).exists())

    def test_dispatch_refuses_to_steal_secretary_name_owned_by_another_pane(self):
        self.run_secretary("bootstrap", self.project)
        conflict_bin = self.root / "dispatch-conflict-bin"
        conflict_bin.mkdir()
        herdr = conflict_bin / "herdr"
        herdr.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                case "$1 $2" in
                  "pane layout") printf '%s\n' '{"result":{"layout":{"panes":[{"pane_id":"w1:p1","rect":{"width":160,"height":40}}]}}}' ;;
                  "agent get") printf '%s\n' '{"result":{"agent":{"pane_id":"w1:p1","name":"secretary"}}}' ;;
                  "agent list") printf '%s\n' '{"result":{"agents":[{"pane_id":"w1:p9","name":"secretary-project"},{"pane_id":"w1:p1","name":"secretary"}]}}' ;;
                  *) printf '%s\n' '{"result":{}}' ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        herdr.chmod(0o755)
        env = self.env.copy()
        env["PATH"] = str(conflict_bin) + os.pathsep + env["PATH"]

        result = self.run_secretary("dispatch", self.project, env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already owned by pane w1:p9", result.stderr)
        self.assertIn("refusing to steal", result.stderr)

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

    def test_bootstrap_requires_herdr(self):
        env = self.env.copy()
        env.pop("HERDR_ENV")

        result = self.run_secretary("bootstrap", self.project, env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Herdr is required", result.stderr)
        self.assertFalse((self.project / ".git" / "kanban").exists())


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
        root.mkdir(parents=True)
        _init_git_repo(root)
        (root / ".git" / "kanban").mkdir()
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
        (root / ".git" / "kanban" / "KANBAN.md").write_text(
            "---\nsecretary_agent: secretary-from-md\n---\n", encoding="utf-8"
        )
        name, source = self.secretary.resolve(str(root), env_override="secretary-from-env")
        self.assertEqual(name, "secretary-from-env")
        self.assertEqual(source, "environment")

    def test_kanban_md_override_wins_over_generated_default(self):
        root = self._project("app")
        (root / ".git" / "kanban" / "KANBAN.md").write_text(
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
        (root / ".git" / "kanban" / "KANBAN.md").write_text(
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
                if [[ ${HERDR_TEST_FAIL:-0} == 1 ]]; then
                  echo "agent not found" >&2
                  exit 9
                fi
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
        root.mkdir(parents=True)
        _init_git_repo(root)
        (root / ".git" / "kanban").mkdir()
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

    def test_notification_failure_is_not_silently_reported_as_success(self):
        env = self.base_env.copy()
        env["KANBAN_HERDR_SECRETARY"] = "secretary-project-a"
        env["HERDR_TEST_FAIL"] = "1"

        result = self.run_notify("done", "card title", env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to notify", result.stderr)
        self.assertIn("agent not found", result.stderr)

    def test_failure_and_blocked_prompts_preserve_workflow_semantics(self):
        env = self.base_env.copy()
        env["KANBAN_HERDR_SECRETARY"] = "secretary-project-a"

        failed = self.run_notify("failed", "failed card", env=env)
        blocked = self.run_notify("blocked", "blocked card", env=env)

        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("製品の検証不合格とは限らない", log)
        self.assertIn("failure_kind", log)
        self.assertIn("review_infra は未検証", log)
        self.assertIn("デプロイ不可と推測しない", log)

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
            "Codex": str(root / "agents" / "kanban-dispatch"),
        }
        self.legacy_targets = {"Codex": str(root / "codex" / "kanban-dispatch")}

    def tearDown(self):
        self.temp.cleanup()

    def test_installs_rendered_skill_for_claude_and_codex(self):
        with mock.patch.object(self.setup_core, "SKILL_TARGETS", self.targets), \
             mock.patch.object(self.setup_core, "LEGACY_SKILL_TARGETS", self.legacy_targets), \
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
            self.assertIn("name: kanban-dispatch", content)
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
    from inside a .git/kanban/wt/<id> checkout by design)."""

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

        report_skill = self.home / ".claude" / "skills" / "kanban-report" / "SKILL.md"
        report_content = report_skill.read_text(encoding="utf-8")
        self.assertIn("name: kanban-report", report_content)
        self.assertIn(str(self.dist), report_content)
        self.assertIn((self.dist / "VERSION").read_text(encoding="utf-8").strip(), report_content)
        codex_report = self.home / ".agents" / "skills" / "kanban-report" / "SKILL.md"
        self.assertIn("name: kanban-report", codex_report.read_text(encoding="utf-8"))

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
        self.assertFalse((self.home / ".claude" / "skills" / "kanban-report").exists())
        self.assertFalse((self.home / ".agents" / "skills" / "kanban-dispatch").exists())
        self.assertFalse((self.home / ".agents" / "skills" / "kanban-report").exists())
        # the repository checkout itself is untouched by uninstall
        self.assertTrue((self.dist / "kanban.sh").exists())

    def test_update_reinstalls_without_invoking_git(self):
        marker = self.home / "git-called"
        fake_bin = self.home / "fake-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text('#!/bin/sh\n: > "$GIT_MARKER"\nexit 99\n', encoding="utf-8")
        fake_git.chmod(0o755)
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env.get("PATH", "")
        self.env["GIT_MARKER"] = str(marker)

        result = self._run("update")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), "update invoked git")
        self.assertNotIn("git pull", result.stdout.lower())
        skill = self.home / ".claude" / "skills" / "kanban-dispatch" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        self.assertIn((self.dist / "VERSION").read_text(encoding="utf-8").strip(), content)
        self.assertIn(str(self.dist), content)

        link = self.home / ".local" / "bin" / "kanban"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(str(link)), os.path.realpath(str(self.dist / "kanban.sh")))

    def test_update_migrates_codex_skill_to_official_user_directory(self):
        legacy = self.home / ".codex" / "skills" / "kanban-dispatch"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text("# MornKanban secretary\n", encoding="utf-8")

        result = self._run("update")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(legacy.exists())
        skill = self.home / ".agents" / "skills" / "kanban-dispatch" / "SKILL.md"
        self.assertIn("name: kanban-dispatch", skill.read_text(encoding="utf-8"))


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


class CardEffortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        _init_git_repo(self.project)
        subprocess.run([str(KANBAN_SH), "init"], cwd=self.project, check=True, capture_output=True, text=True)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *args, env=None, input_text="task"):
        return subprocess.run(
            [str(KANBAN_SH), *args], cwd=self.project, env=env,
            input=input_text, text=True, capture_output=True, check=False,
        )

    def test_card_effort_reaches_worker_and_reviewer(self):
        effort_log = Path(self.temp.name) / "effort.log"
        worker = Path(self.temp.name) / "worker.sh"
        reviewer = Path(self.temp.name) / "reviewer.sh"
        worker.write_text('#!/usr/bin/env bash\nprintf "worker=%s\\n" "$KANBAN_CARD_EFFORT" >> "$EFFORT_LOG"\n', encoding="utf-8")
        reviewer.write_text('#!/usr/bin/env bash\ncat >/dev/null\nprintf "reviewer=%s\\n" "$KANBAN_CARD_EFFORT" >> "$EFFORT_LOG"\nprintf \'{"score":95,"feedback":"ok"}\\n\'\n', encoding="utf-8")
        worker.chmod(0o755)
        reviewer.chmod(0o755)

        added = self._run("add", "effort card", "-b", "codex", "-m", "gpt-5.6-sol", "-e", "high")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("effort: high", Path(added.stdout.strip()).read_text(encoding="utf-8"))

        env = {**os.environ, "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer),
               "KANBAN_JOBS": "1", "EFFORT_LOG": str(effort_log)}
        result = self._run("run", "--once", env=env, input_text=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(effort_log.read_text(encoding="utf-8").splitlines(), ["worker=high", "reviewer=high"])

    def test_add_rejects_unknown_effort(self):
        result = self._run("add", "bad effort", "-e", "extreme")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid effort", result.stderr)
        self.assertEqual(list((self.project / ".git" / "kanban" / "todo").glob("*.md")), [])


class CardAddArgumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        _init_git_repo(self.project)
        subprocess.run(
            [str(KANBAN_SH), "init"], cwd=self.project,
            check=True, capture_output=True, text=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [str(KANBAN_SH), "add", *args], cwd=self.project,
            input="task", text=True, capture_output=True, check=False,
        )

    def test_init_defaults_are_backend_neutral_and_preserve_gitignore(self):
        config = (self.project / ".git" / "kanban" / "KANBAN.md").read_text(encoding="utf-8")
        self.assertRegex(config, r"(?m)^default_model:\s*$")
        self.assertRegex(config, r"(?m)^review_model:\s*haiku\s*$")
        self.assertRegex(config, r"(?m)^resolve_model:\s*$")
        for state in ("todo", "doing", "review", "resolving", "blocked", "done", "failed"):
            self.assertTrue((self.project / ".git" / "kanban" / state / ".gitkeep").is_file())

        ignore = self.project / ".git" / "kanban" / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") + "user-entry\n", encoding="utf-8")
        result = subprocess.run(
            [str(KANBAN_SH), "init"], cwd=self.project,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = ignore.read_text(encoding="utf-8")
        self.assertIn("user-entry\n", text)
        self.assertIn(".secretary-guard/\n", text)

    def test_help_and_invalid_arguments_never_create_cards(self):
        for args, expected_status, message in (
            (("--help",), 0, "usage: kanban add"),
            (("--bogus",), 1, "unknown option"),
            (("-e",), 1, "requires a value"),
            (("real title", "garbage"), 1, "unexpected argument"),
        ):
            with self.subTest(args=args):
                result = self._run(*args)
                self.assertEqual(result.returncode, expected_status)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertEqual(
                    list((self.project / ".git" / "kanban" / "todo").glob("*.md")), []
                )

        outside = subprocess.run(
            [str(KANBAN_SH), "add", "--help"], cwd=self.temp.name,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(outside.returncode, 0)
        self.assertIn("usage: kanban add", outside.stdout)

    def test_invalid_threshold_is_rejected_before_card_creation(self):
        for value in ("-1", "101", "not-a-number"):
            result = self._run("task", "-t", value)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("threshold", result.stderr)
        self.assertEqual(list((self.project / ".git" / "kanban" / "todo").glob("*.md")), [])

    def test_structured_card_waits_in_backlog_until_definition_of_ready_passes(self):
        incomplete = self._run("incomplete", "--type", "feature")
        self.assertEqual(incomplete.returncode, 0, incomplete.stderr)
        incomplete_card = Path(incomplete.stdout.strip())
        self.assertEqual(incomplete_card.parent.name, "backlog")
        incomplete_id = re.search(
            r"(?m)^id: (.+)$", incomplete_card.read_text(encoding="utf-8")
        ).group(1)

        rejected = subprocess.run(
            [str(KANBAN_SH), "ready", "--check", incomplete_id],
            cwd=self.project, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("missing: size", rejected.stdout)
        self.assertIn("missing: Acceptance Criteria", rejected.stdout)

        complete = self._run(
            "complete", "--type", "feature", "--size", "small",
            "--goal", "observable result", "--ac", "result is visible",
            "--scope", "CLI only", "--verify", "python3 -m unittest", "--ready",
        )
        self.assertEqual(complete.returncode, 0, complete.stderr)
        complete_card = Path(complete.stdout.strip())
        self.assertEqual(complete_card.parent.name, "todo")
        text = complete_card.read_text(encoding="utf-8")
        self.assertIn("card_schema: structured", text)
        self.assertIn("## Acceptance Criteria\n\n- result is visible", text)


class BoardMigrationTests(unittest.TestCase):
    def test_migrate_refuses_live_dispatcher_then_preserves_board(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            _init_git_repo(project)
            legacy = project / ".kanban"
            (legacy / "todo").mkdir(parents=True)
            card = legacy / "todo" / "card.md"
            card.write_text("legacy card\n", encoding="utf-8")
            (legacy / ".lock").write_text(str(os.getpid()), encoding="utf-8")

            refused = subprocess.run(
                [str(KANBAN_SH), "migrate"], cwd=project,
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertTrue(card.exists())

            (legacy / ".lock").unlink()
            migrated = subprocess.run(
                [str(KANBAN_SH), "migrate"], cwd=project,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertFalse(legacy.exists())
            self.assertEqual(
                (project / ".git" / "kanban" / "todo" / "card.md").read_text(encoding="utf-8"),
                "legacy card\n",
            )


class SecretaryBoardAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        _init_git_repo(self.project)
        subprocess.run(
            [str(KANBAN_SH), "init"], cwd=self.project,
            check=True, capture_output=True, text=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *args, input_text=None):
        return subprocess.run(
            [str(KANBAN_SH), *args], cwd=self.project, input=input_text,
            text=True, capture_output=True, check=False,
        )

    def test_remove_deletes_only_one_pending_todo_card(self):
        added = self._run("add", "--", "--help", input_text="accidental")
        self.assertEqual(added.returncode, 0, added.stderr)
        card = Path(added.stdout.strip())
        card_id = next(
            line.split(":", 1)[1].strip()
            for line in card.read_text(encoding="utf-8").splitlines()
            if line.startswith("id:")
        )

        removed = self._run("remove", card_id)

        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn(card_id, removed.stdout)
        self.assertFalse(card.exists())

        protected = Path(self._run("add", "real card", input_text="task").stdout.strip())
        protected_id = next(
            line.split(":", 1)[1].strip()
            for line in protected.read_text(encoding="utf-8").splitlines()
            if line.startswith("id:")
        )
        blocked = self.project / ".git" / "kanban" / "blocked" / protected.name
        protected.rename(blocked)
        refused = self._run("remove", protected_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("only todo cards", refused.stderr)
        self.assertTrue(blocked.exists())

    def test_config_set_updates_only_allowlisted_operational_keys(self):
        config = self.project / ".git" / "kanban" / "KANBAN.md"
        for key, value in (
            ("jobs", "12"),
            ("default_backend", "codex"),
            ("default_model", "gpt-5.6-sol"),
            ("reviewer", "claude"),
            ("review_model", "sonnet"),
        ):
            result = self._run("config", "set", key, value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("%s: %s" % (key, value), config.read_text(encoding="utf-8"))

        before = config.read_text(encoding="utf-8")
        for key, value in (("jobs", "0"), ("claude_perms", "acceptEdits")):
            result = self._run("config", "set", key, value)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_text(encoding="utf-8"), before)


class WorkerQuestionBoundaryTests(unittest.TestCase):
    def test_wrapper_diagnostic_before_blocked_line_still_parks_without_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            _init_git_repo(project)
            subprocess.run(
                [str(KANBAN_SH), "init"], cwd=project,
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                [str(KANBAN_SH), "config", "set", "jobs", "1"], cwd=project,
                check=True, capture_output=True, text=True,
            )
            config = project / ".git" / "kanban" / "KANBAN.md"
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "review_enabled: true", "review_enabled: false"
                ),
                encoding="utf-8",
            )
            worker = Path(td) / "worker.sh"
            worker.write_text(
                "#!/usr/bin/env bash\ncat >/dev/null\n"
                "echo 'herdr-agent-worker: role=worker'\n"
                "echo 'BLOCKED: user decision required'\n",
                encoding="utf-8",
            )
            worker.chmod(0o755)
            subprocess.run(
                [str(KANBAN_SH), "add", "question card"], cwd=project,
                input="task", text=True, check=True, capture_output=True,
            )

            result = subprocess.run(
                [str(KANBAN_SH), "run", "--once"], cwd=project,
                env={**os.environ, "KANBAN_WORKER_CMD": str(worker), "KANBAN_JOBS": "1"},
                text=True, capture_output=True, check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            cards = list((project / ".git" / "kanban" / "blocked").glob("*.md"))
            self.assertEqual(len(cards), 1, result.stdout + result.stderr)
            text = cards[0].read_text(encoding="utf-8")
            self.assertIn("attempts: 0", text)
            self.assertIn("blocked_kind: ordering", text)

    def test_agent_question_parks_as_resumable_user_input_without_retry(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            _init_git_repo(project)
            subprocess.run(
                [str(KANBAN_SH), "init"], cwd=project,
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                [str(KANBAN_SH), "config", "set", "jobs", "1"], cwd=project,
                check=True, capture_output=True, text=True,
            )
            worker = Path(td) / "worker.sh"
            worker.write_text(
                "#!/usr/bin/env bash\ncat >/dev/null\n"
                "echo 'KANBAN_INFRA_ERROR: agent_question: interactive choice' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            worker.chmod(0o755)
            added = subprocess.run(
                [str(KANBAN_SH), "add", "question card", "--no-review"], cwd=project,
                input="task", text=True, check=True, capture_output=True,
            )
            card_id = re.search(
                r"^id: (\S+)$", Path(added.stdout.strip()).read_text(encoding="utf-8"), re.M
            ).group(1)

            result = subprocess.run(
                [str(KANBAN_SH), "run", "--once"], cwd=project,
                env={**os.environ, "KANBAN_WORKER_CMD": str(worker), "KANBAN_JOBS": "1"},
                text=True, capture_output=True, check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            card = next((project / ".git" / "kanban" / "blocked").glob("*.md"))
            text = card.read_text(encoding="utf-8")
            self.assertIn("blocked_kind: user_input", text)
            self.assertIn("attempts: 0", text)
            self.assertNotIn("worker infrastructure retry", text)
            resumed = subprocess.run(
                [str(KANBAN_SH), "resume", card_id], cwd=project,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(len(list((project / ".git" / "kanban" / "todo").glob("*.md"))), 1)


class PromptProjectionTests(unittest.TestCase):
    def test_roles_receive_task_plus_current_report_without_accumulated_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "project"
            prompts = root / "prompts"
            project.mkdir()
            prompts.mkdir()
            _init_git_repo(project)
            subprocess.run([str(KANBAN_SH), "init"], cwd=project, check=True, capture_output=True, text=True)
            worker = root / "worker.sh"
            reviewer = root / "reviewer.sh"
            worker.write_text(
                '#!/usr/bin/env bash\ncat > "$PROMPTS/worker-$KANBAN_CARD_ATTEMPT.txt"\nprintf "WORKER_OUTPUT_SENTINEL\\n"\n',
                encoding="utf-8",
            )
            reviewer.write_text(
                '#!/usr/bin/env bash\ncat > "$PROMPTS/reviewer-$KANBAN_CARD_ATTEMPT.txt"\n'
                'if [[ $KANBAN_CARD_ATTEMPT == 1 ]]; then\n'
                '  printf \'{"score":40,"feedback":"FEEDBACK_SENTINEL"}\\n\'\n'
                'else\n'
                '  printf \'{"score":95,"feedback":"ok"}\\n\'\n'
                'fi\n',
                encoding="utf-8",
            )
            worker.chmod(0o755)
            reviewer.chmod(0o755)
            env = {
                **os.environ,
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(reviewer),
                "KANBAN_JOBS": "1",
                "PROMPTS": str(prompts),
            }
            add = subprocess.run(
                [str(KANBAN_SH), "add", "prompt projection"], cwd=project,
                input="TASK_SENTINEL", text=True, capture_output=True, env=env, check=False,
            )
            self.assertEqual(add.returncode, 0, add.stderr)

            for _ in range(2):
                run = subprocess.run(
                    [str(KANBAN_SH), "run", "--once"], cwd=project,
                    text=True, capture_output=True, env=env, check=False,
                )
                self.assertEqual(run.returncode, 0, run.stderr)

            worker1 = (prompts / "worker-1.txt").read_text(encoding="utf-8")
            worker2 = (prompts / "worker-2.txt").read_text(encoding="utf-8")
            reviewer1 = (prompts / "reviewer-1.txt").read_text(encoding="utf-8")
            reviewer2 = (prompts / "reviewer-2.txt").read_text(encoding="utf-8")
            self.assertIn("TASK_SENTINEL", worker1)
            self.assertNotIn("review decision", worker1)
            self.assertIn("FEEDBACK_SENTINEL", worker2)
            self.assertNotIn("WORKER_OUTPUT_SENTINEL", worker2)
            for prompt in (reviewer1, reviewer2):
                self.assertIn("TASK_SENTINEL", prompt)
                self.assertIn("WORKER_OUTPUT_SENTINEL", prompt)
                self.assertNotIn("FEEDBACK_SENTINEL", prompt)


class WorkerParallelismTests(unittest.TestCase):
    def test_default_is_four_and_large_explicit_job_count_has_no_product_cap(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            _init_git_repo(project)
            init = subprocess.run(
                [str(KANBAN_SH), "init"], cwd=project,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            policy = (project / ".git" / "kanban" / "KANBAN.md").read_text(encoding="utf-8")
            self.assertIn("jobs: 4", policy)
            self.assertIn("worker並列数は既定4", policy)
            skill = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("default to `jobs: 4`", skill)
            self.assertIn("no MornKanban upper", skill)
            env = {
                **os.environ,
                "KANBAN_WORKER_CMD": "/usr/bin/true",
                "KANBAN_REVIEW_ENABLED": "false",
            }

            run = subprocess.run(
                [str(KANBAN_SH), "run", "--once", "-j", "1000000"], cwd=project,
                text=True, capture_output=True, env=env, check=False,
            )

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Jobs: 1000000 (pinned", run.stdout)

    def test_non_git_project_is_rejected_without_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            result = subprocess.run(
                [str(KANBAN_SH), "init"], cwd=project,
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Git repository required", result.stderr)


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
                "KANBAN_CARD_ID": "test-card",
                "KANBAN_CARD_ATTEMPT": "attempt-1",
                "KANBAN_HERDR_POLL_INTERVAL": "0.1",
                "KANBAN_HERDR_STABLE_SLEEP": "0.05",
                "KANBAN_HERDR_ANSWER_WAIT_SECS": "3",
            }
        )
        # KANBAN_HERDR_ROLE etc. must come only from each test's overrides.
        for stray in ("KANBAN_HERDR_ROLE", "KANBAN_CARD_BACKEND", "KANBAN_REVIEWER",
                      "KANBAN_CARD_MODEL", "KANBAN_CARD_EFFORT", "KANBAN_REVIEW_MODEL", "KANBAN_BACKEND_ORDER",
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
                  "agent prompt")
                    role=${KANBAN_HERDR_ROLE:-worker}
                    printf 'KANBAN_ANSWER_ID: test-card|worktree|%s|attempt-1\n{"score":90,"feedback":"ok"}\n' \
                      "$role" > "$PWD/.kanban-answer.md"
                    printf '%s\n' '{"result":{}}'
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
        # Default policy is unrestricted (see tests/test_permission_policy.py
        # for the full worker/reviewer permission-policy matrix); this test
        # only checks that claude-specific args (not codex's) are used.
        self._write_fake_cli("claude")
        result = self._run_worker({"KANBAN_CARD_BACKEND": "claude", "KANBAN_CARD_MODEL": "sonnet"})
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind claude", start)
        self.assertIn("--dangerously-skip-permissions", start)
        self.assertIn("--model sonnet", start)
        self.assertNotIn("-s ", start)
        self.assertNotIn("-a never", start)

    def test_codex_worker_gets_codex_only_args(self):
        self._write_fake_cli("codex")
        result = self._run_worker({"KANBAN_CARD_BACKEND": "codex", "KANBAN_CARD_MODEL": "gpt-5.6-terra"})
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind codex", start)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", start)
        self.assertIn("-m gpt-5.6-terra", start)
        self.assertNotIn("--permission-mode", start)
        self.assertNotIn("--model", start)
        self.assertNotIn("sonnet", start)

    def test_codex_reviewer_gets_same_unrestricted_policy_as_worker(self):
        # Regression guard: the reviewer used to be hardcoded to `-s
        # read-only`, independent of KANBAN_CODEX_SANDBOX/full-bypass. It
        # must now share the exact same policy resolution as the worker.
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
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", start)
        self.assertIn("-m gpt-5.6-terra", start)
        self.assertNotIn("read-only", start)
        self.assertNotIn("workspace-write", start)

    def test_card_effort_reaches_all_codex_roles(self):
        self._write_fake_cli("codex")
        routes = (
            {"KANBAN_CARD_BACKEND": "codex"},
            {"KANBAN_HERDR_ROLE": "reviewer", "KANBAN_REVIEWER": "codex"},
            {"KANBAN_HERDR_ROLE": "resolver", "KANBAN_RESOLVER": "codex"},
        )
        for route in routes:
            with self.subTest(role=route.get("KANBAN_HERDR_ROLE", "worker")):
                self.log.unlink(missing_ok=True)
                result = self._run_worker({**route, "KANBAN_CARD_EFFORT": "high"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("-c model_reasoning_effort=high", self._start_call())

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

    def test_auto_codex_drops_legacy_claude_model(self):
        self._write_fake_cli("codex")
        result = self._run_worker({
            "KANBAN_CARD_BACKEND": "auto",
            "KANBAN_BACKEND_ORDER": "codex claude",
            "KANBAN_CARD_MODEL": "sonnet",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self._start_call()
        self.assertIn("--kind codex", start)
        self.assertNotIn("sonnet", start)
        self.assertNotIn(" -m ", start)

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

    def test_resolver_role_gets_unrestricted_claude_args_from_resolver_env(self):
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
        self.assertIn("--dangerously-skip-permissions", start)
        self.assertNotIn("--permission-mode acceptEdits", start)
        self.assertIn("--model sonnet", start)

    def test_resolver_role_gets_unrestricted_codex_args(self):
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
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", start)
        self.assertNotIn("workspace-write", start)
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
        self.env["KANBAN_DISPATCH_POLL_INTERVAL"] = "0.05"

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

    def _add_card(self, title, body="task body", backend=None, model=None, effort=None):
        args = ["add", title]
        if backend:
            args += ["-b", backend]
        if model:
            args += ["-m", model]
        if effort:
            args += ["-e", effort]
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

    def _add_structured_card(self, title="structured card"):
        result = subprocess.run(
            [
                str(KANBAN_SH), "add", title, "--type", "feature", "--size", "small",
                "--goal", "deliver the requested result", "--ac", "result exists",
                "--scope", "new_file.txt", "--verify", "test -f new_file.txt", "--ready",
            ],
            input="implement the result", cwd=self.project, env=self.env,
            check=True, capture_output=True, text=True,
        )
        return Path(result.stdout.strip())

    @FULL_ONLY
    def test_structured_card_persists_brief_report_review_and_accept_merge_facts(self):
        review_prompt = Path(self.temp.name) / "review-prompt.txt"
        worker = self._write_script(
            "structured-worker.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'done\\n' > new_file.txt
                cat <<'EOF'
                ## Summary
                implemented
                ## Acceptance Criteria & Evidence
                result exists: new_file.txt
                ## Verification
                test -f new_file.txt: passed
                ## Changes
                new_file.txt
                ## Deviations & Decisions
                none
                ## Follow-ups
                none
                EOF
                """
            ),
        )
        reviewer = self._write_script(
            "typed-reviewer.sh",
            '#!/usr/bin/env bash\ncat > "$REVIEW_PROMPT"\nprintf \'{"outcome":"accept","score":60,"feedback":"evidence verified"}\\n\'\n',
        )
        card = self._add_structured_card()

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer),
                "KANBAN_JOBS": "1", "REVIEW_PROMPT": str(review_prompt),
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = self.project / ".git" / "kanban" / "done" / card.name
        self.assertTrue(done.exists(), result.stdout + result.stderr)
        text = done.read_text(encoding="utf-8")
        self.assertIn("review_outcome: accept", text)
        self.assertRegex(text, r"(?m)^accepted_at: .+")
        self.assertRegex(text, r"(?m)^merged_at: .+")
        card_id = re.search(r"(?m)^id: (.+)$", text).group(1)
        base = self.project / ".git" / "kanban"
        self.assertIn("## Goal", (base / "briefs" / f"{card_id}-r1.md").read_text(encoding="utf-8"))
        self.assertIn("## Verification", (base / "reports" / f"{card_id}-r1.md").read_text(encoding="utf-8"))
        self.assertIn('"outcome":"accept"', (base / "reviews" / f"{card_id}-r1.md").read_text(encoding="utf-8"))
        self.assertIn("## Worker report", review_prompt.read_text(encoding="utf-8"))

    @FULL_ONLY
    def test_incomplete_structured_report_becomes_needs_info_without_reviewer(self):
        reviewer_called = Path(self.temp.name) / "reviewer-called"
        worker = self._write_script("short-worker.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'done\\n'\n")
        reviewer = self._write_script(
            "must-not-run-reviewer.sh",
            '#!/usr/bin/env bash\ntouch "$REVIEWER_CALLED"\nprintf \'{"outcome":"accept","score":100,"feedback":"wrong"}\\n\'\n',
        )
        card = self._add_structured_card("missing report")
        card.write_text(
            card.read_text(encoding="utf-8").replace("max_attempts: 3", "max_attempts: 1"),
            encoding="utf-8",
        )

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer),
                "KANBAN_JOBS": "1", "REVIEWER_CALLED": str(reviewer_called),
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(reviewer_called.exists())
        failed = self.project / ".git" / "kanban" / "failed" / card.name
        text = failed.read_text(encoding="utf-8")
        self.assertIn("review_outcome: needs_info", text)
        self.assertIn("worker report is incomplete", text)

    @FULL_ONLY
    def test_typed_spike_parks_review_decision_with_worktree_preserved(self):
        worker = self._write_script("spike-worker.sh", "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'work\\n' > spike.txt\n")
        reviewer = self._write_script(
            "spike-reviewer.sh",
            '#!/usr/bin/env bash\ncat >/dev/null\nprintf \'{"outcome":"spike","score":0,"feedback":"research API first"}\\n\'\n',
        )
        card = self._add_card("spike decision")
        card_id = re.search(r"(?m)^id: (.+)$", card.read_text(encoding="utf-8")).group(1)

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        blocked = self.project / ".git" / "kanban" / "blocked" / card.name
        text = blocked.read_text(encoding="utf-8")
        self.assertIn("blocked_kind: review_decision", text)
        self.assertIn("review_outcome: spike", text)
        self.assertTrue((self.project / ".git" / "kanban" / "wt" / card_id).is_dir())

        restarted = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_CMD": str(reviewer),
                "KANBAN_JOBS": "1",
            },
        )
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertTrue(blocked.exists())
        self.assertTrue((self.project / ".git" / "kanban" / "wt" / card_id).is_dir())

    @FULL_ONLY
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
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        self.assertEqual((self.project / "new_file.txt").read_text(encoding="utf-8"), "from worker\n")
        card_text = done[0].read_text(encoding="utf-8")
        self.assertRegex(card_text, r"last_timings: worker=\d+s review=\d+s")
        self.assertIn("phase durations: worker=", card_text)
        self.assertIn("phase durations: merge=", card_text)

    @FULL_ONLY
    def test_operator_card_runs_once_in_main_checkout_without_review_or_merge(self):
        evidence = Path(self.temp.name) / "operator-evidence.txt"
        operator = self._write_script(
            "operator.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                test "$(pwd -P)" = "$(cd "$KANBAN_TEST_MAIN_ROOT" && pwd -P)"
                printf '%s\n' "$KANBAN_TEST_MAIN_ROOT" > "$KANBAN_TEST_EVIDENCE"
                printf 'OPERATION_OK: remote state verified\n'
                """
            ),
        )
        added = subprocess.run(
            [str(KANBAN_SH), "add", "push and deploy", "--operate"],
            input="Push main and deploy the verified site.",
            cwd=self.project,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(added.returncode, 0, added.stderr)

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": "/usr/bin/false",
                "KANBAN_OPERATION_CMD": str(operator),
                "KANBAN_REVIEW_ENABLED": "false",
                "KANBAN_TEST_EVIDENCE": str(evidence),
                "KANBAN_JOBS": "4",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(evidence.exists(), result.stdout + result.stderr)
        self.assertEqual(evidence.read_text(encoding="utf-8").strip(), str(self.project))
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        text = done[0].read_text(encoding="utf-8")
        self.assertIn("task_kind: operation", text)
        self.assertIn("review_enabled: false", text)
        self.assertIn("review_source: operation", text)
        self.assertIn("attempts: 1", text)
        self.assertNotIn("kanban/", self._git("branch", "--show-current").stdout)

    def test_operator_without_explicit_success_is_blocked_not_done(self):
        operator = self._write_script(
            "uncertain-operator.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'deploy command returned but remote state is unknown\\n'\n",
        )
        added = subprocess.run(
            [str(KANBAN_SH), "add", "uncertain deploy", "--operate"], cwd=self.project,
            input="deploy", text=True, capture_output=True, env=self.env, check=False,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": "/usr/bin/false",
            "KANBAN_OPERATION_CMD": str(operator),
            "KANBAN_REVIEW_ENABLED": "false",
            "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((self.project / ".git" / "kanban" / "done").glob("*.md")), [])
        blocked = list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertEqual(len(blocked), 1, result.stdout)
        self.assertIn("blocked_kind: operation_unknown", blocked[0].read_text(encoding="utf-8"))

    def test_operator_infrastructure_error_is_never_auto_retried(self):
        count = Path(self.temp.name) / "operation-infra-count"
        operator = self._write_script(
            "infra-operator.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\necho run >> \"$OPERATION_COUNT\"\nprintf 'KANBAN_INFRA_ERROR: pane_lost: answer unavailable\\n'\n",
        )
        subprocess.run(
            [str(KANBAN_SH), "add", "infra publish", "--operate"], cwd=self.project,
            input="publish", text=True, capture_output=True, env=self.env, check=True,
        )
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": "/usr/bin/false", "KANBAN_OPERATION_CMD": str(operator),
            "OPERATION_COUNT": str(count), "KANBAN_JOBS": "1",
            "KANBAN_REVIEW_INFRA_MAX_RETRIES": "2", "KANBAN_REVIEW_INFRA_BACKOFF_SECONDS": "0",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(count.read_text().splitlines(), ["run"])
        card = next((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertIn("blocked_kind: operation_unknown", card.read_text(encoding="utf-8"))

    def test_stranded_operation_is_not_automatically_reexecuted(self):
        evidence = Path(self.temp.name) / "operation-reran"
        operator = self._write_script(
            "must-not-run.sh",
            "#!/usr/bin/env bash\ntouch \"$KANBAN_TEST_EVIDENCE\"\nprintf 'OPERATION_OK: done\\n'\n",
        )
        added = subprocess.run(
            [str(KANBAN_SH), "add", "publish once", "--operate"], cwd=self.project,
            input="publish", text=True, capture_output=True, env=self.env, check=True,
        )
        card = Path(added.stdout.strip())
        card.rename(self.project / ".git" / "kanban" / "doing" / card.name)
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": "/usr/bin/false",
            "KANBAN_OPERATION_CMD": str(operator),
            "KANBAN_REVIEW_ENABLED": "false",
            "KANBAN_TEST_EVIDENCE": str(evidence),
            "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(evidence.exists(), result.stdout + result.stderr)
        blocked = list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertEqual(len(blocked), 1)
        self.assertIn("blocked_kind: operation_unknown", blocked[0].read_text(encoding="utf-8"))

    def test_blocked_operation_is_not_replayed_and_notifies_secretary(self):
        count = Path(self.temp.name) / "operation-count"
        notifications = Path(self.temp.name) / "notifications"
        operator = self._write_script(
            "blocked-operator.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                cat >/dev/null
                n=0
                [[ -f "$OPERATION_COUNT" ]] && n=$(cat "$OPERATION_COUNT")
                echo $((n + 1)) > "$OPERATION_COUNT"
                printf 'BLOCKED: remote response was inconclusive\n'
                """
            ),
        )
        notify = self._write_script(
            "notify.sh", "#!/usr/bin/env bash\nprintf '%s %s\\n' \"$1\" \"$2\" >> \"$NOTIFICATIONS\"\n",
        )
        added = subprocess.run(
            [str(KANBAN_SH), "add", "uncertain publish", "--operate"], cwd=self.project,
            input="publish", text=True, capture_output=True, env=self.env, check=True,
        )
        env = {
            "KANBAN_WORKER_CMD": "/usr/bin/false", "KANBAN_OPERATION_CMD": str(operator),
            "KANBAN_NOTIFY_CMD": str(notify), "OPERATION_COUNT": str(count),
            "NOTIFICATIONS": str(notifications), "KANBAN_JOBS": "1",
        }
        first = self._run("run", "--once", env_overrides=env)
        second = self._run("run", "--once", env_overrides=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(count.read_text().strip(), "1")
        card = next((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertIn("blocked_kind: operation_unknown", card.read_text(encoding="utf-8"))
        self.assertIn("blocked uncertain publish", notifications.read_text(encoding="utf-8"))

        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)
        resumed = self._run("resume", card_id, env_overrides=env)
        self.assertNotEqual(resumed.returncode, 0)
        self.assertIn("kanban operation", resumed.stdout + resumed.stderr)

        resolved = self._run("operation", card_id, "done", env_overrides=env)
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "done").glob("*.md"))), 1)
        self.assertEqual(count.read_text().strip(), "1")

    def test_operation_retry_requires_explicit_resolution(self):
        count = Path(self.temp.name) / "operation-retry-count"
        operator = self._write_script(
            "retry-operator.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                cat >/dev/null
                n=0
                [[ -f "$OPERATION_COUNT" ]] && n=$(cat "$OPERATION_COUNT")
                n=$((n + 1)); echo "$n" > "$OPERATION_COUNT"
                if [[ $n -eq 1 ]]; then printf 'BLOCKED: check remote\n'; else printf 'OPERATION_OK: verified\n'; fi
                """
            ),
        )
        added = subprocess.run(
            [str(KANBAN_SH), "add", "retry publish", "--operate"], cwd=self.project,
            input="publish", text=True, capture_output=True, env=self.env, check=True,
        )
        card_id = re.search(
            r"^id: (\S+)$", Path(added.stdout.strip()).read_text(encoding="utf-8"), re.M
        ).group(1)
        env = {
            "KANBAN_WORKER_CMD": "/usr/bin/false", "KANBAN_OPERATION_CMD": str(operator),
            "OPERATION_COUNT": str(count), "KANBAN_JOBS": "1",
        }
        self.assertEqual(self._run("run", "--once", env_overrides=env).returncode, 0)
        retry = self._run("operation", card_id, "retry", env_overrides=env)
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(self._run("run", "--once", env_overrides=env).returncode, 0)
        self.assertEqual(count.read_text().strip(), "2")
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "done").glob("*.md"))), 1)

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

    @FULL_ONLY
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
                git add -A
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
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
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

    @FULL_ONLY
    def test_failed_resolver_cannot_merge_unresolved_content_without_review(self):
        cfg = self.project / ".git" / "kanban" / "KANBAN.md"
        cfg.write_text(
            cfg.read_text(encoding="utf-8").replace("review_enabled: true", "review_enabled: false"),
            encoding="utf-8",
        )
        self._seed_conflict()
        worker = self._conflicting_worker()
        resolve = self._write_script(
            "broken-resolve.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'resolver crashed\\n'\nexit 7\n",
        )
        self._add_card("resolver must not lie")
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": str(worker),
            "KANBAN_RESOLVE_CMD": str(resolve),
            "KANBAN_REVIEW_ENABLED": "false",
            "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((self.project / ".git" / "kanban" / "done").glob("*.md")), [])
        blocked = list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertEqual(len(blocked), 1, result.stdout)
        self.assertIn("blocked_kind: review_infra", blocked[0].read_text(encoding="utf-8"))
        self.assertNotIn("<<<<<<<", (self.project / "file.txt").read_text(encoding="utf-8"))

    @FULL_ONLY
    def test_noop_resolver_cannot_hide_binary_conflict(self):
        cfg = self.project / ".git" / "kanban" / "KANBAN.md"
        cfg.write_text(
            cfg.read_text(encoding="utf-8").replace("review_enabled: true", "review_enabled: false"),
            encoding="utf-8",
        )
        (self.project / "binary.dat").write_bytes(b"\0base\n")
        self._git("add", "binary.dat")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "binary base")
        worker = self._write_script(
            "binary-worker.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                ( cd "$KANBAN_TEST_MAIN_ROOT" && printf '\\000main\\n' > binary.dat && \
                  git add binary.dat && git -c user.email=t@t -c user.name=t commit -q -m "main binary" )
                printf '\\000card\\n' > binary.dat
                """
            ),
        )
        resolver = self._write_script("noop-resolver.sh", "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n")
        self._add_card("binary conflict")
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": str(worker), "KANBAN_RESOLVE_CMD": str(resolver),
            "KANBAN_REVIEW_ENABLED": "false", "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list((self.project / ".git" / "kanban" / "done").glob("*.md")), [])
        failed = list((self.project / ".git" / "kanban" / "failed").glob("*.md"))
        self.assertEqual(len(failed), 1, result.stdout + result.stderr)
        self.assertIn("failure_kind: resolve", failed[0].read_text(encoding="utf-8"))
        self.assertEqual((self.project / "binary.dat").read_bytes(), b"\0main\n")

    @FULL_ONLY
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
                git add -A
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
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        card_text = done[0].read_text(encoding="utf-8")
        self.assertIn("still conflicted", card_text)
        self.assertEqual(count_file.read_text(encoding="utf-8").strip(), "2")
        self.assertRegex(card_text, r"last_timings: resolver=\d+s review=\d+s")
        self.assertIn("phase durations: resolver=", card_text)
        self.assertIn("phase durations: merge=", card_text)

    @FULL_ONLY
    def test_resolve_max_attempts_exceeded_moves_to_failed_with_history(self):
        cfg = self.project / ".git" / "kanban" / "KANBAN.md"
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
                git add -A
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
        failed = list((self.project / ".git" / "kanban" / "failed").glob("*.md"))
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

    @FULL_ONLY
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
                  echo "effort=$KANBAN_CARD_EFFORT"
                  echo "conflict=$KANBAN_CONFLICT_FILES"
                  echo "base=$KANBAN_BASE_BRANCH"
                  echo "card=$KANBAN_CARD_BRANCH"
                }} > "{dump_file}"
                printf 'merged change\\n' > file.txt
                git add -A
                """
            ),
        )
        card = self._add_card("routed card", backend="claude", model="opus", effort="high")
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
        self.assertIn("effort=high", dumped)
        self.assertIn("conflict=file.txt", dumped)
        self.assertIn("base=main", dumped)
        self.assertIn(f"card=kanban/{card_id}", dumped)

    def test_dispatcher_refuses_second_run_while_lock_is_live(self):
        lock = self.project / ".git" / "kanban" / ".lock"
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")

        result = self._run("run", "--once")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already running", result.stdout + result.stderr)

    def test_dispatcher_does_not_steal_non_file_lock(self):
        (self.project / ".git" / "kanban" / ".dispatcher.lock").mkdir()
        result = self._run("run", "--once", env_overrides={"KANBAN_WORKER_CMD": "/usr/bin/true"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lock is busy", result.stdout + result.stderr)

    def test_bare_run_refuses_hidden_headless_agent_execution(self):
        result = self._run("run", "--once")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kanban-secretary.sh dispatch", result.stdout + result.stderr)

    def test_managed_git_inspection_disables_configured_helpers(self):
        called = Path(self.temp.name) / "git-helper-called"
        helper = self._write_script(
            "git-helper.sh", "#!/usr/bin/env bash\ntouch \"$GIT_HELPER_CALLED\"\nexit 0\n",
        )
        self._git("config", "diff.external", str(helper))
        self._git("config", "diff.evil.command", str(helper))
        self._git("config", "filter.evil.clean", str(helper))
        self._git("config", "filter.evil.required", "true")
        self._git("config", "core.fsmonitor", str(helper))
        (self.project / ".gitattributes").write_text("* filter=evil diff=evil\n", encoding="utf-8")
        (self.project / "seed.txt").write_text("changed\n", encoding="utf-8")
        env = {"GIT_HELPER_CALLED": str(called)}
        diff = self._run("inspect", "diff", env_overrides=env)
        status = self._run("inspect", "status", env_overrides=env)
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn("changed", diff.stdout)
        self.assertIn("raw files", diff.stderr)
        self.assertIn("conservatively", status.stderr)
        self.assertFalse(called.exists())

        rejected = self._run("inspect", "show", "--output=/tmp/leak", env_overrides=env)
        self.assertNotEqual(rejected.returncode, 0)
        rejected_helper = self._run("inspect", "show", "--ext-diff", env_overrides=env)
        self.assertNotEqual(rejected_helper.returncode, 0)

    def test_managed_git_inspection_preserves_filemode_semantics(self):
        path = self.project / "mode.txt"
        path.write_text("mode\n", encoding="utf-8")
        self._git("add", "mode.txt")
        self._git("commit", "-m", "add mode file")
        self._git("config", "core.filemode", "true")
        path.chmod(0o755)

        status = self._run("inspect", "status")

        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn("mode.txt", status.stdout)

    def _prepare_merge_pending_card(self, title):
        card = self._add_card(title)
        text = card.read_text(encoding="utf-8")
        card_id = re.search(r"^id: (\S+)$", text, re.M).group(1)
        text = text.replace("attempts: 0", "attempts: 1\nmerge_pending: 1\npass_result: 95")
        card.write_text(text, encoding="utf-8")
        branch = "kanban/%s" % card_id
        wt = self.project / ".git" / "kanban" / "wt" / card_id
        self._git("worktree", "add", "-q", "-b", branch, str(wt), "main")
        (wt / "merge-pending.txt").write_text(title + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "prepared"], check=True,
        )
        card.rename(self.project / ".git" / "kanban" / "doing" / card.name)
        return card_id, branch

    @FULL_ONLY
    def test_restart_resumes_merge_checkpoint_without_worker_or_review(self):
        worker_called = Path(self.temp.name) / "worker-called"
        self._prepare_merge_pending_card("resume merge")
        worker = self._write_script(
            "forbidden-worker.sh", "#!/usr/bin/env bash\ntouch \"$WORKER_CALLED\"\nexit 9\n",
        )
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": str(worker), "WORKER_CALLED": str(worker_called),
            "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(worker_called.exists())
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "done").glob("*.md"))), 1)
        self.assertEqual((self.project / "merge-pending.txt").read_text(), "resume merge\n")

    @FULL_ONLY
    def test_restart_recognizes_merge_already_landed_before_card_move(self):
        worker_called = Path(self.temp.name) / "worker-called"
        _, branch = self._prepare_merge_pending_card("already merged")
        self._git("merge", "--no-ff", "-q", "-m", "landed before crash", branch)
        worker = self._write_script(
            "forbidden-worker.sh", "#!/usr/bin/env bash\ntouch \"$WORKER_CALLED\"\nexit 9\n",
        )
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": str(worker), "WORKER_CALLED": str(worker_called),
            "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(worker_called.exists())
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "done").glob("*.md"))), 1)
        self.assertIn("merge was already present", result.stdout)

    @FULL_ONLY
    def test_branch_change_while_waiting_for_merge_lock_never_merges_wrong_branch(self):
        cfg = self.project / ".git" / "kanban" / "KANBAN.md"
        cfg.write_text(
            cfg.read_text(encoding="utf-8").replace("review_enabled: true", "review_enabled: false"),
            encoding="utf-8",
        )
        self._git("branch", "switched")
        ready = Path(self.temp.name) / "merge-lock-ready"
        worker = self._write_script(
            "lock-worker.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'card change\n' > lock-result.txt
                mkdir "$KANBAN_TEST_MAIN_ROOT/.git/kanban/.merge.lock"
                touch "$LOCK_READY"
                """
            ),
        )
        self._add_card("lock branch check")
        env = self.env.copy()
        env.update({
            "KANBAN_WORKER_CMD": str(worker), "KANBAN_REVIEW_ENABLED": "false",
            "KANBAN_JOBS": "1", "LOCK_READY": str(ready),
        })
        proc = subprocess.Popen(
            [str(KANBAN_SH), "run", "--once"], cwd=self.project, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(ready.exists())
        time.sleep(1.2)  # old ordering has now checked HEAD and is waiting on the lock
        self._git("checkout", "-q", "switched")
        (self.project / ".git" / "kanban" / ".merge.lock").rmdir()
        stdout, stderr = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, stdout + stderr)
        self.assertFalse((self.project / "lock-result.txt").exists())
        blocked = list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertEqual(len(blocked), 1, stdout + stderr)
        self.assertIn("blocked_kind: main_branch_changed", blocked[0].read_text(encoding="utf-8"))

    def _run_live_jobs_case(self, *, pinned):
        cfg = self.project / ".git" / "kanban" / "KANBAN.md"
        text = cfg.read_text(encoding="utf-8")
        text = text.replace("jobs: 4", "jobs: 1")
        text = text.replace("review_enabled: true", "review_enabled: false")
        cfg.write_text(text, encoding="utf-8")
        events = Path(self.temp.name) / ("pinned-events" if pinned else "live-events")
        worker = self._write_script(
            "slow-worker.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                echo "start $KANBAN_CARD_TITLE" >> "{events}"
                sleep 1
                echo "end $KANBAN_CARD_TITLE" >> "{events}"
                """
            ),
        )
        for n in range(3):
            self._add_card(f"live jobs {n}")
        env = self.env.copy()
        env.pop("KANBAN_JOBS", None)
        env.update({"KANBAN_WORKER_CMD": str(worker)})
        args = [str(KANBAN_SH), "run"]
        if pinned:
            args += ["-j", "1"]
        proc = subprocess.Popen(
            args, cwd=self.project, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if events.exists() and "start " in events.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        else:
            proc.kill()
            self.fail("first live-jobs worker never started")
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("jobs: 1", "jobs: 3"), encoding="utf-8")
        stdout, stderr = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, stdout + stderr)
        return events.read_text(encoding="utf-8").splitlines(), stdout

    def test_running_dispatcher_increases_parallelism_from_live_project_config(self):
        events, stdout = self._run_live_jobs_case(pinned=False)
        first_end = next(i for i, event in enumerate(events) if event.startswith("end "))
        starts_before_first_end = sum(event.startswith("start ") for event in events[:first_end])
        self.assertGreaterEqual(starts_before_first_end, 2, events)
        self.assertIn("Jobs resized 1 -> 3", stdout)

    def test_explicit_jobs_flag_pins_running_dispatcher(self):
        events, stdout = self._run_live_jobs_case(pinned=True)
        active = 0
        peak = 0
        for event in events:
            active += 1 if event.startswith("start ") else -1
            peak = max(peak, active)
        self.assertEqual(peak, 1, events)
        self.assertIn("Jobs: 1 (pinned", stdout)

    @FULL_ONLY
    def test_resolving_orphan_is_reclaimed_and_not_double_processed(self):
        card = self._add_card("orphan card")
        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)

        # Simulate a dispatcher that crashed mid-resolve: the card is stuck in
        # resolving/ with leftover worktrees/branches from the interrupted
        # attempt.
        wt = self.project / ".git" / "kanban" / "wt"
        self._git("worktree", "add", "-q", "-b", f"kanban/{card_id}", str(wt / card_id), "main")
        self._git(
            "worktree", "add", "-q", "-b", f"kanban-resolve/{card_id}",
            str(wt / f"{card_id}-resolve"), "main",
        )
        resolving_dir = self.project / ".git" / "kanban" / "resolving"
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
                git add -A
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
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        self.assertEqual(len(list(resolving_dir.glob("*.md"))), 0)
        branches = self._git("branch", "--list").stdout
        self.assertNotIn(f"kanban/{card_id}\n", branches.replace(" ", "\n"))
        self.assertNotIn(f"kanban-resolve/{card_id}", branches)

    # -- review infrastructure error vs. quality score -----------------
    #
    # A reviewer that never returns a valid {"score": ...} JSON object
    # (agent_not_found, a bare terminal status line, empty output, ...) is
    # infrastructure flaking, not a "0" verdict, and must not burn a worker
    # attempt. See classify_review_infra_error / review_with_infra_retry in
    # kanban.sh.

    def _counting_worker(self, count_file):
        return self._write_script(
            "worker.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                n=0
                [[ -f "{count_file}" ]] && n=$(cat "{count_file}")
                n=$((n + 1))
                echo "$n" > "{count_file}"
                printf 'from worker\\n' > out.txt
                """
            ),
        )

    def test_review_infra_error_retries_reviewer_without_consuming_worker_attempt(self):
        worker_count = Path(self.temp.name) / "worker_count"
        worker = self._counting_worker(worker_count)
        review_count = Path(self.temp.name) / "review_count"
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                n=0
                [[ -f "{review_count}" ]] && n=$(cat "{review_count}")
                n=$((n + 1))
                echo "$n" > "{review_count}"
                if [[ $n -eq 1 ]]; then
                  printf 'agent target reviewer-56360-23575 not found\\n'
                else
                  printf '{{"score": 90, "feedback": "ok"}}\\n'
                fi
                """
            ),
        )
        self._add_card("infra flaky reviewer card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
                "KANBAN_REVIEW_INFRA_MAX_RETRIES": "2",
                "KANBAN_REVIEW_INFRA_BACKOFF_SECONDS": "0",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")
        self.assertEqual(review_count.read_text(encoding="utf-8").strip(), "2")
        card_text = done[0].read_text(encoding="utf-8")
        self.assertIn("attempts: 1", card_text)
        self.assertIn("review infrastructure retry 1/2: agent_not_found", card_text)
        self.assertNotIn("rework instruction", card_text)

    def test_status_line_reviewer_output_is_classified_as_infra_not_score_zero(self):
        # Regression for the exact real-world shape: a visible Herdr pane's
        # leftover terminal chrome (no JSON braces at all) must not be
        # scored 0 -- it is not an attempted review.
        worker_count = Path(self.temp.name) / "worker_count"
        worker = self._counting_worker(worker_count)
        review_count = Path(self.temp.name) / "review_count"
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                n=0
                [[ -f "{review_count}" ]] && n=$(cat "{review_count}")
                n=$((n + 1))
                echo "$n" > "{review_count}"
                if [[ $n -eq 1 ]]; then
                  printf '20260901-001431-12177  kanban/20260901-0014\\n⏵⏵ auto mode on (shift+tab to cycle)\\n'
                else
                  printf '{{"score": 90, "feedback": "ok"}}\\n'
                fi
                """
            ),
        )
        self._add_card("status line reviewer card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
                "KANBAN_REVIEW_INFRA_MAX_RETRIES": "2",
                "KANBAN_REVIEW_INFRA_BACKOFF_SECONDS": "0",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")
        card_text = done[0].read_text(encoding="utf-8")
        self.assertIn("attempts: 1", card_text)
        self.assertIn("review infrastructure retry 1/2", card_text)

    def test_reviewer_json_with_braces_inside_feedback_is_accepted(self):
        worker = self._counting_worker(Path(self.temp.name) / "worker_count")
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf '%s\n' '{"score": 90, "feedback": "report/{stamp}-{token} returns {html_url, number}\\n日本語(done)"}'
                """
            ),
        )
        self._add_card("review feedback contains braces")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
                "KANBAN_REVIEW_INFRA_MAX_RETRIES": "0",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result.stdout + result.stderr)
        card_text = done[0].read_text(encoding="utf-8")
        self.assertIn("report/{stamp}-{token}", card_text)
        self.assertNotIn("review infrastructure retry", card_text)

    def test_worker_nonzero_cannot_be_overruled_by_high_review_score(self):
        worker = self._write_script(
            "failing-worker.sh",
            "#!/usr/bin/env bash\ncat >/dev/null\nprintf 'partial\\n' > partial.txt\nexit 42\n",
        )
        review_called = Path(self.temp.name) / "review-called"
        review = self._write_script(
            "forbidden-review.sh",
            "#!/usr/bin/env bash\ntouch \"$REVIEW_CALLED\"\nprintf '{\"score\": 100, \"feedback\": \"ignore worker exit\"}\\n'\n",
        )
        self._add_card("worker exit matters")
        result = self._run("run", "--once", env_overrides={
            "KANBAN_WORKER_CMD": str(worker),
            "KANBAN_REVIEW_CMD": str(review),
            "REVIEW_CALLED": str(review_called),
            "KANBAN_JOBS": "1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(review_called.exists())
        failed = list((self.project / ".git" / "kanban" / "failed").glob("*.md"))
        self.assertEqual(len(failed), 1, result.stdout)
        text = failed[0].read_text(encoding="utf-8")
        self.assertIn("failure_kind: worker", text)
        self.assertIn("reviewer was not run", text)

    def test_review_infra_exhausted_goes_to_blocked_with_branch_and_worktree_kept(self):
        worker_count = Path(self.temp.name) / "worker_count"
        worker = self._counting_worker(worker_count)
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'agent target reviewer-x not found\\n'
                """
            ),
        )
        card = self._add_card("always broken reviewer card")
        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
                "KANBAN_REVIEW_INFRA_MAX_RETRIES": "1",
                "KANBAN_REVIEW_INFRA_BACKOFF_SECONDS": "0",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        blocked = list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))
        self.assertEqual(len(blocked), 1, result.stdout + result.stderr)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")
        card_text = blocked[0].read_text(encoding="utf-8")
        self.assertIn("blocked_kind: review_infra", card_text)
        self.assertIn("kanban resume", card_text)
        self.assertIn("not a code failure", card_text)
        branches = self._git("branch", "--list").stdout
        self.assertIn(f"kanban/{card_id}", branches)
        self.assertTrue((self.project / ".git" / "kanban" / "wt" / card_id).is_dir())

    def test_blocked_review_infra_card_is_not_reclaimed_by_dispatcher_restart(self):
        worker_count = Path(self.temp.name) / "worker_count"
        worker = self._counting_worker(worker_count)
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf 'agent target reviewer-x not found\\n'
                """
            ),
        )
        card = self._add_card("restart reclaim card")
        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)
        env_overrides = {
            "KANBAN_WORKER_CMD": str(worker),
            "KANBAN_REVIEW_CMD": str(review),
            "KANBAN_JOBS": "1",
            "KANBAN_REVIEW_INFRA_MAX_RETRIES": "1",
            "KANBAN_REVIEW_INFRA_BACKOFF_SECONDS": "0",
        }
        result = self._run("run", "--once", env_overrides=env_overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))), 1)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")

        # Simulate a dispatcher restart (`kanban run --once` again): the
        # worker must not be re-run, and the card must stay parked in
        # blocked/ rather than being silently requeued to todo.
        result2 = self._run("run", "--once", env_overrides=env_overrides)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        self.assertIn("todo is empty", result2.stdout)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))), 1)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "todo").glob("*.md"))), 0)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")
        branches = self._git("branch", "--list").stdout
        self.assertIn(f"kanban/{card_id}", branches)

    def test_kanban_resume_retries_only_review_and_reaches_done(self):
        worker_count = Path(self.temp.name) / "worker_count"
        worker = self._counting_worker(worker_count)
        review_state = Path(self.temp.name) / "review_state"
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                if [[ -f "{review_state}" ]]; then
                  printf '{{"score": 90, "feedback": "ok"}}\\n'
                else
                  touch "{review_state}"
                  printf 'agent target reviewer-x not found\\n'
                fi
                """
            ),
        )
        card = self._add_card("resume card")
        card_id = re.search(r"^id: (\S+)$", card.read_text(encoding="utf-8"), re.M).group(1)
        env_overrides = {
            "KANBAN_WORKER_CMD": str(worker),
            "KANBAN_REVIEW_CMD": str(review),
            "KANBAN_JOBS": "1",
            "KANBAN_REVIEW_INFRA_MAX_RETRIES": "0",
            "KANBAN_REVIEW_INFRA_BACKOFF_SECONDS": "0",
        }
        result = self._run("run", "--once", env_overrides=env_overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "blocked").glob("*.md"))), 1)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")

        result2 = self._run("resume", card_id, env_overrides=env_overrides)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "todo").glob("*.md"))), 1)
        self.assertEqual(len(list((self.project / ".git" / "kanban" / "done").glob("*.md"))), 0)

        result3 = self._run("run", "--once", env_overrides=env_overrides)
        self.assertEqual(result3.returncode, 0, result3.stderr)
        done = list((self.project / ".git" / "kanban" / "done").glob("*.md"))
        self.assertEqual(len(done), 1, result3.stdout + result3.stderr)
        # the worker must not be re-invoked on resume -- only the reviewer
        # is re-run against the work already committed on the kept branch.
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "1")

    def test_low_review_score_still_retries_worker_and_consumes_attempts(self):
        # Regression: a genuine low-quality review (real parseable JSON,
        # just under threshold) must still behave exactly as before --
        # infra classification must never swallow a real verdict.
        cfg = self.project / ".git" / "kanban" / "KANBAN.md"
        text = cfg.read_text(encoding="utf-8")
        cfg.write_text(text.replace("max_attempts: 3", "max_attempts: 2"), encoding="utf-8")

        worker_count = Path(self.temp.name) / "worker_count"
        worker = self._counting_worker(worker_count)
        review = self._write_script(
            "review.sh",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                cat >/dev/null
                printf '{"score": 40, "feedback": "not good enough"}\\n'
                """
            ),
        )
        self._add_card("genuinely low score card")

        result = self._run(
            "run", "--once",
            env_overrides={
                "KANBAN_WORKER_CMD": str(worker),
                "KANBAN_REVIEW_CMD": str(review),
                "KANBAN_JOBS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        failed = list((self.project / ".git" / "kanban" / "failed").glob("*.md"))
        self.assertEqual(len(failed), 1, result.stdout + result.stderr)
        self.assertEqual(worker_count.read_text(encoding="utf-8").strip(), "2")
        card_text = failed[0].read_text(encoding="utf-8")
        self.assertIn("attempts: 2", card_text)
        self.assertIn("not good enough", card_text)
        self.assertNotIn("review infrastructure", card_text)


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


class SecretaryBoardAdminContractTests(unittest.TestCase):
    def test_board_admin_boundary_is_consistent_across_contracts(self):
        for path in (
            REPO / "README.md",
            REPO / "skills" / "kanban-dispatch" / "SKILL.md",
            REPO / "kanban.sh",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("kanban remove", text, str(path))
            self.assertIn("kanban config", text, str(path))
            self.assertIn("--operate", text, str(path))
        self.assertIn(
            "backlog/Ready以外を拒否", (REPO / "kanban.sh").read_text(encoding="utf-8")
        )
        skill = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("including raw `rm`", skill)


class WorkerQuestionContractTests(unittest.TestCase):
    def test_unattended_question_contract_is_propagated(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        skill = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        report = (REPO / "skills" / "kanban-report" / "SKILL.md").read_text(encoding="utf-8")
        policy = (REPO / "kanban.sh").read_text(encoding="utf-8")
        wrapper = (REPO / "herdr-agent-worker.sh").read_text(encoding="utf-8")

        self.assertIn("AskUserQuestion", readme)
        self.assertIn("agent_question", skill)
        self.assertIn("blocked_kind: user_input", report)
        self.assertIn("kanban resume", policy)
        self.assertIn("--disallowedTools AskUserQuestion", wrapper)


class DiagnosisCardContractTests(unittest.TestCase):
    def test_secretary_skill_keeps_diagnosis_small_read_only_and_separate(self):
        text = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("kanban add --diagnose", normalized)
        self.assertIn("read-only", normalized)
        self.assertIn("5 minutes", normalized)
        self.assertIn("10-minute hard maximum", normalized)
        self.assertIn("File the fix as a separate card", normalized)
        self.assertIn("Never inflate a diagnosis", normalized)

    def test_generated_policy_contains_timebox_and_scope_block_contract(self):
        text = (REPO / "kanban.sh").read_text(encoding="utf-8")
        self.assertIn("diagnosis_target_minutes: 5", text)
        self.assertIn("diagnosis_max_minutes: 10", text)
        self.assertIn("BLOCKED: scope/timebox", text)
        self.assertIn("修正は診断後の別カード", text)


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


class SecretaryRequiresHerdrContractTests(unittest.TestCase):
    def test_contract_has_no_execution_mode_choice(self):
        skill = (REPO / "skills" / "kanban-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        template = (REPO / "kanban.sh").read_text(encoding="utf-8")
        skill = " ".join(skill.split())

        self.assertIn("There is no headless secretary mode", skill)
        self.assertIn("do not ask the user to choose an execution mode", skill)
        self.assertIn("There is no execution-mode choice", readme)
        self.assertIn("実行モードを質問せず", template)


class TestTierContractTests(unittest.TestCase):
    """Locks which tests are FULL_ONLY (real `kanban.sh run --once` git
    worktree/merge integration) so fast/full membership can only change via a
    deliberate edit to this test, not silently. See gui/VERIFY.md "テストの
    段階 (fast / full)"."""

    EXPECTED_FULL_ONLY = {
        "test_no_conflict_merge_still_works",
        "test_merge_conflict_after_review_goes_to_resolver_then_done",
        "test_resolver_retries_on_low_review_score_then_passes",
        "test_resolve_max_attempts_exceeded_moves_to_failed_with_history",
        "test_resolve_cmd_receives_card_routing_and_conflict_context",
        "test_resolving_orphan_is_reclaimed_and_not_double_processed",
        "test_operator_card_runs_once_in_main_checkout_without_review_or_merge",
        "test_failed_resolver_cannot_merge_unresolved_content_without_review",
        "test_restart_recognizes_merge_already_landed_before_card_move",
        "test_restart_resumes_merge_checkpoint_without_worker_or_review",
        "test_noop_resolver_cannot_hide_binary_conflict",
        "test_branch_change_while_waiting_for_merge_lock_never_merges_wrong_branch",
        "test_structured_card_persists_brief_report_review_and_accept_merge_facts",
        "test_incomplete_structured_report_becomes_needs_info_without_reviewer",
        "test_typed_spike_parks_review_decision_with_worktree_preserved",
    }

    def test_full_only_membership_is_exactly_the_documented_set(self):
        # FULL_ONLY's skipIf condition is frozen at decoration time (module
        # import), so re-patching KANBAN_TEST_TIER at test time cannot
        # retroactively toggle __unittest_skip__. Read the source instead:
        # every `@FULL_ONLY` must immediately precede a `def test_...`.
        src = Path(__file__).read_text(encoding="utf-8")
        marked = set(re.findall(r"@FULL_ONLY\s*\n\s*def (test_\w+)", src))
        self.assertEqual(marked, self.EXPECTED_FULL_ONLY)

    def test_full_only_actually_skips_under_fast_tier(self):
        # Confirms the decorator, not just its source annotation, honors
        # KANBAN_TEST_TIER=fast (fresh subprocess: skip decision is baked in
        # at import time, so this must reimport in a clean interpreter).
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "tests.test_kanban_secretary.DispatcherWorkflowTests"],
            cwd=REPO, env={**os.environ, "KANBAN_TEST_TIER": "fast"},
            capture_output=True, text=True,
        )
        skipped_names = set(re.findall(r"^(test_\w+) \([^)]+\) \.\.\. skipped ", result.stderr, re.M))
        self.assertEqual(skipped_names, self.EXPECTED_FULL_ONLY, result.stderr)

    def test_full_only_tests_only_exist_in_dispatcher_workflow_tests(self):
        # FULL_ONLY marks real end-to-end git worktree/merge tests; keeping
        # them confined to one class stops fast/full drift from spreading
        # across unrelated unit-test classes.
        for name in self.EXPECTED_FULL_ONLY:
            self.assertTrue(
                hasattr(DispatcherWorkflowTests, name),
                f"{name} expected on DispatcherWorkflowTests but not found",
            )


if __name__ == "__main__":
    unittest.main()
