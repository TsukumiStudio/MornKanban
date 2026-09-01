#!/usr/bin/env python3
"""Fixed board summary plus scrolling dispatcher log for a Herdr pane."""
import argparse
import curses
import datetime
from collections import deque
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata


STATES = ("todo", "doing", "review", "resolving", "blocked", "done", "failed")
ACTIVE_STATES = ("doing", "review", "resolving")
STATE_ORDER = {state: index for index, state in enumerate(STATES)}
STATE_LABEL = {
    "new": "NEW",
    "todo": "TODO",
    "doing": "DOING",
    "review": "REVIEW",
    "resolving": "RESOLVE",
    "blocked": "BLOCK",
    "done": "DONE",
    "failed": "FAIL",
}
STATE_MARK = {"doing": "▶", "review": "◇", "resolving": "↻"}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(value):
    return CONTROL_RE.sub("", ANSI_RE.sub("", str(value or "")).replace("\t", "  "))


def display_width(value):
    width = 0
    for char in clean_text(value):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def fit_text(value, width):
    value = clean_text(value)
    if width <= 0:
        return ""
    if display_width(value) <= width:
        return value
    if width == 1:
        return "…"
    out = []
    used = 0
    limit = width - 1
    for char in value:
        char_width = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        )
        if used + char_width > limit:
            break
        out.append(char)
        used += char_width
    return "".join(out) + "…"


def wrap_text(value, width):
    value = clean_text(value)
    if not value:
        return [""]
    lines = []
    current = []
    used = 0
    for char in value:
        char_width = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        )
        if current and used + char_width > width:
            lines.append("".join(current))
            current = []
            used = 0
        current.append(char)
        used += char_width
    if current:
        lines.append("".join(current))
    return lines


def _frontmatter(path):
    values = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            if handle.readline().strip() != "---":
                return values
            for line in handle:
                line = line.rstrip("\n")
                if line == "---":
                    break
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    return values


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _active_agents(path):
    active = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                card_id = event.get("card_id")
                if not card_id:
                    continue
                if event.get("event") == "agent_started":
                    active[card_id] = event
                elif event.get("event") in ("answer_accepted", "infra_error"):
                    current = active.get(card_id)
                    if current and current.get("agent_name") == event.get("agent_name"):
                        active.pop(card_id, None)
    except OSError:
        pass
    return active


def scan_board(root):
    cards = {}
    counts = {state: 0 for state in STATES}
    kanban = os.path.join(os.path.realpath(root), ".kanban")
    live_agents = _active_agents(os.path.join(kanban, "activity.jsonl"))
    for state in STATES:
        directory = os.path.join(kanban, state)
        try:
            names = sorted(name for name in os.listdir(directory) if name.endswith(".md"))
        except OSError:
            names = []
        counts[state] = len(names)
        for name in names:
            path = os.path.join(directory, name)
            values = _frontmatter(path)
            card_id = values.get("id") or name.rsplit(".md", 1)[0]
            agent = live_agents.get(card_id, {})
            cards[card_id] = {
                "id": card_id,
                "title": values.get("title") or card_id,
                "state": state,
                "attempts": _integer(values.get("attempts"), 0),
                "max_attempts": _integer(values.get("max_attempts"), 0),
                "backend": agent.get("backend") or "unknown",
                "model": agent.get("model") or "unknown",
                "effort": agent.get("effort") or "unknown",
            }
    active = sorted(
        (card for card in cards.values() if card["state"] in ACTIVE_STATES),
        key=lambda card: (STATE_ORDER[card["state"]], card["id"]),
    )
    return {"counts": counts, "cards": cards, "active": active}


def diff_board(before, after, now=None):
    now = now or datetime.datetime.now()
    moves = []
    old_cards = before.get("cards", {})
    for card_id, card in after.get("cards", {}).items():
        previous = old_cards.get(card_id)
        old_state = previous["state"] if previous else "new"
        if old_state == card["state"]:
            continue
        moves.append({
            "at": now.strftime("%H:%M:%S"),
            "from": old_state,
            "to": card["state"],
            "title": card["title"],
        })
    return sorted(moves, key=lambda move: (move["to"], move["title"]))


