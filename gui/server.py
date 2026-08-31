#!/usr/bin/env python3
"""MornKanban local web GUI backend.

Standard library only. Serves gui/static/ at / and a JSON API under /api/.
Binds to 127.0.0.1 only; port comes from MORNKANBAN_GUI_PORT (default 8765).
"""
import json
import mimetypes
import os
import posixpath
import random
import shutil
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
KANBAN_SH = os.path.join(REPO, "kanban.sh")
WORKER_SH = os.path.join(REPO, "herdr-agent-worker.sh")
CONFIG = os.path.expanduser("~/.config/mornkanban/gui.json")
STATES = ("todo", "doing", "review", "done", "failed")
TIMEOUT = 30


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = str(message)
        self.status = status


# --- helpers ---------------------------------------------------------------

def herdr_env():
    return os.environ.get("HERDR_ENV") == "1"


def run(args, cwd=None, stdin=None):
    """Run an external command; raise ApiError with stderr on failure."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ApiError("command timed out: %s" % " ".join(args), 500)
    except OSError as exc:
        raise ApiError("failed to run %s: %s" % (args[0], exc), 500)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ApiError("command failed (%d): %s" % (proc.returncode, detail), 500)
    return proc.stdout


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"projects": []}
    projects = data.get("projects")
    if not isinstance(projects, list):
        projects = []
    return {"projects": [p for p in projects if isinstance(p, str)]}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def norm_path(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError("path is required")
    return os.path.abspath(os.path.expanduser(raw.strip()))


def project_entry(path):
    return {
        "path": path,
        "name": os.path.basename(path.rstrip(os.sep)) or path,
        "has_kanban": os.path.isdir(os.path.join(path, ".kanban")),
    }


def read_frontmatter(path):
    """Read id/title/attempts from the leading --- ... --- block."""
    fm = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().rstrip("\n")
            if first.strip() != "---":
                return fm
            for line in fh:
                if line.strip() == "---":
                    break
                for key in ("id", "title", "attempts"):
                    prefix = key + ": "
                    if line.startswith(prefix):
                        fm[key] = line[len(prefix):].strip()
    except OSError:
        pass
    return fm


def board_for(project):
    states = {}
    for state in STATES:
        cards = []
        directory = os.path.join(project, ".kanban", state)
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        files = []
        for name in names:
            if not name.endswith(".md"):
                continue
            full = os.path.join(directory, name)
            if not os.path.isfile(full):
                continue
            try:
                files.append((os.path.getmtime(full), full))
            except OSError:
                continue
        for _, full in sorted(files, key=lambda item: item[0]):
            fm = read_frontmatter(full)
            cards.append({
                "id": fm.get("id", ""),
                "title": fm.get("title", os.path.basename(full)),
                "attempts": fm.get("attempts", "0"),
                "file": full,
            })
        states[state] = cards
    return states


def pane_id_from(stdout):
    try:
        data = json.loads(stdout)
    except ValueError:
        raise ApiError("herdr returned non-JSON output: %s" % stdout.strip()[:200], 500)
    try:
        pane_id = data["result"]["pane"]["pane_id"]
    except (KeyError, TypeError):
        raise ApiError("herdr response missing result.pane.pane_id", 500)
    if not pane_id:
        raise ApiError("herdr returned an empty pane_id", 500)
    return pane_id


def split_pane(direction, cwd):
    out = run([
        "herdr", "pane", "split", "--current",
        "--direction", direction, "--cwd", cwd, "--no-focus",
    ])
    return pane_id_from(out)


# --- API handlers ----------------------------------------------------------

def api_status():
    which = shutil.which
    return {
        "deps": {
            "herdr": bool(which("herdr")),
            "claude": bool(which("claude")),
            "codex": bool(which("codex")),
            "kanban": bool(which("kanban")) or os.path.exists(KANBAN_SH),
        },
        "herdr_env": herdr_env(),
        "repo": REPO,
    }


def api_projects_get():
    cfg = load_config()
    return {"projects": [project_entry(p) for p in cfg["projects"] if os.path.isdir(p)]}


def api_projects_post(body):
    path = norm_path(body.get("path"))
    if not os.path.isdir(path):
        raise ApiError("not a directory: %s" % path)
    cfg = load_config()
    if path not in cfg["projects"]:
        cfg["projects"].append(path)
        save_config(cfg)
    return {"ok": True, "project": project_entry(path)}


def api_init(body):
    path = norm_path(body.get("path"))
    if not os.path.isdir(path):
        raise ApiError("not a directory: %s" % path)
    run(["bash", KANBAN_SH, "init", path])
    return {"ok": True}


def policy_path(project):
    return os.path.join(project, ".kanban", "KANBAN.md")


def api_policy_get(query):
    project = norm_path(query.get("path", [None])[0])
    target = policy_path(project)
    if not os.path.isfile(target):
        raise ApiError("policy not found: %s" % target, 404)
    with open(target, encoding="utf-8", errors="replace") as fh:
        return {"content": fh.read()}


def api_policy_put(body):
    project = norm_path(body.get("path"))
    content = body.get("content")
    if not isinstance(content, str):
        raise ApiError("content is required")
    target = policy_path(project)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(content)
    return {"ok": True}


def api_board(query):
    project = norm_path(query.get("path", [None])[0])
    return {"states": board_for(project)}


def api_card_get(query):
    raw = query.get("file", [None])[0]
    if not raw:
        raise ApiError("file is required")
    target = os.path.realpath(raw)
    allowed = False
    for project in load_config()["projects"]:
        root = os.path.realpath(os.path.join(project, ".kanban")) + os.sep
        if target.startswith(root):
            allowed = True
            break
    if not allowed:
        raise ApiError("file is outside registered projects", 403)
    if not os.path.isfile(target):
        raise ApiError("card not found: %s" % target, 404)
    with open(target, encoding="utf-8", errors="replace") as fh:
        return {"content": fh.read()}


def api_card_post(body):
    project = norm_path(body.get("path"))
    title = body.get("title") or ""
    if not isinstance(title, str) or not title.strip():
        raise ApiError("title is required")
    if not os.path.isdir(project):
        raise ApiError("not a directory: %s" % project)
    args = ["bash", KANBAN_SH, "add", title]
    backend = body.get("backend")
    model = body.get("model")
    threshold = body.get("threshold")
    if backend:
        args += ["-b", str(backend)]
    if model:
        args += ["-m", str(model)]
    if threshold not in (None, ""):
        args += ["-t", str(threshold)]
    out = run(args, cwd=project, stdin=body.get("body") or "")
    return {"ok": True, "file": out.strip()}


def api_secretary(body):
    if not herdr_env():
        raise ApiError("not running inside a herdr session (HERDR_ENV != 1)")
    project = norm_path(body.get("path"))
    pane = split_pane("right", project)
    name = "kanban-sec-%04x" % random.randrange(0x10000)
    try:
        run([
            "herdr", "agent", "start", name,
            "--kind", "claude", "--pane", pane, "--timeout", "60000",
        ])
    except ApiError:
        try:
            run(["herdr", "agent", "wait", name, "--timeout", "30000"])
        except ApiError:
            pass
    run(["herdr", "agent", "prompt", name, "kanban の秘書として待機して"])
    return {"ok": True, "pane": pane, "name": name}


def api_dispatch(body):
    if not herdr_env():
        raise ApiError("not running inside a herdr session (HERDR_ENV != 1)")
    project = norm_path(body.get("path"))
    jobs = body.get("jobs")
    if jobs in (None, ""):
        jobs = 2
    pane = split_pane("down", project)
    cmd = (
        "KANBAN_WORKER_CMD=%s "
        "KANBAN_REVIEW_CMD='env KANBAN_HERDR_ROLE=reviewer %s' "
        "bash %s run -j %s; exit" % (WORKER_SH, WORKER_SH, KANBAN_SH, jobs)
    )
    run(["herdr", "pane", "run", pane, cmd])
    return {"ok": True, "pane": pane}


# --- HTTP ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "MornKanbanGUI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[gui] %s %s\n" % (self.address_string(), fmt % args))

    # -- plumbing --
    def send_json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_error_json(self, message, status):
        self.send_json({"ok": False, "error": str(message)}, status)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("request body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError("request body must be a JSON object")
        return data

    def dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self.send_json(self.route_api(method, path, query))
            elif method == "GET":
                self.serve_static(path)
            else:
                raise ApiError("not found: %s" % path, 404)
        except ApiError as exc:
            self.send_error_json(exc.message, exc.status)
        except Exception as exc:  # noqa: BLE001 - API always answers with JSON
            self.send_error_json("internal error: %s" % exc, 500)

    def route_api(self, method, path, query):
        if method == "GET":
            if path == "/api/status":
                return api_status()
            if path == "/api/projects":
                return api_projects_get()
            if path == "/api/policy":
                return api_policy_get(query)
            if path == "/api/board":
                return api_board(query)
            if path == "/api/card":
                return api_card_get(query)
        elif method == "POST":
            body = self.read_body()
            if path == "/api/projects":
                return api_projects_post(body)
            if path == "/api/init":
                return api_init(body)
            if path == "/api/card":
                return api_card_post(body)
            if path == "/api/secretary":
                return api_secretary(body)
            if path == "/api/dispatch":
                return api_dispatch(body)
        elif method == "PUT":
            body = self.read_body()
            if path == "/api/policy":
                return api_policy_put(body)
        raise ApiError("no such endpoint: %s %s" % (method, path), 404)

    def serve_static(self, path):
        rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        target = os.path.normpath(os.path.join(STATIC, rel))
        if not (target == STATIC or target.startswith(STATIC + os.sep)):
            raise ApiError("forbidden", 403)
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            raise ApiError("not found: %s" % path, 404)
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            raw = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")


def main():
    try:
        port = int(os.environ.get("MORNKANBAN_GUI_PORT") or 8765)
    except ValueError:
        print("MORNKANBAN_GUI_PORT must be an integer", file=sys.stderr)
        return 1
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print("MornKanban GUI: http://127.0.0.1:%d/ (repo: %s)" % (port, REPO), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
