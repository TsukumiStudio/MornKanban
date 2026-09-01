"""MornKanban setup dashboard: status collection + rendering for the
terminal setup wizard (`gui/setup_cli.py`).

UI-only: this module never installs/updates/uninstalls anything itself. It
only *describes* current state and the actions available, and builds the
"about to change / what changed" preview and summary text that setup_cli.py
prints around the existing install/update/uninstall calls.

python3 standard library only. bash 3.2 / no-pip constraints of the rest of
the repo don't apply here (this is pure python), but no third-party imports
are used regardless.
"""
import os
import re
import shutil
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import setup_core  # noqa: E402
from monitor import launchagent, server  # noqa: E402
from registry import store as registry_store  # noqa: E402

STATE_INSTALLED = "導入済み"
STATE_NOT_INSTALLED = "未導入"
STATE_UPDATE = "更新あり"
STATE_RUNNING = "稼働中"
STATE_STOPPED = "停止中"
STATE_OPTIONAL = "任意・未設定"
STATE_REGISTERED = "登録あり"
STATE_EMPTY = "登録なし"
STATE_NEEDS_CHECK = "要確認"
STATE_UNKNOWN = "不明"

# state -> (color-name, ascii-symbol, unicode-symbol)
_STATE_STYLE = {
    STATE_INSTALLED: ("green", "[OK]", "✔"),
    STATE_NOT_INSTALLED: ("gray", "[--]", "○"),
    STATE_UPDATE: ("yellow", "[UP]", "▲"),
    STATE_RUNNING: ("green", "[ON]", "●"),
    STATE_STOPPED: ("gray", "[OFF]", "○"),
    STATE_OPTIONAL: ("gray", "[--]", "○"),
    STATE_REGISTERED: ("green", "[OK]", "✔"),
    STATE_EMPTY: ("gray", "[--]", "○"),
    STATE_NEEDS_CHECK: ("red", "[!!]", "⚠"),
    STATE_UNKNOWN: ("gray", "[??]", "•"),
}

_ANSI = {
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
    "gray": "\x1b[90m",
    "bold": "\x1b[1m",
    "reset": "\x1b[0m",
}

_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def sanitize(text):
    """Strip control characters (incl. ESC) from an externally-derived value
    (path, version string, URL, ...) before it is ever interpolated into
    terminal output, so a crafted VERSION file or path can't inject ANSI
    sequences or move the cursor."""
    if text is None:
        return ""
    return _CTRL_RE.sub("", str(text))


# --- terminal capability detection ------------------------------------------

def terminal_caps(env=None, stdout=None, columns_override=None):
    env = os.environ if env is None else env
    stdout = sys.stdout if stdout is None else stdout
    try:
        isatty = bool(stdout.isatty())
    except Exception:
        isatty = False
    no_color = bool(env.get("NO_COLOR"))
    term = env.get("TERM", "")
    dumb = term == "dumb"
    color = isatty and not no_color and not dumb
    unicode_ok = isatty and not dumb and _locale_supports_unicode(env)
    if columns_override is not None:
        width = columns_override
    else:
        width = _detect_width(env)
    width = max(40, width)
    return {"color": color, "unicode": unicode_ok, "width": width, "isatty": isatty}


def _locale_supports_unicode(env):
    for key in ("LC_ALL", "LC_CTYPE", "LANG"):
        val = env.get(key)
        if val:
            return "utf" in val.lower()
    # No locale info at all: assume the common case (utf-8 terminal) rather
    # than silently downgrading every unconfigured environment to ASCII.
    return True


def _detect_width(env):
    cols = env.get("COLUMNS")
    if cols:
        try:
            return int(cols)
        except ValueError:
            pass
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        return 80


def _style(caps, color_name, text):
    if not caps["color"]:
        return text
    code = _ANSI.get(color_name, "")
    if not code:
        return text
    return code + text + _ANSI["reset"]


