# PLAN — Rich "card" view for tasks

> **Status:** Draft for review · **Last updated:** 2026-08-01
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Add an opt-in **`card`** output format to `hs list`/`hs search` and `hs info` (plus `hs wait --info`
for parity). Each task renders as a `rich` **`Panel`** (rounded border, `title="task"`) whose body is
a horizontal identity **header** (id · fingerprint · group · part · reserved zone) over **labeled
regions** of the remaining metadata, with a virtual **`status: <STATUS>`** badge pinned to the
lower-right **via the Panel subtitle**. Layout is **responsive** — the effective width is clamped to
[60, 160] and selects narrow/normal/wide. The status label is derived from the task lifecycle by a
new read-only **`Task.status_label`** property (model-side, per invariant §1); the label→color map is
presentation-side. No schema change, no new dependency, no touch to server/client/queue.

## 2. Design

**Status derivation (`data/model.py`).** New read-only property `Task.status_label -> str` on the
`Task` model, implementing the order-critical ladder (WAITING → RUNNING → OK → CANCELLED → ERROR →
KILLED → FAILED → UNKNOWN; see [`research/02`](research/02-status-lifecycle-derivation.md) and the
digest). It re-derives the same nullable-column predicates the model already uses
(`schedule_time`/`completion_time`/`exit_status == CANCEL_STATUS`), so it belongs with the model, not
scattered into `task.py`. Never-ran sentinels are classified by the **documented `exit_status <=
-1000` range** — we do **not** import `TASK_TEMPLATE_ERROR`/`TASK_RESOURCE_ERROR` from `client.py`
(circular + heavy import). Pure function of already-loaded columns; adds no query and changes no
state.

**Label → style (`task.py`).** A `STATUS_STYLES: Dict[str, str]` beside the existing
`select_style`/`SPECIAL_TASK_STYLES`, mapping OK→`bold green`, FAILED→`bold red`, CANCELLED→`yellow`,
RUNNING→`cyan`, WAITING→`dim`, ERROR/KILLED/UNKNOWN→`magenta`. The card uses this one map for both the
badge text and the Panel border color. CANCELLED=`yellow` is a **deliberate card-specific palette**
(GOAL-fixed) that intentionally differs from `select_style`'s exit-status-only `dim`; the two coloring
systems coexist (normal/plain/table keep `select_color`/`select_style` unchanged).

