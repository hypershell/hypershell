# EVIDENCE — Result writeback at or below 2.8.3's CPU cost

> The measured evidence for [`GOAL.md`](GOAL.md) R1 and R6, and the CI run that proves R3 on
> PostgreSQL and TimescaleDB. Each block after an `<!-- evidence:… -->` marker is read by
> [`check_evidence.py`](check_evidence.py).

## R1 — CPU per result of the writeback, in process

**Method.** [`cpu_bench.py`](cpu_bench.py), unchanged from the plan; its docstring is the full
method. Each build's `src/` is extracted with `git archive` from its commit: `b819de6` standing in
for 2.8 (2.8.3's `Task.update_all` of the full row), `develop` at `bc6547a`, and the fix at
`37bfc72` (the last commit touching `src/`). Every measurement is a fresh child process in its own
session under a kill timer, importing `hypershell` from one tree only, with one interpreter and one
locked environment for all three. The child fills a throwaway SQLite database with 5,000 tasks,
dispatches them, finishes each copy as a client does and passes it through the bundle codec, then
times only `Receiver.update_tasks` on 5,000 one-result bundles with `time.process_time`. One
result in a hundred has a non-zero exit status, and the child checks afterwards that all 5,000 rows
completed and 50 failed. One warm-up round is dropped; six recorded rounds rotate the build order.
Every build ran one UPDATE per result and one statement per result.

**Host.** Apple Silicon (arm64), macOS 27.0.1, 10 CPUs; CPython 3.14.10; SQLAlchemy 2.0.50; the
frozen offline environment described in [`TECH.md`](TECH.md) § Build record.

| build | commit | runs | mean ms | median ms | min | max |
|---|---|---|---|---|---|---|
| 2.8 | `b819de6` | 6 | 0.3669 | 0.3661 | 0.3555 | 0.3844 |
| develop | `bc6547a` | 6 | 0.4864 | 0.4846 | 0.4794 | 0.4958 |
| fix | `37bfc72` | 6 | 0.2981 | 0.2973 | 0.2888 | 0.3114 |

Bar (fix mean at or below 2.8 mean): **met**. The fix spends **18.8% less** CPU per result than
the 2.8 writeback (0.2981 against 0.3669 ms) and **38.7% less** than `develop` (against 0.4864 ms).
The per-round spread (max − min) is 0.0289 ms for 2.8, 0.0164 ms for `develop` and 0.0226 ms for
the fix, so the slowest fix round (0.3114) is still below the fastest 2.8 round (0.3555). Wall time
per result, for reference only: 2.8 0.474, `develop` 0.562, fix 0.372 ms.

The phase gate ran the script again, fresh, on the same host: 2.8 0.3802, `develop` 0.5189, fix
0.3139 ms per result (fix 17.4% under 2.8), bar met. The block below is the first run.

<!-- evidence:r1 -->
```json
{
  "host": {
    "machine": "arm64",
    "system": "Darwin",
    "python": "3.14.10",
    "sqlalchemy": "2.0.50",
    "n": 5000,
    "rounds": 6,
    "cpus": 10
  },
  "summary": {
    "2.8": {
      "sha": "b819de6db373336630217f39ef84224d45d35181",
      "runs": 6,
      "mean": 0.3669,
      "median": 0.3661,
      "min": 0.3555,
      "max": 0.3844,
      "values": [
        0.3608,
        0.3575,
        0.3844,
        0.3555,
        0.3715,
        0.3715
      ]
    },
    "develop": {
      "sha": "bc6547a52fcf0ebd8dd04235a3e188a1480463f0",
      "runs": 6,
      "mean": 0.4864,
      "median": 0.4846,
      "min": 0.4794,
      "max": 0.4958,
      "values": [
        0.4807,
        0.4847,
        0.4958,
        0.4794,
        0.4845,
        0.4933
      ]
    },
    "fix": {
      "sha": "37bfc72d250c487b61b0a8dfc88078b09c093a04",
      "runs": 6,
      "mean": 0.2981,
      "median": 0.2973,
      "min": 0.2888,
      "max": 0.3114,
      "values": [
        0.2954,
        0.3114,
        0.2933,
        0.2888,
        0.3007,
        0.2991
      ]
    }
  },
  "bar_met": true,
  "runs_jsonl": "/tmp/claude-501/wbbuild/bench1/runs.jsonl"
}
```

## R6 hand-back — cluster run request

For the foreman. Build agents run none of this: no cluster job, no push, no GitHub action.

**Builds**, by label and full commit. The fix is the last commit that touches `src/`; every later
commit on the branch changes only `spec/`, so the branch head measures the same code.

| label | ref | commit |
|---|---|---|
| `2.8.3` | tag `2.8.3` | `4d5771b8a70a148142022117a30c5a4bf31e9afe` |
| `develop` | `develop` at shaping | `bc6547a52fcf0ebd8dd04235a3e188a1480463f0` |
| `fix` | `fix/writeback-prebuilt-update` | `37bfc72d250c487b61b0a8dfc88078b09c093a04` |

**Configuration.** 256 single-executor clients, bundle size 1, TLS on, SQLite, a 16-CPU server
node. At least 3 interleaved runs per build: each round runs all three builds, and the order
rotates from round to round.

**The bar,** verbatim from GOAL R6: "The fix's throughput SHALL be at or above 2.8.3's: the fix's
mean over at least three interleaved runs per build is at or above 2.8.3's mean. If it falls short
by less than the runs' spread, the foreman reports the numbers and the maintainer rules."

**How `check_evidence.py r6` reads it.** The runs, sorted by `order`, must fall into consecutive
triples that each hold all three builds, at least three triples. The `2.8.3` and `develop` runs
must carry the commits above, and every `fix` run a commit whose `src/` and `tests/` match the
fix's. If the fix's mean tasks/s is at or above 2.8.3's, the check passes. "The runs' spread" is
the wider of the two builds' max − min ranges, 2.8.3's and the fix's, as ruled at the plan gate
(2026-11-04). A shortfall smaller than that spread passes only with a non-empty `ruling`, the
maintainer's words quoted; a larger one fails.

**Return 1 — the cluster runs.** The harness note in the foreman's own words (the harness, its
version, how throughput is measured, anything unusual), then a fenced `json` block directly after
the marker line `<!-- evidence:r6 -->`, of this form:

