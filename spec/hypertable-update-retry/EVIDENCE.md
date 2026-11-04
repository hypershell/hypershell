# EVIDENCE — A task-row write that waited on a row lock still lands on a TimescaleDB hypertable

> The evidence for [`GOAL.md`](GOAL.md) R7: the contended-write tests run against `develop` and the
> fix on TimescaleDB 2.31.0 and 2.24.0, and the CI run on the draft PR. Each block after an
> evidence marker line is read by [`check_evidence.py`](check_evidence.py). The build machine has
> no PostgreSQL server, so every block below is the foreman's.

## Hand-back — contended-write runs and CI

For the foreman. Build agents run none of this: no cluster job, no container, no push, no GitHub
action.

**Builds**, by label and full commit. The fix is the last commit that touches `src/`
(`git log -1 --format=%H -- src`); every later commit on the branch changes only `spec/` and docs,
so the branch head tests the same code.

| label | ref | commit |
|---|---|---|
| `develop` | `develop` at shaping | `0e8275e9bdb58bb390b08e492c16b68b210fb4e3` |
| `fix` | `fix/hypertable-update-retry` | `6a67b0a89472cb2bb48335f3e34c02c52d459373` |

**Pre-build evidence already in hand** (the foreman's plan-gate probe, recorded in
[`TECH.md`](TECH.md)): on TimescaleDB 2.31.0 (Apache), in all 6 two-session cases, a blocked UPDATE
matched 0 rows and an immediate re-run on the waiter's connection matched 1.

### The four runs

Each run is a fresh TimescaleDB container on a test cluster, and a checkout of this branch at its
final commit (`git fetch` of the pushed branch), with `git`, `uv` and network or a warm uv cache.
[`contention_run.py`](contention_run.py) runs this branch's `tests/test_contended_write.py` against
the `src/` of `--commit`, in a throwaway worktree with its own environment; its docstring is the
manual.

Images: CI's own pins, from `.github/workflows/tests.yml`.

- **2.31.0:** `timescale/timescaledb:2.31.0-pg18@sha256:0fcff5761363809d1242f2261a3482197b5a5337407f8b97a976fb174206d579`
- **2.24.0:** `timescale/timescaledb:2.24.0-pg17@sha256:8ab414f7f73fd47dfa643097f1a753e2ac009c70fcb0de5717d0e79d21e7b7d6`

`$URL` is the container's admin URL in CI's form,
`postgresql+psycopg://postgres:postgres@localhost:<port>/postgres`, and `$OUT` is a directory
outside the checkout. Against the 2.31.0 container:

```
python3 spec/hypertable-update-retry/contention_run.py --label ts231-develop --commit 0e8275e \
    --admin-url "$URL" --timescale community --out "$OUT"
python3 spec/hypertable-update-retry/contention_run.py --label ts231-fix --commit 6a67b0a89472cb2bb48335f3e34c02c52d459373 \
    --admin-url "$URL" --timescale community --out "$OUT"
```

and against the 2.24.0 container the same two, labelled `ts224-develop` and `ts224-fix`.

**Expected.** `ts231-develop` exits 1 with exactly the three red tests `failed` (by assertion, not
`error`): `test_contended_confirm_records_client`, `test_contended_result_applies` and
`test_contended_orphan_revert_lands`. The other two tests pass there. `ts231-fix`, `ts224-develop`
and `ts224-fix` exit 0 with all five tests passed. A `test_rerun_after_contended_update_matches`
failure on 2.31.0 means the design's premise is false (PLAN §5); a test that never observed the lock
wait fails with "was not seen waiting" or "finished without waiting" and is a test defect.

**What to return.**

- The four `<label>.json` summaries as one fenced `json` block, `{"runs": [ … ]}`, placed directly
  after the marker line `<!-- evidence:r7 -->`.
- The four `<label>.log` files, attached or pasted as a collapsed appendix.
- One harness paragraph above the block: host kind, container runtime, Python.

### CI on the draft PR

Push the branch and open the draft PR so CI runs; the `postgres` job now runs
`tests/test_contended_write.py` in all five entries. Return one fenced `json` block directly after
the marker line `<!-- evidence:ci -->`:

```text
{"run_id": <int>, "head_sha": "<full commit>",
 "jobs": {"tests (3.11)": "success", …, "PostgreSQL (timescale-floor)": "success"},
 "contended": {"PostgreSQL (pg13)": {"passed": 5, "skipped": 0}, …},
 "reruns": [{"job": "tests (3.13)",
             "test": "tests/test_restart.py::test_restart_update_adds_novel_and_runs",
             "signature": "assert [''] == ['4', '5']", "location": "test_restart.py:102",
             "first_attempt": "failure"}]}
```

`jobs` covers `tests (3.11)` through `tests (3.14)` and the five `PostgreSQL (…)` entries (`pg13`,
`pg18`, `timescale`, `timescale-oss`, `timescale-floor`), each with its final conclusion. The
`contended` counts are read from each `postgres` job log's lines for `test_contended_write.py`.
`reruns` is optional and present only if used, exactly as in `writeback-prebuilt-update`'s
hand-back: one rerun of `tests (3.13)` whose first attempt failed only in the documented
restart-update flake.

### The bars

From GOAL R7, verbatim: "A test SHALL force the race deterministically on a server database: a
second connection takes the row lock, HyperShell's write is shown to be **waiting on that lock**
(observed from the server, not inferred from a sleep), and the holder then commits. It SHALL cover
Confirm (R1), the Receiver (R2), the refused case (R3: the holder reverts the row and the result is
STALE), and each further path R4 brings into scope. It SHALL run in every entry of the CI
`postgres` job. On `timescale` and `timescale-oss` (TimescaleDB 2.31.0) it SHALL fail on `0e8275e`
and pass after the fix. On `pg13`, `pg18` and `timescale-floor` (2.24.0) it SHALL pass both before
and after, as controls. On SQLite it SHALL be skipped."

`check_evidence.py r7` checks: every run reports TimescaleDB 2.31.0 or 2.24.0 and was made with
this branch's final test module; each run's `src/` is `develop` or the fix; each reports exactly the
five tests; `develop` on 2.31.0 fails exactly the three red tests and passes the other two, with
pytest exit 1; every other run passes all five with exit 0; and all four (build, server) pairs are
present.

`check_evidence.py ci` checks: the run's `head_sha` has the fix's `src/` and `tests/`; all nine
jobs are present and every listed job succeeded; each `PostgreSQL (…)` entry passed all five
contended tests and skipped none; and `reruns`, if present, is at most the one documented
`tests (3.13)` restart-update flake with a failed first attempt.

The Python jobs skip the module (no server), which is R7's "on SQLite it SHALL be skipped".
