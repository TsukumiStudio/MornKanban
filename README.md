# MornKanban

File-based kanban dispatch for agent workers. Keep the dialogue agent free: turn every implementation request into a card, hand it to a background dispatcher, and let a review gate decide completion. Cards double as the work history.

## Setup

- Install once: `git clone git@github.com:TsukumiStudio/MornKanban.git ~/git/MornKanban && ln -s ~/git/MornKanban/kanban.sh ~/.local/bin/kanban`
- Per project: `kanban init` creates `.kanban/{todo,doing,review,done,failed}/` plus a `KANBAN.md` policy template (commit them; cards are git history).
- When asked to set up kanban for a project, run `kanban init`, then fill `.kanban/KANBAN.md` with the project's agent/model composition and card policy through dialogue with the user. A second `kanban init` never overwrites an existing `KANBAN.md`.

## Secretary Bootstrap (one-liner)

A secretary agent is started with a single short phrase — e.g. 「**kanban の秘書として待機して**」 (or `/kanban-dispatch 秘書`). Everything else lives in this README and the project's `.kanban/KANBAN.md`, not in the prompt. On that phrase the agent must:

1. Read `.kanban/KANBAN.md` and the Dialogue-Agent Contract below.
2. Reply with **one short line** (e.g. 「秘書セットアップ完了。課題を待機中 (worker=claude/sonnet, -j 2)」) — no plan dumps.
3. For each subsequent user request: split it into cards per policy, start the dispatcher per policy, reply briefly, and return to waiting. Never implement in the dialogue session.
4. React to card-settlement pushes (`KANBAN_NOTIFY_CMD`) per policy: investigate `failed/` and report immediately; summarize when the board settles.

## Per-Project Policy: .kanban/KANBAN.md

`KANBAN.md` is the project's kanban contract, in two layers:

- **Frontmatter** (machine-readable): the CLI loads it as defaults — `backend_order`, `default_backend`, `default_model`, `reviewer`, `review_model`, `threshold`, `max_attempts`, `jobs`, `claude_perms`, `codex_sandbox`. Precedence: environment variable > `KANBAN.md` > built-in default.
- **Body** (secretary policy): how to split cards, which backend/model to route each kind of task to, whether to auto-start the dispatcher, escalation rules. The dialogue agent must read and follow it before cutting cards.

## Dialogue-Agent Contract

When the user assigns implementation work in a project that has `.kanban/`:

