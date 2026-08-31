"""Read-only access to a single project's `.kanban` board.

Mirrors the card format written by kanban.sh (YAML-ish frontmatter delimited
by `---` lines, followed by a Markdown body with a `## History` section) but
never writes anything back.
"""
import os

STATES = ["todo", "doing", "review", "resolving", "blocked", "done", "failed"]


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


def _parse_bool_lenient(v):
    v = (v or "").strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


def project_review_default(kanban_dir):
    """Effective review_enabled for a card still at review_enabled=auto.

    Mirrors kanban.sh's priority chain (env > KANBAN.md project setting >
    built-in true) so the board doesn't show a stale "Review: ON" for cards
    that haven't been picked up by the dispatcher (and thus resolved/frozen)
    yet. Card-level overrides are handled by the caller before falling back
    to this.
    """
    env = _parse_bool_lenient(os.environ.get("KANBAN_REVIEW_ENABLED"))
    if env is not None:
        return env
    cfg = os.path.join(kanban_dir, "KANBAN.md")
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return True
    fm, _ = parse_card(text)
    project = _parse_bool_lenient(fm.get("review_enabled"))
    return True if project is None else project


def card_summary(kanban_dir, state, filename):
    path = os.path.join(kanban_dir, state, filename)
    fm, _ = read_card(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    rv = fm.get("review_enabled", "auto")
    if rv == "auto":
        rv = "true" if project_review_default(kanban_dir) else "false"
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
        "last_timings": fm.get("last_timings", ""),
        "created": fm.get("created", ""),
        # A "blocked" card reached via exhausted review/worker infrastructure
        # retries (agent_not_found, pane lost, timeout, ...) is a stopped
        # pipeline, not a code-quality failure -- surface that distinction
        # instead of leaving it indistinguishable from the older "worker
        # reported an ordering dependency" blocked kind.
        "blocked_kind": fm.get("blocked_kind", ""),
        "review_infra_retries": fm.get("review_infra_retries", ""),
        "worker_infra_retries": fm.get("worker_infra_retries", ""),
        "review_enabled": rv,
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
