---
name: kanban-report
description: "Create a graphical MornKanban activity report for a user-requested day or date range as an editable slide deck and PDF. Use only when the user asks for a Kanban report, retrospective, work recap, or postmortem; never run it automatically."
---

<!-- MORNKANBAN_INSTALLER_MANAGED -->

# MornKanban period report

The authoritative MornKanban checkout is `__MORNKANBAN_REPO__` (installed
version `__MORNKANBAN_VERSION__`). Read its `README.md` activity-log and
failure-semantics sections before collecting evidence.

This is a user-requested reporting action, not project implementation. It may
read board records and create only the requested `.pptx`/`.pdf` deliverables
outside the project, including while the dialogue agent is acting as the
MornKanban secretary. It must not change a board, project source, registry,
configuration, or any external system.

## Resolve the request

- Run only after an explicit natural-language request or `$kanban-report`.
  Never schedule it or generate it merely because a board settled.
- Default period: today from 00:00 through now in the user's current timezone.
  Respect a stated day, start/end, or relative period and print the resolved
  inclusive period and timezone in the report.
- Default scope: the MornKanban project containing `$PWD`. Use multiple or all
  registered projects only when the user asks; then read
  `~/.config/mornkanban/projects.json` without modifying it.
- Default audience: the user who requested the report. Default purpose: a
  concise operational recap. Ask only when scope or dates cannot be resolved
  safely.

## Evidence and semantics

Use only local MornKanban evidence:

- `.git/kanban/{todo,doing,review,resolving,blocked,done,failed}/*.md` for card
  titles, current states, attempts, structured kinds, and timestamped History.
- `.git/kanban/activity.jsonl` for agent lifecycle, role, backend/model/effort, attempt,
  status, and duration. It contains at most the newest 1000 events.
- `.git/kanban/wt/dispatcher.log` only when a dispatcher failure in the requested
  period needs explanation.

Never run any Git command, and never infer work from commits, branches, or
diffs. Do not include full prompts, terminal transcripts, credentials, or
irrelevant absolute paths. Summarize card outcomes in plain language.

Treat a card as period-relevant only when its `created` timestamp or a
timestamped History entry falls inside the period. A card directory gives its
current state, not necessarily its state at the historical period end; label
that distinction whenever History does not prove the transition time.
`failed` is a work-process failure, not automatically a product defect.
Separate `failure_kind`, `blocked_kind`, infrastructure failure, product
failure, dependency wait, and unverified work. Never turn absence of evidence
into success or failure.
Treat `blocked_kind: user_input` as a pending user decision, not a failure or
an attempted execution.

If `activity.jsonl` has 1000 rows and its earliest retained event overlaps the
requested period, disclose that agent-level counts may be incomplete. State
other missing or contradictory evidence on the relevant slide rather than
silently estimating it.

## Build the report

For Codex, read and follow the installed `Presentations` and `pdf` skills
before authoring. In other environments, use the native presentation/PDF
workflow and perform equivalent render-based visual QA. Create the editable
slide deck first, export that same deck to PDF, then render and inspect every
PDF page. Do not independently redraw the PDF.

Use Japanese unless the user asks otherwise. Default to a clean 16:9 editorial
timeline style: deep navy, teal for completed work, amber for waiting or
unverified work, red only for confirmed failures, generous whitespace, and no
dense dashboard-card wall. Every deck must contain at least one evidence-based
visual such as an hourly/daily timeline, outcome distribution, or agent-role
duration chart. Scale one-day reports by hour and longer reports by day or week.

Choose only sections supported by evidence, normally:

1. title, resolved period, timezone, and project scope;
2. headline outcomes and current board snapshot;
3. chronological work timeline;
4. delivered work grouped by outcome rather than raw file changes;
5. agent execution: roles, attempts, duration, retries, and infrastructure
   interruptions;
6. failures, blocks, unverified items, and unresolved decisions;
7. evidence-backed takeaways and next actions.

Omit empty sections. For no matching records, create a single clear “記録なし”
page naming the period and sources checked. Use “postmortem” only when the user
asks for an incident analysis or the evidence establishes an incident; an
ordinary period recap is an activity report or retrospective.

Put source file paths and the snapshot generation time in speaker notes or a
small methodology footer. Never invent productivity scores, cost estimates,
causal claims, or recommendations unsupported by the records.

## Deliver

Use the user's destination when stated; otherwise write both files to the
Desktop as `MornKanban-Report-<period>.pptx` and
`MornKanban-Report-<period>.pdf`. Deliver both unless the user requests only
one format. Report the resolved scope/period and any material data gaps in one
short handoff. If slide authoring or PDF rendering is unavailable, stop and
name the missing capability instead of returning an unverified artifact.
