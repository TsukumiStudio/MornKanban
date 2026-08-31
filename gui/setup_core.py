"""MornKanban environment setup logic (tkinter非依存).

setup_gui.py から抽出した UI 非依存のロジック関数群。python3 標準ライブラリのみ使用。
"""
import importlib
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
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

# raw VERSION on GitHub main: treated as "latest published version" because
# this repository currently has no tags or GitHub Releases.
DEFAULT_VERSION_URL = "https://raw.githubusercontent.com/TsukumiStudio/MornKanban/main/VERSION"


# --- version -----------------------------------------------------------------

def local_version():
    with open(os.path.join(REPO, "VERSION"), "r", encoding="utf-8") as fh:
        return fh.read().strip()


def version_source_url():
    # KANBAN_VERSION_URL override lets tests point at a file:// URL instead
    # of the network.
    return os.environ.get("KANBAN_VERSION_URL", DEFAULT_VERSION_URL)


def fetch_latest_version(url=None, timeout=TIMEOUT):
    url = url or version_source_url()
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8").strip()


def parse_version(v):
    parts = v.strip().split(".")
    if len(parts) != 3:
        raise ValueError("invalid semantic version: %r" % v)
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise ValueError("invalid semantic version: %r" % v)


def compare_versions(a, b):
    """-1 if a<b, 0 if a==b, 1 if a>b (semantic, X.Y.Z)."""
    ta, tb = parse_version(a), parse_version(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def version_report():
    current = local_version()
    report = {"current": current, "latest": None, "state": "unknown", "error": None}
    try:
        latest = fetch_latest_version()
    except (urllib.error.URLError, OSError, ValueError) as e:
        report["error"] = str(e)
        return report
    report["latest"] = latest
    try:
        cmp = compare_versions(current, latest)
    except ValueError as e:
        report["error"] = str(e)
        return report
    if cmp == 0:
        report["state"] = "up-to-date"
    elif cmp < 0:
        report["state"] = "update-available"
    else:
        report["state"] = "local-ahead"
    return report


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
        content = fh.read()
    content = content.replace("__MORNKANBAN_REPO__", REPO)
    content = content.replace("__MORNKANBAN_VERSION__", local_version())
    return content


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


# --- update (git pull --ff-only + reinstall) ---------------------------------

def _git(args):
    return subprocess.run(
        ["git", "-C", REPO] + args,
        capture_output=True,
        text=True,
        check=False,
    )


def git_current_branch():
    """Short branch name, or None when HEAD is detached."""
    r = _git(["symbolic-ref", "--short", "-q", "HEAD"])
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def git_is_clean():
    r = _git(["status", "--porcelain"])
    return r.returncode == 0 and r.stdout.strip() == ""


def git_pull_ff_only():
    return _git(["pull", "--ff-only", "origin", "main"])


def run_update():
    """Compare versions, git pull --ff-only origin main, reinstall CLI/skills.

    Never discards or stashes user changes: dirty/detached/non-main checkouts
    are refused outright. Returns (ok, [messages]).
    """
    if in_worktree():
        return False, ["refused: kanban worktree 内"]

    branch = git_current_branch()
    if branch is None:
        return False, ["update refused: HEAD is detached (checkout main first)"]
    if branch != "main":
        return False, ["update refused: current branch is '%s', expected 'main'" % branch]
    if not git_is_clean():
        return False, ["update refused: working tree is dirty (commit or stash your changes first)"]

    messages = []
    report = version_report()
    messages.append("current: %s" % report["current"])
    if report["latest"]:
        messages.append("latest: %s" % report["latest"])
        messages.append("state: %s" % report["state"])
    elif report["error"]:
        messages.append("latest: unknown (%s)" % report["error"])

    result = git_pull_ff_only()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return False, messages + ["git pull --ff-only origin main failed: %s" % detail]
    messages.append("git pull --ff-only origin main: %s" % (result.stdout.strip() or "already up to date"))

    # Reload the installer so a freshly pulled setup_core.py drives the
    # reinstall, not the module snapshot that was loaded before the pull.
    self_module = sys.modules[__name__]
    reloaded = importlib.reload(self_module)
    _, cli_msg = reloaded.install_cli()
    messages.append(cli_msg)
    messages += reloaded.install_skills(force=True)
    return True, messages
