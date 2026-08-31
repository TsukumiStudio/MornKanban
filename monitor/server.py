"""Read-only, localhost-only HTTP monitor server (python3 stdlib only).

GET/HEAD only. No endpoint mutates any board, process, or file — this is a
viewer, never a control plane for kanban.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from monitor import board, discovery

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.md$")
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Registry:
    """Cached, thread-safe view of discovered projects (realpath allowlist)."""

    def __init__(self, roots_provider, ttl=3.0):
        self._roots_provider = roots_provider
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache = {}
        self._ts = 0.0

    def get(self):
        with self._lock:
            now = time.time()
            if now - self._ts > self._ttl:
                try:
                    roots = self._roots_provider()
                    self._cache = discovery.build_registry(roots)
                except OSError:
                    pass
                self._ts = now
            return self._cache

    def project(self, slug):
        return self.get().get(slug)


def _project_summary(slug, info):
    try:
        counts = board.project_counts(info["kanban_dir"])
        dispatcher = board.dispatcher_status(info["kanban_dir"])
        activity = board.last_activity(info["kanban_dir"], limit=1)
        last = activity[0]["mtime"] if activity else None
        return {
            "slug": slug,
            "name": info["name"],
            "root": info["root"],
            "counts": counts,
            "dispatcher": dispatcher,
            "last_activity": last,
        }
    except OSError as e:
        return {"slug": slug, "name": info.get("name", slug), "root": info.get("root", ""), "error": str(e)}


def _project_activity(slug, info, limit):
    try:
        items = board.last_activity(info["kanban_dir"], limit=limit)
    except OSError:
        return []
    out = []
    for it in items:
        out.append(dict(it, project=slug, project_name=info["name"]))
    return out


class MonitorHandler(BaseHTTPRequestHandler):
    server_version = "MornKanbanMonitor/1"
    protocol_version = "HTTP/1.1"

    # --- disallow every write method: this server is read-only -------------
    def _reject_write(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        self._reject_write()

    def do_PUT(self):
        self._reject_write()

    def do_PATCH(self):
        self._reject_write()

    def do_DELETE(self):
        self._reject_write()

    # --- read paths ----------------------------------------------------------
    def do_HEAD(self):
        self._handle(body=False)

    def do_GET(self):
        self._handle(body=True)

    def _handle(self, body):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/" or path == "":
                return self._serve_static("index.html", body)
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):], body)
            if path == "/api/projects":
                return self._json(self._api_projects(), body)
            if path == "/api/activity":
                return self._json(self._api_activity(parsed), body)
            m = re.match(r"^/api/projects/([^/]+)$", path)
            if m:
                return self._json(self._api_project_detail(m.group(1)), body)
            m = re.match(r"^/api/projects/([^/]+)/cards/([^/]+)/([^/]+)$", path)
            if m:
                return self._json(self._api_card(m.group(1), m.group(2), m.group(3)), body)
            self._error(404, "not found")
        except _ApiError as e:
            self._error(e.status, e.message)
        except (OSError, ValueError):
            self._error(500, "internal error")

    # --- API handlers ----------------------------------------------------------
    def _api_projects(self):
        registry = self.server.registry.get()
        return {
            "generated_at": time.time(),
            "projects": [_project_summary(slug, info) for slug, info in sorted(registry.items())],
        }

    def _api_activity(self, parsed):
        registry = self.server.registry.get()
        limit = 50
        items = []
        for slug, info in registry.items():
            items.extend(_project_activity(slug, info, limit))
        items.sort(key=lambda it: it["mtime"], reverse=True)
        return {"generated_at": time.time(), "activity": items[:limit]}

    def _api_project_detail(self, slug):
        info = self.server.registry.project(slug)
        if info is None:
            raise _ApiError(404, "unknown project: %s" % slug)
        detail = board.board_detail(info["kanban_dir"])
        detail["slug"] = slug
        detail["name"] = info["name"]
        detail["root"] = info["root"]
        detail["generated_at"] = time.time()
        return detail

    def _api_card(self, slug, state, filename):
        info = self.server.registry.project(slug)
        if info is None:
            raise _ApiError(404, "unknown project: %s" % slug)
        if state not in board.STATES:
            raise _ApiError(404, "unknown state: %s" % state)
        if not _FILENAME_RE.match(filename):
            raise _ApiError(400, "invalid card filename")
        state_dir = os.path.realpath(os.path.join(info["kanban_dir"], state))
        path = os.path.realpath(os.path.join(state_dir, filename))
        if os.path.dirname(path) != state_dir or not os.path.isfile(path):
            raise _ApiError(404, "card not found")
        fm, card_body = board.read_card(path)
        return {"slug": slug, "state": state, "filename": filename, "frontmatter": fm, "body": card_body}

    # --- static files ----------------------------------------------------------
    def _serve_static(self, rel, body):
        rel = rel.lstrip("/") or "index.html"
        target = os.path.realpath(os.path.join(STATIC_DIR, rel))
        static_root = os.path.realpath(STATIC_DIR)
        if target != static_root and not target.startswith(static_root + os.sep):
            return self._error(404, "not found")
        if not os.path.isfile(target):
            return self._error(404, "not found")
        ext = os.path.splitext(target)[1]
        mime = _MIME.get(ext, "application/octet-stream")
        with open(target, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def _json(self, payload, body):
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def _error(self, status, message):
        data = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # keep stdout quiet; rely on LaunchAgent log redirection when daemonized


class _ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host, port, roots_provider):
        super().__init__((host, port), MonitorHandler)
        self.registry = Registry(roots_provider)


def make_server(host=DEFAULT_HOST, port=DEFAULT_PORT, extra_roots=None):
    def roots_provider():
        return discovery.resolve_roots(extra_roots)

    return MonitorServer(host, port, roots_provider)


def run(host=DEFAULT_HOST, port=DEFAULT_PORT, extra_roots=None):
    httpd = make_server(host, port, extra_roots)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print("kanban monitor: WARNING binding to %s (not loopback) exposes this read-only viewer to the network" % host)
    print("kanban monitor: serving on http://%s:%d (read-only, GET/HEAD only)" % httpd.server_address[:2])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
