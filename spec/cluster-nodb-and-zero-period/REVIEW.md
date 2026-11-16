# REVIEW — `hs cluster --no-db` leaves the database alone, and an autoscaling period of zero or less is refused

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 53f939229613fec56d9cde78932d84a58c3a8bd8  ·  **Base:** `67e381e` (`origin/develop`)  ·  **Date:** 2026-11-15
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** one blind correctness reviewer (no `debate`, no `completeness` sub-pass), plus an
  orchestrator re-run of the deciding evidence for F1 and F2.

## Pre-flight

- **Base.** `origin/develop` fetched: `67e381e` (#111). The branch was cut from `90ab2fc`
  (`git merge-base HEAD origin/develop`). The graded diff is
  `git diff origin/develop...HEAD -- . ':(exclude)spec/'` (16 files). Code was graded against the
  `origin/develop` copies of `invariants.md` and `AGENTS.md`, which #111 changed after the cut.
- **Build state.** `TECH.md` `status: in_review`, P1–P4 `done`.
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/cluster-nodb-and-zero-period/GOAL.md`
  shows the shaping commit `57fcced` and the GOAL-gate lock `2f4df3e`, which amends the floor to
  "greater than zero" and rules that the refusal applies on every launch. No drift after the lock.
- **Merge.** Clean: `git merge-tree --write-tree --name-only HEAD origin/develop` exits 0. The files
  #111 changed (`data/model.py`, `task.py`, the `hs update` docs, `AGENTS.md`, `invariants.md`) do not
  overlap the branch's source files; `ROADMAP.md`, edited on both sides, auto-merges. The reviewer
  ran the cycle's three test files against the merged tree's `src`: 473 passed.
- **Readings that bound grading** (gate rulings, passed to the reviewer): the floor is "greater than
  zero" (zero, negative and NaN refused on every launch of `hs cluster`, `hs server`, `hs relay`;
  every positive period accepted as today, a configuration number below one second included); a key
  holding a number is reported naming `autoscale.period`, one holding a string naming `-P/--period`;
  the Python API (`AutoScaler`, `TopologyPublisher`, `SiteScaler`) stays unguarded; a key value that
  is neither a string nor a number keeps today's behaviour.

## Verification run

Commands the reviewer executed (CLI drives in throwaway sites via `.agents/factory/bin/temp_site.sh`,
the key in `HYPERSHELL_SERVER_AUTH`, never on a command line):

- Red evidence: the branch's `tests/test_cluster_no_db.py tests/test_period_floor.py` with the
  `90ab2fc` source first on `PYTHONPATH`, `-k "not kept and not accepted"` → **59 failed, 0 passed,
  0 errors**, the builder's red list exactly, each for the defect (`['initdb'] == []`; the database
  file exists; rc 5 "file is not a database" / "unable to open database file"; `DID NOT RAISE`;
  status 0 instead of 2; the task ran). `-k "kept or accepted"` on the base → 30 passed.
- HEAD: the cycle's files plus `tests/test_duration_options.py` → 473 passed. Targeted suite
  (`test_server_initdb`, `test_autoscale*`, `test_cluster*`, `tests/cluster/`, `test_relay_*`,
  `test_topology_*`, `test_zone_autoscale`, `test_size_duration`) → 1130 passed, 9 skipped.
  Full suite not re-run (the builder's P4 run: 3116 passed, 116 skipped).
- Docs: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs <out>`
  → exit 0, no warnings.
- `--no-db` drives: `hs cluster in.txt --no-db -N1 -t 'echo {}' --no-tls` and `hsx` → rc 0, both tasks,
  no database file; `HYPERSHELL_DATABASE_FILE` under a missing directory → rc 0, the directory not
  created (local and `--launcher`); the default site database, `:memory:` and an empty file → rc 0,
  nothing created. Without `--no-db`: the four tables are created; the missing directory exits 5 as
  before. PostgreSQL (local server, never-initialized schema), HEAD and base identical: without
  `--no-db` `DatabaseUninitialized`, rc 5; with `--no-db` rc 0; unreachable port without/with
  `--no-db` rc 5 / rc 0.
- Period drives: `-P`/`--period` with `0 0s 0m 0:00 0-0 0.0 00 0.000 0d0h0m0s 0h` on `hs cluster`,
  `hsx`, `hs server` (`--autoscaling`, file `--no-db`, `--forever --no-db`) → rc 2,
  `argument -P/--period: Period '<v>' is not valid: must be greater than zero`, no listener, no task.
  Key as a number (env `0 -5 -0 0.0 -0.5 nan -inf`; file `0 -5 0.0 -0.0 nan -inf false`) → rc 2,
  `autoscale.period: Period <v> is not valid: must be greater than zero`; as a string (env `0s`, file
  `"0s"`, `"0:00"`) → rc 2 naming `-P/--period`. `-P 5` over env `0`/`0s` starts. `hs relay` with env
  `0 -5 0s 0:00 0.0 -0.5 nan` and file `0 -5 "0s" 0.0 nan` → rc 2 naming `autoscale.period`, no relay
  database created; `5MB` → rc 2 on base and HEAD. Positive: `-P 1 0.5 0.0001s 1s 2m 99999d`, env and
  file `0.5` → rc 0; `-P 0.5` parses to 1.
- Autoscaler check rate (`--launcher` a script that sleeps, `--autoscaling fixed -I 1`, debug logging,
  about 6 s, a fresh database per run): `-P 1` → 6 checks, `-P 0.5`, env `0.5`, file `0.5` → 6 each,
  **env `0.001` → 90 and 124** (base `90ab2fc`: 151), `-P 2` → 3. `hs server --autoscaling fixed`,
  about 8 s, CPU by `ps -o time=`: `-P 1` → 0:00.35, env `0.5` → 0:00.38, **env `0.001` → 0:07.91**.
- **Orchestrator re-run** (same drive, HEAD): cluster `-P 1` → 6 checks, env `0.5` → 6, env `0.001`
  → 14 in about 6 s; server `-P 1` → 0:00.35 CPU, env `0.001` → 0:05.92 CPU in about 6 s. Every
  process started was stopped by its PID; a final `ps` showed none left.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `cluster/__init__.py` `ClusterApp.__enter__` (`507412d`) | `--no-db` drives above (local, `--launcher`, `hsx`, unopenable path, `:memory:`); `test_no_db_makes_no_database_call[local,launcher,ssh]`; turso shares the `sqlite` dialect branch (unit-covered, not driven: driver not installed); `--ssh` unit-covered only | ✅ |
| R2 | same | tables created without `--no-db`; missing directory still rc 5; PostgreSQL never-initialized refused / unreachable rc 5, both rc 0 under `--no-db`, identical to base; `test_database_step_kept` (15 cases) | ✅ |
| R3 | `core/types.py` `parse_period`; `-P` type in `server.py`, `cluster/__init__.py` (`d472242`) | every zero form, both spellings, all three launch kinds → rc 2, message names option, value, floor; `test_period_option_zero_refused` | ✅ |
| R4 | `check_args`/`check_arguments` first check; string default through the option type | env and file, number and string, NaN, `-inf`, `-0.0`, `false` → rc 2 with the name the rulings give; `test_period_key_refused` | ✅ |
| R5 | `relay/__init__.py` `RelayApp.site_period` | `hs relay` env/file values → rc 2 naming `autoscale.period`, no relay database; `5MB` rc 2 on base and HEAD; `test_relay_period_zero_refused` | ✅ |
| R6 | `parse_period` returns numbers as given, strings via `parse_time` | positive forms start; `0.5` reaches `run_cluster`/`serve_with_sites` as `0.5`; `-P 0.5` → 1; existing "not a duration" errors unchanged | ✅ in code; R6's "one second at the least" does not hold for tiny numbers — F2 |
| R7 | help strings, `cluster_help.rst`, `server_help.rst`, `config_param_ref.rst`, `docs/cli/cluster.rst`, `docs/cli/server.rst`, zsh `_hs` | read against the drives; markers beside each `.. include::` (generated snippets never hand-marked) and inline in `config_param_ref.rst` as its existing marker is; docs build `-W` clean | ❌ F1 |
| R8 | `tests/test_cluster_no_db.py`, `tests/test_period_floor.py`, `tests/test_duration_options.py` | 59 red on `90ab2fc` for the right reasons, green at HEAD | ✅ (F3: no NaN case) |

Unmapped changes (possible scope creep): none. `ROADMAP.md` and the two seeds change only their
`status` to `adopted:cluster-nodb-and-zero-period`.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### F1 — [MEDIUM/CONFIRMED] The `autoscale.period` reference says a sub-second number checks at most once a second; a small one does not
- **Where:** `docs/_include/config_param_ref.rst:598-600` (added by `d472242`): "A number below one
  second is accepted as given; the autoscaler waits a second at a time, so it checks at most once a
  second."
- **Failure scenario:** `AutoScaler.wait` (`cluster/remote.py:563-574`) and `TopologyPublisher.wait`
  (`cluster/topology.py:401-408`) sleep only while the time since the last check is within the
  period; the last-check time is taken at the start of a check, so a period shorter than one check's
  own run re-checks at once without sleeping. `HYPERSHELL_AUTOSCALE_PERIOD=0.001` checks several
  times a second, and `hs server --autoscaling` uses a whole core.
- **Evidence:** reviewer: cluster env `0.001` → 90 and 124 checks in about 6 s against 6 at `0.5` and
  `1`; server env `0.001` → 7.91 s CPU in about 8 s against 0.35 s at `-P 1`. Orchestrator re-run:
  cluster 14 checks against 6; server 5.92 s CPU in about 6 s against 0.35 s.
- **Touches invariant / requirement:** R7 (the documentation SHALL match the behaviour). The code
  meets its contract; the sentence is wrong. Remedy: correct or drop the sentence.

### F2 — [MEDIUM/CONFIRMED, pre-existing] A positive configuration number smaller than one check's run still spins the autoscalers, against the GOAL Outcome "so no autoscaler can spin"
- **Where:** `cluster/remote.py:563-574` (`AutoScaler.wait`; `relay/site.py:342-348` `SiteScaler.wait`
  defers to it), `cluster/topology.py:401-408` (`TopologyPublisher.wait`). Not changed by the diff.
- **Failure scenario:** as F1. Each spin pass reads the database, the defect class this cycle exists
  to close. Only a number from the configuration (file or `HYPERSHELL_AUTOSCALE_PERIOD`) reaches it:
  a string or `-P` rounds up to at least one second. The relay's fleet is affected by reading, not
  driven.
- **Evidence:** the drives under F1; the same drive on the `90ab2fc` source gave 151 checks in about
  6 s, so the behaviour predates the diff.
- **Touches invariant / requirement:** the GOAL Outcome, and R6's parenthetical "one second at the
  least, by its existing granularity", which states the existing behaviour incorrectly. The GOAL-gate
  ruling that accepts numbers below one second rests on the premise "the autoscalers sleep a second
  at a time, so it cannot spin", which these drives refute for numbers below one check's run time.
- **Disposition — for the human, not applied here.** The rubric's *deferred, not blocked* exception
  needs both: the behaviour predates the diff (met), and a GOAL criterion requires preserving it so
  that no repair this cycle passes that criterion (not clearly met). Refusing or rounding up a
  sub-second number fails R6 and the gate ruling; but a repair in the autoscalers' wait (sleep at
  least once between checks) would leave every period longer than one check's run as it is today and
  would arguably satisfy R6's own "one second at the least". That repair lands in `cluster/remote.py` and
  `cluster/topology.py`, brushes the Non-goal on the autoscaler's sleep granularity and the ruling
  that leaves the Python API unguarded, and exceeds a `small` appetite. Which way to go is a contract
  decision: amend GOAL (fix it this cycle), or rule it deferred (seed + `ROADMAP.md` entry committed
  before the next review pins its SHA). Until ruled, it counts as CONFIRMED.

