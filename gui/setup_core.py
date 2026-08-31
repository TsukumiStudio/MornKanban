"""MornKanban environment setup logic (tkinter非依存).

setup_gui.py から抽出した UI 非依存のロジック関数群。python3 標準ライブラリのみ使用。
"""
import json
import os
import shutil
import tempfile
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

# --- secretary direct-action guard -------------------------------------------
# Claude Code has a real PreToolUse hook we can deny through (settings.json,
# see below): matched against every tool a secretary pane could use to
# implement/verify/commit/publish directly, including this CLI's own
# in-process Agent/Task subagent delegation. Codex has no documented
# pre-tool-call deny hook as of this writing (its `hooks`/`rules` surfaces
# are approval-memory/notify, not a deny gate) - its enforcement stays
# prompt/contract-level only (skill text + KANBAN.md + README), and
# guard_status() reports that honestly as "partial" rather than claiming it
# is enforced.
GUARD_SOURCE_DIR = os.path.join(REPO, "guard")
GUARD_HOOK_SCRIPT = os.path.join(GUARD_SOURCE_DIR, "claude_secretary_guard.py")
CLAUDE_SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
GUARD_MATCHER = "Task|Agent|Edit|Write|NotebookEdit|Bash"

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


def _guard_command():
    return "python3 %s" % GUARD_HOOK_SCRIPT


def _is_guard_hook(hook):
    return hook.get("type") == "command" and GUARD_HOOK_SCRIPT in hook.get("command", "")


def _read_json_settings(path):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read().strip()
    if not content:
        return {}
    return json.loads(content)


def _write_json_settings(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def install_claude_guard(settings_path=None):
    """Idempotently add the secretary PreToolUse deny hook to settings.json.

    Never overwrites unrelated hooks/keys: existing content is loaded as-is,
    backed up once (settings.json.mornkanban-guard.bak, only if that backup
    does not already exist), and only our own PreToolUse entry is
    added/removed/updated. Matched by GUARD_HOOK_SCRIPT appearing in a
    hook's `command`, so re-running install/update is a no-op once the
    matcher already matches GUARD_MATCHER, and self-heals an older, narrower
    matcher (e.g. a previous "Task"-only install) forward.
    """
    path = settings_path or CLAUDE_SETTINGS_PATH
    try:
        data = _read_json_settings(path)
    except (OSError, ValueError) as e:
        return "Claude guard: %s の読み込みに失敗しました (%s); 変更を中止しました" % (path, e)

    hooks = data.setdefault("hooks", {})
    pretooluse = hooks.setdefault("PreToolUse", [])
    for entry in pretooluse:
        if any(_is_guard_hook(h) for h in entry.get("hooks", [])):
            if entry.get("matcher") == GUARD_MATCHER:
                return "Claude guard: 導入済み (%s)" % path
            entry["matcher"] = GUARD_MATCHER
            _write_json_settings(path, data)
            return "Claude guard: matcher を更新しました (%s)" % path

    if os.path.isfile(path):
        backup = path + ".mornkanban-guard.bak"
        if not os.path.exists(backup):
            shutil.copy2(path, backup)

    pretooluse.append(
        {
            "matcher": GUARD_MATCHER,
            "hooks": [{"type": "command", "command": _guard_command()}],
        }
    )
    _write_json_settings(path, data)
    return "Claude guard: 導入しました (%s, matcher=%s)" % (path, GUARD_MATCHER)


def uninstall_claude_guard(settings_path=None):
    """Remove only our managed PreToolUse entry; leaves everything else intact."""
    path = settings_path or CLAUDE_SETTINGS_PATH
    if not os.path.isfile(path):
        return "Claude guard: 未導入"
    try:
        data = _read_json_settings(path)
    except (OSError, ValueError) as e:
        return "Claude guard: %s の読み込みに失敗しました (%s); 変更を中止しました" % (path, e)

    pretooluse = data.get("hooks", {}).get("PreToolUse", [])
    changed = False
    kept = []
    for entry in pretooluse:
        entry_hooks = entry.get("hooks", [])
        remaining = [h for h in entry_hooks if not _is_guard_hook(h)]
        if len(remaining) != len(entry_hooks):
            changed = True
            if remaining:
                kept.append(dict(entry, hooks=remaining))
            # else: entry existed only for our hook -> drop it entirely
        else:
            kept.append(entry)

    if not changed:
        return "Claude guard: 未導入"

    data.setdefault("hooks", {})["PreToolUse"] = kept
    _write_json_settings(path, data)
    return "Claude guard: 削除しました (%s)" % path


def claude_guard_state(settings_path=None):
    """'enforced' | 'not-installed' | 'misconfigured'."""
    path = settings_path or CLAUDE_SETTINGS_PATH
    if not os.path.isfile(GUARD_HOOK_SCRIPT):
        return "misconfigured"
    try:
        data = _read_json_settings(path)
    except (OSError, ValueError):
        return "misconfigured"
    for entry in data.get("hooks", {}).get("PreToolUse", []):
        if any(_is_guard_hook(h) for h in entry.get("hooks", [])):
            return "enforced" if entry.get("matcher") == GUARD_MATCHER else "misconfigured"
    return "not-installed"


def guard_status(settings_path=None):
    """{'claude': 'enforced'|'not-installed'|'misconfigured', 'codex': 'partial'}.

    Codex is always reported as "partial": there is no confirmed pre-tool
    deny hook to install, so enforcement there is skill/contract text only
    (see the module docstring above) - never displayed as "enforced".
    """
    return {"claude": claude_guard_state(settings_path), "codex": "partial"}


def run_setup():
    if in_worktree():
        return ["refused: kanban worktree 内"]
    _, cli_msg = install_cli()
    return [cli_msg] + install_skills(force=True) + [install_claude_guard()]


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
    ] + [uninstall_claude_guard()]


# --- update (reinstall from current checkout) --------------------------------


def run_update():
    """Reinstall CLI/skills/guard from this checkout without Git operations."""
    if in_worktree():
        return False, ["refused: kanban worktree 内"]
    return True, run_setup()