def state_badge(caps, state):
    color_name, ascii_sym, uni_sym = _STATE_STYLE.get(state, _STATE_STYLE[STATE_UNKNOWN])
    symbol = uni_sym if caps["unicode"] else ascii_sym
    label = "%s %s" % (symbol, state)
    return _style(caps, color_name, label)


def display_width(text):
    """Terminal column width, counting East-Asian Wide/Fullwidth chars
    (Japanese text, most CJK) as 2 columns like a real terminal does."""
    w = 0
    for ch in _ANSI_RE.sub("", text):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad_display(text, width):
    w = display_width(text)
    if w >= width:
        return text
    return text + " " * (width - w)


def wrap_path(text, width):
    """Safe-fold a long path/value so it never overruns `width` *display
    columns* (accounting for double-width Japanese characters), breaking at
    '/' boundaries where possible and hard-cutting otherwise."""
    text = sanitize(_ANSI_RE.sub("", text))
    if display_width(text) <= width:
        return [text]
    lines = []
    current = ""
    current_w = 0
    last_sep_at = None  # length of `current` right after the last os.sep seen
    for ch in text:
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if current_w + ch_w > width:
            if last_sep_at:
                lines.append(current[:last_sep_at])
                current = current[last_sep_at:]
                current_w = display_width(current)
                last_sep_at = None
            else:
                lines.append(current)
                current = ""
                current_w = 0
        current += ch
        current_w += ch_w
        if ch == os.sep:
            last_sep_at = len(current)
    if current:
        lines.append(current)
    return lines


# --- status collection --------------------------------------------------

def _skill_detail(name, directory):
    skill_path = os.path.join(directory, "SKILL.md")
    if not os.path.isfile(skill_path):
        return {"installed": False, "version": None, "repo": None, "state": STATE_NOT_INSTALLED}
    try:
        with open(skill_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return {"installed": True, "version": None, "repo": None, "state": STATE_NEEDS_CHECK}
    m_ver = re.search(r"installed\s+version `([^`]*)`", content)
    m_repo = re.search(r"checkout is `([^`]*)`", content)
    version = m_ver.group(1) if m_ver else None
    repo = m_repo.group(1) if m_repo else None
    local = setup_core.local_version()
    if version is None:
        state = STATE_NEEDS_CHECK
    elif version != local:
        state = STATE_UPDATE
    else:
        state = STATE_INSTALLED
    return {
        "installed": True,
        "version": sanitize(version),
        "repo": sanitize(repo),
        "state": state,
    }


def _cli_detail():
    link = setup_core.KANBAN_LINK
    if not os.path.lexists(link):
        return {"state": STATE_NOT_INSTALLED, "link": sanitize(link), "target": None}
    if not os.path.islink(link):
        return {"state": STATE_NEEDS_CHECK, "link": sanitize(link), "target": None}
    target = os.path.realpath(link)
    if not os.path.exists(target):
        return {
            "state": STATE_NEEDS_CHECK,
            "link": sanitize(link),
            "target": sanitize(os.readlink(link)),
        }
    return {
        "state": STATE_INSTALLED,
        "link": sanitize(link),
        "target": sanitize(os.readlink(link)),
    }


def _version_detail():
    report = setup_core.version_report()
    state = {
        "up-to-date": STATE_INSTALLED,
        "update-available": STATE_UPDATE,
        "local-ahead": STATE_INSTALLED,
        "unknown": STATE_NEEDS_CHECK,
    }.get(report["state"], STATE_NEEDS_CHECK)
    report = dict(report)
    for key in ("current", "latest", "error"):
        report[key] = sanitize(report[key]) if report[key] is not None else None
    report["badge_state"] = state
    return report


def _monitor_detail():
    st = launchagent.status()
    if not st["installed"]:
        state = STATE_OPTIONAL
    elif st["running"]:
        state = STATE_RUNNING
    else:
        state = STATE_STOPPED
    return {
        "installed": st["installed"],
        "running": st["running"],
        "state": state,
        "plist": sanitize(launchagent.plist_path()),
        "url": "http://%s:%d/" % (server.DEFAULT_HOST, server.DEFAULT_PORT),
    }


def _registry_detail():
    try:
        projects = registry_store.list_all()
        return {
            "state": STATE_REGISTERED if projects else STATE_EMPTY,
            "path": sanitize(registry_store.registry_path()),
            "count": len(projects),
            "error": None,
        }
    except registry_store.RegistryError as e:
        return {
            "state": STATE_NEEDS_CHECK,
            "path": sanitize(registry_store.registry_path()),
            "count": 0,
            "error": sanitize(e),
        }


def _find_kanban_root(start):
    d = os.path.realpath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".kanban")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _project_detail(cwd=None):
    cwd = cwd or os.getcwd()
    root = _find_kanban_root(cwd)
    if root is None:
        return {"state": STATE_NOT_INSTALLED, "root": None}
    return {"state": STATE_INSTALLED, "root": sanitize(root)}