### F3 — [LOW/CONFIRMED] No test refuses NaN
- **Where:** `tests/test_period_floor.py` (no `nan` case in `KEY_ROUTES` or `RELAY_ZEROS`).
- **Failure scenario:** the gate ruling refuses NaN; the code does (`not seconds > 0`), but a floor
  written `seconds <= 0` would accept NaN and still pass every test in the cycle. `-inf` and `-0.0`
  are refused and untested too.
- **Evidence:** `grep -c -i nan tests/test_period_floor.py` → 0; the reviewer's drives: env `nan` and
  file `period = nan` → rc 2 on cluster, server and relay.
- **Touches invariant / requirement:** R8 (missing-but-non-blocking coverage of the NaN ruling).
  Cheap to add with F1's fix.

No invariant violation: §4 (`in_memory` honoured before any database step), §10 (no mutation of the
configuration singleton; CLI still wins), §11 (no launch, argv or key handling changed) and §12
(help, snippets, zsh completions, prose page and markers in the same commits; tests tagged; exit
status constants; docs `-W` clean) all pass. `server.py` changes only the `-P` type and one check at
the top of `check_args`, before `enable_tls`, `check_database` and any serve call; only
`--available-ports` skips it, and that launches nothing.

## Human-gate triggers

- **Triggered — by F2.** Its locus is `cluster/remote.py` (`AutoScaler.wait`), a high-blast-radius
  file, and every remedy either changes it (with `cluster/topology.py`) or amends a GOAL-gate ruling
  whose premise the drives refute. Explicit human sign-off is required before any further step.
