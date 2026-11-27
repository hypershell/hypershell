# GOAL — Graceful guidance when croniter is missing for time-based log rotation

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** croniter-missing-guidance
- **kind:** fix
- **appetite:** small

## Problem

`croniter` is an *optional* dependency, gated behind the `cron` install extra
(`pyproject.toml`: `cron = ["croniter>=6.2.2"]`). It is only needed for **time/cron-like log
rotation** (`logging.file.rotate` values such as `@daily`, `@midnight`, or a cron expression).

A user who installs HyperShell without the `cron` extra (e.g. `uv tool install hypershell`) but
whose config sets a time-like rotation policy gets a hard crash at startup — on **every** `hs`/`hsx`
invocation, before the requested command can run. Worse, the crash is a confusing **double
traceback**: a `ValueError: Memory string '@daily' is not a valid memory unit` from an internal
size-parse probe, chained ("During handling of the above exception, another exception occurred") to
a raw `ModuleNotFoundError: No module named 'croniter'`. Neither traceback tells the user what is
actually wrong or how to fix it.

The project already handles other missing optional dependencies gracefully — a missing `psycopg`
prints a single clear "Missing optional dependency … needed for PostgreSQL" line and exits cleanly
(`data/core.py`). Missing `croniter` should behave the same way: one clear, actionable message
naming the missing package and the remedy (install the `cron` extra), with no traceback. A friendly
message for exactly this case appears to already be *intended* in the code but is not the behavior a
user actually gets (see Clarifications for the suspected mechanism — to be confirmed in `/hs-plan`).

## Outcome / vision

Running any `hs`/`hsx` command with a time-like `logging.file.rotate` policy configured but
`croniter` not installed produces a single, clear, actionable error — comparable to the existing
missing-`psycopg` experience — and a clean non-zero exit. No Python traceback, no chained
double traceback. When `croniter` *is* installed, time-based rotation works exactly as before, and
size-based rotation is unaffected either way.

## Acceptance criteria (the contract)

- **R1** — WHEN `croniter` is not installed AND `logging.file.rotate` is set to a time/cron-like
  policy (e.g. `@daily`), running an `hs`/`hsx` command SHALL terminate with a single, clear message
  that names the missing `croniter` dependency and states the remedy (install the `cron` extra), and
  SHALL exit with a non-zero status — not raise an uncaught exception.
- **R2** — IF the missing-`croniter` condition is hit, THEN the program output SHALL NOT contain a
  Python traceback, and in particular SHALL NOT contain a chained "During handling of the above
  exception, another exception occurred" double traceback.
- **R3** — WHEN `croniter` *is* installed, time/cron-like rotation policies (e.g. `@daily`,
  `@midnight`, and cron expressions) SHALL continue to configure a working time-based rotating log
  handler, unchanged from today (no regression).
- **R4** — Size-like rotation policies (e.g. `2GB`) SHALL continue to work whether or not `croniter`
  is installed; a missing `croniter` SHALL NOT affect size-based rotation.
- **R5** — The graceful missing-`croniter` handling SHALL be consistent in spirit with the existing
  missing-`psycopg` guidance (single critical line + clean exit), not a bespoke divergent style.

## Non-goals (no-gos)

- Making `croniter` a hard (non-optional) dependency — it stays behind the `cron` extra.
- Adding, removing, or changing the set of supported rotation policies or their semantics.
- Redesigning file logging, the rotating-handler class hierarchy, or the size-vs-time policy
  discrimination beyond what is needed to fix the crash and deliver the graceful message.
- Changing any behavior when `croniter` is present.
- Broader "audit every optional dependency for graceful handling" sweep — this fix is scoped to the
  `croniter` / time-based-rotation path only.

## Clarifications

- **Q:** Should the remedy text explicitly name the `cron` install extra (not just the `croniter`
  package)? — **A:** Yes; the user must be told to install with the `cron` extra (resolved
  2026-07-24).
- **Q:** Is a specific exit code required? — **A:** No; a clean non-zero exit consistent with the
  existing missing-dependency precedent is sufficient. The exact code is a `/hs-plan` decision.
- **Suspected mechanism (a lead for `/hs-plan`, NOT an acceptance criterion — root-cause to be
  confirmed):** `initialize_logging` uses `parse_bytes(file_policy)` as a size-vs-time
  *discriminator* — a `ValueError` means "treat as time-like." In the time-like branch the
  `TimedRotatingFileHandler` is constructed *before* the friendly missing-`croniter` check, and the
  handler's `__init__ → reset_interval()` imports `croniter` eagerly, so it crashes first; the
  intended graceful `panic(...)` for missing `croniter` is therefore effectively dead code. Because
  the crash happens inside the `except ValueError:` block, the size-parse `ValueError` becomes the
  chained context of the `ModuleNotFoundError`, producing the double traceback. `/hs-plan` should
  verify this and decide the fix (e.g. probe croniter availability before constructing the handler
  and/or break the exception chain).

## Related materials

- User-reported traceback: `~/ISSUE.md` (double traceback from `uv tool install hypershell` without
  the `cron` extra, config `logging.file.rotate = @daily`).
- Source (crash site): `src/hypershell/core/logging.py` — `initialize_logging` rotation-policy block
  (`parse_bytes` probe, `except ValueError` time-like branch, the post-construction `panic(...)`
  croniter check), `TimedRotatingFileHandler.reset_interval` / `RotatingFileHandler.__init__`,
  `DATE_ELIGIBLE`, `ROTATE_NEVER`, `panic`.
- Reference pattern (graceful optional-dependency handling): `src/hypershell/data/core.py` missing
  `psycopg` / `libpq` branch (`display_critical` + clean `sys.exit`).
- Packaging: `pyproject.toml` — `cron = ["croniter>=6.2.2"]` extra.