def collect_status(cwd=None):
    return {
        "repo": sanitize(REPO),
        "local_version": sanitize(setup_core.local_version()),
        "cli": _cli_detail(),
        "skills": {
            name: _skill_detail(name, directory)
            for name, directory in setup_core.SKILL_TARGETS.items()
        },
        "version": _version_detail(),
        "monitor": _monitor_detail(),
        "registry": _registry_detail(),
        "project": _project_detail(cwd),
        "deps": setup_core.check_deps(),
        "guard": setup_core.guard_status(),
    }


# --- rendering ------------------------------------------------------------

def _box(caps, title, lines):
    width = caps["width"]
    if caps["unicode"]:
        h, v = "─", "│"
        tl, tr, ml, mr, bl, br = "┌", "┐", "├", "┤", "└", "┘"
    else:
        h, v = "-", "|"
        tl = tr = ml = mr = bl = br = "+"
    inner = max(10, width - 4)
    out = []
    out.append(tl + h * (inner + 2) + tr)
    out.append(v + pad_display(" " + title, inner + 2) + v)
    out.append(ml + h * (inner + 2) + mr)
    for line in lines:
        for wrapped in wrap_path(line, inner) if display_width(line) > inner else [line]:
            out.append(v + pad_display(" " + wrapped, inner + 2) + v)
    out.append(bl + h * (inner + 2) + br)
    return "\n".join(out)


def render_status(status, caps):
    lines = []
    lines.append(_style(caps, "bold", "MornKanban セットアップ状況"))

    body = []
    body.append("本体: %s" % status["repo"])
    body.append("VERSION: %s" % status["local_version"])
    body.append(
        "最新確認: %s"
        % (
            "%s (%s)" % (status["version"]["latest"], state_badge(caps, status["version"]["badge_state"]))
            if status["version"]["latest"]
            else "取得不能 (%s) %s" % (status["version"]["error"], state_badge(caps, STATE_NEEDS_CHECK))
        )
    )
    deps = status["deps"]
    body.append(
        "依存コマンド: "
        + ", ".join("%s=%s" % (k, "OK" if v else "NG") for k, v in sorted(deps.items()))
    )
    guard = status["guard"]
    body.append("秘書ガード: claude=%s, codex=%s" % (guard["claude"], guard["codex"]))
    lines.append(_box(caps, "MornKanban 本体", body))

    cli = status["cli"]
    cli_body = ["状態: %s" % state_badge(caps, cli["state"])]
    cli_body.append("リンク: %s" % cli["link"])
    cli_body.append("リンク先: %s" % (cli["target"] or "(なし)"))
    lines.append(_box(caps, "CLI (kanban コマンド)", cli_body))

    for name, detail in status["skills"].items():
        sk_body = ["状態: %s" % state_badge(caps, detail["state"])]
        if detail["installed"]:
            sk_body.append("導入バージョン: %s" % (detail["version"] or "不明"))
            sk_body.append("参照 checkout: %s" % (detail["repo"] or "不明"))
        lines.append(_box(caps, "%s スキル (kanban-dispatch)" % name, sk_body))

    mon = status["monitor"]
    mon_body = [
        "状態: %s" % state_badge(caps, mon["state"]),
        "URL: %s" % mon["url"],
        "plist: %s" % mon["plist"],
    ]
    lines.append(_box(caps, "monitor (常駐監視)", mon_body))

    reg = status["registry"]
    reg_body = ["状態: %s" % state_badge(caps, reg["state"])]
    reg_body.append("登録先: %s" % reg["path"])
    reg_body.append("登録件数: %d" % reg["count"])
    if reg["error"]:
        reg_body.append("エラー: %s" % reg["error"])
    lines.append(_box(caps, "project registry", reg_body))

    proj = status["project"]
    proj_body = ["状態: %s" % state_badge(caps, proj["state"])]
    proj_body.append("root: %s" % (proj["root"] or "(このディレクトリ以下に .kanban なし)"))
    lines.append(_box(caps, "現在のディレクトリ", proj_body))

    return "\n\n".join(lines)


