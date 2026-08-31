#!/usr/bin/env python3
"""E2E tests for the review_enabled setting (`kanban.sh` dispatcher).

Uses mock worker/reviewer/resolver/notify commands (via KANBAN_*_CMD env
overrides) so no real agent CLI or network call is required. Each test runs
in its own git repo under a temp dir; nothing touches the real HOME/registry.
"""
import glob
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
KANBAN_SH = REPO / "kanban.sh"

WORKER_OK = """#!/usr/bin/env bash
cat >/dev/null
echo "worker output for ${KANBAN_CARD_TITLE:-?}" > out.txt
git add -A
echo "worker done"
"""

WORKER_FAIL = """#!/usr/bin/env bash
cat >/dev/null
echo "worker exploded"
exit 3
"""

REVIEWER_FORBIDDEN = """#!/usr/bin/env bash
cat >/dev/null
echo "REVIEWER MUST NOT BE CALLED WHEN review_enabled=false" >&2
exit 1
"""

REVIEWER_PASS = """#!/usr/bin/env bash
cat >/dev/null
echo '{"score": 95, "feedback": "fine"}'
"""

REVIEWER_FAIL_ONCE_THEN_PASS = """#!/usr/bin/env bash
cat >/dev/null
state_file="$KANBAN_STATE_DIR/review_calls"
n=0
[ -f "$state_file" ] && n=$(cat "$state_file")
n=$((n + 1))
echo "$n" > "$state_file"
if [ "$n" -lt 2 ]; then
  echo '{"score": 10, "feedback": "needs work"}'
else
  echo '{"score": 95, "feedback": "fine"}'
fi
"""

RESOLVER_OK = """#!/usr/bin/env bash
cat >/dev/null
python3 - <<'PY'
import re
t = open("f.txt").read()
t = re.sub(r"<<<<<<<[^\\n]*\\n", "", t)
t = re.sub(r"=======\\n", "", t)
t = re.sub(r">>>>>>>[^\\n]*\\n", "", t)
open("f.txt", "w").write(t)
PY
git add -A
echo "resolved"
"""


class ReviewToggleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "proj"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "t"], check=True)
        (self.repo / "f.txt").write_text("line1\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True)

        self.env = dict(os.environ)
        self.env["HOME"] = str(self.root / "home")
        (self.root / "home").mkdir()
        self.env.pop("KANBAN_REVIEW_ENABLED", None)

        self._run("init")

    def _run(self, *args, input_text=None, env=None, check=True):
        result = subprocess.run(
            ["bash", str(KANBAN_SH), *args],
            cwd=str(self.repo),
            input=input_text,
            capture_output=True,
            text=True,
            env=env if env is not None else self.env,
        )
        if check and result.returncode != 0:
            self.fail("kanban %s failed: %s\n%s" % (" ".join(args), result.stdout, result.stderr))
        return result

    def _script(self, name, content):
        p = self.root / name
        p.write_text(content)
        p.chmod(0o755)
        return str(p)

    def _card_text(self, state):
        files = sorted(glob.glob(str(self.repo / ".kanban" / state / "*.md")))
        self.assertEqual(len(files), 1, "expected exactly one card in %s: %s" % (state, files))
        return Path(files[0]).read_text()

    # -- 1. unset default: reviewer still called -----------------------------

    def test_default_unset_calls_reviewer(self):
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_PASS)
        self._run("add", "card", input_text="task", env=env)
        r = self._run("run", "--once", env=env)
        self.assertIn("score=95", r.stdout)
        text = self._card_text("done")
        self.assertIn("review_enabled: true", text)
        self.assertIn("score: 95", text)

    # -- 2. project review_enabled: false -> reviewer 0 calls, done, history --

    def test_project_false_skips_reviewer(self):
        (self.repo / ".kanban" / "KANBAN.md").write_text(
            (self.repo / ".kanban" / "KANBAN.md").read_text().replace(
                "review_enabled: true", "review_enabled: false"
            )
        )
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_FORBIDDEN)
        self._run("add", "card", input_text="task", env=env)
        r = self._run("run", "--once", env=env)
        self.assertIn("review disabled", r.stdout)
        self.assertIn("PASS", r.stdout)
        text = self._card_text("done")
        self.assertIn("review_enabled: false", text)
        self.assertIn("review_source: project", text)
        self.assertIn("review skipped: review_enabled=false (source: project)", text)
        self.assertNotIn("score:", text)

    # -- 3. project true: score/threshold/rework works as before ------------

    def test_project_true_rework_loop(self):
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_FAIL_ONCE_THEN_PASS)
        env["KANBAN_STATE_DIR"] = str(self.root)
        self._run("add", "card", "-t", "50", input_text="task", env=env)
        r = self._run("run", "--once", env=env)
        self.assertIn("RETRY score=10", r.stdout)
        self.assertIn("PASS score=95", r.stdout)
        text = self._card_text("done")
        self.assertIn("attempts: 2", text)

    # -- 4. priority: card > env > project > default -------------------------

    def test_priority_card_overrides_env_and_project(self):
        (self.repo / ".kanban" / "KANBAN.md").write_text(
            (self.repo / ".kanban" / "KANBAN.md").read_text().replace(
                "review_enabled: true", "review_enabled: false"
            )
        )
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_PASS)
        env["KANBAN_REVIEW_ENABLED"] = "false"
        # explicit card override wins over env=false and project=false
        self._run("add", "card", "--review", input_text="task", env=env)
        r = self._run("run", "--once", env=env)
        self.assertIn("score=95", r.stdout)
        text = self._card_text("done")
        self.assertIn("review_source: card", text)

    def test_priority_env_overrides_project(self):
        (self.repo / ".kanban" / "KANBAN.md").write_text(
            (self.repo / ".kanban" / "KANBAN.md").read_text().replace(
                "review_enabled: true", "review_enabled: true"
            )
        )
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_FORBIDDEN)
        env["KANBAN_REVIEW_ENABLED"] = "false"
        self._run("add", "card", input_text="task", env=env)
        r = self._run("run", "--once", env=env)
        self.assertIn("review disabled", r.stdout)
        text = self._card_text("done")
        self.assertIn("review_source: env", text)

    # -- 5. invalid boolean fails clearly ------------------------------------

    def test_invalid_boolean_env_dies(self):
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_PASS)
        env["KANBAN_REVIEW_ENABLED"] = "maybe"
        r = self._run("add", "card", input_text="task", env=env, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid boolean", r.stderr)

    def test_invalid_boolean_project_dies(self):
        (self.repo / ".kanban" / "KANBAN.md").write_text(
            (self.repo / ".kanban" / "KANBAN.md").read_text().replace(
                "review_enabled: true", "review_enabled: sortof"
            )
        )
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        r = self._run("list", env=env, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid boolean", r.stderr)

    # -- 6. review OFF still fails on worker crash; merge conflict -> resolver

    def test_review_off_worker_failure_still_fails(self):
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_FAIL)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_FORBIDDEN)
        self._run("add", "card", "--no-review", input_text="task", env=env)
        r = self._run("run", "--once", env=env)
        self.assertIn("FAIL", r.stdout)
        text = self._card_text("failed")
        self.assertIn("worker exited with status 3", text)

    def test_review_off_merge_conflict_goes_to_resolver_no_re_review(self):
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script(
            "worker.sh",
            '#!/usr/bin/env bash\ncat >/dev/null\necho "line for ${KANBAN_CARD_TITLE:-?}" >> f.txt\ngit add -A\necho done\n',
        )
        env["KANBAN_RESOLVE_CMD"] = self._script("resolver.sh", RESOLVER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_FORBIDDEN)
        env["KANBAN_JOBS"] = "2"
        self._run("add", "card A", "--no-review", input_text="append A", env=env)
        self._run("add", "card B", "--no-review", input_text="append B", env=env)
        r = self._run("run", "-j", "2", env=env)
        self.assertNotIn("MUST NOT BE CALLED", r.stdout + r.stderr)
        done = sorted(glob.glob(str(self.repo / ".kanban" / "done" / "*.md")))
        self.assertEqual(len(done), 2)
        combined = "\n".join(Path(f).read_text() for f in done)
        self.assertIn("resolve review", combined)
        self.assertIn("review skipped: review_enabled=false", combined)

    # -- 7. review ON: resolver path re-reviews as before --------------------

    def test_review_on_merge_conflict_resolver_re_reviews(self):
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script(
            "worker.sh",
            '#!/usr/bin/env bash\ncat >/dev/null\necho "line for ${KANBAN_CARD_TITLE:-?}" >> f.txt\ngit add -A\necho done\n',
        )
        env["KANBAN_RESOLVE_CMD"] = self._script("resolver.sh", RESOLVER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_PASS)
        env["KANBAN_JOBS"] = "2"
        self._run("add", "card A", input_text="append A", env=env)
        self._run("add", "card B", input_text="append B", env=env)
        self._run("run", "-j", "2", env=env)
        done = sorted(glob.glob(str(self.repo / ".kanban" / "done" / "*.md")))
        self.assertEqual(len(done), 2)
        combined = "\n".join(Path(f).read_text() for f in done)
        self.assertIn("resolve review", combined)
        self.assertIn("score: 95", combined)

    # -- 8. restart/reclaim keeps the same decision ---------------------------

    def test_decision_survives_reclaim(self):
        # Project default starts as false; card resolves against it once...
        (self.repo / ".kanban" / "KANBAN.md").write_text(
            (self.repo / ".kanban" / "KANBAN.md").read_text().replace(
                "review_enabled: true", "review_enabled: false"
            )
        )
        env = dict(self.env)
        env["KANBAN_WORKER_CMD"] = self._script("worker.sh", WORKER_OK)
        env["KANBAN_REVIEW_CMD"] = self._script("reviewer.sh", REVIEWER_FORBIDDEN)
        self._run("add", "card", input_text="task", env=env)
        card = Path(sorted(glob.glob(str(self.repo / ".kanban" / "todo" / "*.md")))[0])
        # Simulate a card that already went through resolve_card_review on a
        # prior (crashed) dispatcher run: review_enabled/review_source are
        # already persisted as concrete values, sitting in doing/.
        text = card.read_text().replace(
            "review_enabled: auto\nreview_source: auto",
            "review_enabled: false\nreview_source: project",
        )
        self.assertIn("review_source: project", text)
        card_doing = self.repo / ".kanban" / "doing" / card.name
        card_doing.write_text(text)
        card.unlink()

        # Flip the project default to true. If resolution re-ran from
        # project/env state instead of the card's persisted decision, this
        # card would come back reviewed (and fail, since the reviewer is
        # forbidden here).
        (self.repo / ".kanban" / "KANBAN.md").write_text(
            (self.repo / ".kanban" / "KANBAN.md").read_text().replace(
                "review_enabled: false", "review_enabled: true"
            )
        )
        r = self._run("run", "--once", env=env)  # reclaims doing/ -> todo, then runs
        self.assertIn("review disabled", r.stdout)
        text = self._card_text("done")
        self.assertIn("review_source: project", text)


if __name__ == "__main__":
    unittest.main()