- F1 (docs) and F3 (tests) would not trigger the gate on their own. No CONFIRMED finding touches
  `server.py` or a security or DB-lifecycle invariant.

## Routing

- **Verdict:** changes-requested. F1 is introduced by the diff and blocks; F3 is cheap to fix in the
  same pass.
- **Before `/hs-build`:** the human rules on F2 — amend GOAL (R6 and the Outcome) to repair the
  autoscalers' wait in this cycle, or defer it as a seed. Then `/hs-build` fixes F1 (and F3), and a
  review cycle 2 follows.

## Out of scope observations (not graded)

- A very large period is accepted (`-P 99999d` starts); one too large for `timedelta` fails only once
  autoscaling starts. No maximum is a stated Non-goal.
- With a string key such as `"0s"`, `hs server --available-ports` exits 2, while a number key `0`
  passes; the same holds for `"5MB"` today.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run.

## Review cycle 2 — approved (2026-11-15)

- **Reviewed commit:** 77a3af79d35997ff4a9b026ac1370c1d368d6666  ·  **Base:** `b17ff1a` (`origin/develop`)
- **Verdict:** approved
- **Cycle:** 2 of ≤3
- **Mode:** cycle 1's F1–F3 remediation graded, plus a fresh blind pass over the full spec-excluded
  diff. One blind correctness reviewer (no `debate`, no `completeness` sub-pass). The orchestrator
  re-ran the deciding evidence for F2 and the docs build.

