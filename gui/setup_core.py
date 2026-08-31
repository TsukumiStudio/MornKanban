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
SKILL_SOURCE_DIR = os.path.join(REPO, "skills", "kanban-dispatch")
SKILL_TARGETS = {
    "Claude Code": os.path.expanduser("~/.claude/skills/kanban-dispatch"),
    "Codex": os.path.expanduser("~/.codex/skills/kanban-dispatch"),
}
TIMEOUT = 30


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


def skill_status():
    return {
        name: os.path.isfile(os.path.join(directory, "SKILL.md"))
        for name, directory in SKILL_TARGETS.items()
    }


def skill_installed():
    return all(skill_status().values())


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


def _render_skill(source_path):
    with open(source_path, "r", encoding="utf-8") as fh:
        return fh.read().replace("__MORNKANBAN_REPO__", REPO)


def install_skills(force=False):
    if in_worktree():
        return ["refusing to install from a kanban worktree; run from the real checkout"]

    messages = []
    source_skill = os.path.join(SKILL_SOURCE_DIR, "SKILL.md")
    source_openai = os.path.join(SKILL_SOURCE_DIR, "agents", "openai.yaml")
    for name, directory in SKILL_TARGETS.items():
        skill_path = os.path.join(directory, "SKILL.md")
        if os.path.isfile(skill_path) and not force:
            messages.append("%s skill: already installed" % name)
            continue
        os.makedirs(os.path.join(directory, "agents"), exist_ok=True)
        with open(skill_path, "w", encoding="utf-8") as fh:
            fh.write(_render_skill(source_skill))
        shutil.copy2(source_openai, os.path.join(directory, "agents", "openai.yaml"))
        messages.append("installed %s skill: %s" % (name, skill_path))
    return messages


def run_setup():
    if in_worktree():
        return ["refused: kanban worktree 内"]
    _, cli_msg = install_cli()
    return [cli_msg] + install_skills(force=True)


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


def _uninstall_skill(name, directory):
    try:
        skill_path = os.path.join(directory, "SKILL.md")
        if not os.path.isfile(skill_path):
            return "%s スキル: 未導入" % name
        with open(skill_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        if "MornKanban secretary" in content:
            shutil.rmtree(directory)
            return "%s スキル: 削除しました" % name
        return "%s スキル: 別管理のため残しました" % name
    except Exception as e:
        return "%s スキル: 確認/削除に失敗しました (%s)" % (name, e)


def run_uninstall():
    if in_worktree():
        return ["refused: kanban worktree 内"]
    return [_uninstall_cli()] + [
        _uninstall_skill(name, directory)
        for name, directory in SKILL_TARGETS.items()
    ]
