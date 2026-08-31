"""MornKanban environment setup logic (tkinter非依存).

setup_gui.py から抽出した UI 非依存のロジック関数群。python3 標準ライブラリのみ使用。
"""
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
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


# --- logic functions (UI-independent) --------------------------------------

def check_deps():
    which = shutil.which
    return {
        "herdr": bool(which("herdr")),
        "claude": bool(which("claude")),
        "codex": bool(which("codex")),
    }


def cli_installed():
    return bool(shutil.which("kanban")) or os.path.exists(KANBAN_LINK)


def skill_installed():
    return os.path.isfile(SKILL_PATH)


def in_worktree():
    return "/.kanban/wt/" in REPO


def path_contains(directory):
    path_env = os.environ.get("PATH", "")
    parts = path_env.split(os.pathsep)
    directory = directory.rstrip(os.sep)
    return any(os.path.normpath(p) == os.path.normpath(directory) for p in parts if p)


def install_cli():
    if in_worktree():
        return False, "refusing to install from a kanban worktree; run from the real checkout"
    os.makedirs(LOCAL_BIN, exist_ok=True)
    if os.path.lexists(KANBAN_LINK):
        if not os.path.islink(KANBAN_LINK):
            return False, "%s exists and is not a symlink" % KANBAN_LINK
        os.remove(KANBAN_LINK)
    os.symlink(KANBAN_SH, KANBAN_LINK)
    msg = "installed: %s -> %s" % (KANBAN_LINK, KANBAN_SH)
    if not path_contains(LOCAL_BIN):
        msg += "\n警告: %s が PATH に含まれていません" % LOCAL_BIN
    return True, msg


def install_skill(force=False):
    if in_worktree():
        return False, "refusing to install from a kanban worktree; run from the real checkout"
    if os.path.isfile(SKILL_PATH) and not force:
        return False, "already installed"
    os.makedirs(SKILL_DIR, exist_ok=True)
    content = SKILL_TEMPLATE.format(repo=REPO)
    with open(SKILL_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True, "installed: %s" % SKILL_PATH


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


def project_entry(path):
    return {
        "path": path,
        "name": os.path.basename(path.rstrip(os.sep)) or path,
        "has_kanban": os.path.isdir(os.path.join(path, ".kanban")),
    }


def load_projects():
    cfg = load_config()
    return [project_entry(p) for p in cfg["projects"] if os.path.isdir(p)]


def add_project(path):
    if not isinstance(path, str) or not path.strip():
        return False, "path is required"
    norm = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.isdir(norm):
        return False, "not a directory: %s" % norm
    cfg = load_config()
    if norm not in cfg["projects"]:
        cfg["projects"].append(norm)
        save_config(cfg)
    return True, "added: %s" % norm


def init_project(path):
    if not os.path.isdir(path):
        return False, "not a directory: %s" % path
    try:
        proc = subprocess.run(
            ["bash", KANBAN_SH, "init", path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "command timed out"
    except OSError as exc:
        return False, "failed to run kanban.sh: %s" % exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, "command failed (%d): %s" % (proc.returncode, detail)
    return True, "initialized: %s" % path