### Pre-flight

- **Base.** `origin/develop` fetched: `b17ff1a`. The branch was cut at `90ab2fc`. The graded diff is
  `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, 16 files. Code was graded against the
  `origin/develop` copies of `invariants.md` and `AGENTS.md`.
- **Merge.** `git merge-tree --write-tree origin/develop HEAD` exits 0, so the branch merges cleanly.
- **Contract drift.** `GOAL.md` has only its shaping commit `57fcced` and the gate lock `2f4df3e`.
  The review-cycle-1 ruling `e89f3c9` has maintainer sign-off and is recorded outside `GOAL.md`. It
  amends R6 for `autoscale.period`: a positive number below one second, from a file or
  `HYPERSHELL_AUTOSCALE_PERIOD`, is read as one second, as `-P` and a key string already are. Nothing
  that starts today is refused, and `cluster/remote.py`, `cluster/topology.py` and `relay/site.py`
  stay unchanged. The reviewer got this ruling as a binding reading, along with the gate readings:
  - the floor is greater than zero;
  - a number in the key names `autoscale.period`, and a string names `-P/--period`;
  - NaN is refused;
  - the Python API stays unguarded.

### Verification run

The reviewer ran these in throwaway sites (`temp_site.sh`), with the key in `HYPERSHELL_SERVER_AUTH`.

- **Tests at HEAD.** `tests/test_cluster_no_db.py tests/test_period_floor.py tests/test_duration_options.py`
  gave 556 passed. The orchestrator re-ran them and got the same 556.
  - Targeted wider suite: 1921 passed, 9 skipped. It covered `test_server_initdb`, `test_autoscale*`,
    `test_cluster*`, `test_relay_*`, `test_topology_*`, `test_zone_autoscale`, `test_size_duration`,
    `test_duration*` and `test_period_floor`.
  - `test_server`, `test_main`, `test_key_option` and `test_queue_tls`: 274 passed.
  - Every new test carries a real marker: `--collect-only -m "not unit and not integration"`
    collects 0.
- **Red on `90ab2fc`.** With the base `src` first on `PYTHONPATH`:
  - The no-db and duration files gave 8 failed, each for the defect: `['initdb'] == []`, the
    database file created, exit 5 on an unopenable path, and relay `0.5` not read as one second.
  - `test_period_floor.py` fails at collection on base, because `parse_period` does not exist
    there. With that import guarded in a scratch copy, it gave 121 failed and 28 passed. The 28 that
    pass are the kept-behaviour pins.
- **Mutation.** The floor was rewritten as `seconds <= 0` in a scratch copy of HEAD `src`. 10 tests
  fail against it: the NaN cases in the parser, in the key and environment for every launch, and in
  the relay.
- **`--no-db`.** With `HYPERSHELL_DATABASE_FILE` under a missing directory, `hsx`, `hs cluster` and
  `--launcher` gave rc 0, ran both tasks, and did not create the directory.
  - An initialized `task.db` was left byte-identical by local and `--launcher` runs (same sha and
    mtime).
  - In-process `ClusterApp.__enter__` with the unopenable path, under `--mpi`, `--ssh localhost`,
    `--launcher` and local mode: HEAD creates nothing, and base raises `OperationalError` in every mode.
  - `--autoscaling --no-db` is still refused in `check_arguments`.
  - Without `--no-db`, the four tables are created and the unopenable path still exits 5.
  - PostgreSQL on an unreachable port: rc 0 with `--no-db` and rc 5 without, the same as base.
    The `turso` provider was not driven because its driver is not installed; the skip does not
    depend on the provider.
- **Period refusals.** Each of the following exits 2 on HEAD and starts on base:
  - `-P`/`--period` set to `0 0s 0m 0:00 0-0 0.0`, on `hs cluster`, on `hs server FILE --no-db`
    and on `hs server --autoscaling fixed`. The error is
    `argument -P/--period: Period '<v>' is not valid: must be greater than zero`.
  - Key numbers (env and file) `0 -5 0.0 -0.0 nan -inf false`. The error is
    `autoscale.period: Period <v> is not valid: must be greater than zero`.
  - Key strings `"0s"` and `"0:00"`, which are refused naming `-P/--period`.
  - `hs relay` with env and file values `0 -5 0.0 -0.0 nan -inf 0s 0:00 false`: it exits 2 naming
    `autoscale.period`, before `bind_database` or any upstream connection. On base, `nan` and `-inf`
    exit 5. `5MB` exits 2 on both HEAD and base.
- **Accepted periods.** `0.001`, `0.5`, `1`, `"0.5"` and `true` start.
  `test_period_key_accepted` records what reaches `run_cluster`, `serve_with_sites` and
  `RelayThread`:
  - `0.001` and `0.5` arrive as int `1`;
  - `1.5` arrives as `1.5`;
  - `2` arrives as `2`.
- **Spin drive.** The cluster runs used `--autoscaling fixed -I 1 --launcher <sleeper> --no-tls` with
  debug logging. Each run lasted 7.5 s, measured by `Autoscale check` lines and lifetime CPU.

  | Case | Base `90ab2fc` | HEAD (reviewer) | HEAD (orchestrator re-run) |
  |---|---|---|---|
  | cluster, env `0.001` | 4530 checks, 7.39 s | 7 checks, 0.34 s | 7 checks, 0.36 s |
  | cluster, file `0.001` | 4517 checks, 7.41 s | 7 checks, 0.34 s | 6 checks, 0.37 s |
  | cluster, `-P 0` | 4320 checks, 7.40 s | refused | not run |
  | cluster, env/file `0.5`, file `true`, `-P 0.5` | not run | 7 checks, about 0.35 s each | not run |
  | cluster, `-P 1` | 6 checks, 0.37 s | 6 checks, 0.37 s | 7 checks, 0.35 s |
  | server `--autoscaling fixed`, env `0.001` | 7.41 s CPU | 0.36 s CPU | 0.31 s CPU |
  | server, `-P 0` | 7.42 s CPU | refused | not run |
  | server, `-P 1` | 0.33 s CPU | 0.34 s CPU | 0.33 s CPU |

  Every process the drives started was stopped by its recorded PID or process group. A final
  out-of-sandbox `ps` showed none of them left.
- **Docs.** `sphinx-build -W --keep-going -q -E -b html` exits 0 with no warnings, in both the
  reviewer's run and the orchestrator's.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/__init__.py` `ClusterApp.__enter__`: no `initdb`/`checkdb` under `--no-db` | `--no-db` drives and in-process enters above (local, `--launcher`, `--mpi`, `--ssh`, `hsx`, unopenable path, existing file byte-identical); `test_no_db_makes_no_database_call` | ✅ |
