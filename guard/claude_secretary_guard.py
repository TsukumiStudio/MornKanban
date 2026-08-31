#!/usr/bin/env python3
"""Claude Code PreToolUse hook: deny in-process delegation from a secretary pane.

Installed (see gui/setup_core.py::install_claude_guard) as a PreToolUse hook
matched to the built-in subagent-launching tool ("Task", displayed as
"Agent"). Reads the standard hook JSON on stdin and decides:

  - Not inside Herdr (no HERDR_PANE_ID), or this pane is not the project's
    recorded active secretary pane (guard/secretary_marker.py) -> allow.
    This is the fail-open path: workers, reviewers, resolvers, ordinary
    agents, and other projects' secretaries are never affected.
  - This pane IS the recorded active secretary for the resolved project root
    -> block, with a reason telling the agent to file a card instead.

Exit code is always 0; the decision is communicated entirely through the
`{"decision": ...}` JSON on stdout, matching this machine's other PreToolUse
hooks (see ~/.claude/hooks/unity-commit-guard.sh).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import secretary_marker as marker  # noqa: E402

# The in-process subagent tool is internally named "Task" (Claude Code
# 2.1.x); "Agent" is kept as a defensive alias in case a future version
# renames it. Checked here independently of the settings.json hook matcher
# so the decision is correct even if the matcher is ever loosened.
DENY_TOOL_NAMES = {"Task", "Agent"}

DENY_MESSAGE = (
    "秘書モードでは in-process delegation (Agent/Task サブエージェント) は禁止。"
    "実装・調査・検証が必要なら `kanban add \"<title>\" ...` でカードを起票し、"
    "`kanban-secretary.sh dispatch` で visible Herdr pane へ渡すこと。"
)


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def decide(payload, env):
    tool_name = payload.get("tool_name", "")
    if tool_name not in DENY_TOOL_NAMES:
        return None  # not an in-process delegation tool -> allow

    pane_id = env.get("HERDR_PANE_ID", "")
    if env.get("HERDR_ENV") != "1" or not pane_id:
        return None  # not running inside a Herdr pane at all -> allow

    cwd = payload.get("cwd") or env.get("PWD") or os.getcwd()
    root = marker.project_root_from(cwd)
    if root is None:
        return None  # no .kanban project here -> allow

    if not marker.is_secretary_pane(root, pane_id):
        return None  # this pane is not the recorded secretary -> allow

    marker.append_audit(
        root,
        "deny tool=%s pane=%s cwd=%s" % (tool_name or "?", pane_id, cwd),
    )
    return root


def main():
    payload = _read_stdin_json()
    root = decide(payload, os.environ)
    if root is not None:
        print(json.dumps({"decision": "block", "reason": DENY_MESSAGE}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
