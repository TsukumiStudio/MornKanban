#!/usr/bin/env python3
"""`kanban monitor` CLI: run the read-only monitor server, manage the macOS
LaunchAgent for PC-resident mode, and manage search-root configuration.

python3 standard library only (no pip dependencies), matching the rest of
MornKanban's distribution constraints.
"""
import argparse
import sys

HERE_PARENT = None  # set by kanban.sh's sys.path handling when invoked directly


def _ensure_importable():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)


_ensure_importable()

from monitor import discovery, launchagent, server  # noqa: E402


def cmd_run(args):
    server.run(host=args.host, port=args.port, extra_roots=args.root)
    return 0


def cmd_daemon_install(args):
    ok, msg = launchagent.install(host=args.host, port=args.port, roots=args.root)
    print(msg)
    return 0 if ok else 1


def cmd_daemon_start(args):
    ok, msg = launchagent.start()
    print(msg)
    return 0 if ok else 1


def cmd_daemon_stop(args):
    ok, msg = launchagent.stop()
    print(msg)
    return 0 if ok else 1


def cmd_daemon_status(args):
    st = launchagent.status()
    print("installed: %s" % ("yes" if st["installed"] else "no"))
    print("running: %s" % ("yes" if st["running"] else "no"))
    return 0


def cmd_daemon_uninstall(args):
    ok, messages = launchagent.uninstall()
    for m in messages:
        print(m)
    return 0 if ok else 1


def cmd_config_list_roots(args):
    cfg = discovery.load_config()
    roots = cfg.get("roots") or discovery.DEFAULT_ROOTS
    for r in roots:
        print(r)
    return 0


def cmd_config_add_root(args):
    roots = discovery.add_root(args.path)
    print("roots: %s" % ", ".join(roots))
    return 0


def cmd_config_remove_root(args):
    roots = discovery.remove_root(args.path)
    print("roots: %s" % ", ".join(roots))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="kanban monitor", description="Read-only multi-project kanban monitor")
    sub = p.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="run the monitor server in the foreground")
    run_p.add_argument("--host", default=server.DEFAULT_HOST)
    run_p.add_argument("--port", type=int, default=server.DEFAULT_PORT)
    run_p.add_argument("--root", action="append", default=[], help="additional search root (repeatable)")
    run_p.set_defaults(func=cmd_run)

    daemon_p = sub.add_parser("daemon", help="macOS LaunchAgent lifecycle (PC-resident mode)")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command")

    di = daemon_sub.add_parser("install", help="write/refresh the LaunchAgent plist")
    di.add_argument("--host", default=server.DEFAULT_HOST)
    di.add_argument("--port", type=int, default=server.DEFAULT_PORT)
    di.add_argument("--root", action="append", default=[])
    di.set_defaults(func=cmd_daemon_install)

    ds = daemon_sub.add_parser("start", help="load and start the LaunchAgent")
    ds.set_defaults(func=cmd_daemon_start)

    dp = daemon_sub.add_parser("stop", help="stop and unload the LaunchAgent")
    dp.set_defaults(func=cmd_daemon_stop)

    dst = daemon_sub.add_parser("status", help="show LaunchAgent install/run state")
    dst.set_defaults(func=cmd_daemon_status)

    du = daemon_sub.add_parser("uninstall", help="stop and remove the LaunchAgent plist")
    du.set_defaults(func=cmd_daemon_uninstall)

    config_p = sub.add_parser("config", help="manage discovery search roots")
    config_sub = config_p.add_subparsers(dest="config_command")

    cl = config_sub.add_parser("list-roots")
    cl.set_defaults(func=cmd_config_list_roots)

    ca = config_sub.add_parser("add-root")
    ca.add_argument("path")
    ca.set_defaults(func=cmd_config_add_root)

    cr = config_sub.add_parser("remove-root")
    cr.add_argument("path")
    cr.set_defaults(func=cmd_config_remove_root)

    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # Bare `kanban monitor` (no subcommand) starts the foreground server,
    # same as `kanban monitor run`.
    if not argv or argv[0].startswith("-"):
        argv = ["run"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