| R2 | same | tables created; unopenable path still exits 5; `--initdb`; PostgreSQL identical to base; `test_database_step_kept` | ✅ |
| R3 | `core/types.py` `parse_period` as the `-P` type in `server.py` and `cluster/__init__.py` | every zero form refused with rc 2 on all three launch kinds, message naming option, value and floor; `test_period_option_zero_refused` | ✅ |
| R4 | `check_args` / `check_arguments` parse the key first and keep the result | env/file numbers and strings, NaN, `±0.0`, `-inf`, `false` → rc 2 with the D1 naming; `test_period_key_refused` | ✅ |
| R5 | `RelayApp.site_period` through `parse_period`, read in `check_args` | relay refusals above, before any database or upstream; `test_relay_period_zero_refused` | ✅ |
| R6 (as amended) | `parse_period`: a number below one second → `1`, one or more as given; strings via `parse_time` | values reaching `run_cluster`/`serve_with_sites`/`RelayThread`; positive forms start; spin drive above | ✅ |
| R7 | help strings, `cluster_help.rst`, `server_help.rst`, `config_param_ref.rst`, `docs/cli/cluster.rst`, `docs/cli/server.rst`, zsh `_hs` | each passage read against the drives; markers beside each `.. include::` and under `.period` alongside the existing one; `-W` clean | ✅ |
| R8 | `tests/test_cluster_no_db.py`, `tests/test_period_floor.py`, `tests/test_duration_options.py` | red on `90ab2fc` for the defect, green at HEAD; the NaN cases catch a `<= 0` floor | ✅ |

