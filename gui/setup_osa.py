#!/usr/bin/env python3
"""MornKanban environment setup GUI (macOS native, osascript popups).

tkinter が無い macOS 環境向け。ロジックは gui/setup_core.py を使う。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from setup_core import (  # noqa: E402
    add_project,
    check_deps,
    cli_installed,
    init_project,
    install_cli,
    install_skill,
    load_projects,
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


def osa_choose(items, prompt):
    list_literal = "{" + ", ".join('"%s"' % _esc(i) for i in items) + "}"
    script = (
        "set theList to %s\n"
        'set theChoice to choose from list theList with prompt "%s" '
        "without multiple selections allowed\n"
        "if theChoice is false then\n"
        '    return "__CANCELLED__"\n'
        "else\n"
        "    return item 1 of theChoice\n"
        "end if\n"
    ) % (list_literal, _esc(prompt))
    rc, out, _err = run_osascript(script)
    if rc != 0 or out == "__CANCELLED__":
        return None
    return out


def osa_display_ok(title, message):
    script = 'display dialog %s buttons {"OK"} default button "OK" with title "%s"' % (
        _as_string(message),
        _esc(title),
    )
    run_osascript(script)


def osa_confirm(title, message, yes_label, no_label):
    script = (
        "set theResult to display dialog %s buttons {\"%s\", \"%s\"} "
        'default button "%s" with title "%s"\n'
        "return button returned of theResult\n"
    ) % (_as_string(message), _esc(no_label), _esc(yes_label), _esc(no_label), _esc(title))
    rc, out, _err = run_osascript(script)
    if rc != 0:
        return False
    return out == yes_label


def osa_input(title, prompt):
    script = (
        'set theResult to display dialog "%s" default answer "" with title "%s"\n'
        "return text returned of theResult\n"
    ) % (_esc(prompt), _esc(title))
    rc, out, _err = run_osascript(script)
    if rc != 0:
        return None
    return out


def menu_items():
    return [
        "CLI をインストール (状態: %s)" % ("導入済み" if cli_installed() else "未導入"),
        "Claude Code スキルを導入 (状態: %s)" % ("導入済み" if skill_installed() else "未導入"),
        "プロジェクトを追加して init",
        "プロジェクト一覧",
        "終了",
    ]


def handle_install_cli():
    ok, msg = install_cli()
    osa_display_ok("kanban CLI", msg)


def handle_install_skill():
    ok, msg = install_skill()
    if not ok and msg == "already installed":
        if osa_confirm("Claude Code スキル", "既に導入済みです。上書きしますか?", "上書き", "いいえ"):
            ok, msg = install_skill(force=True)
        else:
            return
    osa_display_ok("Claude Code スキル", msg)


def handle_add_project():
    path = osa_input("プロジェクト追加", "プロジェクトのパスを入力してください")
    if path is None or not path.strip():
        return
    norm = os.path.abspath(os.path.expanduser(path.strip()))
    ok, msg = add_project(path)
    if not ok:
        osa_display_ok("プロジェクト追加", msg)
        return
    if osa_confirm("kanban init", "%s\ninit しますか?" % msg, "init する", "しない"):
        ok2, msg2 = init_project(norm)
        osa_display_ok("kanban init", msg2)
    else:
        osa_display_ok("プロジェクト追加", msg)


def handle_list_projects():
    projects = load_projects()
    if not projects:
        osa_display_ok("プロジェクト一覧", "登録されているプロジェクトはありません")
        return
    lines = "\n".join(
        "[%s] %s (%s)" % ("✓" if p["has_kanban"] else "✗", p["name"], p["path"])
        for p in projects
    )
    osa_display_ok("プロジェクト一覧", lines)


def main():
    if sys.platform != "darwin":
        sys.stderr.write("macOS でのみ動作します\n")
        sys.exit(2)
    if shutil.which("osascript") is None:
        sys.stderr.write("osascript が見つかりません\n")
        sys.exit(2)

    deps = check_deps()
    dep_lines = "\n".join("%s: %s" % (k, "✓" if v else "✗") for k, v in deps.items())
    osa_display_ok(TITLE, dep_lines)

    while True:
        choice = osa_choose(menu_items(), "操作を選択してください")
        if choice is None or choice.startswith("終了"):
            break
        if choice.startswith("CLI をインストール"):
            handle_install_cli()
        elif choice.startswith("Claude Code スキルを導入"):
            handle_install_skill()
        elif choice.startswith("プロジェクトを追加"):
            handle_add_project()
        elif choice.startswith("プロジェクト一覧"):
            handle_list_projects()


if __name__ == "__main__":
    main()
