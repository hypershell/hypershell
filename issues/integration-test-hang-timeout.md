---
status: unshaped
kind: test
appetite: small
lane: public
---

# A hung integration test costs the whole CI job timeout

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Integration tests run `hs cluster` and similar commands in process through `tests.main`
(`tests/__init__.py`), with no per-test time limit. The project does not use a pytest timeout
plugin (`pyproject.toml` `[tool.pytest.ini_options]`). When a run hangs, as
`group-advance-strands-lower-group` showed for about 1–3% of grouped runs under load, the test
never returns. The CI job then runs until its `timeout-minutes` (20 for the test matrix in
`.github/workflows/tests.yml`) and is cancelled. The log names no failing test, and every other
result in that job is lost.

## Why it was deferred

Out of scope for `group-advance-strands-lower-group`, which fixes the hang it found and adds a
deterministic unit test for it. A suite-wide limit is test-harness work with its own choices: a
plugin (a new dev dependency) or a limit in the harness itself, how it ends threads that do not
exit inside an in-process run, and what limit suits the slowest tests. **Pre-existing.**

## Outcome / vision

A test that hangs fails within a bounded time, names itself, and leaves the rest of the job's
results intact.

## Sketch of the acceptance criteria

- **R1** — IF an integration test does not finish within its time limit, THEN the run SHALL fail
  that test by name and continue with the remaining tests.
- **R2** — The limit SHALL be long enough that no test passing on `develop` today fails because of
  it across the CI matrix.

## Notes

- Related: `spec/group-advance-strands-lower-group/` (the CI symptom in its Problem).
- Found by: `group-advance-strands-lower-group` shaping (2026-11-03).