Unmapped changes (possible scope creep): none. In `ROADMAP.md` and the two seeds, only `status:`
changes, to `adopted:cluster-nodb-and-zero-period`. Both seed files exist.

### Cycle-1 remediation

- **F1: remedied.** `config_param_ref.rst` `.period` now reads: "A period below one second, in any
  form, is one second." Its marker reads: "a number below one second is one second". Both match the
  drives: the option, a string and a number all read `0.5` as one second, and `0.001` as one second
  where it is a number.
- **F2: remedied.** The maintainer ruled that numbers round up at the command layer. No autoscaler
  spins from any route; the spin table above shows it. Every caller that builds an autoscaler gets
  its period through the floor:
  - `run_cluster` via `ClusterApp`;
  - `serve_with_sites` via `ServerApp`;
  - the relay fleets via `site_period`.

  Nothing else reads `config.autoscale.period`. As ruled, `cluster/remote.py`, `cluster/topology.py`
  and `relay/site.py` are unchanged.
- **F3: remedied.** NaN, `-inf` and `-0.0` are covered in the parser, in the key and environment for
  every launch, and in the relay. The NaN cases fail against a `seconds <= 0` floor.

### Findings

None. The reviewer dropped each candidate below after running it:

- **`+inf` (or `1e309`) in the key.** It is accepted, and an autoscaling run then exits 5 with
  `OverflowError`, exactly as on base. A maximum period is a Non-goal.
- **Negative strings (`-P -5`, `"-5s"`).** They exit 2 as `Duration … is not valid`, as on base:
  #106 refuses a sign. R3 covers zero, and R4's "negative number" means a number.
- **`true`.** It is read as `True`, which is one second, the same as base. It does not spin.

No invariant violation:

- §4: `in_memory` is honoured before any database step.
- §10: the configuration singleton is not mutated, and the CLI still wins.
- §11: no launch, argv or key handling changed.
- §12: help, snippets, completions, prose pages and markers changed together, tests are tagged, and
  the docs build is `-W` clean.

`server.py` changes only two things: the `-P` type, and a check at the top of `check_args` that stores
the parsed period. That check runs after `special_options()` and `resolve_auth`, and before
`enable_tls`, `check_database` and every `serve_*`. Only `--available-ports` skips it, and that
launches nothing.

### Human-gate triggers

- **Not triggered this cycle.** No CONFIRMED finding exists, so none touches `server.py`, any other
  high-blast-radius file, or a security or DB-lifecycle invariant.
- Cycle 1's trigger (F2) was discharged by the maintainer's sign-off on the review-cycle-1 rulings,
  given before the remediation. The remediation stayed inside that ruling: `parse_period` and the
  two command-layer call sites changed, and `remote.py` and `topology.py` did not.

### Routing

- **Verdict:** approved. Recommend `/hs-publish`.

### Out of scope observations (not graded)

- `+inf` in `autoscale.period` is accepted at start-up and fails with `OverflowError` once an
  autoscaler starts, the same as base. This touches the no-maximum Non-goal.
