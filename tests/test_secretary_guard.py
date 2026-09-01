"""Mock tests for the secretary direct-action guard.

Covers guard/command_classify.py (bypass attempts included),
guard/claude_secretary_guard.py (pane/tool decision), guard/secretary_marker.py
(lifecycle), and gui/setup_core.py's guard install/uninstall/status
(idempotent, preserves unrelated settings.json content, temp-HOME only).

No real HOME, git remote, or Herdr is touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "guard"))
sys.path.insert(0, os.path.join(REPO, "gui"))

import command_classify as classify  # noqa: E402
import claude_secretary_guard as guard  # noqa: E402
import secretary_marker as marker  # noqa: E402
import setup_core  # noqa: E402


class TempProjectMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mornkanban-guard-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "project")
        os.makedirs(self.root)
        subprocess.run(["git", "init", "-q", "-b", "main", self.root], check=True)
        subprocess.run(
            ["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "--allow-empty", "-qm", "init"], check=True,
        )
        os.makedirs(os.path.join(self.root, ".git", "kanban"))


# --- command_classify.py: allowlist and bypass attempts ---------------------

class TestCommandClassify(unittest.TestCase):
    def allow(self, cmd):
        allowed, reason = classify.classify(cmd)
        self.assertTrue(allowed, "expected allow for %r, got deny: %s" % (cmd, reason))

    def deny(self, cmd):
        allowed, reason = classify.classify(cmd)
        self.assertFalse(allowed, "expected deny for %r, got allow: %s" % (cmd, reason))

    # The secretary may use board-control commands; only the visible
    # dispatcher entrypoint may start workers.
    def test_allows_kanban_control_commands(self):
        for cmd in [
            "kanban",
            'kanban add "title"',
            "kanban show 123",
            "kanban list",
            "kanban init",
            "kanban migrate",
            "kanban --version",
            'kanban send alias "title"',
            "kanban remove 20260901-172101-5531",
            "kanban config set jobs 8",
            "kanban config set default_model gpt-5.6-sol",
            "kanban resume 20260901-200631-30037",
            "kanban operation 20260901-200631-30037 done",
            "kanban install",
            "kanban update",
            "kanban uninstall",
            "./kanban.sh resume 20260901-200631-30037",
        ]:
            self.allow(cmd)

    def test_denies_bare_run_unknown_commands_and_env_override(self):
        self.deny("kanban run --once")
        self.deny("kanban future-command --future-option")
        self.deny("env KANBAN_WORKER_CMD=/tmp/payload kanban run --once")

    def test_denies_fake_kanban_executables(self):
        self.deny("/tmp/kanban run")
        self.deny("/tmp/kanban.sh future-command")
        self.deny("/tmp/kanban-secretary.sh dispatch")

    def test_allows_secretary_dispatcher_commands(self):
        for cmd in [
            "~/git/MornKanban/kanban-secretary.sh dispatch",
            os.path.join(REPO, "kanban-secretary.sh") + " bootstrap",
            os.path.join(REPO, "kanban-secretary.sh") + " dispatch --once",
            os.path.join(REPO, "kanban-secretary.sh") + " end",
        ]:
            self.allow(cmd)

    def test_allows_managed_git_and_plain_inspection(self):
        for cmd in [
            "kanban inspect status",
            "kanban inspect log 5",
            "kanban inspect diff",
            "kanban inspect show HEAD",
            "kanban inspect branch",
            "cat .git/kanban/KANBAN.md",
            "ls -la .git/kanban",
            "grep -rn foo .",
            "pwd",
            "find . -name '*.md'",
            'kanban add "build; deploy"',
        ]:
            self.allow(cmd)

    def test_allows_chained_readonly_commands(self):
        self.allow("kanban inspect status && kanban inspect log 1")
        self.allow("cat a.md; cat b.md")

    # denied: implementation / verification / git mutation / external publish
    def test_denies_file_write_redirection(self):
        self.deny("echo hi > file.txt")
        self.deny("cat a > b")
        self.deny("printf secret 1>/tmp/file")
        self.deny("printf secret 2>/tmp/file")
        self.allow("cat missing 2>/dev/null")
        self.allow("cat missing 2>&1")
        self.deny("printf secret 3<>/tmp/file >&3")
        self.deny("rm .git/kanban/todo/card.md")

    def test_denies_git_mutation(self):
        for cmd in [
            "git add .",
            "git commit -am 'msg'",
            "git push origin main",
            "git pull",
            "git fetch --all",
            "git merge feature",
            "git rebase main",
            "git cherry-pick abc",
            "git revert abc",
            "git reset --hard",
            "git checkout -b new",
            "git switch main",
            "git branch -D old",
            "git tag v1.0.0",
            "git worktree add ../x",
            "git -c diff.external=/tmp/payload diff",
            "git remote update",
            "git reflog expire --all",
        ]:
            self.deny(cmd)

    def test_denies_direct_git_even_for_apparent_reads(self):
        for cmd in ["git status", "git diff", "git log -1", "git branch", "git remote -v"]:
            self.deny(cmd)

    def test_denies_read_utility_output_files(self):
        self.deny("find . -fprint /tmp/files")
        self.deny("tree -o /tmp/tree.txt")
        self.deny("tree -o/tmp/tree.txt .")
        self.deny("diff --output=/tmp/diff a b")

    def test_denies_headless_agent_cli(self):
        self.deny("claude -p 'do it'")
        self.deny("codex exec 'do it'")

    def test_denies_external_publish(self):
        self.deny("gh pr create")
        self.deny("gh release create v1.0.0")
        self.deny("gh issue comment 1 --body hi")
        self.deny("npm publish")
        self.deny("docker push myimage")

    def test_denies_build_test_lint(self):
        for cmd in ["npm test", "npm run build", "make test", "pytest", "cargo build"]:
            self.deny(cmd)

    # bypass attempts must still be denied
    def test_denies_shell_chaining_bypass(self):
        self.deny("git status && git push")
        self.deny("git status; git push")
        self.deny("echo ok || git push")
        self.deny("git log | git push")

    def test_denies_absolute_path_bypass(self):
        self.deny("/usr/bin/git push origin main")
        self.deny("/opt/homebrew/bin/git commit -am x")

    def test_denies_sh_c_bypass(self):
        self.deny("sh -c 'git push'")
        self.deny("bash -c 'git commit -am x'")

    def test_denies_env_wrapper_bypass(self):
        self.deny("env FOO=bar git push")
        self.deny("env -i git push")

    def test_denies_wrapper_script_bypass(self):
        self.deny("./deploy.sh")
        self.deny("~/bin/my-git-wrapper.sh push")

    def test_denies_command_substitution(self):
        self.deny("echo $(git push)")
        self.deny("echo `git push`")

    def test_denies_sudo_and_xargs(self):
        self.deny("sudo rm -rf /")
        self.deny("xargs git push")

    def test_denies_general_purpose_interpreters(self):
        # python3/node etc. are arbitrary-code-execution escape hatches: they
        # can write files (bypassing Edit/Write/redirect denial) and shell
        # out to git/gh/headless-agent CLIs (bypassing every other rule).
        for cmd in [
            "python3 -c \"open('evil.py','w').write('x')\"",
            "python3 malicious_script.py --push --commit",
            "python -c 'import os; os.system(\"git push\")'",
            "node -e \"require('fs').writeFileSync('x','y')\"",
            "node script.js",
            "deno run script.ts",
            "perl -e 'print 1'",
            "ruby -e 'puts 1'",
            "php -r 'echo 1;'",
        ]:
            self.deny(cmd)

    def test_denies_interpreter_via_wrapper_bypass(self):
        self.deny("env FOO=bar python3 -c 'import os; os.system(\"git push\")'")
        self.deny("sh -c \"python3 -c 'os.system(\\\"git push\\\")'\"")
        self.deny("/usr/bin/python3 -c 'pass'")


# --- claude_secretary_guard.py: pane/tool decision ---------------------------

class TestGuardDecision(TempProjectMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        marker.write_marker(self.root, "pane-secretary", "secretary-project")
        self.secretary_env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "pane-secretary"}
        self.worker_env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "pane-worker"}
        self.other_project_env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "pane-secretary"}

    def decide(self, tool_name, tool_input, env, cwd=None):
        payload = {"tool_name": tool_name, "cwd": cwd or self.root, "tool_input": tool_input}
        return guard.decide(payload, env)

    def test_denies_in_process_delegation_in_secretary_pane(self):
        for tool in ["Task", "Agent"]:
            deny, category, _, root = self.decide(tool, {}, self.secretary_env)
            self.assertTrue(deny)
            self.assertEqual(category, "delegation")
            self.assertEqual(root, os.path.realpath(self.root))

    def test_denies_direct_file_edit_in_secretary_pane(self):
        for tool in ["Edit", "Write", "NotebookEdit"]:
            deny, category, _, _ = self.decide(tool, {}, self.secretary_env)
            self.assertTrue(deny)
            self.assertEqual(category, "edit")

    def test_denies_git_commit_bash_in_secretary_pane(self):
        deny, category, _, _ = self.decide(
            "Bash", {"command": "git commit -am x"}, self.secretary_env
        )
        self.assertTrue(deny)
        self.assertEqual(category, "bash")

    def test_allows_kanban_control_bash_in_secretary_pane(self):
        for command in ('kanban add "t"', "kanban resume card", "kanban operation card done"):
            deny, _, _, _ = self.decide("Bash", {"command": command}, self.secretary_env)
            self.assertFalse(deny, command)

    def test_denies_bare_dispatcher_in_secretary_pane(self):
        deny, _, _, _ = self.decide("Bash", {"command": "kanban run --once"}, self.secretary_env)
        self.assertTrue(deny)

    def test_allows_readonly_tools_untouched(self):
        for tool in ["Read", "Grep", "Glob", "WebFetch", "WebSearch", "TodoWrite"]:
            deny, _, _, _ = self.decide(tool, {}, self.secretary_env)
            self.assertFalse(deny, "tool %s must never be denied by this guard" % tool)

    # scope: only the recorded secretary pane for this exact project is affected

    def test_worker_pane_not_blocked(self):
        for tool in ["Task", "Edit", "Write"]:
            deny, _, _, _ = self.decide(tool, {}, self.worker_env)
            self.assertFalse(deny)
        deny, _, _, _ = self.decide("Bash", {"command": "git push"}, self.worker_env)
        self.assertFalse(deny)

    def test_no_herdr_env_not_blocked(self):
        deny, _, _, _ = self.decide("Task", {}, {})
        self.assertFalse(deny)

    def test_project_without_kanban_dir_not_blocked(self):
        other = os.path.join(self.tmp, "no-kanban-here")
        os.makedirs(other)
        deny, _, _, _ = self.decide("Task", {}, self.secretary_env, cwd=other)
        self.assertFalse(deny)

    def test_other_project_secretary_not_cross_blocked(self):
        other_root = os.path.join(self.tmp, "other-project")
        os.makedirs(other_root)
        subprocess.run(["git", "init", "-q", "-b", "main", other_root], check=True)
        os.makedirs(os.path.join(other_root, ".git", "kanban"))
        marker.write_marker(other_root, "pane-other-secretary", "secretary-other")
        # pane-secretary is the *first* project's secretary, not the second's
        deny, _, _, _ = self.decide("Task", {}, self.secretary_env, cwd=other_root)
        self.assertFalse(deny)

    def test_stale_marker_after_end_allows(self):
        marker.clear_marker(self.root)
        deny, _, _, _ = self.decide("Task", {}, self.secretary_env)
        self.assertFalse(deny)

    def test_rebootstrap_in_new_pane_supersedes_stale_marker(self):
        marker.write_marker(self.root, "pane-new-secretary", "secretary-project")
        deny, _, _, _ = self.decide("Task", {}, self.secretary_env)  # old pane id
        self.assertFalse(deny)
        deny, _, _, _ = self.decide(
            "Task", {}, {"HERDR_ENV": "1", "HERDR_PANE_ID": "pane-new-secretary"}
        )
        self.assertTrue(deny)

    def test_audit_log_has_no_secrets_and_is_capped(self):
        deny, category, detail, root = self.decide(
            "Bash", {"command": "git commit -am 'super secret token AAA'"}, self.secretary_env
        )
        self.assertTrue(deny)
        marker.append_audit(root, "deny tool=Bash category=%s detail=%s" % (category, detail))
        for _ in range(marker.AUDIT_MAX_LINES + 20):
            marker.append_audit(root, "deny tool=Bash category=bash detail=git-mutation")
        with open(marker.audit_path(self.root), "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        self.assertLessEqual(len(lines), marker.AUDIT_MAX_LINES)
        self.assertTrue(all("secret token AAA" not in line for line in lines))


# --- secretary_marker.py lifecycle ------------------------------------------

class TestSecretaryMarker(TempProjectMixin, unittest.TestCase):
    def test_write_read_clear_roundtrip(self):
        self.assertIsNone(marker.read_marker(self.root))
        marker.write_marker(self.root, "pane-1", "secretary-x")
        data = marker.read_marker(self.root)
        self.assertEqual(data["pane_id"], "pane-1")
        self.assertTrue(marker.is_secretary_pane(self.root, "pane-1"))
        self.assertFalse(marker.is_secretary_pane(self.root, "pane-2"))
        marker.clear_marker(self.root)
        self.assertIsNone(marker.read_marker(self.root))

    def test_project_root_from_walks_up(self):
        nested = os.path.join(self.root, "a", "b")
        os.makedirs(nested)
        self.assertEqual(marker.project_root_from(nested), os.path.realpath(self.root))

    def test_project_root_from_card_worktree_uses_outer_project(self):
        worktree = os.path.join(self.root, ".git", "kanban", "wt", "card-1")
        subprocess.run(
            ["git", "-C", self.root, "worktree", "add", "-q", "-b", "test-card", worktree],
            check=True,
        )
        nested = os.path.join(worktree, "src")
        os.makedirs(nested)
        self.assertEqual(marker.project_root_from(nested), os.path.realpath(self.root))

    def test_project_root_from_none_without_kanban_dir(self):
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(bare)
        self.assertIsNone(marker.project_root_from(bare))


# --- gui/setup_core.py guard install/uninstall/status -----------------------

class TestGuardSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mornkanban-guard-setup-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.settings_path = os.path.join(self.tmp, "settings.json")

    def test_install_on_fresh_settings(self):
        self.assertEqual(setup_core.claude_guard_state(self.settings_path), "not-installed")
        msg = setup_core.install_claude_guard(self.settings_path)
        self.assertIn("導入しました", msg)
        self.assertEqual(setup_core.claude_guard_state(self.settings_path), "enforced")
        with open(self.settings_path) as fh:
            data = json.load(fh)
        entry = data["hooks"]["PreToolUse"][0]
        self.assertEqual(entry["matcher"], setup_core.GUARD_MATCHER)

    def test_install_is_idempotent(self):
        setup_core.install_claude_guard(self.settings_path)
        with open(self.settings_path) as fh:
            first = fh.read()
        msg = setup_core.install_claude_guard(self.settings_path)
        self.assertIn("導入済み", msg)
        with open(self.settings_path) as fh:
            second = fh.read()
        self.assertEqual(first, second)

    def test_install_preserves_existing_unrelated_settings(self):
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "SomeOtherTool", "hooks": [{"type": "command", "command": "echo other"}]}
                ]
            },
            "unrelatedTopLevelKey": {"nested": True},
        }
        with open(self.settings_path, "w") as fh:
            json.dump(existing, fh)

        setup_core.install_claude_guard(self.settings_path)

        with open(self.settings_path) as fh:
            data = json.load(fh)
        self.assertEqual(data["unrelatedTopLevelKey"], {"nested": True})
        matchers = {e["matcher"] for e in data["hooks"]["PreToolUse"]}
        self.assertIn("SomeOtherTool", matchers)
        self.assertIn(setup_core.GUARD_MATCHER, matchers)
        self.assertTrue(os.path.exists(self.settings_path + ".mornkanban-guard.bak"))

    def test_uninstall_removes_only_our_entry(self):
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "SomeOtherTool", "hooks": [{"type": "command", "command": "echo other"}]}
                ]
            }
        }
        with open(self.settings_path, "w") as fh:
            json.dump(existing, fh)
        setup_core.install_claude_guard(self.settings_path)

        msg = setup_core.uninstall_claude_guard(self.settings_path)
        self.assertIn("削除しました", msg)

        with open(self.settings_path) as fh:
            data = json.load(fh)
        matchers = {e["matcher"] for e in data["hooks"]["PreToolUse"]}
        self.assertIn("SomeOtherTool", matchers)
        self.assertNotIn(setup_core.GUARD_MATCHER, matchers)

    def test_uninstall_on_never_installed_is_noop(self):
        msg = setup_core.uninstall_claude_guard(self.settings_path)
        self.assertIn("未導入", msg)
        self.assertFalse(os.path.exists(self.settings_path))

    def test_status_vocabulary(self):
        status = setup_core.guard_status(self.settings_path)
        self.assertIn(status["claude"], {"enforced", "not-installed", "misconfigured"})
        self.assertEqual(status["codex"], "partial")

    def test_narrower_old_matcher_self_heals_on_reinstall(self):
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Task",
                        "hooks": [{"type": "command", "command": setup_core._guard_command()}],
                    }
                ]
            }
        }
        with open(self.settings_path, "w") as fh:
            json.dump(existing, fh)
        self.assertEqual(setup_core.claude_guard_state(self.settings_path), "misconfigured")
        setup_core.install_claude_guard(self.settings_path)
        self.assertEqual(setup_core.claude_guard_state(self.settings_path), "enforced")

    def test_fake_hook_containing_guard_path_is_not_enforced(self):
        existing = {
            "hooks": {
                "PreToolUse": [{
                    "matcher": setup_core.GUARD_MATCHER,
                    "hooks": [{
                        "type": "command",
                        "command": "echo %s" % setup_core.GUARD_HOOK_SCRIPT,
                    }],
                }]
            }
        }
        with open(self.settings_path, "w") as fh:
            json.dump(existing, fh)
        self.assertEqual(setup_core.claude_guard_state(self.settings_path), "not-installed")


# --- kanban-secretary.sh bootstrap/end marker lifecycle (mock Herdr) --------

SECRETARY_SH = os.path.join(REPO, "kanban-secretary.sh")


class TestSecretaryScriptMarkerLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mornkanban-secretary-sh-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)
        subprocess.run(["git", "init", "-q", "-b", "main", self.project], check=True)
        self.log = os.path.join(self.tmp, "herdr.log")

        fake_bin = os.path.join(self.tmp, "bin")
        os.makedirs(fake_bin)
        herdr = os.path.join(fake_bin, "herdr")
        with open(herdr, "w", encoding="utf-8") as fh:
            fh.write(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -eu
                    echo "$*" >>"$HERDR_TEST_LOG"
                    case "$1 $2" in
                      "pane layout")
                        printf '%s\\n' '{"result":{"layout":{"panes":[{"pane_id":"w1:p1","rect":{"width":160,"height":40}}]}}}'
                        ;;
                      *) printf '%s\\n' '{"result":{}}' ;;
                    esac
                    """
                )
            )
        os.chmod(herdr, 0o755)

        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": fake_bin + os.pathsep + self.env.get("PATH", ""),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w1:p1",
                "HERDR_TEST_LOG": self.log,
                "KANBAN_BIN": os.path.join(REPO, "kanban.sh"),
                "KANBAN_CONFIG_DIR": os.path.join(self.tmp, "registry-config"),
            }
        )
        self.env.pop("KANBAN_HERDR_SECRETARY", None)

    def run_secretary(self, *args):
        return subprocess.run(
            [SECRETARY_SH, *args],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

    def test_bootstrap_writes_marker_matching_this_pane(self):
        result = self.run_secretary("bootstrap", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("guard=", result.stdout)
        self.assertTrue(marker.is_secretary_pane(self.project, "w1:p1"))
        self.assertFalse(marker.is_secretary_pane(self.project, "w1:p2"))

    def test_end_clears_marker(self):
        self.run_secretary("bootstrap", self.project)
        self.assertTrue(marker.is_secretary_pane(self.project, "w1:p1"))
        result = self.run_secretary("end", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.is_secretary_pane(self.project, "w1:p1"))

    def test_guard_sees_pane_denied_only_after_bootstrap_and_allowed_after_end(self):
        payload_env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1"}

        # before bootstrap: no marker yet -> not blocked
        deny, _, _, _ = guard.decide({"tool_name": "Task", "cwd": self.project}, payload_env)
        self.assertFalse(deny)

        self.run_secretary("bootstrap", self.project)
        deny, _, _, _ = guard.decide({"tool_name": "Task", "cwd": self.project}, payload_env)
        self.assertTrue(deny)

        self.run_secretary("end", self.project)
        deny, _, _, _ = guard.decide({"tool_name": "Task", "cwd": self.project}, payload_env)
        self.assertFalse(deny)


if __name__ == "__main__":
    unittest.main()
