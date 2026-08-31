"""MornKanban environment setup logic (tkinter非依存).

setup_gui.py から抽出した UI 非依存のロジック関数群。python3 標準ライブラリのみ使用。
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
KANBAN_SH = os.path.join(REPO, "kanban.sh")
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


def run_setup():
    if in_worktree():
        return ["refused: kanban worktree 内"]
    _, cli_msg = install_cli()
    _, skill_msg = install_skill(force=True)
    return [cli_msg, skill_msg]


def _uninstall_cli():
    try:
        if not os.path.lexists(KANBAN_LINK):
            return "CLI: 未導入"
        if os.path.islink(KANBAN_LINK):
            target = os.path.realpath(KANBAN_LINK)
            repo_real = os.path.realpath(REPO)
            if target == repo_real or (target + os.sep).startswith(repo_real + os.sep):
                os.remove(KANBAN_LINK)
                return "CLI: 削除しました"
        return "CLI: このインストーラの管理物ではないため残しました"
    except Exception as e:
        return "CLI: 確認/削除に失敗しました (%s)" % e


def _uninstall_skill():
    try:
        if not os.path.isfile(SKILL_PATH):
            return "スキル: 未導入"
        with open(SKILL_PATH, "r", encoding="utf-8") as fh:
            content = fh.read()
        if "MornKanban" in content:
            shutil.rmtree(SKILL_DIR)
            return "スキル: 削除しました"
        return "スキル: 別管理のスキルのため残しました"
    except Exception as e:
        return "スキル: 確認/削除に失敗しました (%s)" % e


def run_uninstall():
    if in_worktree():
        return ["refused: kanban worktree 内"]
    return [_uninstall_cli(), _uninstall_skill()]
