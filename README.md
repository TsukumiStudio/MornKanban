# MornKanban

Visible, file-based Kanban dispatch for Claude Code and Codex agents. MornKanban
turns implementation requests into cards, runs each card in an isolated Git
worktree, reviews the result, resolves merge conflicts, and keeps the dialogue
agent available as a project secretary.

## Highlights

- Visible Herdr panes for the secretary, dispatcher, workers, reviewers, resolvers, and operators.
- Four concurrent workers by default, with live resizing and no artificial upper limit.
- Per-card backend, model, effort, review, diagnosis, and explicit dependency settings.
- Structured `blocked_kind` / `failure_kind` semantics that distinguish waiting, infrastructure trouble, and product failures.
- A one-second dispatcher TUI showing board counts, live AI/model/effort, recent moves, and raw logs.
- Short work-order prompts with an explicit startup ACK and evidence-shaped completion reports.
- On-demand graphical activity reports as editable PPTX and PDF.

> [!WARNING]
> Worker, reviewer, and resolver agents run unrestricted by default. Use MornKanban only with trusted cards and repositories, or configure the permission policy before dispatching.

## Design principles

MornKanban follows the leader/worker separation described in [ゲーム制作における Herdrとgit worktreeの自立型マルチエージェント環境](https://qiita.com/yuji_yasuhara/items/99c589264a006658a15a): the secretary owns planning and dispatch, each worker gets one isolated worktree, and a different agent reviews actual evidence. MornKanban keeps three deliberate differences: the dispatcher alone mutates `.kanban/`, review and merge proceed automatically through durable checkpoints, and concurrency defaults to four without an artificial upper limit.

## Quick start

Requirements: macOS, Bash 3.2+, Python 3, [Herdr](https://herdr.dev), and at
least one supported agent CLI (Claude Code or Codex).

```sh
git clone https://github.com/TsukumiStudio/MornKanban.git ~/git/MornKanban
~/git/MornKanban/kanban-setup.sh install
```

Then open a Herdr pane in the target project and invoke:

```text
$kanban-dispatch 秘書として開始
```

The skill initializes the project board when needed, verifies the visible Herdr
session, and turns the current dialogue into the secretary. From then on, ask
the secretary for work normally; it creates cards and starts the visible
dispatcher.

For manual board setup, run `kanban init`. It creates
`.kanban/{todo,doing,review,resolving,blocked,done,failed}/` and a project policy
template at `.kanban/KANBAN.md`; a second run never overwrites the policy.

## Installation and setup

- `kanban-setup.sh install` creates or repairs the `~/.local/bin/kanban` symlink, installs the Claude Code and Codex skills, and installs the Claude secretary guard.
- Commit `.kanban/KANBAN.md` and card records when the board history should travel with the project.
- Customize `.kanban/KANBAN.md` for the project's agent/model composition and card policy before regular use.

## Setup Wizard

- `./kanban-setup.sh` (引数なし・TTY) は、現在の `VERSION` と最新版、CLIリンク、Claude Code/Codexスキル、秘書ガード、project registry、現在のproject状態を、枠・色・記号付きのダッシュボードで表示する。
- 起動時は現在の導入状況だけを表示する。操作説明は `h`=help で必要な時だけ開き、表示後はメニューへ戻る。`y`=install、`s`=update、`u`=uninstall、`N`=何もしない、を選べる。変更前に対象を表示して再確認し、完了後は結果と次のコマンドを表示する。
- project registry はインストール物ではないため、0件なら `登録なし`、1件以上なら `登録あり` と表示する。
- `./kanban-setup.sh {install|update|uninstall|version}` はダッシュボードと確認を省いて非対話実行する。初回install前はこの入口を使い、導入後は `kanban {install|update|uninstall|version}` でも同じ操作ができる。`update` は確認なしでも変更プレビューと結果サマリーを表示する。
- Project onboarding is **not** part of the wizard: open a Herdr pane in the project and invoke **`$kanban-dispatch 秘書として開始`**. The skill initializes the board, verifies visible Herdr execution, and makes the current conversation the secretary (see Secretary Bootstrap below).
- 非TTY、`NO_COLOR`、`TERM=dumb` ではASCII・色なしへフォールバックする。狭い端末や日本語を含む長いpathも表示幅に合わせて折り返し、状態は色だけに依存しない。
- Requirements: Bash 3.2+, Python 3, and Herdr for visible secretary sessions.

## Versioning and Updates

- The repository ships a committed `VERSION` file (semantic `X.Y.Z`) as the single source of truth for the installed distribution version.
- `kanban --version` prints only the locally installed version, read from `VERSION` next to the resolved script — no network access.
- `kanban version` prints the current version, the latest published version, and the comparison state (`up-to-date` / `update-available` / `local-ahead` / `unknown`). Because this repository currently has no tags or GitHub Releases, "latest published" means the raw `VERSION` file on `main` on GitHub. Set `KANBAN_VERSION_URL` (a `file://` URL works) to override the source, e.g. for tests.
- `kanban update` reinstalls the CLI symlink, skills, and Claude secretary guard from the current checkout. It never runs Git or changes the checkout; update the repository separately when you explicitly want to.
- `kanban install` / `kanban uninstall` (re)create or remove `~/.local/bin/kanban` and the Claude Code/Codex `kanban-dispatch` and `kanban-report` skills. Codex installs them under `~/.agents/skills/`; install/update removes the old installer-managed `~/.codex/skills/kanban-dispatch`. Uninstall leaves the repository checkout and all project boards untouched.
- Each installed skill's rendered `SKILL.md` embeds both the resolved repository path and the distribution version at install/update time.
- `kanban.sh` resolves its own real location by following symlinks (e.g. the `~/.local/bin/kanban` entry point), so these commands work whether invoked directly or through the installed symlink.

## Secretary Bootstrap (one-liner)

A secretary agent is started with **`$kanban-dispatch 秘書として開始`** (the phrase 「**kanban の秘書として待機して**」 also triggers the skill). Everything else lives in the installed skill, this README, and the project's `.kanban/KANBAN.md`, not in the prompt. On that request the agent must:

1. Run `kanban-secretary.sh bootstrap` from the MornKanban checkout. It runs `kanban init` when needed, verifies the current Herdr pane, and registers this agent as the notification target. It never overwrites an existing `KANBAN.md`.
2. Read `.kanban/KANBAN.md` and the Dialogue-Agent Contract below.
3. Reply with **one short line** (e.g. 「秘書モード開始。課題を待機中 (worker=claude/sonnet, -j 4)」) — no plan dumps.
4. Treat the bootstrap request as active for the rest of the conversation. For each subsequent user request: split it into cards per policy, start the visible dispatcher, reply briefly, and return to waiting. Never implement in the dialogue session.
5. React to card-settlement pushes (`KANBAN_NOTIFY_CMD`) per policy: inspect `failure_kind`/`blocked_kind` and History, distinguish product failure from infrastructure failure or unverified work, and summarize when the board settles.

Herdr is required for secretary sessions. The bootstrap must test `HERDR_ENV`, `HERDR_PANE_ID`, and the current pane through the Herdr CLI. It must not infer availability from the prompt. There is no execution-mode choice or headless secretary mode; if Herdr is unavailable, stop and report it.

## Period reports (`$kanban-report`)

`$kanban-report` creates a graphical activity report from local card History and `.kanban/activity.jsonl`, then delivers the same slide deck as editable PPTX and rendered PDF. It runs only when the user asks for a report, retrospective, work recap, or postmortem. With no dates it covers today (00:00 through now, local timezone) and the current project; explicit dates and multi-project scope override those defaults.

The report workflow is read-only against MornKanban projects and never runs Git. It distinguishes work-process failures, product failures, infrastructure failures, dependency waits, and unverified work; current card directories are not presented as historical end-state evidence without a timestamped History transition. The activity log retains only its newest 1000 events, so reports disclose possible truncation instead of inventing complete agent metrics.

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
- **Body** (secretary policy): how to split cards, which backend/model/effort to route each kind of task to, whether to auto-start the dispatcher, escalation rules. The dialogue agent must read and follow it before cutting cards.

`secretary_agent` overrides the per-project Herdr secretary name (see **Secretary Bootstrap** below); it is read only by `kanban-secretary.sh`, not by `kanban run` itself.

## Dialogue-Agent Contract

When the user assigns implementation work in a project that has `.kanban/`:

1. Read `.kanban/KANBAN.md` and follow its policy; it overrides the generic steps below where they differ.
2. Write a self-contained card: `echo "<full task description>" | kanban add "<title>" [-b claude|codex] [-m model] [-e low|medium|high|xhigh|max] [--depends-on card-id] [-t threshold] [--review|--no-review] [--diagnose|--operate]`. The worker has no conversation context; include target paths, constraints, and completion conditions. Use `--depends-on` only for a dependency explicitly required by the user or project policy. Leave `--review`/`--no-review` off to inherit the project's `review_enabled` policy (see **Review on/off**); pass it only to deliberately override that policy for one card. Use `--diagnose` for a read-only investigation. Use `--operate` only for user-authorized push/deploy/publish or another external mutation; it runs once in the main checkout, serialized with merges, and skips review. It reaches `done` only when its first result line is `OPERATION_OK: <verified result>`; failure or uncertainty must be `BLOCKED: <reason>`.
3. Start `~/git/MornKanban/kanban-secretary.sh dispatch` unless the lock shows a dispatcher is already running. The helper opens a separate Herdr dispatcher pane and binds worker, reviewer, resolver, operator, and notification commands. A nonstandard checkout uses its own absolute helper path.
4. Return to the user immediately. Do not implement the task in the dialogue session.
5. Report `failed/` cards to the user after reading `failure_kind` and History. `failed` means the work process failed; it is not by itself a product-verification verdict.
6. **Verification is delegated too.** After implementation cards merge, cut a follow-up verification card (run the app, click through it, check the acceptance criteria) instead of verifying by hand. The dialogue agent never implements, verifies, or fixes directly — it cards, dispatches, and reports.
7. **Browser role is exclusive.** Ordinary workers must not touch browser-automation tools; verification is curl/CLI level. When a check genuinely needs a browser, cut a dedicated browser-verification card and run it **alone** (`kanban-secretary.sh dispatch --once`) — at most one browser-role agent exists at a time, and no other agent (the dialogue agent included) touches browser tools while it runs.
8. **No in-process delegation from the secretary pane.** Once bootstrapped, the secretary must not launch this CLI's own built-in subagent tool (Claude Code's `Agent`/`Task`; Codex's collaboration/subagent-spawning feature) to do the implementation/research/review/fix/verification itself — that bypasses cards, worktrees, and board history entirely. The only allowed mutations in a bootstrapped secretary pane are the bounded board-administration commands below plus card filing and visible dispatch. If an in-process agent was started by mistake, stop immediately, do not merge or adopt its output, and file the same request as a card instead. See **Secretary Guard** below for the technical enforcement.

**Never hold a card back over file overlap, dependency order inferred by the secretary, or an already-running card. Never invent a dependency or release gate.** Only the user or project policy may declare one, recorded with `--depends-on`; write every otherwise-independent card as soon as its description is self-contained. A declared dependency blocks before worker/reviewer startup and resumes only when its target reaches `done`; a dependency discovered during execution uses `BLOCKED: <reason>`. Investigating conflicts, rebasing/merging, fixing, or re-verifying remains execution-side work.

Run user-authorized push/deploy through an explicit `--operate` card; never assign it to a worktree worker. Independent local test, build, and status-check cards still run. If verification was not performed because infrastructure failed, report **unverified / user decision required**; do not infer either a product failure or an automatic deployment prohibition.

Leave `model` empty to use the backend's own default. Model names are backend-specific — never pass a Claude model name to a codex card.
Leave `effort` empty to inherit the agent's shared setting. `-e` stores a card-level override used by that card's worker, reviewer, and resolver. For `gpt-5.6-sol`, use `medium` for normal work and `high` for complex implementation unless the card has an exceptional reason to go higher.

The secretary may maintain its own board through the validated CLI, never by editing files directly:

```sh
kanban remove <card-id>                       # delete one unstarted todo card only
kanban config set jobs 8                      # live dispatcher concurrency
kanban config set default_backend codex       # defaults for newly created cards
kanban config set default_model gpt-5.6-sol
kanban config set reviewer claude
kanban config set review_model sonnet
kanban config set resolver claude
kanban config set resolve_model sonnet
```

Use `kanban remove` immediately when the secretary itself accidentally created a malformed card, or when the user explicitly asks to discard an unstarted card; it refuses every state except `todo`. `kanban config set` accepts only the seven keys shown above. A running dispatcher re-reads `jobs` live; card defaults affect newly created cards, while reviewer/resolver routing changes take effect on the next dispatcher start. These commands do not authorize project-file edits, Git mutations, builds, tests, or deployment.

## Secretary Guard (technical enforcement, not just instructions)

A bootstrapped secretary pane is restricted to *board administration and
visible dispatch*, and this is enforced technically, not only by the contract text
above — self-reported "I know I shouldn't" is not enough:

| Allowed in a secretary pane | Forbidden in a secretary pane |
| --- | --- |
| Read `.kanban/KANBAN.md`, the README contract, board/card files | Direct project or board file write/edit/delete (Edit/Write/NotebookEdit tools, `apply_patch`-equivalents, raw `rm`) |
| Managed Git inspection (`kanban inspect status|log|diff|diff-cached|show|branch`) | Direct `git`, even apparent reads: repository config can launch helpers; all mutations remain forbidden |
| `kanban` board-control commands (`add`/`remove`/`config`/`resume`/`operation`/...) | Bare `kanban run` (visible dispatch is the only worker entrypoint), build/test/lint/formatter/server commands run directly outside Kanban |
| `kanban-secretary.sh bootstrap`/`dispatch`/`end` | Headless agent CLIs (`claude -p`, `codex exec`) |
| Replying to the user | Claude Code's in-process Agent/Task subagent tool, and any equivalent Codex collaboration/subagent tool |
| | External changes: GitHub/GitLab (`gh`/`glab`/`hub`, PR/issue/release/tag publish), package publish, deploy |

Managed `status` and working-tree `diff` run against isolated Git metadata and never load repository-configured helpers. Filter/LFS-managed paths are therefore compared as raw files and may be conservatively reported as modified; the command says so on stderr instead of presenting that result as exact filter semantics. `core.filemode` is preserved.

**How it's enforced today:**

- **Claude Code: `enforced`** (when installed — see below) via a `PreToolUse`
  hook (`guard/claude_secretary_guard.py`, matcher
  `Task|Agent|Edit|Write|NotebookEdit|Bash`) that fail-closed denies Task/
  Agent/Edit/Write/NotebookEdit outright, and classifies every `Bash`
  command through `guard/command_classify.py` — an **allowlist**, not a
  denylist, so shell chaining (`;`/`&&`/`||`/`|`), absolute paths, `env`/
  `sh -c` wrappers, and unrecognized wrapper scripts fall through to deny
  rather than bypassing it. Allowed `kanban` executables are also verified by
  realpath against this managed checkout; a fake `/tmp/kanban` cannot inherit
  the name-based permission. Git reads use `kanban inspect`, which runs from
  an isolated, temporary Git metadata copy without repository/system/user
  config, so diff, filter, textconv, fsmonitor, and pager helpers cannot run;
  direct `git status`/`diff` is deliberately denied. The guard only fires when the current pane is
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
| `claude` | `claude -p [--model M] [--effort E] <claude perm flag>` | `claude -p [--model M] [--effort E] <claude perm flag>` |
| `codex` | `codex exec --skip-git-repo-check <codex perm flag> [-m M] [-c model_reasoning_effort=E]` | `codex exec --skip-git-repo-check <codex perm flag> [-m M] [-c model_reasoning_effort=E]` |

Worker and reviewer use the **same** permission policy — nothing forces the reviewer to a safer mode. `<claude perm flag>` is `--dangerously-skip-permissions` when `claude_perms` (default `bypassPermissions`) is `bypassPermissions`, else `--permission-mode <claude_perms>`. `<codex perm flag>` is `--dangerously-bypass-approvals-and-sandbox` when `codex_full_bypass` (default `true`) is `true`, else `-s <codex_sandbox> -a <codex_approval>`. See **UNRESTRICTED permission policy** below for the risk and how to dial it back.

`auto` (default for worker, reviewer, and resolver) resolves to the first installed CLI in `backend_order` (built-in default `claude codex`), so machines with only one CLI keep working unchanged. New boards leave all model defaults empty: Claude then uses `sonnet`, while Codex inherits its own CLI default. Legacy Claude model names are discarded when `auto` resolves to Codex, preventing an invalid `-m sonnet`. Codex does not run tests by default; state the test command in the card.

Custom worker commands (`KANBAN_WORKER_CMD`) receive the card's routing as `KANBAN_CARD_MODEL` / `KANBAN_CARD_EFFORT` / `KANBAN_CARD_BACKEND` environment variables, since the override bypasses the built-in routing handling.

## Model Policy (default)

Top-tier models (fable / opus) are reserved for the **secretary (dialogue) and design roles**. Hands-on workers and reviewers default to a lower tier — claude cards to `sonnet`, light codex cards to `gpt-5.3-codex-spark` — regardless of backend. Raise a specific card with `-m` only for design-heavy or hard cards, and note why in the card. For `gpt-5.6-sol`, use `-e medium` for normal work and `-e high` for complex implementation instead of inheriting an unnecessarily high shared setting. Projects customize models in `.kanban/KANBAN.md` (`default_model`, `review_model`), but effort remains a per-card choice.

## Herdr Integration (no headless workers)

`kanban-secretary.sh dispatch` opens the dispatcher below the secretary in a [Herdr](https://herdr.dev) pane and binds `herdr-agent-worker.sh` for workers, reviewers, resolvers, and operators alike. AI panes fill the right side beside the secretary and dispatcher first, then split downward as concurrency grows. Every parallel card, including a merge-conflict resolution or explicit external operation, appears as a **visible interactive agent in its own pane**, so it can be watched or interrupted:

```sh
~/git/MornKanban/kanban-secretary.sh dispatch
```

The helper verifies that it is called from the current Herdr pane, keeps focus on the secretary, uses the project's `jobs` setting, and closes the dispatcher's pane when the run finishes. At startup it copies and `bash -n` validates one private worker-wrapper snapshot, so a later MornKanban checkout update cannot change a running dispatcher's script. `dispatcher pane started` confirms only that Herdr accepted the pane command. The dispatcher output remains visible and is also written to `.kanban/wt/dispatcher.log`; a nonzero exit sends the secretary a `dispatcher_failed` notification with the exit status and log path, so it reports the real startup/runtime error instead of assuming cards ran. The secretary must not improvise recovery with `git init`, `commit`, or another Git mutation. `dispatch --once` preserves the browser-role exclusivity contract.

The secretary has no board watcher of its own, so card results are pushed to it: set `KANBAN_NOTIFY_CMD` and the dispatcher invokes it as `<cmd> <done|failed|blocked> <title>` whenever a card settles (never fatal to the run). The pane runner also invokes the hook as `<cmd> dispatcher_failed <log-path> <exit-status>` if the dispatcher itself stops unsuccessfully. `herdr-notify-secretary.sh` is the Herdr hook — it prompts the secretary agent (name from `KANBAN_HERDR_SECRETARY`, which `dispatch` always sets to that project's resolved secretary name — see **Secretary agent naming** above; a standalone invocation with no `KANBAN_HERDR_SECRETARY` resolves the same name itself from its own project root) to inspect structured state and History instead of treating every failure as a product verdict.

The wrapper starts an interactive agent whose `--kind` (`claude` or `codex`) follows the card's own routing — worker/operator backend from `KANBAN_CARD_BACKEND`, reviewer backend from `KANBAN_REVIEWER`, resolver backend from `KANBAN_RESOLVER`, and `auto` resolved via `KANBAN_BACKEND_ORDER`. Every role gets the **same** full-trust permission policy (see **UNRESTRICTED permission policy** below): Claude gets `--dangerously-skip-permissions` (or `--permission-mode <claude_perms>` when overridden away from `bypassPermissions`), while Codex gets `--dangerously-bypass-approvals-and-sandbox` (or `-s <codex_sandbox> -a <codex_approval>` when `codex_full_bypass` is `false`). Model selection remains role-specific and Codex never inherits the Claude default of `sonnet`; the card's `effort` applies uniformly to every role. The dispatcher compiles a role-specific temporary work order instead of resending the card ledger: workers/operators receive `Task` plus only the latest rework feedback, while reviewers and resolvers receive `Task` and inspect the current files/diff themselves. The accumulated `History` remains on disk. Herdr receives only the short path to that rereadable work order. The agent must write a card/role/attempt-bound ACK within 180 seconds; missing ACK is an infrastructure failure, while a complete answer is accepted as stronger implicit proof for compatibility. Non-review roles report acceptance criteria, actual verification commands/results, changes, deviations, and follow-ups under fixed headings.

The pane title and wrapper diagnostic identify the role and `UNRESTRICTED` policy. It accepts the folder-trust dialog, waits specifically for `idle`/`done`, and records `agent_started` only after Herdr accepts the task prompt; `agent_acknowledged` is a separate fact shown by the dispatcher. A rejected prompt becomes an immediate infrastructure retry instead of an invisible wait. Completion still requires a card/role/attempt-bound stable `.kanban-answer.md`, never terminal chrome. If an agent finishes chat without that file, the wrapper asks the same session once to write only the missing answer, waits up to 60 seconds, then reports `missing_answer` rather than rerunning the whole task silently. Panes are closed when the attempt ends.

Unattended agents never ask the user a question inside their worker pane. Claude starts with `AskUserQuestion` disallowed, and every role is instructed not to display interactive choices. A worker that needs a decision returns `BLOCKED: <reason>`; a reviewer returns score 0 with the missing decision in feedback. If an `Enter to select … Esc to cancel` choice UI still appears, the wrapper emits `agent_question` immediately instead of waiting until timeout.

### UNRESTRICTED permission policy

All agents run **without any permission prompt or sandbox restriction** by default: Claude gets `--dangerously-skip-permissions`, Codex gets `--dangerously-bypass-approvals-and-sandbox`. This is a deliberate default, not a bug — the dispatcher still runs inside a **visible** Herdr pane so a human can watch or interrupt it (see Herdr Integration above). Ordinary roles run in a card worktree; only an explicit `--operate` card runs in the main checkout.

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

`kanban run [-j N] [--once]` processes `todo/`; `-j N` runs N cards in parallel. It requires the visible worker wrapper supplied by `kanban-secretary.sh dispatch`; a bare invocation cannot fall back to `claude -p` or `codex exec`. New boards default to `jobs: 4`. Any positive integer is accepted: MornKanban imposes no artificial upper limit, though the machine's process, memory, API, and Herdr capacity still apply. Without `-j` or `KANBAN_JOBS`, the dispatcher uses `.kanban/KANBAN.md`'s `jobs:` value and re-reads it while running. Raising it fills new slots; lowering it keeps current jobs alive and only pauses new starts. In a git repository every card gets its own worktree, so parallel cards never touch the same checkout:

1. Create branch `kanban/<id>` and worktree `.kanban/wt/<id>` from the branch checked out at dispatch start.
2. Write the task plus only the latest rework feedback to a temporary work order, send its path to the visible Herdr worker, and require a startup ACK before work continues; commit the result on the card's branch. Accumulated History stays on disk and is not resent.
3. A separate review agent inspects the worktree itself (it must not trust the worker's claims) and outputs `{"score": 0-100, "feedback": "..."}`.
4. `score >= threshold` (default 80) → merge into the base branch (merges are serialized by a lock), delete the branch and worktree, card → `done`. Below threshold, the feedback is appended and the worker retries **in the same worktree**; after `max_attempts` (default 3) the card moves to `failed` and the branch is kept for inspection.
5. A merge conflict does **not** fail the card immediately. The card moves to `resolving` and a dedicated resolver role takes over — see below.

An `--operate` card is the narrow exception to worktree execution: it runs once in the project main checkout under the same lock used for merges, invokes no reviewer, and creates no card branch or merge. Use it only when the user explicitly authorizes push, deploy, publish, or another external mutation; the operator contract forbids unrelated implementation changes. An interrupted or unconfirmed operation moves to `blocked_kind: operation_unknown` instead of being replayed, because its external effect may already have occurred. After checking the real external state, use `kanban operation <id> done` to finish without replay, or `kanban operation <id> retry` only when another execution is known to be safe; `kanban resume` deliberately refuses this state.

### Conflict resolution (`resolving`)

A card that passed review can still conflict with `main` at merge time if another card landed first. Instead of dropping straight to `failed`, the dispatcher hands the conflict to a resolver role, a formal state distinct from an ordinary worker retry:

1. The card moves `doing`/passed review → `resolving`; the original card branch (`kanban/<id>`) is kept, never discarded.
2. A fresh worktree/branch (`.kanban/wt/<id>-resolve` / `kanban-resolve/<id>`) is created from the current `main`, and the card branch is merged into it, reproducing the conflict.
3. The resolver backend (`KANBAN_RESOLVE_CMD`, or the card's own backend/model/effort by default) is given the conflicted file list, both branch names, and the original task, and is instructed to **preserve both sides' intent — never discard one side outright** — and to run whatever tests the task requires.
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

### Declared and discovered dependencies (`blocked`)

`kanban add ... --depends-on <card-id>` and `kanban send ... --depends-on <card-id>` record an explicit dependency in card frontmatter. The target must already exist in the same destination board. Before dispatch, an unresolved target moves the card to `blocked` with `blocked_kind: dependency` and `dependency_state: <state>`; no worker/reviewer starts and no attempt is consumed. A `failed`, `blocked`, or missing dependency remains parked. The dispatcher moves it back to `todo` only when that exact dependency reaches `done`, including while the same dispatcher is still running.

If a worker or resolver discovers a genuine undeclared dependency mid-run, its stdout's first line is `BLOCKED: <reason>`. The dispatcher records `blocked_kind: ordering`, does not consume an attempt or review, discards its temporary worktree/branch, and reclaims it for a fresh attempt on the next dispatcher start. Workers must not deliberately fail a card merely because another result is still pending.

An interactive choice caused by a worktree-boundary conflict, project policy, or a missing user decision becomes `blocked_kind: user_input`. It consumes no attempt or review, notifies the secretary, and remains parked across dispatcher restarts; MornKanban never chooses an option for the user. After resolving the ambiguity, run `kanban resume <id>` and restart visible dispatch. `resume` only returns the card to `todo`; it never runs a headless worker or reviewer in the secretary pane. A diagnosis hard-timebox likewise remains parked as `blocked_kind: scope_timebox` until explicitly resumed.

### Failure semantics (`failure_kind`)

`failed` means the card's **work process** stopped unsuccessfully; it is not automatically a product-verification failure. The dispatcher records `failure_kind` as `infrastructure`, `worker`, `review`, `resolve`, `merge`, or `dispatcher`, and History carries the evidence. Infrastructure failures imply **unverified**, while `review` means the implementation/review gate was not satisfied; neither alone proves a production defect. The secretary reports that distinction and asks for user judgment when an irreversible action depends on missing verification.

Every worker/review/resolver/merge phase's wall-clock duration is appended to History as a `phase durations: worker=Ns review=Ns` (or `resolver=Ns`, `merge=Ns`) line and mirrored into the card's `last_timings` frontmatter key, so a slow card can be diagnosed as test time vs. agent/review wait without re-running it.

### Review infrastructure errors (`blocked`, kind `review_infra`)

A reviewer (or resolver review) that never returns a parseable `{"score": ...}` object — a visible Herdr pane/agent that disappeared (`agent_not_found`), a timeout, a wrapper/tool error, empty output, or leftover terminal chrome (a status line, another card's output) — is **infrastructure flaking, not a quality verdict**. It is never converted to `score: 0`:

1. `classify_review_infra_error` (kanban.sh) inspects the raw reviewer output only once JSON-score parsing has already failed, and tags it with a category (`agent_not_found`, `pane_lost`, `timeout`, `wrapper_error`, `empty_output`, `tool_error`, `unparseable_output`). `herdr-agent-worker.sh` also emits an explicit `KANBAN_INFRA_ERROR: <category>: <detail>` sentinel for the lifecycle failures it can detect directly (pane never started, agent never settled, `herdr agent get` itself failed, or a settled reviewer wrote no answer file); the same sentinel is honored on the worker/resolver side too (`classify_worker_infra_error`), applying the identical principle symmetrically.
2. The reviewer is retried **on the same worktree and commit** — no worker/resolver re-run — with a short bounded backoff (`review_infra_backoff_seconds`, default 2s × retry number, capped at 10s), up to `review_infra_max_retries` (`KANBAN.md`/card frontmatter/`KANBAN_REVIEW_INFRA_MAX_RETRIES`, default 2). This retry count is tracked independently of `attempts`/`resolve_attempts` and is reset to 0 once a real score is obtained.
3. Only a genuinely parsed JSON score is ever threshold-judged; every infra retry in between is invisible to the quality-review logic. History records each retry as `review infrastructure retry N/M: <category>` (worker-side: `worker infrastructure retry N/M: <category>`), distinct from a quality `review`/`rework instruction` entry.
4. If retries are exhausted, the card moves to `blocked` with frontmatter `blocked_kind: review_infra` — **not** `failed` — and its branch, worktree, and every commit so far are kept. History explains this is a review infrastructure stop, not a code failure, and names the recovery command.
5. Unlike the ordering-dependency `blocked` kind above, a `review_infra` blocked card is **not** auto-reclaimed to `todo` on dispatcher restart. It stays parked until `kanban resume <id>` resets the infra counters and requeues it; the visible dispatcher then resumes only the pending review on the kept worker or resolver branch. `review_pending` / `resolve_review_pending` checkpoints prevent completed work from being rerun, and merge checkpoints recover a crash before or just after merge without repeating the worker/reviewer.

Outside a git repository the unpinned `jobs: 4` default automatically falls back to one sequential in-place worker; only an explicit `-j`/`KANBAN_JOBS` greater than one is refused. No Git initialization or commit is attempted. Worker/resolver output tail, every review score, and rework instructions are appended to the card's History section. A restarted dispatcher reclaims safe local work, preserves resolver checkpoints, and parks an interrupted external operation as `operation_unknown`. The dispatcher's atomic single-instance lock guarantees no card is picked up twice.

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
| `KANBAN_JOBS` | Parallelism for `kanban run` (default 4; overridden by `-j`; any positive integer, no MornKanban upper cap) |
| `KANBAN_DISPATCH_POLL_INTERVAL` | Dispatcher scheduling/config-refresh interval in seconds (default `1`; tests may use a smaller decimal value) |
| `KANBAN_HERDR_ACK_GRACE_SECS` | Seconds allowed for a visible agent to acknowledge its work order (default `180`) |
| `KANBAN_HERDR_GET_TIMEOUT_SECS` | Maximum seconds for one Herdr agent-status query (default `10`; also bounded by remaining ACK time) |
| `KANBAN_HERDR_MISSING_ANSWER_GRACE_SECS` | Seconds allowed after the one focused same-session recovery prompt for a missing `.kanban-answer.md` (default `60`) |
| `KANBAN_REVIEW_ENABLED` | `true`/`false` (aliases: `1`/`0`, `yes`/`no`, `on`/`off`); overrides `KANBAN.md`'s `review_enabled`, but a card's own explicit `--review`/`--no-review` still wins — see **Review on/off** above |
| `KANBAN_WORKER_CMD` / `KANBAN_REVIEW_CMD` / `KANBAN_RESOLVE_CMD` / `KANBAN_OPERATION_CMD` | Full command overrides; use mock scripts to test state transitions without spending tokens |
| `KANBAN_NOTIFY_CMD` | Hook run as `<cmd> <done\|failed\|blocked> <title>` when a card settles; pane failures use `<cmd> dispatcher_failed <log-path> <exit-status>` (see Herdr Integration) |
| `KANBAN_DEBUG` | Write per-job xtrace logs to `.kanban/wt/job.*.trace` |
| `KANBAN_HERDR_SECRETARY` | Overrides the resolved per-project Herdr secretary agent name (see **Secretary agent naming**); no `KANBAN.md` counterpart of the same name — use the `secretary_agent:` frontmatter key for a persistent override instead |
| `KANBAN_REVIEW_INFRA_MAX_RETRIES` | Bounded reviewer-infrastructure-error retries before a card moves to `blocked` (kind `review_infra`); default 2 — see **Review infrastructure errors** |
| `KANBAN_REVIEW_INFRA_BACKOFF_SECONDS` | Base seconds for the review-infra retry backoff (`base × retry number`, capped at 10s); default 2 |

## Dispatcher pane view

The visible dispatcher pane uses `dispatcher_tui.py`, a Python-standard-library terminal UI. Its fixed upper region refreshes once per second and shows state counts, up to four active cards (`Showing M of N` semantics), attempts, work-order ACK (`-` not prompted / `…` waiting / `✓` received), each live agent's actual AI backend/model/effort, and the latest observed card move such as `TODO → DOING`. A value that the wrapper cannot observe because it is inherited from the agent is shown as `unknown`, never guessed. The lower region continuously shows the tail of the normal dispatcher log. Routing metadata precedes display-width-truncated Japanese titles so it remains visible inside a narrow pane.

The TUI follows terminal resizes and uses symbols plus labels rather than color alone. On a non-TTY, `TERM=dumb`, or a pane smaller than 40×10, it falls back to the original plain streaming log. Both modes write the complete raw child output to `.kanban/wt/dispatcher.log` and preserve the dispatcher's real exit status for failure notification.

Each visible worker/reviewer/resolver/operator also appends a bounded correlation event to `.kanban/activity.jsonl`. The newest 1000 events retain only operational metadata (timestamp, card id, role, attempt, backend/model/effort, Herdr agent name, pane id, event/status, and duration); prompts, answers, card descriptions, and credentials are never written. Card History and this log remain the durable diagnostic record after the live pane closes.

## Cross-Project Send (file a card into any registered project, from anywhere)

`kanban projects` and `kanban send` let you file a card into any project's `.kanban/todo/` regardless of the current directory or session — from inside project A into project B, from B into A, or from an unrelated directory into either, using a PC-wide alias registry.

### Registering projects

```sh
kanban projects add <alias> <path>      # register: realpath-normalized, must already have .kanban (run `kanban init` first)
kanban projects list [--json]           # alias -> root, one per line (or JSON)
kanban projects show <alias>            # root, .kanban dir, timestamps, dispatcher running/stopped
kanban projects update <alias> <path>   # repoint an existing alias at a new path
kanban projects remove <alias>          # unregister (send stops recognizing it immediately)
```

- Aliases are lowercase letters/digits/`-`/`_`, starting with a letter or digit, max 64 chars; anything else is rejected with a clear error.
- A path with no `.kanban` directory, a path that doesn't exist, a duplicate alias, or a path already registered under a different alias (checked by `realpath`, so a symlink or `..` traversal can't register a second alias for the same project) are all rejected — `--force` on `add` overrides the alias/path-duplicate checks explicitly.
- The registry file defaults to `${XDG_CONFIG_HOME:-~/.config}/mornkanban/projects.json`; `KANBAN_CONFIG_DIR` changes its config directory and `KANBAN_PROJECTS_FILE` changes the exact file.

### Sending a card

```sh
kanban send <alias> "title" [-b claude|codex|auto] [-m model] [-e effort] [--depends-on card-id] [-t threshold] [--diagnose|--operate] [--from PATH] < description
```

- The card is always created in the **destination** project's `.kanban/todo/` — never in the directory `kanban send` was run from. Body comes from stdin (same as `kanban add`); with no stdin it falls back to the title.
- `--diagnose` and `--operate` preserve the same mutually-exclusive task contracts as local `kanban add`, including review-off metadata for read-only diagnosis and serialized external operations.
- Unset `-b`/`-m`/`-t` default from the destination's own `.kanban/KANBAN.md` (`default_backend`/`default_model`/`threshold`), exactly like a card added locally with `kanban add` there; unset `-e` inherits the agent's shared effort setting. The sending project's policy is never consulted.
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