**Renderer (`task.py`).** `render_card(task, width, source_map=None) -> Panel` — a pure function
returning the rich renderable for a given clamped width. It reuses `print_normal`'s field-prep
(waited/duration/timeout→`timedelta`, memory→`format_bytes`, cores, source→`resolve_source`,
tags→`format_tag`) and composes: `Panel(Group(header_grid, "", regions), title="task",
title_align="left", box=box.ROUNDED, width=width, padding=(0,1), subtitle=f"status:
{task.status_label}", subtitle_align="right", border_style=STATUS_STYLES[...])`. Regions group the
NORMAL_MODE_TEMPLATE fields logically — **command**, **timing** (submitted/scheduled/started/
completed + waited/duration/timeout), **resources** (cores/memory req vs used), **execution hosts**
(submit/server/client host+id), **output** (out/err/csv paths), **retry** (attempt/retried/
previous/next), **source**, **tags** — as `Table.grid` label:value blocks, side-by-side via `Columns`
in normal/wide and stacked in narrow. **The id is never truncated** (it's actionable); the fingerprint
may abbreviate in narrow. Exact narrow composition is a build detail within R5.

**Width & color (`task.py`).** One `Console()`; `W = max(60, min(160, console.size.width))`;
thresholds `<90` narrow / `90–129` normal / `>=130` wide. Non-TTY yields width 80 → narrow, rich
auto-strips ANSI, and the status is always literal text in the subtitle — so R6 holds on one code
path. `BrokenPipeError` stays handled by the existing `handle_broken_pipe` mapping.

**CLI wiring.** `TaskSearchApp`: add `'card'` to `output_formats`; add `print_card(self, results)`
(mirrors the static `print_normal`: rebuild `Task` objects, `Source.paths_for_ids` batch, one
`Console()`, loop `console.print(render_card(task, W, source_map))`); extend the subset-rejection
guard in `check_output_format` from `== 'normal'` to `in ('normal','card')`. `TaskInfoApp`: add
`'card'` to `output_formats` and branch `run()` to `render_card` the single task. `TaskWaitApp`: add
`'card'` to its forwarded `output_formats` for parity. No conflict with the `--json`/`--csv`/`--yaml`
mutually-exclusive const groups (card has no alias; it's accepted purely via `choices`).

**Docs/completions (same PR).** Help strings (`SEARCH_HELP`/`INFO_HELP`/`WAIT_HELP`), argparse
`choices`, `docs/_include/task_{search,info,wait}_help.rst`, `share/bash_completion.d/hs`,
`share/zsh/site-functions/_hs`, and regenerated `share/man/man1/*.1`. Exact file:line list in
[`research/05`](research/05-docs-completions.md).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `'card'` added to `output_formats` on `TaskSearchApp`/`TaskInfoApp`(/`TaskWaitApp`); `print_card` dispatch; `normal` stays default; other formats untouched |
| R2 | `render_card` → `Panel(box.ROUNDED)` with a `Table.grid` header row for id·fingerprint·group·part·(zone) |
| R3 | `Group(header, "", regions)` with `Table.grid` label:value region blocks (command/timing/resources/hosts/output/retry/source/tags) |
| R4 | `Task.status_label` ladder (model) + `STATUS_STYLES` (presentation) rendered as the lower-right Panel `subtitle` |
| R5 | `W = clamp(console.size.width, 60, 160)`; `<90/90–129/>=130` → narrow/normal/wide region layout |
| R6 | Single code path: rich strips ANSI on non-TTY, status always literal text; `BrokenPipeError` handled |
| R7 | `hs info --format=card` and `hs list --format=card` both call the same `render_card` |
| R8 | Help strings + `docs/_include/*` + bash/zsh completions + regenerated man pages, in-PR |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research and again after
this design.

- **§1 Task lifecycle** — `Task.status_label` reproduces the exact nullable-column predicates
  (unscheduled/in-flight/done/cancelled) and lives as a read-only `Task` property (state-query logic
  on the model, not in FSM/driver code). It adds **no query** and performs **no state transition** —
  display only.
- **§2 exit_status overloading** — the ladder honors every reserved range: `0` OK, `>0` FAILED,
  `CANCEL_STATUS(−1)` CANCELLED (checked before other negatives), `−2..−64` KILLED, `<=−1000` ERROR
  (never-ran). No failure/retry/count query is added, so the "`!= 0 AND != CANCEL_STATUS`" rule can't
  be violated — nothing here can resurrect a cancelled task.
- **§12 conventions** — docs/_include help + bash/zsh completions land in the same PR as the CLI
  change; man pages regenerated (generated asset); no new return codes (card reuses existing
  success/`ArgumentError` paths and `cmdkit.app.exit_status`); idiomatic Python with real-equivalence
  care.

Not touched: §3 retry model (card displays `attempt`/`retried`/chain but changes no retry logic),
§4–§11 (no server/client/queue/FSM/signal/config/cluster changes).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

*(CANCELLED=yellow vs `select_style`'s `dim` is a GOAL-specified presentation choice on a new
palette, not an invariant bend — `select_style` and normal/table coloring are unchanged.)*

## 4. Rabbit holes (resolved)

- **Can rich pin a badge to the lower-right corner without a custom layout?** → Yes: Panel
  `subtitle=..., subtitle_align="right"` renders it flush in the bottom border; live-verified at
  w=70 and w=120 ([`research/01`](research/01-rich-card-layout.md)).
- **Where does status derivation belong, given it needs the whole row (not just exit_status)?** →
  A read-only `Task.status_label` property on the model; existing `select_color`/`select_style` stay
  (exit_status-only) for the other formats ([`research/02`](research/02-status-lifecycle-derivation.md)).
- **How to classify never-ran tasks without a circular import?** → By the documented `exit_status
  <= -1000` range, not by importing the `client.py` sentinels ([`research/02`](research/02-status-lifecycle-derivation.md)).
- **How to get a clamped, testable width incl. the piped case?** → `console.size.width` (free 80-col
  pipe fallback + `COLUMNS` honored), clamped to [60,160]; tests force width via a constructed
  `Console` ([`research/04`](research/04-tty-width-color.md)).
- **Shared renderer without an import cycle?** → Keep `render_card` in `task.py` next to
  `print_normal`; do not move to `core/pretty_print.py` ([`research/03`](research/03-output-wiring.md)).

## 5. Risks & open questions

- **Man-page regeneration noise.** `sphinx-build -b man` may emit a date/line diff beyond the `card`
  additions. *Mitigation:* regenerate in the final phase and inspect; if the diff is only `card` (±a
  date line) keep it, otherwise **defer man to the next `/hs-release`** — the bash/zsh completions and
  `docs/_include` snippets are the hard §12 requirement and are done with their CLI phases regardless.
- **`hs wait --info -f card` parity** is included (small, adjacent) to avoid a spurious rejection;
  it's technically beyond the GOAL's stated `list`+`info` scope. Flag for sign-off — trivial to drop
  if unwanted.
- **Narrow-mode header at 60 cols.** Full 36-char id + 32-char fingerprint can't sit horizontally;
  the header stacks the id on its own line in narrow. Exact composition is a build decision inside R5;
  the invariant is *never truncate the id*.
- **Status badge in the border subtitle** is elegant but modest; if it reads as too subtle at narrow
  widths, the fallback is a right-justified bottom body cell (`Table.grid(expand=True)`), same map —
  no contract change.

## 6. Verification strategy

- **Unit** (`tests/`, `@mark.unit`):
  - `status_label` ladder — construct `Task` objects covering each branch (unscheduled; scheduled but
    incomplete; exit 0; `CANCEL_STATUS`; `-1001`; `-9`; `>0`; completed-with-null-exit) and assert the
    label.
  - `render_card` — render real `Task` objects through a **width-forced `Console`** (capture to a
    buffer) at 60/70/120/150; assert the id appears in full, region labels present, `status: <LABEL>`
    text present, and the panel honors the clamp; assert a non-TTY/no-color render still contains the
    border + literal status text.
  - `check_output_format` rejects `--format=card` with a field subset (parity with `normal`).
- **Integration** (`@mark.integration`, shells out to the installed CLI): submit a handful of tasks in
  a throwaway site and drive `hs list --format=card` and `hs info <id> --format=card`; grep the output
  for a border glyph and `status:`.
- **CLI eyeball** during build:
  `.agents/factory/bin/temp_site.sh sh -c "seq 20 | uv run hsx -t 'echo {}' -N4 && uv run hs list --format=card"`,
  repeated under `COLUMNS=70` and `COLUMNS=140` to see narrow/wide.
- **Docs:** `uv run sphinx-build docs docs/_build` clean (no new warnings); grep completions + man for
  `card`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
