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

### Secretary agent naming (per project, not a fixed shared name)

Every project used to default its secretary's Herdr agent name to the same fixed `secretary`, so two projects bootstrapped in the same Herdr environment fought over one agent name — in practice this forced hand-picking a second name like `secretary-kimekyawa` and keeping every later `dispatch` in sync by hand. `kanban-secretary.sh` now resolves a stable, project-specific name instead, with this precedence:

1. **`KANBAN_HERDR_SECRETARY` environment variable** — highest priority; also how a user who already relies on the old fixed `secretary` name keeps it (`KANBAN_HERDR_SECRETARY=secretary` still works unchanged).
2. **`secretary_agent:` in this project's `.kanban/KANBAN.md` frontmatter** — a persistent, committed override, e.g. `secretary_agent: secretary-kimekyawa`.
3. **Generated default** — `secretary-<project-slug>`. The slug prefers this exact project's alias in the PC-wide registry (`kanban projects add <alias> <path>`, see **Cross-Project Send**) when one is registered; otherwise it slugifies the project root's directory name. The project's root is identified by its **realpath**, so bootstrapping/dispatching from a subdirectory — or even from inside one of the project's own `.kanban/wt/<id>` card worktrees — always resolves to the same name as bootstrapping from the root itself.

An explicit override (environment or `KANBAN.md`) that isn't a valid Herdr agent name (`^[a-z][a-z0-9_-]{0,63}$`) is rejected outright with a clear error — it is never silently replaced by a different name. A basename that is empty, symbol-only, or entirely non-ASCII (e.g. fully Unicode), or long enough to need truncation, gets a short stable hash suffix appended so unrelated projects in those categories don't collapse onto the same placeholder name; a plain ASCII basename that happens to match another *different* project's basename is **not** auto-disambiguated this way (two projects legitimately named `app` both default to `secretary-app`) — see the next paragraph for how that case is actually handled.

