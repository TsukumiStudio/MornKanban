#!/usr/bin/env python3
"""`kanban projects` (PC-wide project registry) and `kanban send` (file a
card into a registered project's `.kanban/todo/` from anywhere).

Invoked by kanban.sh as:
  python3 registry/cli.py projects add <alias> <path> [--force]
  python3 registry/cli.py projects list [--json]
  python3 registry/cli.py projects show <alias>
  python3 registry/cli.py projects update <alias> <path>
  python3 registry/cli.py projects remove <alias>
  python3 registry/cli.py send <alias> <title> [-b ...] [-m ...] [-e ...] [--depends-on ID] [-t ...] [--diagnose|--operate] [--from PATH]
  python3 registry/cli.py secretary resolve <project-root>

python3 standard library only (no pip dependencies), matching the rest of
MornKanban's distribution constraints.
"""
import argparse
import datetime
import json
import os
import re
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

from registry import secretary, store  # noqa: E402

BACKENDS = ("auto", "claude", "codex")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CARD_STATES = ("todo", "doing", "review", "resolving", "blocked", "done", "failed")
DEFAULTS = {
    "default_backend": "auto",
    "default_model": "",
    "threshold": "80",
    "max_attempts": "3",
    "diagnosis_target_minutes": "5",
    "diagnosis_max_minutes": "10",
}


# --- projects subcommand ------------------------------------------------

def cmd_projects_add(args):
    try:
        entry = store.add(args.alias, args.path, force=args.force)
    except store.RegistryError as e:
        print("kanban projects: %s" % e, file=sys.stderr)
        return 1
    print("registered %s -> %s" % (entry["alias"], entry["root"]))
    return 0


def cmd_projects_update(args):
    try:
        entry = store.update(args.alias, args.path)
    except store.RegistryError as e:
        print("kanban projects: %s" % e, file=sys.stderr)
        return 1
    print("updated %s -> %s" % (entry["alias"], entry["root"]))
    return 0


def cmd_projects_remove(args):
    try:
        store.remove(args.alias)
    except store.RegistryError as e:
        print("kanban projects: %s" % e, file=sys.stderr)
        return 1
    print("removed %s" % args.alias)
    return 0