```text
{"config": {"clients": 256, "executors_per_client": 1, "bundlesize": 1, "tls": true,
            "database": "sqlite", "server_cpus": 16},
 "runs": [{"label": "2.8.3" | "develop" | "fix", "sha": "<full commit>", "order": <1, 2, …>,
           "tasks_per_s": <float>}, …],
 "ruling": "<only if the fix falls short within the spread>"}
```

**Return 2 — CI on the draft PR.** Push the branch and open the draft PR so CI runs. Return a
fenced `json` block directly after the marker line `<!-- evidence:ci -->`:

```text
{"run_id": <int>, "head_sha": "<full commit>",
 "jobs": {"tests (3.11)": "<conclusion>", "tests (3.12)": "…", "tests (3.13)": "…",
          "tests (3.14)": "…", "PostgreSQL (pg13)": "…", "PostgreSQL (pg18)": "…",
          "PostgreSQL (timescale)": "…", "PostgreSQL (timescale-oss)": "…",
          "PostgreSQL (timescale-floor)": "…"},
 "reruns": [{"job": "tests (3.13)",
             "test": "tests/test_restart.py::test_restart_update_adds_novel_and_runs",
             "signature": "assert [''] == ['4', '5']", "location": "test_restart.py:102",
             "first_attempt": "failure"}]}
```

`jobs` holds each job's final conclusion, and every one must be `success`. `reruns` is present only
if the plan-gate ruling was used: one rerun of `tests (3.13)` whose first attempt failed only in the
documented restart-update flake, with exactly that signature. `check_evidence.py ci` refuses any
other rerun, a second rerun, or a different signature. Anything else red blocks.
