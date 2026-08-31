"""Tests for the read-only multi-project kanban monitor (monitor/*.py).

Uses temporary HOME/config directories throughout so nothing touches the
real user's filesystem, and real subprocess.Popen HTTP integration tests so
the actual dispatch/serialization code paths run end to end.
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from monitor import board, discovery, launchagent, server  # noqa: E402


def write_card(kanban_dir, state, filename, frontmatter, body="## Task\n\nbody\n\n## History\n"):
    d = os.path.join(kanban_dir, state)
    os.makedirs(d, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append("%s: %s" % (k, v))
    lines.append("---")
    lines.append("")
    lines.append(body)
    path = os.path.join(d, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def make_project(root, name, states=("todo", "doing", "review", "done", "failed")):
    proj_root = os.path.join(root, name)
    kanban_dir = os.path.join(proj_root, ".kanban")
    for s in states:
        os.makedirs(os.path.join(kanban_dir, s), exist_ok=True)
    return proj_root, kanban_dir


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbmon-disc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.git_root = os.path.join(self.tmp, "git")
        os.makedirs(self.git_root)

    def test_finds_project_and_counts_cards(self):
        _, kb = make_project(self.git_root, "alpha")
        write_card(kb, "todo", "a.md", {"id": "1", "title": "t1"})
        write_card(kb, "done", "b.md", {"id": "2", "title": "t2"})

        registry = discovery.build_registry([self.git_root])
        self.assertIn("alpha", registry)
        counts = board.project_counts(registry["alpha"]["kanban_dir"])
        self.assertEqual(counts["todo"], 1)
        self.assertEqual(counts["done"], 1)
        self.assertEqual(counts["doing"], 0)

    def test_excludes_kanban_wt_worktrees_as_separate_projects(self):
        _, kb = make_project(self.git_root, "beta")
        write_card(kb, "todo", "a.md", {"id": "1", "title": "t"})
        # a worktree checkout carries its own nested .kanban directory
        wt_root = os.path.join(kb, "wt", "20260101-000000-1")
        wt_kb = os.path.join(wt_root, ".kanban")
        for s in ("todo", "doing", "review", "done", "failed"):
            os.makedirs(os.path.join(wt_kb, s), exist_ok=True)
        write_card(wt_kb, "todo", "nested.md", {"id": "9", "title": "nested"})

        registry = discovery.build_registry([self.git_root])
        self.assertEqual(list(registry.keys()), ["beta"])
        self.assertNotIn("20260101-000000-1", registry)

    def test_symlink_loop_and_dedup(self):
        _, kb = make_project(self.git_root, "gamma")
        write_card(kb, "todo", "a.md", {"id": "1", "title": "t"})
        loop_link = os.path.join(self.git_root, "gamma", "loop-to-root")
        try:
            os.symlink(self.git_root, loop_link)
        except OSError:
            self.skipTest("symlink not supported in this environment")

        registry = discovery.build_registry([self.git_root])
        # must terminate and must not register the same project twice
        self.assertEqual(list(registry.keys()), ["gamma"])

    def test_duplicate_basenames_get_unique_slugs(self):
        make_project(os.path.join(self.git_root, "ns1"), "dup")
        make_project(os.path.join(self.git_root, "ns2"), "dup")
        registry = discovery.build_registry([self.git_root])
        self.assertEqual(sorted(registry.keys()), ["dup", "dup-2"])

    def test_build_registry_uses_send_alias_when_project_is_registered(self):
        # `kanban projects add` and the roots-scan must agree on a name: a
        # project registered under an explicit alias always keeps that alias
        # as its monitor slug, even though its basename would slugify to
        # something else.
        proj_root, _ = make_project(os.path.join(self.git_root, "some-repo"), "renamed-in-registry")
        cfg_dir = os.path.join(self.tmp, "cfgdir")
        os.environ["KANBAN_MONITOR_CONFIG_DIR"] = cfg_dir
        try:
            from registry import store as project_registry
            project_registry.add("project-a", proj_root)
            registry = discovery.build_registry([self.git_root])
            self.assertIn("project-a", registry)
            self.assertNotIn("some-repo", registry)
            self.assertEqual(registry["project-a"]["root"], os.path.realpath(proj_root))
        finally:
            del os.environ["KANBAN_MONITOR_CONFIG_DIR"]

    def test_build_registry_includes_registered_project_outside_roots(self):
        # A `kanban projects add`-registered project need not live under any
        # scanned root at all; it must still show up under its alias.
        outside = os.path.join(self.tmp, "outside")
        proj_root, _ = make_project(outside, "elsewhere")
        cfg_dir = os.path.join(self.tmp, "cfgdir")
        os.environ["KANBAN_MONITOR_CONFIG_DIR"] = cfg_dir
        try:
            from registry import store as project_registry
            project_registry.add("project-b", proj_root)
            registry = discovery.build_registry([self.git_root])
            self.assertIn("project-b", registry)
            self.assertEqual(registry["project-b"]["root"], os.path.realpath(proj_root))
        finally:
            del os.environ["KANBAN_MONITOR_CONFIG_DIR"]

    def test_config_roots_saved_under_overridden_home(self):
        cfg_dir = os.path.join(self.tmp, "cfgdir")
        os.environ["KANBAN_MONITOR_CONFIG_DIR"] = cfg_dir
        try:
            self.assertEqual(discovery.load_config(), {"roots": []})
            discovery.add_root("/some/root")
            cfg = discovery.load_config()
            self.assertEqual(cfg["roots"], ["/some/root"])
            self.assertTrue(os.path.isfile(discovery.config_path()))
            self.assertTrue(discovery.config_path().startswith(cfg_dir))
            discovery.remove_root("/some/root")
            self.assertEqual(discovery.load_config()["roots"], [])
        finally:
            del os.environ["KANBAN_MONITOR_CONFIG_DIR"]

    def test_resolve_roots_env_override_wins(self):
        extra = os.path.join(self.tmp, "extra")
        os.makedirs(extra)
        os.environ["KANBAN_MONITOR_ROOTS"] = extra
        try:
            roots = discovery.resolve_roots()
            self.assertEqual(roots, [os.path.realpath(extra)])
        finally:
            del os.environ["KANBAN_MONITOR_ROOTS"]


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbmon-board-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _, self.kb = make_project(self.tmp, "proj")

    def test_parse_card_frontmatter_and_body(self):
        text = "---\nid: 1\ntitle: hello\n---\n\n## Task\n\nbody text\n"
        fm, body = board.parse_card(text)
        self.assertEqual(fm["id"], "1")
        self.assertEqual(fm["title"], "hello")
        self.assertIn("body text", body)

    def test_dispatcher_status_live_pid(self):
        proc = subprocess.Popen(["sleep", "5"])
        try:
            with open(os.path.join(self.kb, ".lock"), "w") as fh:
                fh.write(str(proc.pid))
            status = board.dispatcher_status(self.kb)
            self.assertTrue(status["running"])
            self.assertFalse(status["stale"])
        finally:
            proc.terminate()
            proc.wait()

    def test_dispatcher_status_stale_pid_not_running(self):
        proc = subprocess.Popen(["sleep", "0.1"])
        proc.wait()
        dead_pid = proc.pid
        with open(os.path.join(self.kb, ".lock"), "w") as fh:
            fh.write(str(dead_pid))
        status = board.dispatcher_status(self.kb)
        self.assertFalse(status["running"])
        self.assertTrue(status["stale"])

    def test_dispatcher_status_no_lock(self):
        status = board.dispatcher_status(self.kb)
        self.assertFalse(status["running"])
        self.assertFalse(status["stale"])

    def test_last_activity_sorted_desc(self):
        p1 = write_card(self.kb, "todo", "a.md", {"id": "1", "title": "old"})
        time.sleep(0.02)
        p2 = write_card(self.kb, "done", "b.md", {"id": "2", "title": "new"})
        os.utime(p1, (time.time() - 100, time.time() - 100))
        os.utime(p2, None)
        items = board.last_activity(self.kb)
        self.assertEqual(items[0]["filename"], "b.md")

    def test_agent_activity_reads_recent_jsonl_and_ignores_malformed_rows(self):
        path = os.path.join(self.kb, "activity.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"timestamp":1,"event":"agent_started","card_id":"c1"}\n')
            fh.write('not json\n')
            fh.write('{"timestamp":2,"event":"answer_accepted","card_id":"c1"}\n')
        events = board.agent_activity(self.kb)
        self.assertEqual([event["event"] for event in events], ["answer_accepted", "agent_started"])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbmon-srv-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _, self.kb = make_project(self.tmp, "proj")
        write_card(self.kb, "todo", "a.md", {"id": "1", "title": "サンプル <b>x</b>"})

        # Without this, make_server()'s registry falls back to
        # discovery.DEFAULT_ROOTS ("~/git") whenever no env/config override is
        # set, so every request walks the real user's ~/git tree (seconds per
        # test on a populated checkout) instead of just this tmp fixture.
        os.environ["KANBAN_MONITOR_ROOTS"] = self.tmp
        self.addCleanup(os.environ.pop, "KANBAN_MONITOR_ROOTS", None)

        self.httpd = server.make_server(host="127.0.0.1", port=0, extra_roots=[self.tmp])
        self.host, self.port = self.httpd.server_address[:2]
        # serve_forever()'s default poll_interval is 0.5s, and shutdown()
        # blocks for up to one interval waiting for the loop to notice --
        # a short interval keeps each test's teardown near-instant instead of
        # paying a fixed ~0.5s per test regardless of what it does.
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _get(self, path):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()

    def _request(self, method, path):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            resp.read()
            return resp.status, dict(resp.getheaders())
        finally:
            conn.close()

    def test_binds_loopback_by_default(self):
        self.assertEqual(self.host, "127.0.0.1")

    def test_projects_listing(self):
        status, data = self._get("/api/projects")
        self.assertEqual(status, 200)
        payload = json.loads(data)
        slugs = [p["slug"] for p in payload["projects"]]
        self.assertIn("proj", slugs)
        proj = next(p for p in payload["projects"] if p["slug"] == "proj")
        self.assertEqual(proj["counts"]["todo"], 1)

    def test_project_detail_and_card(self):
        with open(os.path.join(self.kb, "activity.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"timestamp":1,"event":"agent_started","card_id":"c1","role":"worker"}\n')
        status, data = self._get("/api/projects/proj")
        self.assertEqual(status, 200)
        detail = json.loads(data)
        self.assertEqual(detail["counts"]["todo"], 1)
        self.assertEqual(detail["agent_activity"][0]["card_id"], "c1")

        status, data = self._get("/api/projects/proj/cards/todo/a.md")
        self.assertEqual(status, 200)
        card = json.loads(data)
        self.assertEqual(card["frontmatter"]["title"], "サンプル <b>x</b>")
        # response is JSON, not HTML -- the raw string travels unescaped in
        # the JSON payload (the client renders it via textContent, never
        # innerHTML), so this must NOT come back as &lt;b&gt;.
        self.assertIn("<b>x</b>", card["frontmatter"]["title"])

    def test_activity_endpoint(self):
        status, data = self._get("/api/activity")
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertTrue(any(it["project"] == "proj" for it in payload["activity"]))

    def test_unknown_project_404(self):
        status, _ = self._get("/api/projects/does-not-exist")
        self.assertEqual(status, 404)

    def test_path_traversal_rejected(self):
        status, _ = self._get("/api/projects/proj/cards/todo/..%2f..%2fetc%2fpasswd")
        self.assertIn(status, (400, 404))

    def test_bad_state_rejected(self):
        status, _ = self._get("/api/projects/proj/cards/not-a-state/a.md")
        self.assertEqual(status, 404)

    def test_static_index_served(self):
        status, data = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"MornKanban Monitor", data)

    def test_static_traversal_rejected(self):
        status, _ = self._get("/static/..%2f..%2fserver.py")
        self.assertEqual(status, 404)

    def test_write_methods_rejected_with_405(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, headers = self._request(method, "/api/projects")
            self.assertEqual(status, 405, method)
            self.assertIn("Allow", headers)


class LaunchAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbmon-launchd-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def test_install_writes_plist_under_fake_home_only(self):
        decoy_dir = os.path.join(self.tmp, "Library", "LaunchAgents")
        os.makedirs(decoy_dir, exist_ok=True)
        decoy = os.path.join(decoy_dir, "com.example.other.plist")
        with open(decoy, "w") as fh:
            fh.write("keep me")

        ok, msg = launchagent.install(host="127.0.0.1", port=18900, roots=["/tmp/x"])
        self.assertTrue(ok)
        plist_path = launchagent.plist_path()
        self.assertTrue(plist_path.startswith(self.tmp))
        self.assertTrue(os.path.isfile(plist_path))

        import plistlib
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
        self.assertEqual(data["Label"], launchagent.LABEL)
        self.assertIn("--port", data["ProgramArguments"])
        self.assertIn("18900", data["ProgramArguments"])

        # decoy (unrelated) LaunchAgent must be untouched
        with open(decoy) as fh:
            self.assertEqual(fh.read(), "keep me")

    def test_start_stop_status_use_injected_runner(self):
        calls = []

        class FakeResult:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "print":
                return FakeResult(0, stdout="state = running\n")
            return FakeResult(0)

        launchagent.install(host="127.0.0.1", port=18901, roots=[])
        ok, _ = launchagent.start(runner=fake_runner)
        self.assertTrue(ok)
        self.assertIn("bootstrap", calls[0])

        st = launchagent.status(runner=fake_runner)
        self.assertTrue(st["installed"])
        self.assertTrue(st["running"])

        ok, _ = launchagent.stop(runner=fake_runner)
        self.assertTrue(ok)

        ok, messages = launchagent.uninstall(runner=fake_runner)
        self.assertTrue(ok)
        self.assertFalse(os.path.isfile(launchagent.plist_path()))

    def test_uninstall_when_never_installed_is_idempotent(self):
        ok, messages = launchagent.uninstall(runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run launchctl")))
        self.assertTrue(ok)
        self.assertIn("plist not present", messages)


class KanbanShDispatchTest(unittest.TestCase):
    def test_monitor_subcommand_listed_in_usage(self):
        result = subprocess.run(["bash", os.path.join(REPO, "kanban.sh"), "bogus-command"],
                                 capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("monitor", result.stderr)

    def test_monitor_config_roundtrip_through_kanban_sh(self):
        tmp = tempfile.mkdtemp(prefix="kbmon-cli-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, HOME=tmp, KANBAN_MONITOR_CONFIG_DIR=os.path.join(tmp, "cfg"))
        r = subprocess.run(["bash", os.path.join(REPO, "kanban.sh"), "monitor", "config", "add-root", "/tmp/somewhere"],
                            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/tmp/somewhere", r.stdout)

        r2 = subprocess.run(["bash", os.path.join(REPO, "kanban.sh"), "monitor", "config", "list-roots"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("/tmp/somewhere", r2.stdout)


if __name__ == "__main__":
    unittest.main()
