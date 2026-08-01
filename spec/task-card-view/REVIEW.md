# REVIEW — Rich "card" view for tasks

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 232b2773caf4d7d17cc2c0acd39e7f1a37ff6b96  ·  **Base:** develop  ·  **Date:** 2026-08-01
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Verification run

Blind correctness pass delegated to a fresh `general-purpose` reviewer (curated inputs: `GOAL.md`
inline, spec-excluded diff `git diff develop...HEAD -- . ':(exclude)spec/'`, `invariants.md`,
`review-rubric.md`; denied `PLAN.md`/`TECH.md`/`research/`/`META.md`). Commands actually executed
and their outcomes (the spine of the review):

- `uv run pytest -q -m unit -k "card or status_label"` → **34 passed**.
- `uv run pytest -q -k card` → **27 passed** (incl. the `@mark.integration` card test).
- `uv run pytest -q` (full suite) → **458 passed** in ~252s — **no regressions** (covers the
  existing `normal`-output tests, proving the `format_task_fields` extraction is behavior-preserving).
- Real CLI in a throwaway site (`.agents/factory/bin/temp_site.sh`, never the developer's DB):
  - **OK** cards (`hsx` run to completion), **FAILED** cards (`false`, `sh -c 'exit 3'`), **WAITING**
    cards (submit-only) — all with the correct lower-right badge.
  - Shell-metachar commands `sed 's/x/[/]/'` and `echo '[bold]hi[/bold]'` render **verbatim** — no
    `MarkupError`, no bracket stripping (the `rich.text.Text` cell-wrapping holds).
  - Piped (non-TTY): every line exactly 80 wide, box border intact, `status: OK` survives as plain
    text; `NO_COLOR=1` piped: no ANSI emitted, border drawn, status readable (**R6**).
  - `hs info <id> --format=card` renders the **same** card as `hs list` for that id (**R7**).
  - `json` / `csv` / `table` / `plain` / `normal` outputs all **unchanged** (**R1** non-goal held).
  - `hs list id --format=card` correctly rejected: *"Cannot use --format=card with subset of field names"*.
  - Controlled `render_card` at widths 60/70/80/90/100/130/150/160 → every line exactly the target
    width (border alignment); `card_width(10)==60`, `card_width(9999)==160` (**R5** clamp).
- `uv run sphinx-build docs docs/_build` → only pre-existing warnings
  (`task_submit.rst`/`manual.rst` toctree + `pkg_resources` deprecation); **no new warnings**.
- `git status --porcelain` empty at reviewer hand-back (tree left clean).

Orchestrator sanity check: independently inspected the `data/model.py` diff — a single additive
read-only `status_label` property, no query/state mutation, no new column; the lifecycle ladder
reproduces the nullable-column predicates exactly and guards `None` before the numeric comparisons.

## Requirement → evidence matrix

| R-ID | Implemented by (file) | Verified how | Status |
|------|-----------------------|--------------|--------|
| R1 — card additive; `normal` default; other formats unchanged | `task.py` `output_formats`/dispatch (L172/199/218, L751); info branch (L206–208) | Full suite 458 passed (normal tests intact); live CLI json/csv/table/plain/normal unchanged; subset guard rejects `card` | ✅ |
| R2 — self-contained border, custom layout (not `table`), id/fp/group/part horizontal + room for `zone` | `render_card` → `Panel(box=ROUNDED)` over `Table.grid`; identity header (L370–385) with reserved `zone` cell | CLI shows `╭─ task …╮` border, header row across the top; distinct from `print_table` | ✅ |
| R3 — labeled logical regions w/ color/emphasis | `card_region` titled blocks: command/timing/resources/execution/output/retry/result/tags (L387–415) | CLI shows all region titles (`bold underline`), labels `dim` | ✅ |
| R4 — lower-right `status:` badge from lifecycle, ≥6 states | `Task.status_label` (model.py:349–366) + `STATUS_STYLES` (task.py:260–269); Panel `subtitle`, `subtitle_align='right'` | Parametrized test + live OK/FAILED/WAITING; `None`-guard before numeric compare (no crash) | ✅ |
| R5 — auto narrow/normal/wide, clamp [60,160] | `card_width` clamp; `ncols = 1 if <90 else 2 if <150 else 3`; header stacks <130 | 1/2/3-col verified at 70/100/155; clamp at 40/300 → 60/160 | ✅ |
| R6 — non-TTY / NO_COLOR / COLOR_STDOUT-false legible; status plain | `Text` cells + rich off-terminal style drop | Piped 80-wide aligned; `NO_COLOR=1` no ANSI, border intact, `status:` plain | ✅ |
| R7 — `hs info --format=card` == `hs list` card (shared renderer) | Both call `render_card`; `hs wait` delegates to `TaskInfoApp` | Live CLI: identical single card for the same id | ✅ |
| R8 — docs snippets + completions list `card`, same commit | `task_{info,search,wait}_help.rst`, `bash_completion.d/hs`, `zsh/site-functions/_hs` | grep: every `-f/--format` completion in both shells includes `card`; updated in P3/P4 commits | ✅ |

**Unmapped changes (possible scope creep):** none. `format_task_fields` is a behavior-preserving
extraction shared by `normal` and the card (supports R3/R7, keeps `normal` byte-identical — a
refactor, not a restyle, so the "normal unchanged" non-goal holds).

## Findings

**None.** No CRITICAL / HIGH / MEDIUM / LOW correctness findings survived the refutation protocol.
The diff is clean against GOAL R1–R8 and the AGENTS.md invariants.

Invariant check (informational, no violation): `data/model.py` gains only a read-only `status_label`
property — no query, no state mutation. It reproduces the nullable-column lifecycle predicates
exactly (`schedule_time`→WAITING, `completion_time`→RUNNING, `==CANCEL_STATUS`→CANCELLED **before**
the signal range, `<=-1000`→ERROR, `None`-guard before numeric compare) and honors the `exit_status`
reserved ranges. Correctly placed in the model per §1. No footgun tripped.

Transparency note (not a finding): the generated man pages
`share/man/man1/{hs,hsx,hyper-shell}.1` still list the old format set and do not yet mention `card`.
This is **consistent with the documented repo workflow** — man pages are a release-time generated
aggregate rebuilt by `/hs-release`; the §12 same-commit rule names `docs/_include` + `share/`
completions, both of which **are** updated. Flagged only so the `card` man lines are not forgotten
at the next release.

## Human-gate triggers

**None triggered.** The only high-blast-radius file touched (`data/model.py`) received a purely
additive read-only property with **no** CONFIRMED finding against it, and no security/DB-lifecycle
invariant was weakened. No human sign-off gate is forced by this cycle.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

Not run this cycle (invoked as plain `/hs-review`). All five planned phases (P1–P5) are marked `done`
in `TECH.md` and the requirement→evidence matrix shows full R1–R8 coverage; scope stayed within the
`big` appetite with no unmapped changes.
