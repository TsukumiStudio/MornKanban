#!/usr/bin/env python3
"""MornKanban environment setup GUI (CLI wizard, no tkinter/osascript needed).

ロジックは gui/setup_core.py を使う。環境構築のみ・1画面完結。
"""
import os
import sys

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


def status_summary():
    lines = []
    deps = check_deps()
    lines.append(
        "deps: " + ", ".join("%s=%s" % (k, "OK" if v else "NG") for k, v in deps.items())
    )
    lines.append("kanban CLI: %s" % ("導入済み" if cli_installed() else "未導入"))
    lines.append("Claude Code スキル: %s" % ("導入済み" if skill_installed() else "未導入"))
    lines.append("導入後はプロジェクトのペインで claude に『kanban の秘書として待機して』")
    return "\n".join(lines)


def prompt(text):
    try:
        return input(text)
    except EOFError:
        return None


def main():
    print(status_summary())

    if not sys.stdin.isatty():
        sys.exit(0)

    ans = prompt("[y=セットアップ / u=アンインストール / N=何もしない]: ")
    if ans is None:
        sys.exit(0)
    choice = ans.strip().lower()
    if choice == "y":
        for msg in run_setup():
            print(msg)
    elif choice == "u":
        for msg in run_uninstall():
            print(msg)
    sys.exit(0)


if __name__ == "__main__":
    main()
