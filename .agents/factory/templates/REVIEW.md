# REVIEW — {Title}

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** {sha}  ·  **Base:** {base}  ·  **Date:** {YYYY-MM-DD}
- **Verdict:** approved | changes-requested
- **Cycle:** {n} of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

## Verification run

Commands actually executed and their outcomes (the spine of the review):

- `uv run pytest -m unit` → <result>
- `.agents/factory/bin/temp_site.sh sh -c "seq 100 | uv run hsx -t 'echo {}' -N4 && uv run hs list"` → <observed behavior>
- <docs build / other CLI drives when relevant>

## Requirement → evidence matrix

Bidirectional traceability. Flag requirements with no implementing change **and** changes that map
to no requirement (scope creep).

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1   | <…>                          | <command>    | ✅ / ❌ |

Unmapped changes (possible scope creep): <list or "none">.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### [CRITICAL/CONFIRMED] <one-line defect>
- **Where:** `file:line`
- **Failure scenario:** <concrete inputs/state → wrong output/crash>
- **Evidence:** <the command run and what it showed>
- **Touches invariant / requirement:** <R-ID or invariant name>

## Human-gate triggers

Set if any CONFIRMED finding touches the high-blast-radius core (`data/model.py`, `server.py`,
`client.py`, `core/queue|fsm|thread|signal.py`) or a security/DB-lifecycle invariant — these
**always** require human sign-off before `hs-publish`, regardless of auto-loop.

- <triggered? which finding?>

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Was every planned phase actually shipped? Did scope balloon beyond the appetite? <notes>