1. Read `.kanban/KANBAN.md` and follow its policy; it overrides the generic steps below where they differ.
2. Write a self-contained card: `echo "<full task description>" | kanban add "<title>" [-b claude|codex] [-m model] [-t threshold]`. The worker has no conversation context; include target paths, constraints, and completion conditions.
3. Start `kanban run` (with the policy's `-j`) in the background unless the lock shows it is already running.
4. Return to the user immediately. Do not implement the task in the dialogue session.
5. Report `failed/` cards to the user; they need human judgment.
6. **Verification is delegated too.** After implementation cards merge, cut a follow-up verification card (run the app, click through it, check the acceptance criteria) instead of verifying by hand. The dialogue agent never implements, verifies, or fixes directly — it cards, dispatches, and reports.

Leave `model` empty to use the backend's own default. Model names are backend-specific — never pass a Claude model name to a codex card.

## Backends

| Backend | Worker command | Reviewer command |
| --- | --- | --- |
| `claude` | `claude -p [--model M] --permission-mode <claude_perms>` | `claude -p [--model M]` |
| `codex` | `codex exec --skip-git-repo-check -s <codex_sandbox> [-m M]` | `codex exec --skip-git-repo-check -s read-only [-m M]` |

`auto` (default for both worker and reviewer) resolves to the first installed CLI in `backend_order` (built-in default `claude codex`), so machines with only one CLI keep working unchanged. Codex does not run tests by default; state the test command in the card.

Custom worker commands (`KANBAN_WORKER_CMD`) receive the card's routing as `KANBAN_CARD_MODEL` / `KANBAN_CARD_BACKEND` environment variables, since the override bypasses the built-in model handling.

## Model Policy (default)

Top-tier models (fable / opus) are reserved for the **secretary (dialogue) and design roles**. Hands-on workers and reviewers default to a lower tier — claude cards to `sonnet`, light codex cards to `gpt-5.3-codex-spark` — regardless of backend. Raise a specific card with `-m` only for design-heavy or hard cards, and note why in the card. Projects customize this in `.kanban/KANBAN.md` (`default_model`, `review_model`), but new projects start from this default.

## Herdr Integration (no headless workers)

When the dispatcher runs inside a [Herdr](https://herdr.dev) pane, `herdr-agent-worker.sh` replaces the headless `claude -p` worker/reviewer with a **visible interactive agent in its own pane**, so every parallel card appears in the Herdr sidebar and can be watched or interrupted:

```sh
KANBAN_WORKER_CMD=~/git/MornKanban/herdr-agent-worker.sh \
KANBAN_REVIEW_CMD='env KANBAN_HERDR_ROLE=reviewer /Users/<you>/git/MornKanban/herdr-agent-worker.sh' \
kanban run -j 2; exit
```

The trailing `; exit` closes the dispatcher's pane when the run finishes; without it an empty shell pane is left behind.

The secretary has no board watcher of its own, so card results are pushed to it: set `KANBAN_NOTIFY_CMD` and the dispatcher invokes it as `<cmd> <done|failed> <title>` whenever a card settles (never fatal to the run). `herdr-notify-secretary.sh` is the Herdr hook — it prompts the secretary agent (name from `KANBAN_HERDR_SECRETARY`, default `secretary`) to inspect and report, so `failed/` cards reach the user through the secretary instead of dying silently.

The wrapper splits a pane below the dispatcher, starts an interactive claude (`--permission-mode acceptEdits`, model from the card via `KANBAN_CARD_MODEL`, default `sonnet`), accepts the folder-trust dialog for the card's own worktree, prompts it with the card body, and waits. Because Claude Code renders on the terminal's alternate screen, the final answer cannot be read back from scrollback — the wrapper instructs the agent to also write its answer (the review JSON included) to `.kanban-answer.md` in the worktree, reads that, and deletes it before the card is committed. Panes are closed when the attempt ends.

## Dispatcher Behavior

`kanban run [-j N] [--once]` processes `todo/`; `-j N` runs N cards in parallel (default 1). In a git repository every card gets its own worktree, so parallel cards never touch the same checkout:

1. Create branch `kanban/<id>` and worktree `.kanban/wt/<id>` from the branch checked out at dispatch start.
2. Pipe the card body (task + accumulated rework instructions) into the worker backend, headless, inside the worktree; commit the result on the card's branch.
3. A separate review agent inspects the worktree itself (it must not trust the worker's claims) and outputs `{"score": 0-100, "feedback": "..."}`.
4. `score >= threshold` (default 80) → merge into the base branch (merges are serialized by a lock), delete the branch and worktree, card → `done`. Below threshold, the feedback is appended and the worker retries **in the same worktree**; after `max_attempts` (default 3) the card moves to `failed` and the branch is kept for inspection.
5. A merge conflict also moves the card to `failed` with its branch kept; merge it manually.

Outside a git repository the dispatcher falls back to sequential in-place execution (`-j` > 1 is refused). Worker output tail, every review score, and rework instructions are appended to the card's History section. A restarted dispatcher reclaims cards stranded in `doing/` or `review/`.

## Configuration (environment variables)

Each has a `KANBAN.md` frontmatter counterpart except the last three; the environment wins.

| Variable | Meaning |
| --- | --- |
| `KANBAN_BACKEND_ORDER` | `auto` resolution order (default `claude codex`) |
| `KANBAN_REVIEWER` | Reviewer backend: `auto`, `claude`, or `codex` |
| `KANBAN_REVIEW_MODEL` | Reviewer model (empty = backend default) |
| `KANBAN_CLAUDE_PERMS` | claude worker `--permission-mode` (default `acceptEdits`; use `bypassPermissions` only for fully unattended runs in trusted repositories) |
| `KANBAN_CODEX_SANDBOX` | codex worker `-s` mode (default `workspace-write`) |
| `KANBAN_JOBS` | Default parallelism for `kanban run` (overridden by `-j`) |
| `KANBAN_WORKER_CMD` / `KANBAN_REVIEW_CMD` | Full command overrides; use mock scripts to test state transitions without spending tokens |
| `KANBAN_NOTIFY_CMD` | Hook run as `<cmd> <done\|failed> <title>` when a card settles (see Herdr Integration) |
| `KANBAN_DEBUG` | Write per-job xtrace logs to `.kanban/wt/job.*.trace` |

## Constraints

- One dispatcher per project (`.kanban/.lock`); parallelism comes from `-j`, not from extra dispatchers.
- Parallel cards that edit the same files will collide at merge time and land in `failed`; split cards along file boundaries, or accept manual merges.
- Merging targets the branch checked out when `kanban run` started; keep the main checkout clean while the dispatcher runs, and do not switch branches under it.
- The reviewer scores against the card text. A vague card passes vacuously; completion conditions belong in the card, not in the conversation.
- The script targets bash 3.2 (macOS default). Inside it, never end a loop body with `[[ ... ]] && cmd` — when the test is false the status-1 list trips `set -e` and kills the job silently; use the `if` form.
