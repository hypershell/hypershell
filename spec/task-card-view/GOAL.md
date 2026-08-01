# GOAL — Rich "card" view for tasks

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** task-card-view
- **kind:** feature
- **appetite:** big

## Problem

`hs list` / `hs search` (and single-task `hs info`) default to **`normal`** mode: a vertical
block per task built from `NORMAL_MODE_TEMPLATE`, ~30 fields deep, `---`-separated, some fields
compressed parenthetically, with a single **flat** color applied to the whole block from the
task's exit status. It reads well and the recent color-coding gives quick at-a-glance state
feedback — but it is **running out of vertical room**. We keep adding columns to `Task`
(`fingerprint`, `part`, …) and a **`zone`** field is coming next, so each task now costs a tall
scroll and the flat coloring can only say "green/red/yellow", not *what* the state actually is.

We don't want to lose `normal` mode — it's good for dense mental-compression scanning. Instead we
want a **new, opt-in display mode** that trades vertical stacking for horizontal layout: a richly
formatted, bordered **"card"** (baseball-card metaphor) per task that uses the `rich` package's
layout capabilities to spread metadata across the width of the terminal, group related fields into
labeled regions, and surface an explicit, *labeled* status badge instead of a flat wash of color.

## Outcome / vision

A user runs `hs list --format=card` (and `hs info --format=card`) and, instead of a tall flat
block, sees each task as a **self-contained bordered card** that:

- puts the identity fields (**id**, **fingerprint**, **group**, **part**, and — once it exists —
  **zone**) horizontally along the **top**;
- groups the rest of the metadata (submission/provenance, scheduling & timing, resources, output
  artifacts, retry chain, tags) into **logical, labeled regions** using color and emphasis for
  meaning rather than one flat field list;
- shows a virtual **`status: <STATUS>`** badge in the **lower-right** — `OK` in bold green,
  `FAILED` in bold red, `CANCELLED` in yellow, and so on for the rest of the lifecycle — replacing
  normal mode's flat whole-block coloring;
- **adapts to the terminal width**, choosing among *narrow / normal / wide* layouts between a
  **minimum of 60** and **maximum of 160** columns, so it looks right on a laptop split-pane and
  takes advantage of a full-width terminal.

Illustrative sketch only — **non-binding**; exact layout, glyphs, and region grouping are
`/hs-plan`'s job:

```
┌ task ─────────────────────────────────────────────────────────────────────┐
│ id 3f2a…9c1   fp 8b0e…   group 4   part 0                                   │
│                                                                            │
│ command   echo hello world              submitted  2026-07-31 10:12:04     │
│ source    tasks.txt (a1b2…)             started    2026-07-31 10:12:07     │
│ resources 1 core · 512 MB               completed  2026-07-31 10:12:09     │
│ output    out: …/3f2a.out  err: …/…     duration   00:00:02                │
│ tags      priority=high, phase=warmup                                      │
│                                                            status: OK      │
└────────────────────────────────────────────────────────────────────────────┘
```

## Acceptance criteria (the contract)

- **R1** — WHEN the user passes `-f/--format=card` to `hs list` / `hs search` **or** `hs info`,
  the CLI SHALL render each matched task as a card; `normal` SHALL remain the default and the
  existing `normal` / `plain` / `table` / `json` / `csv` outputs SHALL be unchanged (card is
  purely additive).
- **R2** — The card SHALL be a self-contained unit with an **outer border** built from a custom
  `rich` layout (not the existing `table` format), presenting **id, fingerprint, group, part**
  laid out **horizontally across the top** and leaving room for a future **`zone`** field in that
  header.
- **R3** — The remaining task metadata SHALL be organized into **labeled logical regions** (e.g.
  submission/provenance, scheduling & timing, resources, output artifacts, retry chain, tags)
  rather than one flat field list, using color/emphasis to aid scanning.
- **R4** — The card SHALL display a virtual **`status: <STATUS>`** indicator in the
  **lower-right** region, where `<STATUS>` is a human-readable label **derived from the task
  lifecycle** (not a stored column). It SHALL distinguish at least: **OK** (bold green), **FAILED**
  (bold red), **CANCELLED** (yellow), **RUNNING** (in-flight), **WAITING** (unscheduled), and an
  **abnormal** state (signal-kill / never-ran sentinels such as template/resource errors). This
  replaces normal mode's flat whole-block coloring.
- **R5** — WHILE the output width varies, the card SHALL **auto-select** among **narrow / normal /
  wide** layouts from the detected terminal width, clamped to a **minimum of 60** and a
  **maximum of 160** columns.
- **R6** — IF stdout is not a TTY or color is disabled (piped output, `NO_COLOR`, `COLOR_STDOUT`
  false), THEN the card SHALL still render legibly and the status SHALL remain readable as plain
  text.
- **R7** — `hs info --format=card` SHALL render a single task's card **consistent** with how
  `hs list --format=card` renders that same task (shared renderer).
- **R8** — The affected `docs/_include/*.rst` help snippets and the `share/` shell completions
  SHALL list `card` as a valid format value, updated in the **same commit** (repo convention).

## Non-goals (no-gos)

- **Not** removing, replacing, or restyling `normal` mode — it stays the default and unchanged.
- **Not** adding the `zone` column/field itself — that is separate upcoming work; here we only
  reserve a place for it in the header layout so the card is forward-compatible.
- **Not** an interactive/pager TUI — output stays line-oriented (cards printed in sequence), just
  as `normal` does today.
- **Not** applying the card treatment to `plain` / `table` / `json` / `csv`.
- **Not** adding a manual layout-override flag (e.g. `--width narrow|normal|wide`) — layout is
  auto-selected from terminal width. (Deferred; can be a follow-up if demand appears.)

## Clarifications

- **Q:** Which commands get the card view? — **A:** Both `hs list` / `hs search` **and**
  `hs info` (a card is inherently a single-task view; the renderer is shared) (resolved
  2026-07-31).
- **Q:** How is narrow/normal/wide chosen? — **A:** Auto-detected from terminal width, clamped
  60–160 cols; **no** manual override flag in this unit of work (resolved 2026-07-31).
- **Q:** Which states get a labeled status badge? — **A:** The full lifecycle set (OK / FAILED /
  CANCELLED / RUNNING / WAITING / abnormal), derived from the nullable-column lifecycle, not just
  `exit_status` (resolved 2026-07-31).
- **Q:** What is the mode named? — **A:** `card` (resolved 2026-07-31).

## Related materials

- Current `normal` renderer & color logic — `src/hypershell/task.py`: `NORMAL_MODE_TEMPLATE`
  (~L1294), `print_normal` (~L1382), `select_color` / `select_style` / `SPECIAL_TASK_COLORS`
  (~L1328–1360), `TaskSearchApp` output-format wiring (~L605–845), `TaskInfoApp` (~L160–360).
- Task fields & lifecycle contract — `src/hypershell/data/model.py`: `Task.columns` (~L305),
  `CANCEL_STATUS` (L49), and the "Task lifecycle contract" / `exit_status` reserved ranges in
  `AGENTS.md` (source of truth for what "status" means: unscheduled / in-flight / done /
  cancelled, and the `-1..-64` signal + `<-1000` never-ran sentinels).
- Docs & completions to update — `docs/_include/*.rst` (list help), `share/` (bash+zsh
  completions).
- Dependency — `rich` is already a runtime dependency (used by `table` / `json` rendering via
  `rich.Console` / `rich.Table` / `rich.Syntax`).