If `herdr agent rename` fails during bootstrap (most likely because another project's secretary is already running under that exact name), bootstrap fails loudly: it reports the project's identity, the candidate name, and how to pick a different one (`KANBAN_HERDR_SECRETARY=<name>` for a one-off, or `secretary_agent: <name>` in `KANBAN.md` for a persistent fix). It never takes over or renames another project's running agent. A successful bootstrap's one-line reply always includes `secretary=<resolved-name>` so the resolved name is visible immediately.

`kanban-secretary.sh dispatch` resolves the same name the same way and passes it to the dispatcher pane as `KANBAN_HERDR_SECRETARY`, which `herdr-notify-secretary.sh` then uses verbatim — so bootstrap, dispatch, and every done/failed notification for a project always address the same secretary, and a notification never reaches a different project's agent. Running two projects' secretaries and dispatchers side by side looks like:

```
# Project A (~/git/app-a)
$kanban-dispatch 秘書として開始   # → "secretary ready: project=~/git/app-a secretary=secretary-app-a ..."
... later, after cards are cut ...
~/git/MornKanban/kanban-secretary.sh dispatch   # notifications go to secretary-app-a only

# Project B (~/git/app-b), a separate Herdr pane
$kanban-dispatch 秘書として開始   # → "secretary ready: project=~/git/app-b secretary=secretary-app-b ..."
~/git/MornKanban/kanban-secretary.sh dispatch   # notifications go to secretary-app-b only
```

`kanban-secretary.sh end [project-dir]` clears this pane's active-secretary marker (see **Secretary Guard** below) when a secretary session ends; a fresh `bootstrap` in a new pane also supersedes it automatically, so `end` is a courtesy, not a requirement.

## Per-Project Policy: .kanban/KANBAN.md

`KANBAN.md` is the project's kanban contract, in two layers:

- **Frontmatter** (machine-readable): the CLI loads it as defaults — `backend_order`, `default_backend`, `default_model`, `reviewer`, `review_model`, `resolver`, `resolve_model`, `threshold`, `max_attempts`, `resolve_max_attempts`, `review_infra_max_retries`, `review_infra_backoff_seconds`, `jobs`, `claude_perms`, `codex_sandbox`, `codex_full_bypass`, `codex_approval`, `secretary_agent`. Precedence: environment variable > `KANBAN.md` > built-in default. The `claude_perms`/`codex_*` keys default to an **unrestricted** worker/reviewer/resolver permission policy — see **UNRESTRICTED permission policy**.
- **Body** (secretary policy): how to split cards, which backend/model to route each kind of task to, whether to auto-start the dispatcher, escalation rules. The dialogue agent must read and follow it before cutting cards.

`secretary_agent` overrides the per-project Herdr secretary name (see **Secretary Bootstrap** below); it is read only by `kanban-secretary.sh`, not by `kanban run` itself.

## Dialogue-Agent Contract

When the user assigns implementation work in a project that has `.kanban/`:

1. Read `.kanban/KANBAN.md` and follow its policy; it overrides the generic steps below where they differ.
2. Write a self-contained card: `echo "<full task description>" | kanban add "<title>" [-b claude|codex] [-m model] [-t threshold] [--review|--no-review] [--diagnose]`. The worker has no conversation context; include target paths, constraints, and completion conditions. Leave `--review`/`--no-review` off to inherit the project's `review_enabled` policy (see **Review on/off**); pass it only to deliberately override that policy for one card. Use `--diagnose` for a read-only investigation: it targets 5 minutes, stops at 10 minutes, skips reviewer by default, and must produce evidence/cause/uncertainty rather than a fix. File implementation separately unless the user explicitly requested diagnosis and repair together.
3. In visible secretary mode, start `~/git/MornKanban/kanban-secretary.sh dispatch` unless the lock shows a dispatcher is already running. The helper opens a separate Herdr dispatcher pane and binds worker, reviewer, resolver, and notification commands. Never substitute bare `kanban run`, which starts invisible headless workers. A nonstandard checkout uses its own absolute helper path.
4. Return to the user immediately. Do not implement the task in the dialogue session.
5. Report `failed/` cards to the user; they need human judgment.
6. **Verification is delegated too.** After implementation cards merge, cut a follow-up verification card (run the app, click through it, check the acceptance criteria) instead of verifying by hand. The dialogue agent never implements, verifies, or fixes directly — it cards, dispatches, and reports.
7. **Browser role is exclusive.** Ordinary workers must not touch browser-automation tools; verification is curl/CLI level. When a check genuinely needs a browser, cut a dedicated browser-verification card and run it **alone** (`kanban-secretary.sh dispatch --once`) — at most one browser-role agent exists at a time, and no other agent (the dialogue agent included) touches browser tools while it runs.
8. **No in-process delegation from the secretary pane.** Once bootstrapped, the secretary must not launch this CLI's own built-in subagent tool (Claude Code's `Agent`/`Task`; Codex's collaboration/subagent-spawning feature) to do the implementation/research/review/fix/verification itself — that bypasses cards, worktrees, and board history entirely. The only allowed actions in a bootstrapped secretary pane are reading project policy/board, `kanban add`/`kanban send`, `kanban-secretary.sh dispatch`/`dispatch --once`, and reporting to the user. If an in-process agent was started by mistake, stop immediately, do not merge or adopt its output, and file the same request as a card instead. See **Secretary Guard** below for the technical enforcement.

**Never hold a card back over file overlap, dependency order, or a collision with a card already in flight.** Those are execution-time concerns, resolved by the dispatcher/worker/reviewer/resolver via the formal state transitions below (`resolving`, `blocked`) — never by the secretary. Investigating conflicts, rebasing/merging, fixing, or re-verifying are not secretary actions either; write the card and dispatch it as soon as its own task description is self-contained. `resolving` and `blocked` cards are handled automatically and never need secretary attention; only `failed/` needs a human.

Leave `model` empty to use the backend's own default. Model names are backend-specific — never pass a Claude model name to a codex card.

## Secretary Guard (technical enforcement, not just instructions)

A bootstrapped secretary pane is restricted to *filing and dispatching
cards*, and this is enforced technically, not only by the contract text
above — self-reported "I know I shouldn't" is not enough:

| Allowed in a secretary pane | Forbidden in a secretary pane |
| --- | --- |
| Read `.kanban/KANBAN.md`, the README contract, board/card files | File write/edit/delete (Edit/Write/NotebookEdit tools, `apply_patch`-equivalents) |
| Read-only git (`status`/`log`/`diff`/`show`/`branch`/`remote`/...) | Any git mutation (`add`/`commit`/`push`/`pull`/`fetch`/`merge`/`rebase`/`cherry-pick`/`revert`/`reset`/`checkout`/`switch`/branch or tag create-delete/`worktree`/...) |
| `kanban add`/`show`/`list`/`init`/`send` (card creation and board confirmation) | Build/test/lint/formatter/server commands; bare `kanban run`; `kanban monitor`/`install`/`update`/`uninstall` |
| `kanban-secretary.sh bootstrap`/`dispatch`/`end` | Headless agent CLIs (`claude -p`, `codex exec`) |
| Replying to the user | Claude Code's in-process Agent/Task subagent tool, and any equivalent Codex collaboration/subagent tool |
| | External changes: GitHub/GitLab (`gh`/`glab`/`hub`, PR/issue/release/tag publish), package publish, deploy |

**How it's enforced today:**

- **Claude Code: `enforced`** (when installed — see below) via a `PreToolUse`
  hook (`guard/claude_secretary_guard.py`, matcher
  `Task|Agent|Edit|Write|NotebookEdit|Bash`) that fail-closed denies Task/
  Agent/Edit/Write/NotebookEdit outright, and classifies every `Bash`
  command through `guard/command_classify.py` — an **allowlist**, not a
  denylist, so shell chaining (`;`/`&&`/`||`/`|`), absolute paths, `env`/
  `sh -c` wrappers, and unrecognized wrapper scripts fall through to deny
  rather than bypassing it. The guard only fires when the current pane is
  the recorded active secretary for the current project (see below) —
  visible Herdr workers, reviewers, resolvers, ordinary Claude sessions, and
  other projects' secretaries are never affected.
- **Codex: `partial`.** As of this writing Codex CLI has no confirmed
  pre-tool-call deny hook (`~/.codex/hooks` and `~/.codex/rules` are an
  approval-memory/notify surface, not a deny gate) — enforcement there is
  the skill/contract text (this file, `skills/kanban-dispatch/SKILL.md`,
  `.kanban/KANBAN.md`) only. This is reported honestly, never displayed as
  `enforced`.

`kanban version`/`kanban --version`/`kanban-setup.sh` status output and
`kanban-secretary.sh bootstrap`'s one-line reply both show the current
`claude=<state>,codex=<state>` guard status (`enforced` / `not-installed` /
`misconfigured` for Claude; `partial` for Codex — never a false
`enforced`). `kanban install`/`update` idempotently add or repair the
Claude hook in `~/.claude/settings.json` without touching unrelated hooks
or keys (a one-time `.mornkanban-guard.bak` backup is made on first
install); `kanban uninstall` removes only the managed entry.

**Scope and lifecycle:** `kanban-secretary.sh bootstrap` atomically records
the current Herdr pane id as the project's active secretary marker
(`.kanban/.secretary-guard/marker.json`, keyed by project root realpath); a
re-bootstrap in a new pane silently supersedes a stale marker left by a
dead pane, and `kanban-secretary.sh end` clears it explicitly. The guard
denies a tool only when the invoking pane id matches that marker for the
resolved project root — it never blocks a worker/reviewer/resolver pane, a
plain Claude/Codex session, or another project's secretary, and a missing
or unreadable marker fails open toward *allowing* the tool.

**When a deny fires**, the message tells the agent to file a card and
dispatch instead of asking the user to re-confirm the boundary. A capped,
secret-free audit log (`.kanban/.secretary-guard/audit.log`, timestamp +
tool + category + reason only — never the command text or conversation
body) records what was denied.

**If a direct action already happened before this was noticed** (or
happened through Codex, where enforcement is contract-level only): stop
immediately. Do not self-roll-back, commit further, push, or delete a tag.
Report the fact — including any push/tag that already reached a remote — to
the user, and file a follow-up card to audit and recover the result;
recovery is delegated work too.

## Backends

| Backend | Worker command | Reviewer command |
| --- | --- | --- |
| `claude` | `claude -p [--model M] <claude perm flag>` | `claude -p [--model M] <claude perm flag>` |
| `codex` | `codex exec --skip-git-repo-check <codex perm flag> [-m M]` | `codex exec --skip-git-repo-check <codex perm flag> [-m M]` |

Worker and reviewer use the **same** permission policy — nothing forces the reviewer to a safer mode. `<claude perm flag>` is `--dangerously-skip-permissions` when `claude_perms` (default `bypassPermissions`) is `bypassPermissions`, else `--permission-mode <claude_perms>`. `<codex perm flag>` is `--dangerously-bypass-approvals-and-sandbox` when `codex_full_bypass` (default `true`) is `true`, else `-s <codex_sandbox> -a <codex_approval>`. See **UNRESTRICTED permission policy** below for the risk and how to dial it back.

`auto` (default for both worker and reviewer) resolves to the first installed CLI in `backend_order` (built-in default `claude codex`), so machines with only one CLI keep working unchanged. Codex does not run tests by default; state the test command in the card.

Custom worker commands (`KANBAN_WORKER_CMD`) receive the card's routing as `KANBAN_CARD_MODEL` / `KANBAN_CARD_BACKEND` environment variables, since the override bypasses the built-in model handling.

## Model Policy (default)

Top-tier models (fable / opus) are reserved for the **secretary (dialogue) and design roles**. Hands-on workers and reviewers default to a lower tier — claude cards to `sonnet`, light codex cards to `gpt-5.3-codex-spark` — regardless of backend. Raise a specific card with `-m` only for design-heavy or hard cards, and note why in the card. Projects customize this in `.kanban/KANBAN.md` (`default_model`, `review_model`), but new projects start from this default.

## Herdr Integration (no headless workers)

`kanban-secretary.sh dispatch` opens the dispatcher in a sibling [Herdr](https://herdr.dev) pane and binds `herdr-agent-worker.sh` for workers, reviewers, and resolvers alike. Every parallel card, including a merge-conflict resolution, appears as a **visible interactive agent in its own pane**, so it can be watched or interrupted:

```sh
~/git/MornKanban/kanban-secretary.sh dispatch
```

The helper verifies that it is called from the current Herdr pane, keeps focus on the secretary, uses the project's `jobs` setting, and closes the dispatcher's pane when the run finishes. `dispatch --once` preserves the browser-role exclusivity contract.

The secretary has no board watcher of its own, so card results are pushed to it: set `KANBAN_NOTIFY_CMD` and the dispatcher invokes it as `<cmd> <done|failed> <title>` whenever a card settles (never fatal to the run). `herdr-notify-secretary.sh` is the Herdr hook — it prompts the secretary agent (name from `KANBAN_HERDR_SECRETARY`, which `dispatch` always sets to that project's resolved secretary name — see **Secretary agent naming** above; a standalone invocation with no `KANBAN_HERDR_SECRETARY` resolves the same name itself from its own project root) to inspect and report, so `failed/` cards reach the user through the correct project's secretary instead of dying silently or reaching a different project's agent.

The wrapper splits a pane below the dispatcher and starts an interactive agent whose `--kind` (`claude` or `codex`) follows the card's own routing — worker backend from `KANBAN_CARD_BACKEND`, reviewer backend from `KANBAN_REVIEWER`, resolver backend from `KANBAN_RESOLVER`, and `auto` resolved via `KANBAN_BACKEND_ORDER`. Worker, reviewer, and resolver panes get the **same** full-trust permission policy (see **UNRESTRICTED permission policy** below): Claude gets `--dangerously-skip-permissions` (or `--permission-mode <claude_perms>` when overridden away from `bypassPermissions`), while Codex gets `--dangerously-bypass-approvals-and-sandbox` (or `-s <codex_sandbox> -a <codex_approval>` when `codex_full_bypass` is `false`). Model selection remains role-specific and Codex never inherits the Claude default of `sonnet`. The pane title and wrapper diagnostic identify the role and `UNRESTRICTED` policy. It accepts the folder-trust dialog for the card worktree, sends the worker/reviewer/resolver prompt, and waits for a stable `.kanban-answer.md`; terminal chrome is never substituted for a missing answer. Panes are closed when the attempt ends.

### UNRESTRICTED permission policy

Worker and reviewer agents (Claude and Codex, all roles) run **without any permission prompt or sandbox restriction** by default: Claude gets `--dangerously-skip-permissions`, Codex gets `--dangerously-bypass-approvals-and-sandbox`. This is a deliberate default, not a bug — the dispatcher still runs inside a **visible** Herdr pane so a human can watch or interrupt it (see Herdr Integration above), and every attempt is confined to its own git worktree/branch.

**Risk**: an unrestricted agent can read/write files outside the worktree, read credentials on disk, make arbitrary network requests, and run any local process or git/GitHub command — including against a card body that carries a prompt injection. Treat every card as if its instructions could be adversarial.

**Dial it back per project** by overriding these in `.kanban/KANBAN.md` frontmatter (or the matching env var, which always wins):

```yaml
claude_perms: acceptEdits        # or manual / plan / dontAsk — see `claude --help`
codex_full_bypass: false
codex_sandbox: workspace-write   # or read-only
codex_approval: on-request       # or untrusted
```

| Variable | KANBAN.md key | Meaning |
| --- | --- | --- |
| `KANBAN_CLAUDE_PERMS` | `claude_perms` | Claude permission mode (default `bypassPermissions` → `--dangerously-skip-permissions`; any other value maps to `--permission-mode <value>`) |
| `KANBAN_CODEX_SANDBOX` | `codex_sandbox` | Codex `-s` sandbox mode, used only when `codex_full_bypass` is `false` (default `danger-full-access`) |
| `KANBAN_CODEX_FULL_BYPASS` | `codex_full_bypass` | `true` (default) uses `--dangerously-bypass-approvals-and-sandbox`; `false` falls back to `-s <codex_sandbox> -a <codex_approval>` |
| `KANBAN_CODEX_APPROVAL` | `codex_approval` | Codex `-a` approval policy, used only when `codex_full_bypass` is `false` (default `never`) |

A project's own explicit `claude_perms`/`codex_sandbox`/`codex_full_bypass`/`codex_approval` (or the matching env var) is always honored as-is. A project that never set these keys picks up the new unrestricted default the next time it runs `kanban run` with this version of `kanban.sh` — migrate to a safer mode explicitly with the YAML block above if that project's board processes untrusted card content.

## Dispatcher Behavior

`kanban run [-j N] [--once]` processes `todo/`; `-j N` runs N cards in parallel. Without `-j` or `KANBAN_JOBS`, the dispatcher uses `.kanban/KANBAN.md`'s `jobs:` value and re-reads it while running. Raising it fills new slots; lowering it keeps current jobs alive and only pauses new starts. In a git repository every card gets its own worktree, so parallel cards never touch the same checkout:

1. Create branch `kanban/<id>` and worktree `.kanban/wt/<id>` from the branch checked out at dispatch start.
2. Pipe the card body (task + accumulated rework instructions) into the worker backend (a headless CLI or the visible Herdr wrapper) inside the worktree; commit the result on the card's branch.
3. A separate review agent inspects the worktree itself (it must not trust the worker's claims) and outputs `{"score": 0-100, "feedback": "..."}`.
4. `score >= threshold` (default 80) → merge into the base branch (merges are serialized by a lock), delete the branch and worktree, card → `done`. Below threshold, the feedback is appended and the worker retries **in the same worktree**; after `max_attempts` (default 3) the card moves to `failed` and the branch is kept for inspection.
5. A merge conflict does **not** fail the card immediately. The card moves to `resolving` and a dedicated resolver role takes over — see below.

### Conflict resolution (`resolving`)

A card that passed review can still conflict with `main` at merge time if another card landed first. Instead of dropping straight to `failed`, the dispatcher hands the conflict to a resolver role, a formal state distinct from an ordinary worker retry:

1. The card moves `doing`/passed review → `resolving`; the original card branch (`kanban/<id>`) is kept, never discarded.
2. A fresh worktree/branch (`.kanban/wt/<id>-resolve` / `kanban-resolve/<id>`) is created from the current `main`, and the card branch is merged into it, reproducing the conflict.
3. The resolver backend (`KANBAN_RESOLVE_CMD`, or the card's own backend/model by default) is given the conflicted file list, both branch names, and the original task, and is instructed to **preserve both sides' intent — never discard one side outright** — and to run whatever tests the task requires.
4. Once conflict markers are gone, the same review agent inspects the resolve worktree and scores it exactly like a normal attempt.
5. `score >= threshold` → only the **resolve branch** is merged into `main` (the original card branch is deleted without ever being merged itself, so nothing merges twice); both worktrees are removed, card → `done`.
6. Below threshold → feedback is appended and the resolver retries in the same resolve worktree; after `resolve_max_attempts` (default 2, `KANBAN.md`/card frontmatter `resolve_max_attempts`) the card moves to `failed`. Its History records the conflicted files, every resolve attempt's score/feedback, and the two branches (`kanban-resolve/<id>`, `kanban/<id>`) kept for manual inspection.
7. A merge of the passing resolve branch into `main` that itself fails (a rare race) also moves the card to `failed` with both branches kept, instead of silently retrying forever.

### Review on/off (`review_enabled`)

Every card carries `review_enabled: true|false` in its frontmatter. It gates step 3 above:

- `true` (default): unchanged from the description above — a reviewer runs, scores, and gates the merge; below-threshold work reworks in place; a merge conflict's resolve worktree is re-reviewed the same way.
- `false`: **no reviewer process/pane is ever started** (`KANBAN_REVIEW_CMD` is not invoked even if set). The worker's own exit status is the only success signal — a zero exit and no `BLOCKED:` line means the card merges immediately; a non-zero exit still moves the card to `failed`. There is no score, no threshold gate, and no rework retry loop — `max_attempts`/`threshold`/`reviewer`/`review_model` are kept in frontmatter but ignored. A merge conflict still hands off to the resolver role, but the resolver's result is *not* re-reviewed either — conflict markers being gone is enough. Every place a review would normally have run instead gets a History entry: `review skipped: review_enabled=false (source: ...)`. Turning review off is a speed/quality trade — it does **not** imply skipping whatever tests the task itself asks the worker to run.

Resolution order — the first one set wins, and is a one-time decision permanently recorded on the card (`review_enabled`/`review_source` in its frontmatter) so a dispatcher restart or a different machine picking up the card never re-derives a different answer:

1. Card frontmatter, set explicitly at creation time with `kanban add "title" --review` / `--no-review`.
2. The `KANBAN_REVIEW_ENABLED` environment variable (`true`/`false`; also accepts `1`/`0`, `yes`/`no`, `on`/`off`).
3. `KANBAN.md` frontmatter `review_enabled: true|false`.
4. Built-in default: `true`.

Any other value at any of these layers (env, `KANBAN.md`, or an explicit `--review`/`--no-review`-set card) is a hard error (`kanban: invalid boolean for ...`) — it is never silently coerced to `true` or `false`.

`kanban list`, `kanban show`, and the dispatcher's startup log all surface the effective policy as `Review: ON` or `Review: OFF (fast iteration)`.

### Real-time ordering dependencies (`blocked`)

If a worker or resolver discovers mid-run that it genuinely needs another card's result first (its stdout's first line starts with `BLOCKED: <reason>`), the dispatcher — never the dialogue secretary — handles it: the attempt doesn't count against `attempts`, its worktree/branch are discarded, the reason is recorded in History, and the card moves to `blocked`. A restarted dispatcher (and a normal `kanban run` startup) reclaims every `blocked` card back to `todo` for a fresh attempt from a clean worktree.

Every worker/review/resolver/merge phase's wall-clock duration is appended to History as a `phase durations: worker=Ns review=Ns` (or `resolver=Ns`, `merge=Ns`) line and mirrored into the card's `last_timings` frontmatter key, so a slow card can be diagnosed as test time vs. agent/review wait without re-running it — see the Monitor section below.

### Review infrastructure errors (`blocked`, kind `review_infra`)

A reviewer (or resolver review) that never returns a parseable `{"score": ...}` object — a visible Herdr pane/agent that disappeared (`agent_not_found`), a timeout, a wrapper/tool error, empty output, or leftover terminal chrome (a status line, another card's output) — is **infrastructure flaking, not a quality verdict**. It is never converted to `score: 0`:

1. `classify_review_infra_error` (kanban.sh) inspects the raw reviewer output only once JSON-score parsing has already failed, and tags it with a category (`agent_not_found`, `pane_lost`, `timeout`, `wrapper_error`, `empty_output`, `tool_error`, `unparseable_output`). `herdr-agent-worker.sh` also emits an explicit `KANBAN_INFRA_ERROR: <category>: <detail>` sentinel for the lifecycle failures it can detect directly (pane never started, agent never settled, `herdr agent get` itself failed, or a settled reviewer wrote no answer file); the same sentinel is honored on the worker/resolver side too (`classify_worker_infra_error`), applying the identical principle symmetrically.
2. The reviewer is retried **on the same worktree and commit** — no worker/resolver re-run — with a short bounded backoff (`review_infra_backoff_seconds`, default 2s × retry number, capped at 10s), up to `review_infra_max_retries` (`KANBAN.md`/card frontmatter/`KANBAN_REVIEW_INFRA_MAX_RETRIES`, default 2). This retry count is tracked independently of `attempts`/`resolve_attempts` and is reset to 0 once a real score is obtained.
3. Only a genuinely parsed JSON score is ever threshold-judged; every infra retry in between is invisible to the quality-review logic. History records each retry as `review infrastructure retry N/M: <category>` (worker-side: `worker infrastructure retry N/M: <category>`), distinct from a quality `review`/`rework instruction` entry.
4. If retries are exhausted, the card moves to `blocked` with frontmatter `blocked_kind: review_infra` — **not** `failed` — and its branch, worktree, and every commit so far are kept. History explains this is a review infrastructure stop, not a code failure, and names the recovery command. The Monitor UI shows these cards with a distinct "review infrastructure stopped (not a code failure)" badge instead of the ordinary attempts counter treatment.
5. Unlike the ordering-dependency `blocked` kind above, a `review_infra` blocked card is **not** auto-reclaimed to `todo` on dispatcher restart (that would either re-run the worker for nothing or collide with the surviving worktree/branch) — it stays parked until `kanban resume <id>`, which resets the infra retry counters and resumes **only the review step** on the kept worktree/branch (or the kept resolve worktree/branch, for a card blocked mid conflict-resolution). A dispatcher restart mid-retry (before exhaustion) reclaims the still-`doing`/`resolving` card back to `todo`/`resolving` as usual, but the worker step is **not** re-run: `process_card_wt`/`process_resolve_wt` reuse an already-existing worktree/branch instead of failing on `git worktree add`, and a `review_pending` frontmatter checkpoint (set right after the worker's commit, cleared only once a real score lands) tells the resumed run to go straight to the review step.

Outside a git repository the dispatcher falls back to sequential in-place execution (`-j` > 1 is refused; `resolving` never applies since there is nothing to merge). Worker/resolver output tail, every review score, and rework instructions are appended to the card's History section. A restarted dispatcher reclaims cards stranded in `doing/`, `review/`, `resolving/`, or `blocked/` — folding back any leftover worktree/branch first so the card can restart cleanly, and the dispatcher's single-instance lock (`.kanban/.lock`) guarantees no card is ever picked up twice.

## Configuration (environment variables)

Each has a `KANBAN.md` frontmatter counterpart except the last three; the environment wins.

| Variable | Meaning |
| --- | --- |
| `KANBAN_BACKEND_ORDER` | `auto` resolution order (default `claude codex`) |
| `KANBAN_REVIEWER` | Reviewer backend: `auto`, `claude`, or `codex` |
| `KANBAN_REVIEW_MODEL` | Reviewer model (empty = backend default) |
| `KANBAN_RESOLVER` | Resolver backend for merge-conflict handling: `auto`, `claude`, or `codex` (default: the card's own backend) |
| `KANBAN_RESOLVE_MODEL` | Resolver model (empty = card's own model / backend default) |
| `KANBAN_CLAUDE_PERMS` | Claude worker/reviewer/resolver permission mode (default `bypassPermissions` → `--dangerously-skip-permissions`; see **UNRESTRICTED permission policy**) |
| `KANBAN_CODEX_SANDBOX` | Codex worker/reviewer/resolver `-s` mode, used only when `KANBAN_CODEX_FULL_BYPASS=false` (default `danger-full-access`) |
| `KANBAN_CODEX_FULL_BYPASS` | `true` (default) → `--dangerously-bypass-approvals-and-sandbox`; `false` → `-s <sandbox> -a <approval>` |
| `KANBAN_CODEX_APPROVAL` | Codex worker/reviewer/resolver `-a` approval policy, used only when `KANBAN_CODEX_FULL_BYPASS=false` (default `never`) |
| `KANBAN_JOBS` | Default parallelism for `kanban run` (overridden by `-j`) |
| `KANBAN_DISPATCH_POLL_INTERVAL` | Dispatcher scheduling/config-refresh interval in seconds (default `1`; tests may use a smaller decimal value) |
| `KANBAN_REVIEW_ENABLED` | `true`/`false` (aliases: `1`/`0`, `yes`/`no`, `on`/`off`); overrides `KANBAN.md`'s `review_enabled`, but a card's own explicit `--review`/`--no-review` still wins — see **Review on/off** above |
| `KANBAN_WORKER_CMD` / `KANBAN_REVIEW_CMD` / `KANBAN_RESOLVE_CMD` | Full command overrides; use mock scripts to test state transitions without spending tokens |
| `KANBAN_NOTIFY_CMD` | Hook run as `<cmd> <done\|failed> <title>` when a card settles (see Herdr Integration) |
| `KANBAN_DEBUG` | Write per-job xtrace logs to `.kanban/wt/job.*.trace` |
| `KANBAN_HERDR_SECRETARY` | Overrides the resolved per-project Herdr secretary agent name (see **Secretary agent naming**); no `KANBAN.md` counterpart of the same name — use the `secretary_agent:` frontmatter key for a persistent override instead |
| `KANBAN_REVIEW_INFRA_MAX_RETRIES` | Bounded reviewer-infrastructure-error retries before a card moves to `blocked` (kind `review_infra`); default 2 — see **Review infrastructure errors** |
| `KANBAN_REVIEW_INFRA_BACKOFF_SECONDS` | Base seconds for the review-infra retry backoff (`base × retry number`, capped at 10s); default 2 |

## Monitor (read-only, multi-project)

`kanban monitor` is a **read-only** localhost web viewer that shows every `.kanban` board on this machine — Kanban columns, card frontmatter/body/History, dispatcher running/stopped state, and recent activity — across multiple projects at once. It never adds cards, changes card state, or touches any process; every write HTTP method (`POST`/`PUT`/`PATCH`/`DELETE`) is rejected with `405`, and there is no UI control that mutates anything. The card detail modal renders every frontmatter key verbatim, including `last_timings` (the most recent phase-duration line) and `created` (so `mtime - created` gives a card's total elapsed time) — no dedicated UI code is needed to surface new frontmatter fields.

- Start in the foreground: `kanban monitor` (equivalent to `kanban monitor run`). Open `http://127.0.0.1:8787/` (default port `8787`). Stop with Ctrl+C.
- Flags: `--host` (default `127.0.0.1`; only change this if you intentionally want to expose the viewer beyond localhost — a warning is printed), `--port` (default `8787`), `--root <path>` (repeatable; adds a search root for this run only).
- python3 standard library only, matching the rest of MornKanban's distribution constraints — no `pip install` is required.

### Project discovery

By default the monitor only looks for `.kanban` directories under `~/git`. It never scans the whole filesystem, never descends into a `.kanban` directory once found (so `.kanban/wt/<id>` worktree checkouts are never listed as separate projects), and deduplicates by `realpath` so symlink loops cannot cause repeats or hangs.

Search roots are configured in a JSON file:

- Default location: `${XDG_CONFIG_HOME:-~/.config}/mornkanban/monitor.json` — `{"roots": ["/path/one", "/path/two"]}`.
- Manage it with the CLI: `kanban monitor config list-roots` / `add-root <path>` / `remove-root <path>`.
- `KANBAN_MONITOR_ROOTS` (a `:`-separated list of paths) overrides the config file entirely — handy for one-off runs and for tests.
- `KANBAN_MONITOR_CONFIG` (a file path) or `KANBAN_MONITOR_CONFIG_DIR` (a directory) overrides where the config file itself is read from/written to, independent of `$HOME` — used by the test suite so it never touches a real user's config.

### Running it PC-resident (macOS user LaunchAgent)

This is a **separate command namespace from `kanban install/update/uninstall`** (which manage the `kanban` CLI symlink and the secretary skill) — the monitor's own resident-process lifecycle lives entirely under `kanban monitor daemon`:

```sh
kanban monitor daemon install [--host H] [--port P] [--root R ...]   # write/refresh the LaunchAgent plist
kanban monitor daemon start                                          # load + start it
kanban monitor daemon status                                         # installed? running?
kanban monitor daemon stop                                           # stop + unload it
kanban monitor daemon uninstall                                      # stop it and remove the plist
```

All of these are idempotent and only ever touch one file: `~/Library/LaunchAgents/dev.mornkanban.monitor.plist` (plus its own log files under `~/Library/Logs/MornKanban/`). No other LaunchAgent or file is ever read, modified, or removed.

### Security boundary

- Binds to `127.0.0.1` by default; it is not exposed to the network unless you explicitly pass a different `--host`.
- Only `GET`/`HEAD` are implemented; every other HTTP method gets `405`.
- Every project, card, and static-file path is checked against a `realpath` allowlist before being read, rejecting directory traversal and any path outside a discovered project's `.kanban` directory.
- All dynamic content is served as JSON and rendered client-side via `textContent` (never `innerHTML`), so card titles/bodies containing HTML-like text cannot inject markup into the page.
- A project that fails to read (permission error, corrupt file, etc.) shows an inline error on its own card instead of breaking the rest of the page.

### Project-switch UI state

Selecting a project (or an already-selected project again) synchronously clears the previous board's columns, counts, and any open card-detail modal, and shows a loading skeleton for the newly selected project only. Every board fetch, poll, and card-detail fetch is tagged with a per-selection generation number in `monitor/static/state.js`; a response is applied only if it still matches the live selection, so a slow response from a previous project (or a superseded card fetch) can never overwrite what is currently on screen, and a fetch failure surfaces as an error state for the *selected* project instead of falling back to stale data.

Each visible worker/reviewer/resolver also appends a bounded correlation event to `.kanban/activity.jsonl`. The newest 1000 events retain only operational metadata (timestamp, card id, role, attempt, backend/model, Herdr agent name, pane id, event/status, and duration); prompts, answers, card descriptions, and credentials are never written. The selected project's **エージェント時系列** panel renders these events so a slow or lost pane can be identified without opening every card.

To check manually: open the monitor, click into a project, then quickly click a second project before the first board finishes loading — the first project's columns must never flash into view. Repeat with a third click back to the first project (A → B → A) and confirm only the final board is shown. Kill the monitor's network mid-load (or throttle it in DevTools) to confirm the board shows an error/retry state rather than a stale one. Automated coverage lives in `tests/test_monitor_state.js` (`node --test tests/test_monitor_state.js`).

## Cross-Project Send (file a card into any registered project, from anywhere)

`kanban projects` and `kanban send` let you file a card into any project's `.kanban/todo/` regardless of the current directory or session — from inside project A into project B, from B into A, or from an unrelated directory into either, using a PC-wide alias registry.

### Registering projects

```sh
kanban projects add <alias> <path>      # register: realpath-normalized, must already have .kanban (run `kanban init` first)
kanban projects list [--json]           # alias -> root, one per line (or JSON)
kanban projects show <alias>            # root, .kanban dir, timestamps, dispatcher running/stopped
kanban projects update <alias> <path>   # repoint an existing alias at a new path
kanban projects remove <alias>          # unregister (send/monitor stop recognizing it immediately)
```

- Aliases are lowercase letters/digits/`-`/`_`, starting with a letter or digit, max 64 chars; anything else is rejected with a clear error.
- A path with no `.kanban` directory, a path that doesn't exist, a duplicate alias, or a path already registered under a different alias (checked by `realpath`, so a symlink or `..` traversal can't register a second alias for the same project) are all rejected — `--force` on `add` overrides the alias/path-duplicate checks explicitly.
- The registry file (`${XDG_CONFIG_HOME:-~/.config}/mornkanban/projects.json`) lives in the **same config directory monitor's `monitor.json` uses** (`KANBAN_MONITOR_CONFIG_DIR`/`KANBAN_CONFIG_DIR` both point at it) — one shared config root, not two. `kanban monitor`'s project listing gives a registered project its `kanban projects` alias as its slug (even if that project sits outside every configured search root), so the name you see in the monitor UI is always the same alias you'd `kanban send` to.

### Sending a card

```sh
kanban send <alias> "title" [-b claude|codex|auto] [-m model] [-t threshold] [--from PATH] < description
```

- The card is always created in the **destination** project's `.kanban/todo/` — never in the directory `kanban send` was run from. Body comes from stdin (same as `kanban add`); with no stdin it falls back to the title.
- Unset `-b`/`-m`/`-t` default from the destination's own `.kanban/KANBAN.md` (`default_backend`/`default_model`/`threshold`), exactly like a card added locally with `kanban add` there — the sending project's policy is never consulted.
- The card's frontmatter records `source_alias` (set only when the current directory — or `--from PATH` — is itself inside a registered project), `source_path` (always, the realpath of where the send was issued from), and `dispatched_via: send`, so a worker or reviewer can see where a card came from without leaking any secret/env-var values.
- Card IDs are allocated with a random suffix and written via a temp-file-then-hardlink swap inside the destination's `todo/`, so concurrent `kanban send` calls from multiple sessions into the same project never collide on an id or leave a partially-written card visible to the dispatcher.
- `kanban send` never starts or touches a dispatcher. If the destination's `.kanban/.lock` shows no live process, it prints (to stderr, after the created card's path on stdout) that the card is filed but nothing will run it until `kanban run` or the visible Herdr dispatcher is started there — it does not silently fall back to a headless worker.

## Testing MornKanban Itself

This repo's suite has three bounded tiers — see `gui/VERIFY.md`. Use `python3 tests/run.py targeted <unittest-name>` while iterating, `python3 tests/run.py fast` at a checkpoint, and `python3 tests/run.py full` exactly once before integration. Every step has a hard timeout and kills its whole subprocess group on expiry, so a failed test cannot leave a dispatcher/worker behind. Full discovers every Python test and additionally runs the visible-worker lifecycle, frontend, and skill validation; targeted/fast are not substitutes for that final gate. For a deliberately fast card, use `--no-review` or project-level `review_enabled: false`; this skips only the reviewer, not the card's requested checks.

## Constraints

- One dispatcher per project (`.kanban/.lock`); parallelism comes from `-j`, not from extra dispatchers.
- Parallel cards that edit the same files will collide at merge time; the resolver role handles that automatically (see `resolving` above) and only lands in `failed` if it cannot genuinely resolve it. The secretary must not hold cards back to avoid this — split by file boundaries where convenient, but never as a precondition for adding a card.
- Merging targets the branch checked out when `kanban run` started; keep the main checkout clean while the dispatcher runs, and do not switch branches under it.
- The reviewer scores against the card text. A vague card passes vacuously; completion conditions belong in the card, not in the conversation.
- The script targets bash 3.2 (macOS default). Inside it, never end a loop body with `[[ ... ]] && cmd` — when the test is false the status-1 list trips `set -e` and kills the job silently; use the `if` form.