GUIDE_FLOWS = [
    (
        "初回 install",
        "どこからでも (git clone 後)",
        "kanban-setup.sh install",
        "CLI、Claude Code/Codex の kanban-dispatch スキル、Claude秘書ガードを作成/修復",
        "~/.local/bin/kanban, Claude/Codexのskill、~/.claude/settings.jsonの管理対象hook",
        "リポジトリ本体、既存の project board、registry、monitor 設定",
    ),
    (
        "update",
        "どこからでも",
        "kanban update / kanban-setup.sh update",
        "現在の MornKanban checkout からCLI、スキル、Claude秘書ガードを再導入 (Git操作なし)",
        "~/.local/bin/kanban、Claude/Codexのskill、~/.claude/settings.jsonの管理対象hook",
        "MornKanban checkout、project board、registry、monitor 設定",
    ),
    (
        "uninstall",
        "どこからでも",
        "kanban uninstall / kanban-setup.sh uninstall",
        "このインストーラが作成した CLI、スキル、Claude秘書ガードだけを削除",
        "~/.local/bin/kanban, Claude/Codexのskill、~/.claude/settings.jsonの管理対象hook",
        "リポジトリ本体、project board (.kanban/)、registry、monitor 設定 (すべて削除されない)",
    ),
    (
        "project で init",
        "対象 project の root",
        "kanban init",
        ".kanban/{todo,doing,review,done,failed}/ と KANBAN.md ポリシーの雛形を作成 (既存 KANBAN.md は上書きしない)",
        "対象 project 直下の .kanban/",
        "他の project、PC 共通の registry",
    ),
    (
        "秘書として開始",
        "Herdr pane 内 (対象 project)",
        "$kanban-dispatch 秘書として開始",
        "対話エージェントを秘書モードにし、.kanban/ を初期化して Herdr 実行を検証",
        "対象 project の .kanban/ (未初期化なら)",
        "他 project、CLI/スキルの導入状態",
    ),
    (
        "projects add/list/remove",
        "どこからでも",
        "kanban projects add|list|remove <alias> [<path>]",
        "PC 共通 registry へ project の alias を登録/一覧/削除",
        "registry ファイル (projects.json) のみ",
        "登録先 project 自体の .kanban/ の中身",
    ),
    (
        "send による別 project への投函",
        "どこからでも",
        "kanban send <alias> <title>",
        "registry に登録済みの alias 先 project の .kanban/todo/ にカードを1件作成",
        "送信先 project の .kanban/todo/ に新規カード1件",
        "送信元 project、registry、他のカード",
    ),
    (
        "monitor 一時起動",
        "MornKanban checkout (kanban monitor はどこからでも)",
        "./kanban-monitor.sh (kanban monitor と同じ)",
        "読み取り専用の監視サーバをフォアグラウンドで起動し、http://127.0.0.1:8787/ で閲覧可能にする (Ctrl+C で停止)",
        "何も変更しない (読み取り専用)",
        "すべての project board、registry、CLI/スキルの導入状態",
    ),
    (
        "monitor 常駐化",
        "どこからでも",
        "kanban monitor daemon install / start / status / stop / uninstall",
        "macOS LaunchAgent として monitor を常駐/起動/状態確認/停止/削除",
        "~/Library/LaunchAgents/dev.mornkanban.monitor.plist, ~/Library/Logs/MornKanban/ 以下のログ",
        "project board、registry、CLI/スキルの導入状態",
    ),
]


