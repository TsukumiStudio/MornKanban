# MornKanban

File-based kanban dispatch for agent workers. Keep the dialogue agent free: turn every implementation request into a card, hand it to a background dispatcher, and let a review gate decide completion. Cards double as the work history.

## Setup

- Install once: `git clone git@github.com:TsukumiStudio/MornKanban.git ~/git/MornKanban && ~/git/MornKanban/kanban-setup.sh install` (creates/repairs the `~/.local/bin/kanban` symlink and the Claude Code/Codex skills; `ln -s ~/git/MornKanban/kanban.sh ~/.local/bin/kanban` alone still works but skips the skill install).
- Per project: `kanban init` creates `.kanban/{todo,doing,review,done,failed}/` plus a `KANBAN.md` policy template (commit them; cards are git history).
- When asked to set up kanban for a project, run `kanban init`, then fill `.kanban/KANBAN.md` with the project's agent/model composition and card policy through dialogue with the user. A second `kanban init` never overwrites an existing `KANBAN.md`.

## Setup Wizard

- `./kanban-setup.sh` runs an interactive CLI wizard: it shows the environment status and asks once — `y` installs (CLI symlink + Claude Code/Codex skill, idempotent), `u` uninstalls (removes only what this installer created), `N` does nothing.
- `./kanban-setup.sh {install|update|uninstall|version}` runs the same actions non-interactively. This is how a first install works, before `~/.local/bin/kanban` exists — `kanban.sh {install|update|uninstall|version}` is equivalent once the symlink is in place.
- Project onboarding is **not** part of the wizard: open a Herdr pane in the project and invoke **`$kanban-dispatch 秘書として開始`**. The skill initializes the board, verifies visible Herdr execution, and makes the current conversation the secretary (see Secretary Bootstrap below).
- Requirements: bash + python3 (the same as `kanban.sh` itself).

## Versioning and Updates

- The repository ships a committed `VERSION` file (semantic `X.Y.Z`) as the single source of truth for the installed distribution version.
- `kanban --version` prints only the locally installed version, read from `VERSION` next to the resolved script — no network access.
- `kanban version` prints the current version, the latest published version, and the comparison state (`up-to-date` / `update-available` / `local-ahead` / `unknown`). Because this repository currently has no tags or GitHub Releases, "latest published" means the raw `VERSION` file on `main` on GitHub. Set `KANBAN_VERSION_URL` (a `file://` URL works) to override the source, e.g. for tests.
- `kanban update` refuses a dirty, detached, or non-`main` checkout (it never discards or stashes user changes), then runs `git pull --ff-only origin main` and reinstalls the CLI symlink and skills from the freshly pulled code.
- `kanban install` / `kanban uninstall` (re)create or remove `~/.local/bin/kanban` and the Claude Code/Codex `kanban-dispatch` skills; uninstall only removes installer-managed files and leaves the repository checkout and all project boards untouched.
- The installed skill's rendered `SKILL.md` embeds both the resolved repository path and the distribution version at install/update time.
- `kanban.sh` resolves its own real location by following symlinks (e.g. the `~/.local/bin/kanban` entry point), so these commands work whether invoked directly or through the installed symlink.

## Secretary Bootstrap (one-liner)

A secretary agent is started with **`$kanban-dispatch 秘書として開始`** (the phrase 「**kanban の秘書として待機して**」 also triggers the skill). Everything else lives in the installed skill, this README, and the project's `.kanban/KANBAN.md`, not in the prompt. On that request the agent must:

1. Run `kanban-secretary.sh bootstrap` from the MornKanban checkout. It runs `kanban init` when needed, verifies the current Herdr pane, and registers this agent as the notification target. It never overwrites an existing `KANBAN.md`.
2. Read `.kanban/KANBAN.md` and the Dialogue-Agent Contract below.
3. Reply with **one short line** (e.g. 「秘書モード開始。課題を待機中 (worker=claude/sonnet, -j 2, visible Herdr)」) — no plan dumps.
4. Treat the bootstrap request as active for the rest of the conversation. For each subsequent user request: split it into cards per policy, start the visible dispatcher, reply briefly, and return to waiting. Never implement in the dialogue session.
5. React to card-settlement pushes (`KANBAN_NOTIFY_CMD`) per policy: investigate `failed/` and report immediately; summarize when the board settles.

Visible Herdr workers are the secretary default. The bootstrap must test `HERDR_ENV`, `HERDR_PANE_ID`, and the current pane through the Herdr CLI. It must not infer availability from the prompt, and it must not silently fall back to headless workers. Headless secretary mode requires an explicit user request.

## Per-Project Policy: .kanban/KANBAN.md

