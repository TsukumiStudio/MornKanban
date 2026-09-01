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
    local_version,
    REPO,
    run_setup,
    run_uninstall,
    run_update,
    skill_status,
    version_report,
)
import dashboard  # noqa: E402


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
    status = {"repo": REPO, "local_version": local_version()}
    for line in dashboard.build_update_preview(status):
        print(line)
    ok, messages = run_update()
    for line in dashboard.build_summary(
        "update", messages,
        ["kanban version"] if ok else [],
    ):
        print(line)
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
    """Plain compatibility summary for callers that import it directly."""
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
    lines.append("導入後は『$kanban-dispatch 秘書として開始』または『$kanban-report 今日』")
    return "\n".join(lines)


def prompt(text):
    try:
        return input(text)
    except EOFError:
        return None


def _confirm(action_label):
    ans = prompt("%s を実行しますか? [y/N]: " % action_label)
    return bool(ans) and ans.strip().lower() == "y"


def _interactive_install(status):
    for line in dashboard.build_install_preview(status):
        print(line)
    if not _confirm("install"):
        print("中止しました。変更は行っていません。")
        return
    messages = run_setup()
    for line in dashboard.build_summary(
        "install", messages,
        ["kanban version", "Herdr pane で $kanban-dispatch 秘書として開始"],
    ):
        print(line)


def _interactive_update(status):
    for line in dashboard.build_update_preview(status):
        print(line)
    if not _confirm("update"):
        print("中止しました。変更は行っていません。")
        return
    ok, messages = run_update()
    for line in dashboard.build_summary(
        "update", messages,
        ["kanban version"] if ok else [],
    ):
        print(line)


def _interactive_uninstall(status):
    for line in dashboard.build_uninstall_preview(status):
        print(line)
    if not _confirm("uninstall"):
        print("中止しました。変更は行っていません。")
        return
    messages = run_uninstall()
    for line in dashboard.build_summary(
        "uninstall", messages,
        ["再導入する場合: kanban-setup.sh install"],
    ):
        print(line)


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

    caps = dashboard.terminal_caps()
    status = dashboard.collect_status()
    print(dashboard.render_status(status, caps))
    print()

    if not sys.stdin.isatty():
        sys.exit(0)

    while True:
        ans = prompt("[h=ヘルプ / y=セットアップ / s=更新 / u=アンインストール / N=何もしない]: ")
        if ans is None:
            break
        choice = ans.strip().lower()
        if choice == "h":
            print(dashboard.render_guide(caps))
            print()
            continue
        if choice == "y":
            _interactive_install(status)
        elif choice == "s":
            _interactive_update(status)
        elif choice == "u":
            _interactive_uninstall(status)
        break
    sys.exit(0)


if __name__ == "__main__":
    main()
