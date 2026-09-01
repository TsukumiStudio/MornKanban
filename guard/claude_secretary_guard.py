#!/usr/bin/env python3
"""Claude Code PreToolUse hook: fail-closed deny for a secretary pane.

Installed (see gui/setup_core.py::install_claude_guard) as a PreToolUse hook
matched against every tool a secretary pane could use to do the work itself
instead of filing a card (see settings.json's `matcher`, kept in sync with
GUARD_MATCHER below). Reads the standard hook JSON on stdin and decides:

  - Not inside Herdr (no HERDR_PANE_ID), or this pane is not the project's
    recorded active secretary pane (guard/secretary_marker.py) -> allow.
    This is the fail-open path for pane identity: workers, reviewers,
    resolvers, ordinary agents, and other projects' secretaries are never
    affected.
  - This pane IS the recorded active secretary for the resolved project
    root -> apply the fail-closed per-tool policy below.

Per-tool policy inside a secretary pane:
  - Task/Agent (in-process subagent/collaboration delegation) -> always deny.
  - Edit/Write/NotebookEdit (direct file mutation) -> always deny. Board
    administration goes through the `kanban` CLI (Bash invocations), never
    through these tools.
  - Bash -> classified by guard/command_classify.py, an allowlist (not a
    denylist) covering managed inspection commands (`kanban inspect`) and the
    `kanban`/`kanban-secretary.sh` subcommands a secretary is allowed to
    run. Anything else - implementation, verification, git mutation,
    GitHub/GitLab mutation, headless agent CLIs, package publish/deploy,
    shell chaining/wrapper/absolute-path bypass attempts - is denied by
    default.
  - Any other tool name (Read, Grep, Glob, WebFetch, WebSearch, ...) ->
    allow; this hook only ever tightens the tools listed above.

Exit code is always 0; the decision is communicated entirely through the
`{"decision": ...}` JSON on stdout, matching this machine's other
PreToolUse hooks (see ~/.claude/hooks/unity-commit-guard.sh).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import command_classify  # noqa: E402
import secretary_marker as marker  # noqa: E402

# The in-process subagent tool is internally named "Task" (Claude Code
# 2.1.x); "Agent" is kept as a defensive alias in case a future version
# renames it. Checked here independently of the settings.json hook matcher
# so the decision is correct even if the matcher is ever loosened.
DENY_DELEGATION_TOOLS = {"Task", "Agent"}
DENY_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
BASH_TOOLS = {"Bash"}

CARD_HINT = (
    'board変更は `kanban ...` を使い、'
    "`kanban-secretary.sh dispatch` で visible Herdr pane へ渡すこと。"
)

DENY_MESSAGES = {
    "delegation": "秘書モードでは in-process delegation (Agent/Task サブエージェント) は禁止。" + CARD_HINT,
    "edit": "秘書モードではproject/boardファイルの直接編集・作成・削除は禁止。" + CARD_HINT,
    "bash": "秘書モードでは実装・検証・Git変更・外部公開コマンドの直接実行は禁止 (%s)。" + CARD_HINT,
}


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _secretary_root(payload, env):
    """Resolve the project root when this pane is its recorded secretary.

    Returns None (allow) whenever pane identity can't be confirmed, matching
    secretary_marker.is_secretary_pane's fail-open contract for identity.
    """
    pane_id = env.get("HERDR_PANE_ID", "")
    if env.get("HERDR_ENV") != "1" or not pane_id:
        return None

    cwd = payload.get("cwd") or env.get("PWD") or os.getcwd()
    root = marker.project_root_from(cwd)
    if root is None:
        return None

    if not marker.is_secretary_pane(root, pane_id):
        return None

    return root


def decide(payload, env):
    """Return (deny: bool, category: str, detail: str, root: str|None)."""
    tool_name = payload.get("tool_name", "")

    if tool_name in DENY_DELEGATION_TOOLS:
        category, detail = "delegation", tool_name
    elif tool_name in DENY_EDIT_TOOLS:
        category, detail = "edit", tool_name
    elif tool_name in BASH_TOOLS:
        command = (payload.get("tool_input") or {}).get("command", "")
        allowed, reason = command_classify.classify(command)
        if allowed:
            return False, None, reason, None
        category, detail = "bash", reason
    else:
        return False, None, None, None

    root = _secretary_root(payload, env)
    if root is None:
        return False, None, None, None
    return True, category, detail, root


def main():
    payload = _read_stdin_json()
    deny, category, detail, root = decide(payload, os.environ)
    if deny:
        marker.append_audit(
            root,
            "deny tool=%s category=%s detail=%s"
            % (payload.get("tool_name", "?"), category, detail),
        )
        message = DENY_MESSAGES[category]
        if "%s" in message:
            message = message % detail
        print(json.dumps({"decision": "block", "reason": message}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
