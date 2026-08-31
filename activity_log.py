#!/usr/bin/env python3
"""Bounded, prompt-free JSONL activity log for visible Kanban agents."""
import argparse
import datetime
import fcntl
import json
import os
import tempfile
import time

MAX_EVENTS = 1000
TEXT_LIMIT = 200


def _clean(value):
    return str(value or "").replace("\n", " ").replace("\r", " ")[:TEXT_LIMIT]


def append_event(path, values, max_events=MAX_EVENTS):
    """Append one sanitized event while keeping only the newest entries."""
    path = os.path.realpath(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    event = {
        "timestamp": time.time(),
        "at": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    for key in (
        "event", "card_id", "role", "attempt", "backend", "model",
        "agent_name", "pane_id", "status",
    ):
        event[key] = _clean(values.get(key))
    try:
        event["duration_secs"] = max(0, int(values.get("duration_secs") or 0))
    except (TypeError, ValueError):
        event["duration_secs"] = 0

    lock_path = path + ".lock"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        lines = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            pass
        lines.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        lines = lines[-max_events:]
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".activity.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return event


def main(argv=None):
    parser = argparse.ArgumentParser(description="append one MornKanban agent activity event")
    parser.add_argument("path")
    for name in (
        "event", "card-id", "role", "attempt", "backend", "model",
        "agent-name", "pane-id", "status", "duration-secs",
    ):
        parser.add_argument("--" + name, default="")
    args = parser.parse_args(argv)
    append_event(args.path, vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
