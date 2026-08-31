#!/usr/bin/env python3
"""MornKanban environment setup GUI (macOS native, osascript popups).

tkinter が無い macOS 環境向け。ロジックは gui/setup_core.py を使う。
環境構築のみ・最大2枚のダイアログで完結する(メニューループ無し)。
"""
import shutil
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from setup_core import (  # noqa: E402
    check_deps,
    cli_installed,
    run_setup,
    run_uninstall,
    skill_installed,
)

TITLE = "MornKanban Setup"


def _esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _as_string(s):
    parts = str(s).split("\n")
    return " & return & ".join('"%s"' % _esc(p) for p in parts)


def run_osascript(script):
    proc = subprocess.run(
        ["osascript", "-"], input=script, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def status_message():
    deps = check_deps()
    lines = [
        "deps: " + ", ".join("%s %s" % (k, "✓" if v else "✗") for k, v in deps.items()),
        "kanban CLI: %s" % ("導入済み" if cli_installed() else "未導入"),
        "スキル: %s" % ("導入済み" if skill_installed() else "未導入"),
        "",
        "導入後はプロジェクトのペインで claude に",
        "『kanban の秘書として待機して』",
    ]
    return "\n".join(lines)


def show_status_dialog():
    already_done = cli_installed() and skill_installed()
    buttons = '{"閉じる", "アンインストール"}' if already_done else '{"閉じる", "アンインストール", "セットアップ実行"}'
    default_button = "閉じる" if already_done else "セットアップ実行"
    script = (
        "set theResult to display dialog %s buttons %s "
        'default button "%s" with title "%s"\n'
        "return button returned of theResult\n"
    ) % (_as_string(status_message()), buttons, _esc(default_button), _esc(TITLE))
    rc, out, _err = run_osascript(script)
    if rc != 0:
        return None
    return out


def show_result_dialog(messages):
    lines = list(messages)
    lines.append("")
    lines.append(status_message())
    script = 'display dialog %s buttons {"OK"} default button "OK" with title "%s"' % (
        _as_string("\n".join(lines)),
        _esc(TITLE),
    )
    run_osascript(script)


def main():
    if sys.platform != "darwin":
        sys.stderr.write("macOS でのみ動作します\n")
        sys.exit(2)
    if shutil.which("osascript") is None:
        sys.stderr.write("osascript が見つかりません\n")
        sys.exit(2)

    choice = show_status_dialog()
    if choice is None or choice == "閉じる":
        sys.exit(0)

    if choice == "セットアップ実行":
        messages = run_setup()
        show_result_dialog(messages)
    elif choice == "アンインストール":
        messages = run_uninstall()
        show_result_dialog(messages)

    sys.exit(0)


if __name__ == "__main__":
    main()
