# Research digest — writeback-prebuilt-update

Three briefs, written one after another by the planner against `develop` `bc6547a`, because the
change lands in `data/model.py` (high blast radius): [`01-statement-reuse.md`](01-statement-reuse.md),
[`02-types-and-dialects.md`](02-types-and-dialects.md), [`03-measurement.md`](03-measurement.md).
They agree; the decisions below are what `PLAN.md` builds on.

## Decisions

1. **Build the conditional UPDATE once per process** against `Task.__table__`, with bind parameters
   typed from the table's own columns and prefixed names, and execute it with one parameter dict
   per result. Same SQL text as `bc6547a` on SQLite (captured), same single commit per bundle, same
   rowcount-driven classification. (01 §2)
2. **Prebuild the classifying SELECT the same way.** The GOAL leaves it optional; it is a few lines
   and lets the guard cover both statements. (01 §2)
3. **The R4 guard is statement identity**, observed by wrapping `Session.execute` across at least 8
   results with mixed outcomes: at most one UPDATE object and one SELECT object. Compiled-cache
   misses are rejected as the observable, since they are already flat on `bc6547a` (1 miss, then
   hits). On `bc6547a` the guard sees N distinct UPDATEs. (01 §3)
4. **R3 types are proven by a reference-equivalence test**: the fix's write and a reference statement
   written in the test the way `bc6547a` builds it store the same raw values. It runs locally on
   SQLite and in the five CI `postgres` entries (pg13, pg18, timescale, timescale-oss,
   timescale-floor) through `tests/test_writeback_postgres.py`. (02 §3)
5. **R1 is measured by [`cpu_bench.py`](../cpu_bench.py)**: extracted trees, one environment,
   `Receiver.update_tasks` on one-result bundles, interleaved rounds after a dropped warm-up, CPU
   per result, bar on the means. On the base it reports the bar missed, so the bar can fail. (03 §1)
6. **R6 is a hand-back**: `EVIDENCE.md` names the three builds and the configuration; the foreman
   returns one JSON block that [`check_evidence.py`](../check_evidence.py) checks. (03 §2)
