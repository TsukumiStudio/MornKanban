"""Project/pane-scoped "active secretary" marker.

`kanban-secretary.sh bootstrap` records which Herdr pane is currently acting
as the dialogue secretary for a given project root. The marker is what lets a
tool-invocation guard (see claude_secretary_guard.py) tell "this pane is the
secretary, direct implementation/verification/git/publish tools are
forbidden here" apart from any other pane (workers, reviewers, ordinary
agents, other projects).

python3 stdlib only, matching the rest of MornKanban's distribution
constraints.
"""
import json
import os
import tempfile
import time

MARKER_DIR_NAME = ".secretary-guard"
MARKER_FILE_NAME = "marker.json"
AUDIT_FILE_NAME = "audit.log"
AUDIT_MAX_LINES = 200


def marker_dir(project_root):
    return os.path.join(project_root, ".kanban", MARKER_DIR_NAME)


def marker_path(project_root):
    return os.path.join(marker_dir(project_root), MARKER_FILE_NAME)


def audit_path(project_root):
    return os.path.join(marker_dir(project_root), AUDIT_FILE_NAME)


def write_marker(project_root, pane_id, secretary_name, pid=None):
    """Atomically (create-or-replace) record this pane as the active secretary."""
    d = marker_dir(project_root)
    os.makedirs(d, exist_ok=True)
    data = {
        "pane_id": pane_id,
        "secretary_name": secretary_name,
        "project_root": os.path.realpath(project_root),
        "pid": pid if pid is not None else os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".marker.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, marker_path(project_root))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return data


def read_marker(project_root):
    path = marker_path(project_root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def clear_marker(project_root):
    try:
        os.remove(marker_path(project_root))
        return True
    except OSError:
        return False


def is_secretary_pane(project_root, pane_id):
    """True when `pane_id` is the recorded active secretary for this project.

    A missing/unreadable marker, or a pane_id that does not match, means "not
    the secretary" - the guard must fail open toward *allowing* tools rather
    than blocking an unrelated pane (worker/reviewer/other project).
    """
    if not pane_id:
        return False
    marker = read_marker(project_root)
    if not marker:
        return False
    return marker.get("pane_id") == pane_id


def append_audit(project_root, message):
    """Append one capped audit line. No secrets, no conversation text."""
    d = marker_dir(project_root)
    os.makedirs(d, exist_ok=True)
    path = audit_path(project_root)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    line = "%s %s\n" % (ts, message)
    try:
        lines = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        lines.append(line)
        lines = lines[-AUDIT_MAX_LINES:]
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".audit.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except OSError:
        pass


def project_root_from(start):
    """Walk up from `start` looking for a `.kanban` directory. None if absent."""
    d = os.path.realpath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".kanban")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
