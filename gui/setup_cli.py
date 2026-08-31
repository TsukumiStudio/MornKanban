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
    guard_status,
    install_cli,
    install_skills,
    run_setup,
    run_uninstall,
    run_update,
    skill_status,
    version_report,
)


def cmd_install():
    ok, cli_msg = install_cli()
    print(cli_msg)
    for msg in install_skills(force=True):
        print(msg)
    return ok


def cmd_uninstall():
    for msg in run_uninstall():
        print(msg)
    return True


def cmd_update():
    ok, messages = run_update()
    for msg in messages:
        print(msg)
    return ok


def cmd_version():
    report = version_report()
    print("current: %s" % report["current"])
    if report["latest"]:
        print("latest: %s" % report["latest"])
        print("state: %s" % report["state"])
    else:
        print("latest: unknown (%s)" % report["error"])
    return True


def cmd_guard_status():
    status = guard_status()
    print("claude=%s,codex=%s" % (status["claude"], status["codex"]))
    return True


COMMANDS = {
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "update": cmd_update,
    "version": cmd_version,
    "guard-status": cmd_guard_status,
}


def status_summary():
    lines = []
    deps = check_deps()
    lines.append(
        "deps: " + ", ".join("%s=%s" % (k, "OK" if v else "NG") for k, v in deps.items())
    )
    lines.append("kanban CLI: %s" % ("導入済み" if cli_installed() else "未導入"))
    for name, installed in skill_status().items():
        lines.append("%s スキル: %s" % (name, "導入済み" if installed else "未導入"))
    guard = guard_status()
    lines.append(
        "秘書ガード (直接実装/検証/Git変更/外部公開/in-process delegation の拒否): claude=%s, codex=%s"
        % (guard["claude"], guard["codex"])
    )
    lines.append("導入後はプロジェクトのペインで『$kanban-dispatch 秘書として開始』")
    return "\n".join(lines)


def prompt(text):
    try:
        return input(text)
    except EOFError:
        return None


def main():
    args = sys.argv[1:]
    if args:
        cmd = args[0]
        handler = COMMANDS.get(cmd)
        if handler is None:
            print("kanban-setup: unknown command: %s" % cmd, file=sys.stderr)
            sys.exit(1)
        ok = handler()
        sys.exit(0 if ok else 1)

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
