# REVIEW — Graceful guidance when croniter is missing for time-based log rotation

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 44905d19a148f2419e49316f2950d52b61638292  ·  **Base:** develop  ·  **Date:** 2026-07-24
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Verification run

Commands actually executed (blind `general-purpose` reviewer, curated inputs: `GOAL.md` + spec-excluded
diff + `invariants.md` + `review-rubric.md`; denied `PLAN.md`/`TECH.md`/`research/`/`META.md`):

- `git diff develop...HEAD -- . ':(exclude)spec/'` → only `src/hypershell/core/logging.py` and
  `tests/test_logging.py` changed (blind-review pathspec confirmed applied).
- `uv run pytest -v tests/test_logging.py` → **42 passed**.
- `uv run pytest -q -m unit` → **169 passed, 252 deselected** (no regressions).
- **R1/R2 independent repro** — subprocess injecting `sys.modules['croniter']=None`,
  `HYPERSHELL_LOGGING_FILE_ROTATE=@daily`, real CLI in a throwaway site
  (`.agents/factory/bin/temp_site.sh`): stderr =
  `CRITICAL [core.logging] Missing optional dependency "croniter" (the "cron" extra) needed for time-based log rotation`,
  `EXIT=3` (`exit_status.bad_config`). **No** `Traceback (most recent call last)`, **no** `During
  handling of the above exception`, **no** `ModuleNotFoundError`. Reproduced identically with `@hourly`.
- **R3** — croniter present, `@daily` / `@midnight` / raw cron `0 3 * * *` → all `EXIT=0`.
- **R4** — `2GB`, croniter **absent** and **present** → both `EXIT=0`.
- **`never` edge** (croniter absent) → `EXIT=0`, no croniter panic (closed false-positive verified).
- Orchestrator sanity check: re-inspected the spec-excluded diff and confirmed the code matches the
  reviewer's account; `git status --porcelain` empty at hand-back (no instrumentation left behind).

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `require_croniter()` + gated probe, `core/logging.py` (2bd9c63) | Subprocess `@daily`+croniter-absent → single CRITICAL line naming `croniter` and the `cron` extra, `EXIT=3` non-zero | ✅ |
| R2 | dead post-construction block removed, `core/logging.py` (2bd9c63) | Same run: no `Traceback`, no `During handling of the above exception`, no `ModuleNotFoundError` | ✅ |
| R3 | probe is a no-op when croniter present (unchanged handler path), `core/logging.py` | `@daily`/`@midnight`/`0 3 * * *` with croniter present → `EXIT=0` | ✅ |
| R4 | size policies succeed in the `try` branch, never reach the gate | `2GB` with croniter absent **and** present → `EXIT=0` | ✅ |
| R5 | reuses `panic()` (single critical line + clean exit), message style mirrors `data/core.py` | Message format matches the `data/core.py` optional-dep precedents; `exit_status` constant, not a magic literal | ✅ |

Unmapped changes (possible scope creep): **none**. The `raises` import supports the new unit test.
No CLI-surface / help-text / `share/` completion change, so the §12 docs-in-same-commit rule does not
apply (behavior with croniter present is unchanged).

## Findings

**None.** No candidate finding survived the refutation protocol.

Actively refuted and dropped:
- *Would `never`/default falsely demand croniter?* — No. `if file_policy != ROTATE_NEVER:` gates the
  probe and mirrors `TimedRotatingFileHandler.reset_interval`'s own `ROTATE_NEVER` guard
  (`core/logging.py:441`). Verified by execution (`never` + croniter absent → `EXIT=0`).
- *Does the probe faithfully predict the handler's eager import?* — Yes. The probe's `import croniter`
  and the handler's `from croniter import croniter` (`core/logging.py:442`) both fail under the same
  `sys.modules['croniter']=None` injection, matching the real uninstalled case.
- *Any invariant touched?* — No. §8 signals (no stray `reset_signal()`), §10 config, and the
  `exit_status`-constants rule are all respected; `core/logging.py` is not high-blast-radius.

## Human-gate triggers

None. No CONFIRMED finding (indeed, no findings at all), and `core/logging.py` is not in the
high-blast-radius core list; no security/DB-lifecycle invariant is touched. Human sign-off gate **not**
triggered.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

Not run (clean correctness pass on a small, well-scoped fix). Both planned phases (P1 core fix + unit
proof; P2 end-to-end regression proof) are marked `done` in `TECH.md`, and the requirement→evidence
matrix above shows full R1–R5 coverage with no scope creep.
