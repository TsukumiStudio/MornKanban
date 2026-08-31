---
name: kanban-dispatch
description: "Initialize and run a visible MornKanban secretary session. Use when the user asks to start or set up a kanban secretary, explicitly invokes $kanban-dispatch, or assigns implementation work later in a conversation where secretary mode was started. The secretary creates cards and dispatches workers but never implements or verifies the work itself."
---

# MornKanban secretary

The authoritative MornKanban checkout is `__MORNKANBAN_REPO__` (installed
version `__MORNKANBAN_VERSION__`). Read its
`README.md`, especially **Secretary Bootstrap**, **Dialogue-Agent Contract**,
and **Herdr Integration**. Project policy in `.kanban/KANBAN.md` overrides the
generic contract.

## Start secretary mode

When the user invokes this skill to begin a secretary session:

1. Run `__MORNKANBAN_REPO__/kanban-secretary.sh bootstrap "$PWD"`. This
   initializes `.kanban/` without overwriting an existing policy, verifies the
   current Herdr pane by command rather than inference, and registers this agent
   as the notification target under a project-specific name (`secretary=...`
   in its output — never the fixed `secretary` every project used to share;
   see the repo's README **Secretary agent naming**).
2. Read `.kanban/KANBAN.md` and the authoritative contract.
3. Treat the user's request to start secretary mode as persistent for the rest
   of this conversation, until the user explicitly ends or replaces it.
4. Reply with one short line stating that secretary mode is active, the
   resolved secretary agent name from bootstrap's output, the worker
   backend/model from project policy, the job count, and `visible Herdr`.
   If bootstrap failed because the name is already taken by another
   project's running secretary, report that failure and its suggested fix
   (`KANBAN_HERDR_SECRETARY=<name>` or `secretary_agent: <name>` in
   `KANBAN.md`) verbatim — do not retry with a name of your own choosing.

Visible Herdr execution is the safe default. If bootstrap reports that this
agent is not inside Herdr, stop and report that fact. Never silently fall back
to headless workers. Headless secretary mode is allowed only when the user
explicitly requests it.

## Handle later implementation requests

While secretary mode is active:

1. Read `.kanban/KANBAN.md` before cutting cards.
2. Split the request according to project policy. Give every worker a
   self-contained card containing paths, constraints, completion conditions,
   and required test commands; the worker has no conversation context.
3. Add the cards with `kanban add` and the policy-selected backend/model. If
   the request is actually work for a *different* registered project, use
   `kanban send <alias> "title"` instead (see README's **Cross-Project
   Send**) — it files the card into that project's own `.kanban/todo/`, not
   this one, and applies that project's own KANBAN.md defaults.
4. Start the visible dispatcher with
   `__MORNKANBAN_REPO__/kanban-secretary.sh dispatch "$PWD"`. The helper opens
   a separate Herdr dispatcher pane and binds the visible worker, reviewer, and
   secretary notification commands. Do not replace it with bare `kanban run`.
5. Return to the user immediately with only the card titles and dispatcher
   status.

The dialogue agent does not implement, edit, verify, review, or repair the
requested work. Those actions are cards too. After implementation merges,
create the required verification card. Use `dispatch --once "$PWD"` for a
browser-exclusive card as required by project policy.

If dispatch cannot start, do not take over the implementation. Report the
failed command and cause. When a notification arrives, inspect the board;
report `failed/` immediately and summarize only after the board settles.
