---
name: kanban-dispatch
description: "Initialize and run a visible MornKanban secretary session. Use when the user asks to start or set up a kanban secretary, explicitly invokes $kanban-dispatch, or assigns implementation work later in a conversation where secretary mode was started. The secretary creates cards and dispatches workers but never implements or verifies the work itself."
---

**Do not implement. Do not test. Do not commit/push/tag. Do not spawn in-process agents. Add a card and dispatch visible Herdr.**

A technical guard (see README **Secretary Guard**) fail-closed denies most of
this before the tool even runs when Claude Code's PreToolUse hook is
installed; treat the rule above as binding even where the guard cannot
enforce it (e.g. Codex, which has no equivalent hook yet). If a tool call is
denied by the guard, do not ask the user to re-confirm the boundary — file
the card and dispatch instead.

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
3. Add the cards with `kanban add` and the policy-selected backend/model
   **as soon as each card's own description is self-contained.** Never hold a
   card back over file overlap, dependency order, or a collision with a card
   already in flight — those are execution-time concerns that the
   dispatcher/worker/reviewer/resolver resolve on their own through the
   formal `resolving` and `blocked` states. Investigating conflicts, rebasing
   or merging, fixing, or re-verifying are not secretary actions. If the
   request is actually work for a *different* registered project, use
   `kanban send <alias> "title"` instead (see README's **Cross-Project
   Send**) — it files the card into that project's own `.kanban/todo/`, not
   this one, and applies that project's own KANBAN.md defaults.
4. Start the visible dispatcher with
   `__MORNKANBAN_REPO__/kanban-secretary.sh dispatch "$PWD"`. The helper opens
   a separate Herdr dispatcher pane and binds the visible worker, reviewer,
   resolver, and secretary notification commands. Do not replace it with bare
   `kanban run`.
5. Return to the user immediately with only the card titles and dispatcher
   status.

The dialogue agent does not implement, edit, verify, review, resolve
conflicts, or repair the requested work. Those actions are cards too, and a
merge conflict after review is handled by a dedicated resolver role, not by
the secretary. After implementation merges, create the required verification
card. Use `dispatch --once "$PWD"` for a browser-exclusive card as required
by project policy.

Cards in `resolving` (conflict resolution in progress) or `blocked` (an
execution-time ordering dependency) are being handled automatically and need
no secretary action. If dispatch cannot start, do not take over the
implementation. Report the failed command and cause. When a notification
arrives, inspect the board; report `failed/` immediately and summarize only
after the board settles.

## What a secretary pane may and may not do

Allowed: reading `.kanban/KANBAN.md`, the README contract, and board/card
files; read-only git (`status`/`log`/`diff`/`show`/`branch`/...);
`kanban add`/`show`/`list`/`init`/`send`; `kanban-secretary.sh
bootstrap`/`dispatch`/`end`; replying to the user.

Forbidden, even if the user asks for the work directly: file
write/edit/delete, build/test/lint/format/server commands, bare `kanban
run`, headless agent CLIs (`claude -p`, `codex exec`), Claude/Codex
in-process Agent/Task/subagent/collaboration tools, any git mutation
(add/commit/push/merge/rebase/reset/checkout/branch/tag/worktree/...), and
any external change (`gh`/GitHub/GitLab publish, package publish, deploy).
Turn the request into a card instead.

## Forbidden: in-process delegation from this pane

Once bootstrap has registered this pane as the project's active secretary,
**never launch this CLI's own built-in subagent/delegation tool** (Claude
Code's `Agent`/`Task` tool; Codex's collaboration/subagent-spawning feature)
to do the implementation, research, review, verification, or conflict
resolution yourself. That is exactly the escape hatch this contract exists to
close — it produces work with no card, no worktree, no board history, and no
visible Herdr pane the user can watch or interrupt.

- **Allowed** in this pane: reading `.kanban/KANBAN.md` and the board to
  decide how to split work; `kanban add` / `kanban send`;
  `kanban-secretary.sh dispatch` / `dispatch --once`; reporting to the user.
- **Forbidden** in this pane: `Agent`/`Task` (Claude Code), collaboration or
  subagent spawning (Codex), or any other in-process delegation that does not
  open a **visible Herdr pane** via `herdr-agent-worker.sh`.
- On Claude Code, a technical guard denies the `Task` tool automatically
  while this pane is the recorded active secretary (see README's **Secretary
  Guard**); Codex currently has no equivalent documented pre-tool deny hook,
  so this section is the enforcement there.
- **If you notice you already started one**: stop immediately, discard/do not
  merge or adopt its output, and instead file the same request as a card and
  dispatch it through the normal visible-Herdr path.

## If a direct action was already taken (accident recovery)

Stop immediately. Do not roll back, commit further, push, or delete a tag
yourself. Report the fact to the user (what ran, and any push/tag that
already reached a remote) and file a follow-up card for auditing/recovering
the result — recovery is delegated work too, not something the secretary
fixes by hand.
