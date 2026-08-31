"""Read-only access to a single project's `.kanban` board.

Mirrors the card format written by kanban.sh (YAML-ish frontmatter delimited
by `---` lines, followed by a Markdown body with a `## History` section) but
never writes anything back.
"""
import os

STATES = ["todo", "doing", "review", "done", "failed"]


def list_cards(kanban_dir, state):
    d = os.path.join(kanban_dir, state)
    if not os.path.isdir(d):
        return []
    try:
        files = [f for f in os.listdir(d) if f.endswith(".md")]
    except OSError:
        return []
    files.sort()
    return files


def parse_card(text):
    """Split card text into (frontmatter dict, body str)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
        i += 1
    body = "\n".join(lines[i + 1:]) if i < len(lines) else ""
    return fm, body.lstrip("\n")


def read_card(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    fm, body = parse_card(text)
    return fm, body


def card_summary(kanban_dir, state, filename):
    path = os.path.join(kanban_dir, state, filename)
    fm, _ = read_card(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    return {
        "filename": filename,
        "state": state,
        "id": fm.get("id", ""),
        "title": fm.get("title", filename),
        "backend": fm.get("backend", ""),
        "model": fm.get("model", ""),
        "threshold": fm.get("threshold", ""),
        "attempts": fm.get("attempts", ""),
        "max_attempts": fm.get("max_attempts", ""),
        "mtime": mtime,
    }


def project_counts(kanban_dir):
    return {s: len(list_cards(kanban_dir, s)) for s in STATES}


def pid_alive(pid):
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def dispatcher_status(kanban_dir):
    lock = os.path.join(kanban_dir, ".lock")
    if not os.path.isfile(lock):
        return {"running": False, "pid": None, "stale": False}
    try:
        with open(lock, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read().strip()
        pid = int(content)
    except (OSError, ValueError):
        return {"running": False, "pid": None, "stale": True}
    if pid_alive(pid):
        return {"running": True, "pid": pid, "stale": False}
    return {"running": False, "pid": pid, "stale": True}


def last_activity(kanban_dir, limit=None):
    """List of card summaries across all states, sorted by mtime desc."""
    items = []
    for s in STATES:
        for f in list_cards(kanban_dir, s):
            items.append(card_summary(kanban_dir, s, f))
    items = [it for it in items if it["mtime"] is not None]
    items.sort(key=lambda it: it["mtime"], reverse=True)
    if limit is not None:
        items = items[:limit]
    return items


def board_detail(kanban_dir):
    columns = {}
    for s in STATES:
        columns[s] = [card_summary(kanban_dir, s, f) for f in list_cards(kanban_dir, s)]
    return {
        "counts": {s: len(columns[s]) for s in STATES},
        "columns": columns,
        "dispatcher": dispatcher_status(kanban_dir),
    }
