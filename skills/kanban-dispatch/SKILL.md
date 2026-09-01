---
name: kanban-dispatch
description: "Initialize and run a visible MornKanban secretary session. Use when the user asks to start or set up a kanban secretary, explicitly invokes $kanban-dispatch, or assigns implementation work later in a conversation where secretary mode was started. The secretary creates cards and dispatches workers but never implements or verifies the work itself."
---

<!-- MORNKANBAN_INSTALLER_MANAGED -->

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
   backend/model from project policy, and the job count.
   If bootstrap failed because the name is already taken by another
   project's running secretary, report that failure and its suggested fix
   (`KANBAN_HERDR_SECRETARY=<name>` or `secretary_agent: <name>` in
   `KANBAN.md`) verbatim — do not retry with a name of your own choosing.

Herdr is required. If bootstrap reports that this agent is not inside Herdr,
stop and report that fact. There is no headless secretary mode: do not ask the
user to choose an execution mode and never fall back to headless workers.

## Handle later implementation requests

While secretary mode is active:

1. Read `.kanban/KANBAN.md` before cutting cards.
2. Split the request according to project policy. Give every worker a
   self-contained card containing paths, constraints, completion conditions,
   and required test commands; the worker has no conversation context.
   A diagnosis/investigation request is a special small card: use
   `kanban add --diagnose` (or `kanban send ... --diagnose`), keep it
   read-only, target a supported conclusion in 5 minutes, and stop at the
   10-minute hard maximum with partial evidence plus `BLOCKED:
   scope/timebox`. Its output is evidence, likely cause, uncertainty, and one
   small next action — not implementation. File the fix as a separate card
   after diagnosis unless the user explicitly asked to diagnose and fix in
   the same request. Never inflate a diagnosis with related benchmarks, UI,
   refactors, mutation tests, or exhaustive adjacent improvements; one card
   has one main result and one main responsibility.
3. Add the cards with `kanban add` and the policy-selected backend/model.
   Set card effort with `-e`: for `gpt-5.6-sol`, use `medium` for normal work
   and `high` for complex work unless project policy gives a different reason;
   do not let every worker/reviewer/resolver inherit a shared `xhigh` by default.
   Add each card **as soon as its own description is self-contained.** Never
   hold a card back over file overlap, dependency order inferred by the
   secretary, or another running card. Never invent a dependency or release
   gate: only the user or project policy may require one, recorded with
   `--depends-on <card-id>`. Stop only explicitly dependent,
   irreversible external work such as push/deploy; still file independent
   local test, build, and status-check cards. A worker that discovers a real
   undeclared dependency must return `BLOCKED: <reason>` as its first line,
   never deliberately fail so that attempts/reviews are wasted. Investigating
   conflicts, rebasing or merging, fixing, or re-verifying are not secretary
   actions. If the request is actually work for a *different* registered project, use
   `kanban send <alias> "title"` instead (see README's **Cross-Project
   Send**) — it files the card into that project's own `.kanban/todo/`, not
   this one, and applies that project's own KANBAN.md defaults.
   If your own malformed `kanban add` nevertheless creates an unintended
   card, immediately run `kanban remove <card-id>` and then add the correct
   card; do not ask the user to run raw `rm`. This is also allowed when the
   user explicitly asks to discard an unstarted card. `kanban remove` is
   intentionally limited to `todo` and must never be used to erase execution
   history.
   Cards must never ask a visible worker to choose interactively. State a
   decision in the card when policy already answers it; otherwise require
   `BLOCKED: <needed decision and reason>` instead of `AskUserQuestion` or a
   numbered choice UI.
4. Start the visible dispatcher with
   `__MORNKANBAN_REPO__/kanban-secretary.sh dispatch "$PWD"`. The helper opens
   the dispatcher below the secretary, places AI panes on the right and stacks
   additional AIs downward, and binds the visible worker, reviewer, resolver,
   and secretary notification commands. Its fixed status rows show the live
   AI backend/model/effort; `unknown` means the agent inherited a value the
   wrapper cannot observe. Do not replace it with bare
   `kanban run`. New boards default to `jobs: 4`; honor the project's live
   `jobs:` value or an explicit user override, and impose no MornKanban upper
   cap on a positive worker count.
5. Return to the user immediately with only the card titles and dispatcher
   pane launch status. A successful pane launch does not prove that the
   dispatcher kept running or that any card started.

The dialogue agent does not implement, edit, verify, review, resolve
conflicts, or repair the requested work. Those actions are cards too, and a
merge conflict after review is handled by a dedicated resolver role, not by
the secretary. After implementation merges, create the required verification
card. Use `dispatch --once "$PWD"` for a browser-exclusive card as required
by project policy.

Cards in `resolving` or `blocked` are handled by their structured state. A
declared dependency resumes only after its target reaches `done`; a
`review_infra` block means verification was not performed. If dispatch cannot
start, do not take over implementation. A `dispatcher_failed` notification
means the pane command exited nonzero: read `.kanban/wt/dispatcher.log`,
report its actual error, and do not claim the cards ran. Never improvise a
recovery with `git init`, `commit`, or any other Git mutation. On card
notifications, inspect
`failure_kind`/`blocked_kind` and History: `failed` is a work-process failure,
not automatically a product failure. Distinguish product defects,
infrastructure failures, and unverified results. For missing verification,
report **unverified / user decision required** instead of inferring that
deployment is prohibited.
An `agent_question` becomes `blocked_kind: user_input` without consuming an
attempt. Report the requested decision to the user and, after it is resolved,
run `kanban resume <id>`. Never select an interactive option for the user.

## What a secretary pane may and may not do

Allowed: reading `.kanban/KANBAN.md`, the README contract, and board/card
files; read-only git (`status`/`log`/`diff`/`show`/`branch`/...);
`kanban add`/`remove`/`config set`/`show`/`list`/`init`/`send`;
`kanban-secretary.sh
bootstrap`/`dispatch`/`end`; replying to the user.

When the user asks to change board operation, use `kanban config set` rather
than editing `KANBAN.md`: secretary-editable keys are `jobs`,
`default_backend`, `default_model`, `reviewer`, `review_model`, `resolver`,
and `resolve_model`. `jobs` is picked up live; card defaults affect newly
created cards, and reviewer/resolver routing changes require the next
dispatcher start. Per-card backend/model/effort still belongs on
`kanban add -b/-m/-e`. Do not change these settings opportunistically.

Forbidden, even if the user asks for the work directly: direct project or
board file write/edit/delete (including raw `rm`), build/test/lint/format/server commands, bare `kanban
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
  decide how to split work; `kanban add` / `kanban remove` / `kanban config` / `kanban send`;
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
