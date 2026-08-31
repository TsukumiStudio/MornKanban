#!/usr/bin/env python3
"""MornKanban environment setup GUI (CLI wizard, no tkinter/osascript needed).

ロジックは gui/setup_core.py を使う。TTY が無い場合は状態サマリを表示して終了する。
"""
import os
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


def status_summary():
    lines = []
    deps = check_deps()
    lines.append(
        "deps: " + ", ".join("%s=%s" % (k, "OK" if v else "NG") for k, v in deps.items())
    )
    lines.append("kanban CLI: %s" % ("導入済み" if cli_installed() else "未導入"))
    lines.append("Claude Code スキル: %s" % ("導入済み" if skill_installed() else "未導入"))
    projects = load_projects()
    if projects:
        lines.append("プロジェクト:")
        for p in projects:
            lines.append(
                "  [%s] %s (%s)" % ("✓" if p["has_kanban"] else "✗", p["name"], p["path"])
            )
    else:
        lines.append("プロジェクト: なし")
    return "\n".join(lines)


def print_menu():
    print("1) CLI をインストール (状態: %s)" % ("導入済み" if cli_installed() else "未導入"))
    print(
        "2) Claude Code スキルを導入 (状態: %s)"
        % ("導入済み" if skill_installed() else "未導入")
    )
    print("3) プロジェクトを追加して init")
    print("4) プロジェクト一覧")
    print("5) 終了")


def prompt(text):
    try:
        return input(text)
    except EOFError:
        return None


def main():
    if not sys.stdin.isatty():
        print(status_summary())
        sys.exit(0)

    while True:
        print_menu()
        choice = prompt("番号を選択 (q で終了): ")
        if choice is None:
            print()
            sys.exit(0)
        choice = choice.strip()
        if choice in ("q", "Q", "5"):
            sys.exit(0)
        elif choice == "1":
            ok, msg = install_cli()
            print(msg)
        elif choice == "2":
            ok, msg = install_skill()
            if not ok and msg == "already installed":
                ans = prompt("既に導入済みです。上書きしますか? (y/N): ")
                if ans is None:
                    sys.exit(0)
                if ans.strip().lower() == "y":
                    ok, msg = install_skill(force=True)
                else:
                    continue
            print(msg)
        elif choice == "3":
            path = prompt("プロジェクトのパスを入力: ")
            if path is None:
                sys.exit(0)
            if not path.strip():
                continue
            norm = os.path.abspath(os.path.expanduser(path.strip()))
            ok, msg = add_project(norm)
            print(msg)
            if not ok:
                continue
            ans = prompt("init しますか? (y/N): ")
            if ans is None:
                sys.exit(0)
            if ans.strip().lower() == "y":
                ok2, msg2 = init_project(norm)
                print(msg2)
        elif choice == "4":
            projects = load_projects()
            if not projects:
                print("登録されているプロジェクトはありません")
            for p in projects:
                print(
                    "[%s] %s (%s)" % ("✓" if p["has_kanban"] else "✗", p["name"], p["path"])
                )
        else:
            print("不明な選択: %s" % choice)


if __name__ == "__main__":
    main()
