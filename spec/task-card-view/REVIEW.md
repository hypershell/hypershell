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

---

## Review cycle 2 — changes-requested (2026-08-01)

- **Reviewed commit:** dbfe83b93ab04c59cdabda8bec987eb1d22bef75  ·  **Base:** develop
- **Trigger:** cycle 1 approved at `232b277`, then two commits landed after it — `b67d827`
  (*Refine: submission region + source fingerprint*, re-touching `data/model.py`) and `dbfe83b`
  (*docstring reformat*). `last_reviewed_commit` was stale, so this is a **fresh full blind pass**
  over `git diff develop...HEAD -- . ':(exclude)spec/'` (not a scoped remediation pass — cycle 1
  had no findings to remediate).
- **Mode:** single blind `general-purpose` reviewer (curated inputs: `GOAL.md` inline,
  spec-excluded diff, `invariants.md`, `review-rubric.md`; denied `PLAN.md`/`TECH.md`/`research/`/
  `META.md`/prior `REVIEW.md`). Orchestrator second-pass sanity check by code inspection.

### Verification run (executed, not asserted)

- `uv run pytest -q -m unit -k "card or status_label"`, `uv run pytest -q -k card`, and the full
  `uv run pytest -q` suite — **459 passed**; the only failure (`test_groups.py::
  test_group_hard_failure_halts`) is a **pre-existing timing-flaky** server group-gating test that
  passes in isolation and lies on a path this diff does not touch.
