# Research 03 — Design intent & prior art: is console task-output guaranteed?

## Design intent verdict: console task-output is a CONVENIENCE echo, not a guarantee

The docs are consistent: the **database is the canonical source of truth**; task `stdout`/`stderr`
is a stream written **directly** to the console fd (or a file via `--capture`/`-o`), with **no
documented completeness- or ordering guarantee**.

- `docs/getting_started.rst:74-75`: *"By default, all command `stdout` and `stderr` are joined and
  written out directly. Capture individual task `stdout` and `stderr` with ``--capture``."*
  ("joined and written out directly" = merged onto one stream, no per-task framing/ordering claim.)
- `docs/security.rst:70-73`: *"Task ``stdout`` and ``stderr`` do *not* [travel through the queue] —
  they are written to disk on the client ... and retrieved on demand over SFTP ... when the operator
  runs ``hs task info --stdout``"* — i.e. the retrievable, authoritative copy is on disk, not the console.
- `docs/security.rst:334`: *"The database (SQLite or PostgreSQL) is the canonical source of truth"*;
  `docs/alternatives.rst:45`: *"the database is the source of truth"*.
- `docs/cli/cluster.rst` / `cluster/__init__.py:105`: `-o, --output PATH  File path for task outputs
  (default: <stdout>)` — file redirect is the deliberate way to capture reliably; `<stdout>` is the default.

**Mechanism confirms "directly" is literal** (code = ground truth): each task subprocess inherits the
client's real fd — `client.py:772-773` `Popen(..., stdout=self.redirect_output, stderr=self.redirect_errors ...)`
where `redirect_output = redirect_output or sys.stdout` (`client.py:671`), and the cluster passes a
**single** `output_stream = sys.stdout` (`cluster/__init__.py:426`, wired at `:292`) to **all N executor
threads**. So N concurrent children share one fd (fd 1); Python never buffers/forwards/locks task output.
Nothing in the design promises every line is captured — the guaranteed copy is the DB row (+ `--capture`
file). This squarely supports GOAL R1 (assert on authoritative task state, not scraped stdout).

## History findings

- **Test origin:** `0844bf1` (2025-12-23) `[feature] Add task groups for dependency management` added
  `tests/test_groups.py` **including `test_group_failed_task_with_retries`**. `git blame -L 115,147`
  shows the test body is **unchanged since `0844bf1`** — never touched for flakiness/timing.
- Only later touches to the file: `655c7f2` (copyright years) and `b27ee58` (#50 re-submission gating —
  added `--restart`-based tests; retry test untouched).
- **No prior flaky/output-loss work exists.** `git log --grep`: `flak` → only `1044b22` (this goal);
  `timing`/`intermittent`/`buffer`/`stdout`(re tests) → **none**. `flush` → `ed9b34d` `[fix] Ensure tasks
  are flushed for queue-only submission before shutdown` (submit-side, unrelated to task stdout). `race`
  hits are harness/broken-pipe, not this.
- **Console-echo path is NOT a recent regression.** Churn on the `Popen(stdout=...)` / `redirect_output`
  path: `415d21f` (resource tracking), `a6a613d` (task-level capture), `523e4f9` (Add IO redirects),
  `2ede301` (full rewrite) — all predate task groups; the redirect model is old and stable. The separate
  `server.py:465` `print(task.args, file=self.redirect_failures)` is the **failed-task-args** echo (not
  task stdout), last touched `e629a90`. Nothing recent explains new flakiness → points to runner load,
  not a code change.

## CI invocation

- `.github/workflows/tests.yml`: `runs-on: ubuntu-latest`, `timeout-minutes: 20`, `fail-fast: false`,
  **matrix Python `3.11 3.12 3.13 3.14`**.
- Command: **`uv run --python ${{ matrix.python-version }} pytest -v`** — **no `-n auto` / no pytest-xdist**,
  no `--forked`, no rerun plugin. Pytest runs **serially**; the "load" is the **constrained 2-core hosted
  runner itself**, not xdist. Each integration test still spins a full cluster (server + client + N executor
  threads + subprocesses), so intra-test concurrency is where the loss surfaces. Matches GOAL evidence
  (failed on both 3.11 and 3.14 → runner load, not a version bug).

## Other fragile tests (blast radius — R-noted, mostly non-goals)

Same latent stdout-scraping assumption (`assert sorted(stdout...) == [...]`):
- `tests/test_groups.py:39, 61, 82, 143, 186, 224, 287` — every group test scrapes cluster stdout.
- `tests/test_groups.py:167` `assert '2' not in stdout` (inverse scrape; asserts absence, lower risk).
- `tests/test_cluster.py:248, 257` — `sorted(stdout...) == range(1,9)` / `{0,1,2,3}`.
- `tests/test_restart.py:35, 54, 102, 118, 157` — `sorted(stdout...) == [...]`.
- `tests/test_submit_json.py:275, 358` — `sorted(stdout.splitlines()) == [...]`.
- `tests/test_cancel.py:68, 93` — `'RUN_2' not in stdout` (absence assertion).

Wall-clock / log-index timing (GOAL explicit non-goals):
- `tests/test_groups.py:292` `assert elapsed > 4 + 3*2` (>10s) — lower-bound, robust direction on slow runners.
- `tests/test_groups.py:137-139` — the repair target's own `group_idx < n2_idx` raw-log-line-index compare
  (secondary fragility; **passed** in the failing run per GOAL, but R3 wants a load-robust signal).

## Open questions

1. Where exactly is `"1"` lost — the shared-fd child write (client), or the test harness `main()` capture
   of the subprocess/pipe? (Research 01/02 territory; design says the DB row is authoritative regardless.)
2. Under R4, is a real fix even warranted, or is "directly to fd, best-effort" the documented contract and
   the test the only thing to change? Docs support the latter (console echo is never promised complete).
3. If a product fix is pursued, would it touch the shared-`sys.stdout`-across-N-executors design
   (`cluster/__init__.py:292,426`) — which the GOAL marks as out-of-scope beyond the minimum for a
   *confirmed* defect?