def render_guide(caps):
    lines = [_style(caps, "bold", "どこで・何をすると・何が起こるか")]
    for title, where, cmd, effect, changed, kept in GUIDE_FLOWS:
        body = [
            "実行場所: %s" % where,
            "コマンド: %s" % cmd,
            "起こること: %s" % effect,
            "変更/作成される場所: %s" % changed,
            "保持されるもの: %s" % kept,
        ]
        lines.append(_box(caps, title, body))
    return "\n\n".join(lines)


# --- install/uninstall preview & summary ------------------------------------

def build_install_preview(status):
    lines = ["--- これから行う変更 (install) ---"]
    lines.append("作成/更新: %s -> %s" % (sanitize(setup_core.KANBAN_LINK), sanitize(setup_core.KANBAN_SH)))
    for name, directory in setup_core.SKILL_TARGETS.items():
        lines.append("作成/更新: %s/SKILL.md (バージョン %s を埋め込み)" % (sanitize(directory), status["local_version"]))
    lines.append("作成/更新: %s の MornKanban 管理対象hook" % sanitize(setup_core.CLAUDE_SETTINGS_PATH))
    lines.append("変更しない: project board、registry、monitor 設定、リポジトリ本体")
    return lines


def build_update_preview(status):
    lines = ["--- これから行う変更 (update) ---"]
    lines.append("Git操作: なし (現在のcheckoutを読み取るだけ)")
    lines.append("参照元: %s" % sanitize(status["repo"]))
    lines.append("更新: %s -> %s" % (sanitize(setup_core.KANBAN_LINK), sanitize(setup_core.KANBAN_SH)))
    for name, directory in setup_core.SKILL_TARGETS.items():
        lines.append("更新: %s/SKILL.md (バージョン %s へ再導入)" % (sanitize(directory), status["local_version"]))
    lines.append("更新: %s の MornKanban 管理対象hook" % sanitize(setup_core.CLAUDE_SETTINGS_PATH))
    lines.append("変更しない: MornKanban checkout、project board、registry、monitor 設定")
    return lines


def build_uninstall_preview(status):
    lines = ["--- これから行う変更 (uninstall) ---"]
    cli = status["cli"]
    if cli["state"] == STATE_INSTALLED:
        lines.append("削除: %s" % cli["link"])
    else:
        lines.append("削除対象なし: %s (未導入)" % cli["link"])
    for name, directory in setup_core.SKILL_TARGETS.items():
        detail = status["skills"][name]
        if detail["installed"]:
            lines.append("削除: %s/" % sanitize(directory))
        else:
            lines.append("削除対象なし: %s/ (未導入)" % sanitize(directory))
    lines.append("削除: %s の MornKanban 管理対象hookのみ (存在する場合)" % sanitize(setup_core.CLAUDE_SETTINGS_PATH))
    lines.append("削除しない: リポジトリ本体 (%s)" % status["repo"])
    lines.append("削除しない: project board (.kanban/ 配下)、registry、monitor 設定")
    return lines


def build_summary(action, messages, next_commands):
    lines = ["--- %s 結果 ---" % action]
    lines.extend(sanitize(m) for m in messages)
    if next_commands:
        lines.append("次に:")
        for c in next_commands:
            lines.append("  %s" % c)
    return lines