- Real CLI in a throwaway site (`.agents/factory/bin/temp_site.sh`, never the developer's DB):
  OK/FAILED/WAITING badges correct; shell-metachar commands (`sed 's/x/[/]/'`,
  `echo '[bold]hi[/bold]'`) render verbatim (rich-markup safety holds); piped non-TTY + `NO_COLOR=1`
  emit zero ANSI with border + literal `status:` intact (**R6**); `hs info … --format=card` matches
  `hs list` for the same id (**R7**); `json`/`csv`/`table`/`plain`/`normal` unchanged (**R1**).
- Width sweep `COLUMNS` 30/60/100/150/200 → rendered widths 30/60/100/150/**160**: the **max** clamp
  holds; the **min-60** clamp does **not** (finding C2-1 below).
- Non-goal proof: the `format_task_fields` extraction is behavior-preserving — full suite (incl.
  existing `normal` tests) green; the only textual delta (`int(format_json(x))`→`int(x)` for the
  INTEGER `waited`/`duration` columns) is a verified identity.
- `git status --porcelain` empty at reviewer hand-back (tree left clean); orchestrator re-verified
  the `data/model.py` additions are read-only (`Task.status_label` property + `Source.
  fingerprints_for_ids` batched query mirroring `paths_for_ids`) — no query/state-transition or
  `exit_status`-range logic altered (§1/§2 clean).

### Findings

**C2-1 — MEDIUM · CONFIRMED · R5 (partial).** The min-60 width clamp is **inert below 60 columns**,
so the card renders narrower than the contracted floor and the identity header (full UUID +
fingerprint) is truncated with an ellipsis.

- **Where:** `render_card`/`card_width` (`src/hypershell/task.py:1480`, `1516`) are correct in
  isolation, but both call sites — `TaskInfoApp.run` (`task.py:207-208`) and
  `TaskSearchApp.print_card` (`task.py:805-810`) — print into a **bare `Console()`** sized to the
  real terminal while passing the clamped width only to the `Panel`. Rich clips a Panel wider than
  its Console to the console width, defeating the min-60 floor.
- **Failure scenario:** `COLUMNS=30 hs list --format=card` →
  `id 019fbe23-7da9-7b11-9801-0…` (UUID truncated, uncopyable); `fp 5ebb0a127fb5bd6… g… …`
  (fingerprint + group/part truncated). Contradicts R5's explicit "minimum of 60" and the
  renderer's own docstring guarantee ("the id … never truncated"). `normal` mode preserves the full
  id at the same width.
- **Severity rationale:** edge path (default piped width 80; typical TTYs ≥ 80), and clipping is a
  *defensible* fallback — hence MEDIUM, not HIGH. But it is a real, reproducible gap against a stated
  acceptance criterion.
- **Fix direction:** size the console to the clamped width at both call sites —
  `Console(width=card_width(console.size.width))` — so a sub-60 terminal renders a structurally
  60-wide card (id on its own line, intact; terminal soft-wraps/scrolls the overflow, matching how
  `normal` mode's long lines already behave) instead of truncating. Add a render-through-a-narrow-
  console regression test (the existing `test_id_never_truncated_even_at_min_width` forces the width
  directly and so does not exercise the call-site console-sizing path).

No other correctness bugs, invariant violations, or scope creep survived the refutation protocol.

### Requirement → evidence matrix (cycle 2)

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 — additive; other formats unchanged | full suite + live normal/plain/json/csv/table; `format_task_fields` equivalence proof | ✅ |
| R2 — bordered custom layout; id/fp/group/part horizontal + zone slot | live render 100/150; unit tests | ✅ (id truncation <60 → C2-1) |
| R3 — labeled regions w/ color/emphasis | live render shows all region titles; unit tests | ✅ |
| R4 — lower-right lifecycle `status:` badge, ≥6 states | `status_label` + `STATUS_STYLES`; live OK/FAILED/WAITING; parametrized tests | ✅ |
| R5 — auto narrow/normal/wide, clamp [60,160] | width sweep 30–200 | ⚠️ **partial** — max clamp holds, **min-60 inert** (C2-1) |
| R6 — non-TTY/NO_COLOR legible, status plain | live piped + `NO_COLOR=1`; unit test | ✅ |
| R7 — info card == list card (shared renderer) | live `hs info` vs `hs list` same id; integration test | ✅ |
| R8 — docs snippets + completions list `card`, same commit | grep of 3× rst + bash + zsh | ✅ |

**Unmapped changes:** none blocking. Docstring reformatting (`dbfe83b`) of pre-existing functions is
comments-only/behavior-inert (house style); the source content-fingerprint parenthetical sits within
R3's submission/provenance region.

### Human-gate triggers

**None mandatory.** The sole CONFIRMED finding (C2-1) is in `task.py` rendering + its call sites —
**not** a high-blast-radius core file and not a security/DB-lifecycle invariant. The `data/model.py`
additions this cycle were re-verified read-only with no finding. Verdict routed to
`changes-requested` per the rubric (any CONFIRMED finding loops to `/hs-build`); the fix is small and
localized to the two call sites.

---

## Review cycle 3 — approved (2026-08-01)

- **Reviewed commit:** b3fd5b79e2d5ac578225d33b692558c9e5ae0a74  ·  **Base:** develop
- **Mode:** **scoped remediation-verification** of cycle-2 finding **C2-1** (not a fresh full blind
  pass — the remediation delta since `dbfe83b` is only `task.py` +20/−4 and `test_card_render.py`
  +16/−1; no `data/model.py`/coupled-core change). Delegated to a fresh blind subagent to keep the
  executed-evidence spine; denied PLAN/TECH/research/META/prior-REVIEW.

### C2-1 — CLOSED

The fix adds a shared `card_console()` (`task.py:1485`) = `Console(width=card_width(Console().size.
width))`, and routes both card call sites (`TaskInfoApp.run` `task.py:207`, `TaskSearchApp.print_card`
`task.py:805`) through it, so the **console** — not just the Panel — carries the clamped width. A
wider-than-terminal Panel is no longer clipped.

- Executed at `COLUMNS=30` (throwaway site): `hs list --format=card` and `hs info <id> --format=card`
  both contain the **full task id** (`LIST_OK` / `INFO_OK`) — no truncation.
- **R5** closed (unit `test_card_console_clamps_terminal_width`: 30→60, 9999→160, 100→100).
- **R6** non-regressed: piped + `NO_COLOR=1` emits **zero ANSI**, `status:` literal (fixing `width`
  leaves `is_terminal` detection intact).
- **R1** non-regressed: default (~80) card renders; `normal`/`json`/`csv` unchanged; `normal` default.
- Card suite `-k "card or status_label"` → **38 passed**. Tree clean on hand-back.

**New findings:** none. **Human-gate triggers:** none (fix is in `task.py`, not a high-blast-radius
core file or security/DB invariant). Verdict: **approved** → `/hs-publish`.