def cmd_projects_list(args):
    try:
        projects = store.list_all()
    except store.RegistryError as e:
        print("kanban projects: %s" % e, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(projects, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not projects:
        print("(no projects registered; kanban projects add <alias> <path>)")
        return 0
    for alias in sorted(projects):
        print("%s\t%s" % (alias, projects[alias]["root"]))
    return 0


def cmd_projects_show(args):
    try:
        entry = store.get(args.alias)
    except store.RegistryError as e:
        print("kanban projects: %s" % e, file=sys.stderr)
        return 1
    for key in ("alias", "root", "kanban_dir", "added_at", "updated_at"):
        print("%s: %s" % (key, entry.get(key, "")))
    print("dispatcher: %s" % _dispatcher_status_text(entry["kanban_dir"]))
    return 0


# --- send -----------------------------------------------------------------

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _slugify(title):
    slug = _SLUG_RE.sub("-", title).strip("-").lower()[:40].rstrip("-")
    return slug or "task"


def _read_frontmatter(path):
    """Minimal reader for the simple `key: value` frontmatter kanban.sh's
    fm_get/cmd_init produce; not a general YAML parser."""
    values = dict(DEFAULTS)
    if not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    if not lines or lines[0].strip() != "---":
        return values
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in values:
            values[key] = value.strip()
    return values


def _dispatcher_pid(kanban_dir):
    lock = os.path.join(kanban_dir, ".lock")
    if not os.path.isfile(lock):
        return None
    try:
        with open(lock, "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def _dispatcher_status_text(kanban_dir):
    pid = _dispatcher_pid(kanban_dir)
    if pid:
        return "running (pid %d) - the card will be picked up automatically" % pid
    return (
        "not running - the card is filed but nothing will process it until "
        "someone runs `kanban run` (or starts the visible Herdr dispatcher) "
        "in that project"
    )


def _resolve_source(explicit_from):
    source_path = os.path.realpath(explicit_from) if explicit_from else os.path.realpath(os.getcwd())
    source_alias = store.find_by_path(source_path)
    return source_alias, source_path


def _card_state_by_id(kanban_dir, card_id):
    for state in CARD_STATES:
        state_dir = os.path.join(kanban_dir, state)
        try:
            names = os.listdir(state_dir)
        except OSError:
            continue
        if any(name.endswith(".md") and name.startswith(card_id + "-") for name in names):
            return state
    return None


def _write_card_atomic(todo_dir, title, body, backend, model, effort, depends_on,
                        threshold, max_attempts,
                        source_alias, source_path, task_kind="implementation",
                        diagnosis_target_minutes="5", diagnosis_max_minutes="10"):
    slug = _slugify(title)
    for _ in range(50):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        card_id = "%s-%s" % (stamp, secrets.token_hex(4))
        filename = "%s-%s.md" % (card_id, slug)
        dest = os.path.join(todo_dir, filename)
        tmp = os.path.join(todo_dir, ".tmp-%d-%s.md" % (os.getpid(), secrets.token_hex(4)))
        created = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        content = (
            "---\n"
            "id: %s\n"
            "title: %s\n"
            "backend: %s\n"
            "model: %s\n"
            "effort: %s\n"
            "depends_on: %s\n"
            "dependency_state: \n"
            "blocked_kind: \n"
            "failure_kind: \n"
            "threshold: %s\n"
            "max_attempts: %s\n"
            "review_enabled: %s\n"
            "review_source: %s\n"
            "task_kind: %s\n"
            "diagnosis_target_minutes: %s\n"
            "diagnosis_max_minutes: %s\n"
            "attempts: 0\n"
            "created: %s\n"
            "source_alias: %s\n"
            "source_path: %s\n"
            "dispatched_via: send\n"
            "---\n\n"
            "## Task\n\n%s\n\n## History\n"
        ) % (
            card_id, title, backend, model, effort, depends_on, threshold, max_attempts,
            "false" if task_kind != "implementation" else "auto",
            task_kind if task_kind != "implementation" else "auto",
            task_kind,
            diagnosis_target_minutes, diagnosis_max_minutes, created,
            source_alias or "", source_path, body,
        )
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        try:
            os.link(tmp, dest)
        except FileExistsError:
            os.remove(tmp)
            continue
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return dest
    raise store.RegistryError("could not allocate a unique card id after 50 attempts")


def cmd_send(args):
    try:
        target = store.get(args.alias)
    except store.RegistryError as e:
        print("kanban send: %s" % e, file=sys.stderr)
        return 1

    kanban_dir = target["kanban_dir"]
    todo_dir = os.path.join(kanban_dir, "todo")
    if not os.path.isdir(todo_dir):
        print(
            "kanban send: %s no longer has a .kanban/todo directory "
            "(project may have been moved/uninitialized; check `kanban projects show %s`)"
            % (kanban_dir, args.alias),
            file=sys.stderr,
        )
        return 1

    defaults = _read_frontmatter(os.path.join(kanban_dir, "KANBAN.md"))
    backend = args.backend or defaults["default_backend"]
    if backend not in BACKENDS:
        print("kanban send: unknown backend: %s (auto|claude|codex)" % backend, file=sys.stderr)
        return 1
    model = args.model if args.model is not None else defaults["default_model"]
    effort = args.effort or ""
    if effort and effort not in EFFORTS:
        print("kanban send: invalid effort: %s (%s)" % (effort, "|".join(EFFORTS)), file=sys.stderr)
        return 1
    depends_on = args.depends_on or ""
    if depends_on and not _card_state_by_id(kanban_dir, depends_on):
        print("kanban send: dependency card not found: %s" % depends_on, file=sys.stderr)
        return 1
    threshold = args.threshold if args.threshold is not None else defaults["threshold"]
    max_attempts = defaults["max_attempts"]

    body = args.title if sys.stdin.isatty() else (sys.stdin.read() or args.title)
    task_kind = "diagnose" if args.diagnose else "operation" if args.operate else "implementation"

    source_alias, source_path = _resolve_source(args.__dict__.get("from_path"))

    try:
        dest = _write_card_atomic(
            todo_dir, args.title, body, backend, model, effort, depends_on,
            threshold, max_attempts,
            source_alias, source_path, task_kind=task_kind,
            diagnosis_target_minutes=defaults["diagnosis_target_minutes"],
            diagnosis_max_minutes=defaults["diagnosis_max_minutes"],
        )
    except OSError as e:
        print("kanban send: failed to write card: %s" % e, file=sys.stderr)
        return 1

    print(dest)
    print(_dispatcher_status_text(kanban_dir), file=sys.stderr)
    return 0


# --- secretary --------------------------------------------------------------

def cmd_secretary_resolve(args):
    root = args.root
    if not os.path.isdir(root):
        print("kanban secretary: not a directory: %s" % root, file=sys.stderr)
        return 1
    env_override = os.environ.get("KANBAN_HERDR_SECRETARY") or None
    try:
        name, source = secretary.resolve(root, env_override=env_override)
    except secretary.SecretaryNameError as e:
        print("kanban secretary: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps({"name": name, "source": source, "root": os.path.realpath(root)}))
    return 0


# --- argparse wiring --------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="kanban", description="PC-wide project registry and card send")
    sub = p.add_subparsers(dest="command")

    projects_p = sub.add_parser("projects", help="manage the PC-wide project registry")
    projects_sub = projects_p.add_subparsers(dest="projects_command")

    pa = projects_sub.add_parser("add", help="register alias -> project root")
    pa.add_argument("alias")
    pa.add_argument("path")
    pa.add_argument("--force", action="store_true", help="overwrite an existing alias or duplicate path")
    pa.set_defaults(func=cmd_projects_add)

    pu = projects_sub.add_parser("update", help="repoint an existing alias")
    pu.add_argument("alias")
    pu.add_argument("path")
    pu.set_defaults(func=cmd_projects_update)

    pr = projects_sub.add_parser("remove", help="unregister an alias")
    pr.add_argument("alias")
    pr.set_defaults(func=cmd_projects_remove)

    pl = projects_sub.add_parser("list", help="list registered projects")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_projects_list)

    ps = projects_sub.add_parser("show", help="show one registered project")
    ps.add_argument("alias")
    ps.set_defaults(func=cmd_projects_show)

    send_p = sub.add_parser("send", help="file a card into a registered project from anywhere")
    send_p.add_argument("alias")
    send_p.add_argument("title")
    send_p.add_argument("-b", "--backend", default=None)
    send_p.add_argument("-m", "--model", default=None)
    send_p.add_argument("-e", "--effort", default=None)
    send_p.add_argument("--depends-on", default=None, metavar="CARD_ID")
    send_p.add_argument("-t", "--threshold", default=None)
    kind = send_p.add_mutually_exclusive_group()
    kind.add_argument("--diagnose", action="store_true",
                      help="file a read-only 5/10-minute diagnosis card")
    kind.add_argument("--operate", action="store_true",
                      help="file a serialized external-operation card")
    send_p.add_argument("--from", dest="from_path", default=None,
                         help="record this path as the send origin instead of cwd")
    send_p.set_defaults(func=cmd_send)

    secretary_p = sub.add_parser(
        "secretary", help="resolve the per-project Herdr secretary agent name"
    )
    secretary_sub = secretary_p.add_subparsers(dest="secretary_command")
    sr = secretary_sub.add_parser(
        "resolve", help="print {name, source, root} JSON for a project root"
    )
    sr.add_argument("root", help="project root (must already contain .kanban/)")
    sr.set_defaults(func=cmd_secretary_resolve)

    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