`KANBAN.md` is the project's kanban contract, in two layers:

- **Frontmatter** (machine-readable): the CLI loads it as defaults — `backend_order`, `default_backend`, `default_model`, `reviewer`, `review_model`, `threshold`, `max_attempts`, `jobs`, `claude_perms`, `codex_sandbox`. Precedence: environment variable > `KANBAN.md` > built-in default.
- **Body** (secretary policy): how to split cards, which backend/model to route each kind of task to, whether to auto-start the dispatcher, escalation rules. The dialogue agent must read and follow it before cutting cards.

## Dialogue-Agent Contract

When the user assigns implementation work in a project that has `.kanban/`:

1. Read `.kanban/KANBAN.md` and follow its policy; it overrides the generic steps below where they differ.
2. Write a self-contained card: `echo "<full task description>" | kanban add "<title>" [-b claude|codex] [-m model] [-t threshold]`. The worker has no conversation context; include target paths, constraints, and completion conditions.
3. In visible secretary mode, start `~/git/MornKanban/kanban-secretary.sh dispatch` unless the lock shows a dispatcher is already running. The helper opens a separate Herdr dispatcher pane and binds worker, reviewer, and notification commands. Never substitute bare `kanban run`, which starts invisible headless workers. A nonstandard checkout uses its own absolute helper path.
4. Return to the user immediately. Do not implement the task in the dialogue session.
5. Report `failed/` cards to the user; they need human judgment.
6. **Verification is delegated too.** After implementation cards merge, cut a follow-up verification card (run the app, click through it, check the acceptance criteria) instead of verifying by hand. The dialogue agent never implements, verifies, or fixes directly — it cards, dispatches, and reports.
7. **Browser role is exclusive.** Ordinary workers must not touch browser-automation tools; verification is curl/CLI level. When a check genuinely needs a browser, cut a dedicated browser-verification card and run it **alone** (`kanban-secretary.sh dispatch --once`) — at most one browser-role agent exists at a time, and no other agent (the dialogue agent included) touches browser tools while it runs.

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

`kanban-secretary.sh dispatch` opens the dispatcher in a sibling [Herdr](https://herdr.dev) pane and binds `herdr-agent-worker.sh` for both workers and reviewers. Every parallel card appears as a **visible interactive agent in its own pane**, so it can be watched or interrupted:

```sh
~/git/MornKanban/kanban-secretary.sh dispatch
```

The helper verifies that it is called from the current Herdr pane, keeps focus on the secretary, uses the project's `jobs` setting, and closes the dispatcher's pane when the run finishes. `dispatch --once` preserves the browser-role exclusivity contract.

The secretary has no board watcher of its own, so card results are pushed to it: set `KANBAN_NOTIFY_CMD` and the dispatcher invokes it as `<cmd> <done|failed> <title>` whenever a card settles (never fatal to the run). `herdr-notify-secretary.sh` is the Herdr hook — it prompts the secretary agent (name from `KANBAN_HERDR_SECRETARY`, default `secretary`) to inspect and report, so `failed/` cards reach the user through the secretary instead of dying silently.

The wrapper splits a pane below the dispatcher and starts an interactive agent whose `--kind` (`claude` or `codex`) follows the card's own routing — worker backend from `KANBAN_CARD_BACKEND`, reviewer backend from `KANBAN_REVIEWER`, `auto` resolved via `KANBAN_BACKEND_ORDER` exactly like the headless path. A Claude worker gets `--permission-mode acceptEdits` and a model (from `KANBAN_CARD_MODEL`, default `sonnet`); a Claude reviewer gets no permission-mode override, keeping it read-only-ish. A Codex worker gets `-s <codex_sandbox> -a never`; a Codex reviewer gets `-s read-only -a never`; either only adds `-m <model>` when one is set — Codex never inherits the Claude default of `sonnet`. It accepts the folder-trust dialog for the card's own worktree, prompts it with the card body, and waits. Because both Claude Code and Codex render on the terminal's alternate screen, the final answer cannot be read back from scrollback — the wrapper instructs the agent to also write its answer (the review JSON included) to `.kanban-answer.md` in the worktree, reads that, and deletes it before the card is committed. Panes are closed when the attempt ends.

## Dispatcher Behavior

`kanban run [-j N] [--once]` processes `todo/`; `-j N` runs N cards in parallel (default 1). In a git repository every card gets its own worktree, so parallel cards never touch the same checkout:

1. Create branch `kanban/<id>` and worktree `.kanban/wt/<id>` from the branch checked out at dispatch start.
2. Pipe the card body (task + accumulated rework instructions) into the worker backend (a headless CLI or the visible Herdr wrapper) inside the worktree; commit the result on the card's branch.
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