def render_header(snapshot, transitions, width, max_lines=10, status="RUNNING", now=None):
    width = max(20, width)
    max_lines = max(6, max_lines)
    now = now or datetime.datetime.now()
    counts = snapshot["counts"]
    active = snapshot["active"]
    active_slots = min(4, max(1, max_lines - 6))
    recent_slots = max_lines - 5 - active_slots
    visible_active = active[:active_slots]
    visible_recent = list(transitions)[-recent_slots:] if recent_slots else []

    lines = [
        "MornKanban dispatcher  ● %-9s %s" % (status, now.strftime("%H:%M:%S")),
        (
            "○ TODO:%d  ▶ RUN:%d  ◇ REV:%d  ↻ FIX:%d  ‖ HOLD:%d  "
            "✓ DONE:%d  ! FAIL:%d"
        ) % tuple(counts[state] for state in STATES),
        "ACTIVE %d/%d" % (len(visible_active), len(active)),
    ]
    if visible_active:
        for card in visible_active:
            attempts = "%d/%d" % (card["attempts"], card["max_attempts"] or 0)
            lines.append(
                "%s %-7s [%s] AI:%s  MODEL:%s  EFFORT:%s │ %s"
                % (
                    STATE_MARK[card["state"]], STATE_LABEL[card["state"]], attempts,
                    card["backend"], card["model"], card["effort"], card["title"],
                )
            )
    else:
        lines.append("— 実行中のカードなし")
    while len(lines) < 3 + active_slots:
        lines.append("")

    lines.append("RECENT MOVES")
    if visible_recent:
        for move in visible_recent:
            lines.append(
                "%s  %s → %s  %s"
                % (
                    move["at"],
                    STATE_LABEL.get(move["from"], "UNKNOWN"),
                    STATE_LABEL.get(move["to"], "UNKNOWN"),
                    move["title"],
                )
            )
    else:
        lines.append("— 起動後の移動を待機中")
    while len(lines) < max_lines - 1:
        lines.append("")
    lines.append("─ LIVE LOG " + "─" * max(0, width - 11))
    return [fit_text(line, width) for line in lines[:max_lines]]


def _start(command, root):
    return subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )


def run_plain(root, log_path, command):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    process = _start(command, root)
    with open(log_path, "w", encoding="utf-8") as log:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
    return process.wait()


def _stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()


def _reader(stream, output):
    try:
        for line in stream:
            output.put(line.rstrip("\r\n"))
    finally:
        output.put(None)


def _draw(screen, snapshot, transitions, logs, status):
    rows, cols = screen.getmaxyx()
    width = max(20, cols - 1)
    header_height = min(11, max(6, rows - 4))
    header = render_header(snapshot, transitions, width, header_height, status=status)
    screen.erase()
    for row, line in enumerate(header):
        attr = curses.A_NORMAL
        if row == 0:
            attr = curses.A_BOLD
            if status.startswith("ERROR") and curses.has_colors():
                attr |= curses.color_pair(2)
            elif curses.has_colors():
                attr |= curses.color_pair(1)
        elif line.startswith(("▶", "◇", "↻")) and curses.has_colors():
            attr = curses.color_pair(3)
        try:
            screen.addstr(row, 0, line, attr)
        except curses.error:
            pass

    log_rows = max(0, rows - header_height)
    wrapped = []
    for line in logs:
        wrapped.extend(wrap_text(line, width))
    for offset, line in enumerate(wrapped[-log_rows:]):
        try:
            screen.addstr(header_height + offset, 0, fit_text(line, width))
        except curses.error:
            pass
    screen.refresh()


def run_tui(screen, root, log_path, command):
    curses.curs_set(0)
    screen.nodelay(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    process = _start(command, root)
    output = queue.Queue()
    threading.Thread(target=_reader, args=(process.stdout, output), daemon=True).start()
    logs = deque(maxlen=500)
    transitions = deque(maxlen=20)
    snapshot = scan_board(root)
    next_scan = time.monotonic()
    reader_done = False
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            while True:
                while True:
                    try:
                        line = output.get_nowait()
                    except queue.Empty:
                        break
                    if line is None:
                        reader_done = True
                        break
                    logs.append(line)
                    log.write(line + "\n")
                    log.flush()

                now = time.monotonic()
                if now >= next_scan:
                    current = scan_board(root)
                    transitions.extend(diff_board(snapshot, current))
                    snapshot = current
                    next_scan = now + 1.0

                code = process.poll()
                status = "RUNNING" if code is None else ("DONE" if code == 0 else "ERROR %d" % code)
                _draw(screen, snapshot, transitions, logs, status)
                if code is not None and reader_done:
                    time.sleep(0.8)
                    return code
                time.sleep(0.1)
    except KeyboardInterrupt:
        _stop_process(process)
        return 130
    finally:
        _stop_process(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description="MornKanban graphical dispatcher pane")
    parser.add_argument("--root", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        parser.error("a dispatcher command is required after --")
    root = os.path.realpath(args.root)
    log_path = os.path.realpath(args.log)
    size = shutil.get_terminal_size(fallback=(80, 24))
    use_tui = (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.environ.get("TERM") != "dumb"
        and size.columns >= 40
        and size.lines >= 10
    )
    if not use_tui:
        return run_plain(root, log_path, command)
    return curses.wrapper(run_tui, root, log_path, command)


if __name__ == "__main__":
    raise SystemExit(main())
