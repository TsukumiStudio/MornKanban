#!/usr/bin/env python3
"""MornKanban environment setup GUI backend.

Standard library only. Serves gui/static/ at / and a JSON API under /api/.
Binds to 127.0.0.1 only; port comes from MORNKANBAN_GUI_PORT (default 8765).
"""
import json
import mimetypes
import os
import posixpath
import shutil
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
KANBAN_SH = os.path.join(REPO, "kanban.sh")
CONFIG = os.path.expanduser("~/.config/mornkanban/gui.json")
LOCAL_BIN = os.path.expanduser("~/.local/bin")
KANBAN_LINK = os.path.join(LOCAL_BIN, "kanban")
SKILL_DIR = os.path.expanduser("~/.claude/skills/kanban-dispatch")
SKILL_PATH = os.path.join(SKILL_DIR, "SKILL.md")
TIMEOUT = 30

SKILL_TEMPLATE = """---
name: kanban-dispatch
description: "File-based kanban dispatch: card every implementation request and run the background dispatcher. Use when assigned implementation work in a project with .kanban/, or when asked to set up or operate kanban dispatch."
user_invocable: true
---
# kanban-dispatch
The kanban CLI and the full workflow contract live in {repo}.
**Read {repo}/README.md and follow it** (Secretary Bootstrap, Dialogue-Agent Contract, Model Policy, Herdr Integration).
"""


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


def cli_installed():
    return bool(shutil.which("kanban")) or os.path.exists(KANBAN_LINK)


def skill_installed():
    return os.path.isfile(SKILL_PATH)


def path_contains(directory):
    path_env = os.environ.get("PATH", "")
    parts = path_env.split(os.pathsep)
    directory = directory.rstrip(os.sep)
    return any(os.path.normpath(p) == os.path.normpath(directory) for p in parts if p)


# --- API handlers ----------------------------------------------------------

def api_status():
    which = shutil.which
    return {
        "deps": {
            "herdr": bool(which("herdr")),
            "claude": bool(which("claude")),
            "codex": bool(which("codex")),
            "python3": True,
        },
        "install": {
            "cli": cli_installed(),
            "skill": skill_installed(),
        },
        "repo": REPO,
        "herdr_env": herdr_env(),
    }


def guard_not_worktree():
    if "/.kanban/wt/" in REPO:
        raise ApiError(
            "refusing to install from a kanban worktree; run the GUI from the real checkout"
        )


def api_install_cli():
    guard_not_worktree()
    os.makedirs(LOCAL_BIN, exist_ok=True)
    if os.path.lexists(KANBAN_LINK):
        if not os.path.islink(KANBAN_LINK):
            raise ApiError("%s exists and is not a symlink" % KANBAN_LINK)
        os.remove(KANBAN_LINK)
    os.symlink(KANBAN_SH, KANBAN_LINK)
    return {"ok": True, "in_path": path_contains(LOCAL_BIN)}


def api_install_skill(body):
    guard_not_worktree()
    force = bool(body.get("force"))
    if os.path.isfile(SKILL_PATH) and not force:
        raise ApiError("already installed (force で上書き)", 409)
    os.makedirs(SKILL_DIR, exist_ok=True)
    content = SKILL_TEMPLATE.format(repo=REPO)
    with open(SKILL_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    return {"ok": True, "path": SKILL_PATH}


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
        elif method == "POST":
            body = self.read_body()
            if path == "/api/install/cli":
                return api_install_cli()
            if path == "/api/install/skill":
                return api_install_skill(body)
            if path == "/api/projects":
                return api_projects_post(body)
            if path == "/api/init":
                return api_init(body)
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
