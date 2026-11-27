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

## Recorded — the foreman's runs and CI

Delivered by the foreman on 2026-11-04 and pasted as delivered. `check_evidence.py r7 ci` passes
on these blocks.

### The four runs

**Harness note**, in the foreman's words:

> Four runs on a test cluster node: TimescaleDB in a container (Apptainer) from CI's pinned images by
> digest — 2.31.0-pg18 Community (`sha256:0fcff576…`) and 2.24.0-pg17 Community (`sha256:8ab414f7…`),
> each server fresh per pair, node-local data; `contention_run.py` from the branch at `25e60d2`
> (src/tests identical to the head), run by a conda Python 3.13 with uv 0.13.7 syncing
> `--frozen --extra postgres` (the tests ran on uv's Python 3.14); develop = `0e8275e`, fix = `6a67b0a`. Before
> the build, the foreman's two-session probe on 2.31.0 (Apache) showed a blocked UPDATE matching 0
> rows and an immediate re-run matching 1, in 6/6 cases; TimescaleDB 2.24.0 and 2.28.0 match 1 the
> first time, 2.29.0 and 2.31.0 match 0.

| run | server | build | exit | red tests |
|---|---|---|---|---|
| `ts231-develop` | TimescaleDB 2.31.0 (PostgreSQL 18.6) | `develop` `0e8275e` | 1 | `test_contended_confirm_records_client`, `test_contended_result_applies`, `test_contended_orphan_revert_lands` (all `failed`) |
| `ts231-fix` | TimescaleDB 2.31.0 (PostgreSQL 18.6) | fix `6a67b0a` | 0 | none |
| `ts224-develop` | TimescaleDB 2.24.0 (PostgreSQL 17.7) | `develop` `0e8275e` | 0 | none |
| `ts224-fix` | TimescaleDB 2.24.0 (PostgreSQL 17.7) | fix `6a67b0a` | 0 | none |

Every run used the test module at `25e60d2`, the same `tests/test_contended_write.py` as the
branch head. On `develop` with 2.31.0 the contended Confirm and the orphan revert raised
`StaleDataError` ("0 were matched"), and the contended result was refused as stale and never
written; `test_rerun_after_contended_update_matches` passed there, as the premise requires. The
logs are in the appendix below.

<!-- evidence:r7 -->
```json
{
 "runs": [
  {
   "label": "ts231-develop",
   "commit": "0e8275e9bdb58bb390b08e492c16b68b210fb4e3",
   "tests": "25e60d27549450fba64f2dd6dc5be010c1623f43",
   "timescale": "community",
   "admin_url": "postgresql+psycopg://postgres:***@localhost:47670/postgres",
   "server": {
    "postgres": "18.6",
    "timescaledb": "2.31.0"
   },
   "exit": 1,
   "outcomes": {
    "test_rerun_after_contended_update_matches": "passed",
    "test_contended_confirm_records_client": "failed",
    "test_contended_result_applies": "failed",
    "test_contended_result_refused_when_reverted": "passed",
    "test_contended_orphan_revert_lands": "failed"
   }
  },
  {
   "label": "ts231-fix",
   "commit": "6a67b0a89472cb2bb48335f3e34c02c52d459373",
   "tests": "25e60d27549450fba64f2dd6dc5be010c1623f43",
   "timescale": "community",
   "admin_url": "postgresql+psycopg://postgres:***@localhost:47670/postgres",
   "server": {
    "postgres": "18.6",
    "timescaledb": "2.31.0"
   },
   "exit": 0,
   "outcomes": {
    "test_rerun_after_contended_update_matches": "passed",
    "test_contended_confirm_records_client": "passed",
    "test_contended_result_applies": "passed",
    "test_contended_result_refused_when_reverted": "passed",
    "test_contended_orphan_revert_lands": "passed"
   }
  },
  {
   "label": "ts224-develop",
   "commit": "0e8275e9bdb58bb390b08e492c16b68b210fb4e3",
   "tests": "25e60d27549450fba64f2dd6dc5be010c1623f43",
   "timescale": "community",
   "admin_url": "postgresql+psycopg://postgres:***@localhost:47671/postgres",
   "server": {
    "postgres": "17.7",
    "timescaledb": "2.24.0"
   },
   "exit": 0,
   "outcomes": {
    "test_rerun_after_contended_update_matches": "passed",
    "test_contended_confirm_records_client": "passed",
    "test_contended_result_applies": "passed",
    "test_contended_result_refused_when_reverted": "passed",
    "test_contended_orphan_revert_lands": "passed"
   }
  },
  {
   "label": "ts224-fix",
   "commit": "6a67b0a89472cb2bb48335f3e34c02c52d459373",
   "tests": "25e60d27549450fba64f2dd6dc5be010c1623f43",
   "timescale": "community",
   "admin_url": "postgresql+psycopg://postgres:***@localhost:47671/postgres",
   "server": {
    "postgres": "17.7",
    "timescaledb": "2.24.0"
   },
   "exit": 0,
   "outcomes": {
    "test_rerun_after_contended_update_matches": "passed",
    "test_contended_confirm_records_client": "passed",
    "test_contended_result_applies": "passed",
    "test_contended_result_refused_when_reverted": "passed",
    "test_contended_orphan_revert_lands": "passed"
   }
  }
 ]
}
```

### CI on the draft PR

Run 41933335543 on the branch head `e085aca05b6ce4d3dcc1ee9861a73e0a60b7afc2`: all 14 checks
succeeded, with no reruns, and each of the five `PostgreSQL (…)` entries ran
`tests/test_contended_write.py` with 5 passed and 0 skipped. The head is a prose-only commit
after the fix (it names the top-level `hs update` in docs and seeds), so its `src/` and `tests/`
are the fix's; `check_evidence.py ci` checks exactly that.

<!-- evidence:ci -->
```json
{
 "run_id": 41933335543,
 "head_sha": "e085aca05b6ce4d3dcc1ee9861a73e0a60b7afc2",
 "jobs": {
  "tests (3.11)": "success",
  "tests (3.12)": "success",
  "tests (3.13)": "success",
  "tests (3.14)": "success",
  "PostgreSQL (pg13)": "success",
  "PostgreSQL (pg18)": "success",
  "PostgreSQL (timescale)": "success",
  "PostgreSQL (timescale-oss)": "success",
  "PostgreSQL (timescale-floor)": "success"
 },
 "contended": {
  "PostgreSQL (pg13)": {
   "passed": 5,
   "skipped": 0
  },
  "PostgreSQL (pg18)": {
   "passed": 5,
   "skipped": 0
  },
  "PostgreSQL (timescale)": {
   "passed": 5,
   "skipped": 0
  },
  "PostgreSQL (timescale-oss)": {
   "passed": 5,
   "skipped": 0
  },
  "PostgreSQL (timescale-floor)": {
   "passed": 5,
   "skipped": 0
  }
 }
}
```

### Appendix — run logs

The four `contention_run.py` logs as delivered, scrubbed by the foreman: the run directory reads
`<run>` and the node's hostname `<host>`.

<details>
<summary><code>ts231-develop.log</code></summary>

```text
<run>/tree-ts231-develop/.venv/lib/python3.14/site-packages/pluggy/_callers.py:141: SyntaxWarning: 'return' in a 'finally' block
  return result
<run>/tree-ts231-develop/.venv/lib/python3.14/site-packages/pluggy/_callers.py:182: SyntaxWarning: 'return' in a 'finally' block
  return outcome.get_result()
============================= test session starts ==============================
platform linux -- Python 3.14.10, pytest-9.0.3, pluggy-1.5.0 -- <run>/tree-ts231-develop/.venv/bin/python
hypothesis profile 'default'
rootdir: <run>/tree-ts231-develop
configfile: pyproject.toml
plugins: xdist-3.8.0, hypothesis-6.155.1
collecting ... collected 5 items

tests/test_contended_write.py::test_rerun_after_contended_update_matches PASSED [ 20%]
tests/test_contended_write.py::test_contended_confirm_records_client FAILED [ 40%]
tests/test_contended_write.py::test_contended_result_applies FAILED      [ 60%]
tests/test_contended_write.py::test_contended_result_refused_when_reverted PASSED [ 80%]
tests/test_contended_write.py::test_contended_orphan_revert_lands FAILED [100%]

=================================== FAILURES ===================================
____________________ test_contended_confirm_records_client _____________________

contended_db = Engine(postgresql+psycopg://postgres:***@localhost:47670/t_ef675da1bb3a4dcb)

    def test_contended_confirm_records_client(contended_db) -> None:
        """A confirmation that waited on a result write stamps the client and keeps the result."""
        task_id = submit()
        dispatch_one(task_id)
        completed = datetime.now().astimezone()
        holder_sql = f'UPDATE task SET completion_time = :completed, exit_status = 0, duration = 2 {BY_ID}'
    
        outcome = contend(contended_db, holder_sql, {'completed': completed, 'id': task_id},
                          lambda: confirm([task_id]))
    
>       assert outcome.error is None, repr(outcome.error)
E       AssertionError: StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.")
E       assert StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.") is None
E        +  where StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.") = Outcome(value=None, error=StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.")).error

tests/test_contended_write.py:241: AssertionError
________________________ test_contended_result_applies _________________________

contended_db = Engine(postgresql+psycopg://postgres:***@localhost:47670/t_52276778d22948ec)
caplog = <_pytest.logging.LogCaptureFixture object at 0x145d76987a10>

    def test_contended_result_applies(contended_db, caplog: pytest.LogCaptureFixture) -> None:
        """A result that waited on a confirmation is applied, with the failure written to the failures file."""
        caplog.set_level(logging.DEBUG, logger='hypershell')
        task_id = submit('echo fails')
        copy = dispatch_one(task_id)
        confirm([task_id])
        finished = finish(copy, exit_status=3)
        failures = io.StringIO()
        holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'
    
        outcome = contend(contended_db, holder_sql, {'client_id': CLIENT[0], 'client_host': CLIENT[1], 'id': task_id},
                          lambda: receive([finished], failures))
    
        assert outcome.error is None, repr(outcome.error)
        data = row(contended_db, task_id)
>       assert data['completion_time'] == finished.completion_time
E       AssertionError: assert None == datetime.datetime(2026, 11, 4, 18, 13, 59, 169401, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400), 'EST'))
E        +  where datetime.datetime(2026, 11, 4, 18, 13, 59, 169401, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400), 'EST')) = Task(id='01a1a8d7-2f86-7dd1-a080-b6df8f9dbd22', group=0, args='echo fails', submit_id='01a1a8d7-1840-7042-885a-2948e47d0320', submit_time=datetime.datetime(2026, 11, 4, 18, 13, 57, 125979, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400))), submit_host='<host>', cores=None, memory=None, cores_max=None, memory_max=None, timeout=None, server_id='01a1a8d7-1840-7042-885a-2948e47d0320', server_host='<host>', schedule_time=datetime.datetime(2026, 11, 4, 18, 13, 57, 165130, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400))), client_id='02c6fcde-4221-4f29-8633-3b456c630f05', client_host='client-a', command='echo fails', start_time=datetime.datetime(2026, 11, 4, 18, 13, 57, 169401, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400), 'EST')), completion_time=datetime.datetime(2026, 11, 4, 18, 13, 59, 169401, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400), 'EST')), exit_status=3, outpath='task-01a1a8d7-2f86-7dd1-a080-b6df8f9dbd22.out', errpath='task-01a1a8d7-2f86-7dd1-a080-b6df8f9dbd22.err', csvpath=None, attempt=1, retried=False, waited=1, duration=2, previous_id=None, next_id=None, source=None, fingerprint='60380e4bc8c45a2c78676ddd0da9ca17', part=0, tag={}).completion_time

tests/test_contended_write.py:264: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  hypershell.server:server.py:529 Refused stale result for task (01a1a8d7-2f86-7dd1-a080-b6df8f9dbd22) from client (client-a: 02c6fcde-4221-4f29-8633-3b456c630f05)
______________________ test_contended_orphan_revert_lands ______________________

contended_db = Engine(postgresql+psycopg://postgres:***@localhost:47670/t_d5edd8eb7ef942d4)

    def test_contended_orphan_revert_lands(contended_db) -> None:
        """Evicting a client reverts its task even when the revert waited on a late confirmation."""
        task_id = submit()
        dispatch_one(task_id)
        confirm([task_id])
        holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'
    
        outcome = contend(contended_db, holder_sql, {'client_id': CLIENT[0], 'client_host': CLIENT[1], 'id': task_id},
                          lambda: Task.revert_orphaned(CLIENT[0]))
    
>       assert outcome.error is None, repr(outcome.error)
E       AssertionError: StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.")
E       assert StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.") is None
E        +  where StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.") = Outcome(value=None, error=StaleDataError("UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.")).error

tests/test_contended_write.py:300: AssertionError
- generated xml file: <run>/ts231-develop.xml -
========================= 3 failed, 2 passed in 9.86s ==========================
```

</details>

<details>
<summary><code>ts231-fix.log</code></summary>

```text
<run>/tree-ts231-fix/.venv/lib/python3.14/site-packages/pluggy/_callers.py:141: SyntaxWarning: 'return' in a 'finally' block
  return result
<run>/tree-ts231-fix/.venv/lib/python3.14/site-packages/pluggy/_callers.py:182: SyntaxWarning: 'return' in a 'finally' block
  return outcome.get_result()
============================= test session starts ==============================
platform linux -- Python 3.14.10, pytest-9.0.3, pluggy-1.5.0 -- <run>/tree-ts231-fix/.venv/bin/python
hypothesis profile 'default'
rootdir: <run>/tree-ts231-fix
configfile: pyproject.toml
plugins: xdist-3.8.0, hypothesis-6.155.1
collecting ... collected 5 items

tests/test_contended_write.py::test_rerun_after_contended_update_matches PASSED [ 20%]
tests/test_contended_write.py::test_contended_confirm_records_client PASSED [ 40%]
tests/test_contended_write.py::test_contended_result_applies PASSED      [ 60%]
tests/test_contended_write.py::test_contended_result_refused_when_reverted PASSED [ 80%]
tests/test_contended_write.py::test_contended_orphan_revert_lands PASSED [100%]

- generated xml file: <run>/ts231-fix.xml -
============================== 5 passed in 9.76s ===============================
```

</details>

<details>
<summary><code>ts224-develop.log</code></summary>

```text
<run>/tree-ts224-develop/.venv/lib/python3.14/site-packages/pluggy/_callers.py:141: SyntaxWarning: 'return' in a 'finally' block
  return result
<run>/tree-ts224-develop/.venv/lib/python3.14/site-packages/pluggy/_callers.py:182: SyntaxWarning: 'return' in a 'finally' block
  return outcome.get_result()
============================= test session starts ==============================
platform linux -- Python 3.14.10, pytest-9.0.3, pluggy-1.5.0 -- <run>/tree-ts224-develop/.venv/bin/python
hypothesis profile 'default'
rootdir: <run>/tree-ts224-develop
configfile: pyproject.toml
plugins: xdist-3.8.0, hypothesis-6.155.1
collecting ... collected 5 items

tests/test_contended_write.py::test_rerun_after_contended_update_matches PASSED [ 20%]
tests/test_contended_write.py::test_contended_confirm_records_client PASSED [ 40%]
tests/test_contended_write.py::test_contended_result_applies PASSED      [ 60%]
tests/test_contended_write.py::test_contended_result_refused_when_reverted PASSED [ 80%]
tests/test_contended_write.py::test_contended_orphan_revert_lands PASSED [100%]

- generated xml file: <run>/ts224-develop.xml -
============================== 5 passed in 9.81s ===============================
```

</details>

<details>
<summary><code>ts224-fix.log</code></summary>

```text
<run>/tree-ts224-fix/.venv/lib/python3.14/site-packages/pluggy/_callers.py:141: SyntaxWarning: 'return' in a 'finally' block
  return result
<run>/tree-ts224-fix/.venv/lib/python3.14/site-packages/pluggy/_callers.py:182: SyntaxWarning: 'return' in a 'finally' block
  return outcome.get_result()
============================= test session starts ==============================
platform linux -- Python 3.14.10, pytest-9.0.3, pluggy-1.5.0 -- <run>/tree-ts224-fix/.venv/bin/python
hypothesis profile 'default'
rootdir: <run>/tree-ts224-fix
configfile: pyproject.toml
plugins: xdist-3.8.0, hypothesis-6.155.1
collecting ... collected 5 items

tests/test_contended_write.py::test_rerun_after_contended_update_matches PASSED [ 20%]
tests/test_contended_write.py::test_contended_confirm_records_client PASSED [ 40%]
tests/test_contended_write.py::test_contended_result_applies PASSED      [ 60%]
tests/test_contended_write.py::test_contended_result_refused_when_reverted PASSED [ 80%]
tests/test_contended_write.py::test_contended_orphan_revert_lands PASSED [100%]

- generated xml file: <run>/ts224-fix.xml -
============================== 5 passed in 9.27s ===============================
```

</details>
