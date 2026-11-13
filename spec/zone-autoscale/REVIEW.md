# REVIEW — Zone-aware autoscaling (zone, cycle 3)

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-12)

- **Reviewed commit:** 86fe37225a9337724f08c9692ba2c82c6e9ba327  ·  **Base:** `origin/develop` `1ea9145`  ·  **Date:** 2026-11-12
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** one fresh blind reviewer (no `debate`, no `completeness` sub-pass). It saw `GOAL.md`,
  the spec-excluded diff, the base `invariants.md` and the rubric. It was told not to read anything
  else under `spec/zone-autoscale/` (`drive/` and `evidence/` included). The orchestrator
  reproduced F1 again in a fresh throwaway site and checked F2–F4 against the reviewer's captured
  logs.

### Pre-flight

- **Base.** `TECH.md` records `base: develop`, but the local `develop` (`5f22298`) is stale. The
  graded diff is `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, and `1ea9145` is the
  merge base.
- **Build state.** `TECH.md` is `status: in_review`, and P1–P4 are done.
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/zone-autoscale/GOAL.md` lists
  `8a85d46` (shape), `8d6c663` (plan) and `5173ba2` (plan gate). The plan commits only append
  three dated clarifications from the `hs-plan` gate: empty edge, relay fleets, and R3/R4 at the
  default zone. No R-ID text changed, and all three commits predate the first build commit
  (`c76cc03`), so the contract did not move mid-build.
- **Invariants handed to the reviewer.** The branch edits `AGENTS.md` and
  `.agents/factory/invariants.md` itself (§11, autoscaled launches per zone). The reviewer got the
  base `invariants.md` as its checklist and graded the branch's edits as part of the diff. It
  found every added claim accurate against the code.

### Verification run

- **Targeted tests:** `uv run pytest tests/test_zone_autoscale.py tests/test_zone_model.py
  tests/test_zone_postgres.py tests/test_autoscale.py tests/test_cluster_launch.py
  tests/test_relay_launch.py tests/test_relay_fsm.py tests/test_completions.py` →
  **344 passed, 6 skipped**.
- **Full suite:** `uv run pytest -q -rs` → **1718 passed, 88 skipped, 0 failed** (16m13s). Every
  skip is environmental: PostgreSQL (`HS_TEST_POSTGRES`; no server here, so CI runs these),
  TimescaleDB, fish not installed, and the 2.8.3 fixture regeneration.
- **Docs:** `sphinx-build -W --keep-going -E -b html` with every extension except
  `sphinx_sitemap`, which cannot bind a socket in the sandbox → exit 0, no warnings.
- **CLI drives:** every drive ran `hsx … --autoscaling` in its own `temp_site.sh` site. The
  launcher was a recording shim (`sh shim.sh --part={zone}`) that logs its argv and
  `HYPERSHELL_ZONE` and then execs the client. For the comparison runs, the base build was a
  `git archive origin/develop src` copy run through `PYTHONPATH`. The scenarios:
  - zone-less founding case under fixed and dynamic policies, including an empty-database start
    with `-I 2 -X 1` and a late submit, base against branch;
  - own named `--zone`, base against branch;
  - two zones in successive groups (`cpu -g 0`, `gpu -g 1`);
  - two zones in one group under the dynamic policy with `-Y 3`;
  - retry gating;
  - `HYPERSHELL_ZONE` as configuration, with nested `hs submit` and `hs list` inside a task;
  - a discovered default zone under a named own zone;
  - restart with a late lower group (F1);
  - own named zone across a group change (F2);
  - in-process probes of `client_launch` (Open MPI forward list, the key never in argv, SSH with
    `expose_zone`) and of a relay `zone_fleet` (F4).
- **Tree:** `git status --porcelain` was empty on hand-back, and the reviewer created no scratch
  worktree.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `data/model.py`: `count_remaining(zone=)`, `count_remaining_by_zone`, `count_retryable_by_zone`, and the zone-filtered `effective_rate`/`avg_duration`/`time_to_completion`/`task_pressure`. `cluster/demand.py`: `read_zone_loads`. `AutoScaler.check_zones`. Per-zone `launched()`. | Dynamic drive with zones a and b in one group, `-Y 3`: a scaled to 3 on its own pressure (`23.60`, then `max-size reached (zone: a, 3 >= 3)`) while b stayed at 1. The PostgreSQL test covers the per-zone counts in CI. | ✅ |
| R2 | `read_zone_loads(discover=True)`: zones found from the group the scheduler runs next (`increment_group`, read-only) | cpu `-g 0` / gpu `-g 1`: gpu was not launched until `Completed task group 0`, then launched once. Retry gating held. **But see F1** (after a restart, the autoscaler provisions a gated zone and starves the active one) **and F2** (the own named zone is stranded at a group change). | ❌ (F1, F2) |
| R3 | `client_launch(expose_zone=True)` (`{zone}` in launcher words, `HYPERSHELL_ZONE` in the env), `LaunchActuator`, `--zone` for named zones; `docs/_include/cluster_help.rst` (shim example), `docs/cli/cluster.rst` and `config_param_ref.rst` (`versionchanged:: 2.9.0`), zsh completion | `--part={zone}` → `cpu`/`gpu`/`default`. `HYPERSHELL_ZONE` set on every launch. `--zone <name>` only for named zones, and the own default zone's argv is identical to base. `-t 'echo {zone}-{}'` untouched. `HYPERSHELL_ZONE` never in the Open MPI forward list, the key never in argv, SSH + `expose_zone` raises `ValueError`. Docs build clean. | ✅ |
| R4 | `ScalingKnobs` / `zone_demand` / `fleet_demand`: uniform knobs per zone, with init-size and min-size for the own zone only | Founding case, base against branch, under fixed, dynamic and an empty-database start: the same launch counts (1/1, 4/4, 8/8, 10/10), the same decision sequence and the same argv. Own named zone: argv identical to base. Log text differs: **F3**. | ✅ (decisions); F3 for logs |
| R5 | No termination path: `LaunchActuator.clean` only polls | Every zone's clients `disconnected` on idle in the two-zone drives. | ✅ |

**Relay fleets (GOAL clarification).** `site_cluster` passes `discover_zones=False`, site sizes
are ≥ 1 so min-size governs, and the relay tests pass. Fleet decisions are unchanged; only the
log labels changed (F4).

**Unmapped changes:** none of substance. `AutoScalerPolicy` moved to `cluster/demand.py`, and
`cluster/remote.py` still re-exports it. `issues/zone.md` and `ROADMAP.md` now read
`status: adopted:zone-autoscale`, which matches reality.

**Probed and clean:**
- `HYPERSHELL_ZONE` falls under the configuration prefix. It lands as an inert top-level `zone`
  key: `hs config which zone` names it, config loads for odd values (`[x`, `1e5`, `nan`, `a:b`, …),
  and a task's nested `hs submit --zone inner` and `hs list` behave normally.
- §5 concurrency: the FSM states and `HALT` are unchanged, `stop()` still halts the machine, no
  new blocking operation was added, and `Dispatcher.clients` takes its lock.
- On PostgreSQL the zone column comes back as string keys.

### Findings

#### F1 — [MEDIUM/CONFIRMED] After a restart, zone discovery reads a different group than the scheduler runs
- **Where:** `src/hypershell/cluster/remote.py:519` (`group = Task.current_group().value`) with
  `src/hypershell/cluster/demand.py:110` (`ahead = Task.increment_group(group, …)`).
- **Failure scenario:** `Task.current_group()` is the group of the most recently scheduled task,
  and discovery moves past it only once that group is complete. On a restart the scheduler's
  `starting_group()` drops to the lowest incomplete group, even when the most recently scheduled
  group still holds incomplete tasks. The two reads then diverge. Suppose a late lower group 1
  (zone x) sits under a partly run group 2 (zone y). The scheduler holds group 1 and waits on x,
  while the autoscaler reads group 2. It keeps launching y clients, which get no work and idle
  out, and it never launches x. The run stalls with no end. R2 is broken both ways: the gated zone
  is provisioned, and the active zone gets nothing.
- **Evidence** (reviewer, then reproduced by the orchestrator in a fresh site):
  1. `hs submit -f y1.in --zone y -g 2` (two tasks), run to completion with
     `hsx --restart --zone y -N 1`.
  2. `hs submit -f y2.in --zone y -g 2` (two tasks) and `hs submit -f x.in --zone x -g 1` (three
     tasks).
  3. `hsx --restart --autoscaling fixed -P 2 -T 3 -N 1 -I 0 --launcher "sh shim.sh --part={zone}"`
     for 30 s.
  - The server logs `Moving to task group 1 (lowest group with incomplete tasks)` and
    `Task group 1 waiting on zone x: 3 tasks, no client serving it`.
  - The shim recorded **5 launches with `HYPERSHELL_ZONE=y` and none with `x`**. Every check read
    `tasks: 2 in group 2`, and all five pending tasks were still unrun at the end.
- **Touches:** R2. `cluster/remote.py` is high-blast-radius core.

#### F2 — [MEDIUM/CONFIRMED] The own named zone is stranded when its next group's work arrives after its fleet idled out
- **Where:** `src/hypershell/cluster/demand.py:114-117`. The own zone's next-group work is dropped
  ("read in the current group … as without zones"), and the own zone is excluded from discovery
  (`found`, `:118`).
- **Failure scenario:** a cluster with a named own zone, `--zone x`, finishes group 0 and its x
  clients idle out. Group-1 x tasks then arrive. With no x client there is no x pool, so nothing
  is staged and `Task.current_group()` stays at 0. The autoscaler reads the own zone in group 0,
  finds 0 work, and never launches. A non-own zone in the same position is discovered and gets a
  client; only the own named zone is left out. The default own zone is unaffected, because the
  default pool stages without a client.
- **Evidence:**
  1. `hs submit -f x0.in --zone x -g 0` (two tasks).
  2. `hsx --restart --zone x --autoscaling fixed -P 2 -T 2 -N 1 --launcher "sh shim.sh --part={zone}"`
     for 40 s.
  3. After 14 s, `hs submit -f x1.in --zone x -g 1` (two tasks).
  - The run shows one x launch, then `Completed task group 0 - starting task group 1` and
    `Task group 1 waiting on zone x: 2 tasks, no client serving it`.
  - After that, every check reads `Autoscale check (zone: x, clients: 0/0, tasks: 0 in group 0)`
    with no further launch, and the group-1 tasks never run.
  - In a control run that also submitted a y task at `-g 1`, y was discovered and launched, its
    staging moved the group, and x then proceeded.
- **Not deferred.** `origin/develop` stalls the same way, so the behaviour predates the diff. But
  no GOAL criterion requires keeping it:
  - The empty-edge clarification preserves the own zone's init-size and min-size, not this read of
    a stale group.
  - R4's "identical to today" covers the default single-zone case, which this does not reach.
  - GOAL's note on R2/R4 says "no active zone is left without a client while max-size allows one".

  It can be repaired this cycle, so it blocks.
- **Touches:** R2, R4 (GOAL note). F1 and F2 share a root cause: the autoscaler takes its group
  from `Task.current_group()`, the most recently scheduled group, not from the scheduler's own
  current group. With per-zone pools, the two can diverge.

#### F3 — [LOW/CONFIRMED] Zone-less founding case: the autoscaler's log lines changed
- **Where:** `src/hypershell/cluster/launch.py:287` and `:294`, and `src/hypershell/cluster/remote.py:536`
  (`log_check`).
- **Failure scenario:** decisions, argv and launch counts match base, but the text changed:
  - INFO `Autoscale adding client (PID)` → `(PID, zone: default)`.
  - DEBUG `Autoscale launcher:` → `Autoscale launcher (zone default):`.
  - The check line drops `, N total` and gains `zone: default`.
  - The dynamic policy's `Autoscale pressure high/low` and `Autoscale pause (…)` lines are gone.
  - The registered count now comes from the dispatcher instead of `Client.count_connected()`:
    base logged `clients: 0/2` where the branch logs `2/2`.
- **Evidence:** the founding-case drives under fixed and dynamic policies, base against branch,
  with the log lines compared side by side. No docs page cites these lines.
- **Triage:** this needs a ruling on whether R4's "identical to today's behavior" covers log text.
  If the new text is accepted, F3 closes without a code change.

#### F4 — [LOW/CONFIRMED] A relay's per-zone fleets now label themselves `zone: default`
- **Where:** `src/hypershell/relay/site.py:149`. `zone_fleet` builds `AutoScalerThread` without
  `own_zone`, so the fleet's own zone defaults to the default zone.
- **Failure scenario:** a relay fleet serving `gpu` logs
  `Autoscale launcher (zone default): … client --zone gpu` and
  `Autoscale adding client (PID, zone: default)`. Base logged `Autoscale launcher: …` and
  `Autoscale adding client (PID)`. The fleet's decisions are unchanged (min = max = size ≥ 1).
  The relay clarification asks for no behaviour change in `relay/`, and these labels are wrong.
- **Evidence:** an in-process `zone_fleet` probe in a temp site, run on both builds.

### Human-gate triggers

- **Triggered.** F1 is CONFIRMED in `cluster/remote.py` (high-blast-radius core), and F2's remedy
  shares its root cause. A human must sign off before any further step.

### Open questions for the maintainer

1. F2: should the own named zone be read in the next group, as every other zone is? That would
   close F2 without changing the default own zone's case.
2. F3: do the autoscaler's INFO and DEBUG log lines count as "today's behavior" under R4?

## Review cycle 2 — approved (2026-11-12)

- **Reviewed commit:** a9c1c76d15492caa0398bc1c0404fede1f798d8f  ·  **Base:** `origin/develop` `1ea9145`  ·  **Date:** 2026-11-12
- **Verdict:** approved
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** remediation-scoped re-review, as the foreman's brief directed. It verified that each
  cycle-1 finding is closed, and graded the remediation (`3de6929..a9c1c76`) for new defects
  against `GOAL.md` and the invariants. One fresh blind reviewer took part (no `debate`, no
  `completeness` sub-pass). Its inputs were exact copies of four documents:
  - `GOAL.md` at HEAD, with the four 2026-11-12 Clarifications;
  - the base `invariants.md`;
  - the rubric;
  - the cycle-1 findings F1–F4, verbatim.

  It had the remediation diff and the full branch diff, both spec-excluded. It was told not to read
  anything under `spec/zone-autoscale/` or any commit message body. The orchestrator reproduced F1
  and the named-zone F2 case again on HEAD, each in a fresh site.

### Pre-flight

- **Base.** The local `develop` is still stale, so this review graded against `origin/develop`
  (`1ea9145`), the merge base.
- **Build state.** `TECH.md` is `status: in_review`, and P1–P4 are done.
- **Contract drift.** Since cycle 1, `git log --oneline origin/develop..HEAD --
  spec/zone-autoscale/GOAL.md` adds `bab3877`. That remediation commit appends four dated
  Clarifications, which record the maintainer's rulings on F1–F4 at the cycle-1 human gate. No R-ID
  text changed. The foreman's brief names these rulings as this cycle's contract, so this review
  graded against them.
- **Invariants.** The reviewer got the base copy, as in cycle 1. The branch adds lines to §11 of
  `invariants.md` and to `AGENTS.md`; the reviewer graded those as part of the diff and found them
  accurate against the code.

### Verification run

- **Targeted tests** (reviewer) → **530 passed, 1 skipped**. The command:
  `uv run pytest -q tests/test_zone_autoscale.py tests/test_autoscale.py tests/test_zone_model.py
  tests/test_relay_launch.py tests/test_relay_fsm.py tests/test_completions.py tests/test_cluster.py
  tests/test_group_order.py tests/test_retry_gate.py tests/test_relay_return.py tests/test_restart.py
  tests/test_server_fsm.py tests/test_zone_dispatch.py tests/test_cluster_launch.py`
- **Full suite** (orchestrator): `uv run pytest -q -rs` → **1732 passed, 88 skipped, 0 failed**
  (16m24s). Every skip is environmental: 70 PostgreSQL, 16 TimescaleDB, 1 fish, and 1 for the
  2.8.3 fixture regeneration.
- **PostgreSQL** (orchestrator, against a local server, with `HS_TEST_POSTGRES` and
  `HS_TEST_TIMESCALE=community`): `tests/test_upgrade_postgres.py tests/test_writeback_postgres.py
  tests/test_timescale.py tests/test_contended_write.py tests/test_zone_postgres.py` → **131
  passed, 1 skipped, 29 failed**.
  - All 29 failures are `Template hs283|hs290a1 not listed in HS_TEST_POSTGRES_TEMPLATES`: 28 in
    the upgrade tests and 1 in `test_timescale.py`. Those templates need Docker.
  - `test_zone_postgres.py` passed.
- **Docs:** `sphinx-build -W --keep-going -E -b html` with every extension except `sphinx_sitemap`
  → exit 0 and no Sphinx warnings. The remediation touches no docs.
- **CLI drives:**
  - **Setup.**
    - Every drive ran in its own `temp_site.sh` site.
    - The launcher was a recording shim that logs argv and `HYPERSHELL_ZONE`, then execs the
      client. It points the client's `-H` at loopback, because the sandbox cannot resolve the
      machine's host name.
    - The base builds were `git archive` copies of `origin/develop` and `86fe372`, run through
      `PYTHONPATH`.
  - **Scenarios:**
    - F1 on HEAD and on `86fe372`, plus an own-zone variant;
    - F2 with the own zone named, under the fixed and dynamic policies, and with the default own
      zone;
    - the dynamic policy's log lines;
    - a relay `zone_fleet` probe on both builds;
    - the zone-less case, base against branch:
      - fixed and dynamic policies;
      - an empty database with `-I 2 -X 1` and a late submit, under fixed and dynamic;
      - a restart with every task complete, then a late submit;
      - clients idled out, then a late submit;
    - a late lower group submitted mid-run, three ways;
    - a group halted on an exhausted failure.
- **Tree:** `git status --porcelain` was empty on hand-back.

### Cycle-1 findings

| ID | Status | Evidence (HEAD; pre-remediation `86fe372`) |
|----|--------|---------------------------------------------|
| F1 | ✅ closed | **HEAD:**<br>• The server logs `Moving to task group 1 (lowest group with incomplete tasks)`.<br>• x is launched first (`3 tasks remaining in group 1`).<br>• y is launched only after `Completed task group 1 - starting task group 2`.<br>• Two launches in all, and 7/7 tasks complete.<br>**`86fe372`:**<br>• 9 launches, all y, none x.<br>• Every check reads `tasks: 2 in group 2`.<br>• The 5 pending tasks never ran.<br>**Own zone y (HEAD, default init-size):** 7/7 tasks, with one init-size y launch, which the empty-edge clarification allows. |
| F2 | ✅ closed | **Named `--zone x`, group-1 work submitted after the x clients idled out:**<br>• HEAD launches x right after the submit, and 4/4 tasks complete under fixed and dynamic.<br>• `86fe372` stays at `Autoscale check (zone: x, clients: 0/0, tasks: 0 in group 0)`, and group 1 never runs.<br>**Default own zone:**<br>• HEAD launches at the next check, 4/4.<br>• Base also completes 4/4, 5 s later, so this case did not stall on base. |
| F3 | ✅ closed | The dynamic drive logs `Autoscale pressure high (zone: default, 11.10)` and `Autoscale pause (zone: default, …)` for both reasons. `pressure low` is covered by `tests/test_zone_autoscale.py:737`. |
| F4 | ✅ closed | A `zone_fleet(…, 'gpu', size=2)` probe logs `Autoscale launcher (zone gpu): …` and `Autoscale adding client (PID, zone: gpu)`. Its launch sequence matches base: init 2, then the min-size relaunches at the same checks. The argv is identical. |

### Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | The F1 drive logs a check line per zone, each with its own client count. The dynamic zone-less run matches base, decision for decision. | ✅ |
| R2 | The F1 drive on HEAD. Three mid-run drives submitted a late lower group (x at `-g 1` while y at `-g 2` runs): with the default own zone, with own zone y, and under dynamic. In each, x was never launched before `Completed task group 2`. In the halted-group drive, x got one launch, then `Failed task group 0`, and the group-1 work got none. | ✅ |
| R3 | The shim log shows `--part=x`, `HYPERSHELL_ZONE=x` and `--zone x`. The default zone shows `--part=default` with no `--zone`. The docs are unchanged since cycle 1. | ✅ |
| R4 | The zone-less comparison below. The one difference is the earlier launch under open question 1. | ✅ |
| R5 | Every drive logs `Autoscale client disconnected` after the `-T` idle-out, and the remediation adds no termination path. | ✅ |
| F1 ruling | The F1 drives on HEAD and on `86fe372`. | ✅ |
| F2 ruling | The named and default drives. | ✅ |
| F3 ruling | The drive's log lines, and `tests/test_zone_autoscale.py:735-739`. | ✅ |
| F4 ruling | The relay probe on both builds. | ✅ |

The zone-less case, base against HEAD, same drive and deadline:

| Drive | Base launches | HEAD launches | Tasks |
|-------|---------------|---------------|-------|
| fixed | 1 | 1 | 6/6 each |
| dynamic | 4 | 4 | 12/12 each |
| empty database, `-I 2 -X 1`, late submit, fixed | 7 | 7 | all |
| empty database, `-I 2 -X 1`, late submit, dynamic | 7 | 8 | all |
| clients idled out, then a late submit (fixed, dynamic, restart with every task complete) | 2, 2, 1 | 2, 2, 1 | all |

The 7-against-8 run is a phase shift within the deadline. Clients idle out after `-T 3` and are
relaunched at min-size, and every launch in both runs is logged `min-size reached (0 < 1)`. The argv
is identical throughout.

**Unmapped changes:** none.
- The `log_check` restructure maps to F3.
- Using the registered count in the own-idle read maps to F2.
- The new `invariants.md` and `AGENTS.md` lines record the F1 and F2 rulings.

### Findings

None. No finding is CONFIRMED and none is PLAUSIBLE.

**Probed and clean:**
- **Thread-safety of `Scheduler.active_group`** (`server.py`). It reads two attributes, and each read
  is atomic.
  - `first_group_pending` turns false only in `mark_group_started`, after `current_group` is set.
  - `current_group` is `None` until `start()` assigns it.
  - A read that interleaves with a write therefore gets either `None`, which falls back to the start
    rule, or a group the scheduler holds. Neither leads to a wrong launch.
- **Staleness while the scheduler backs off.** The read-only `increment_group` advance covers it. The
  mid-run drives in the R2 row show that a late lower group never launches early.
- **`--no-db`.** `AutoScalingCluster` ignores `in_memory`, so a scheduler always exists. Only relay
  fleets use the `Task.starting_group` fallback.
- **`Task.starting_group`** behaves exactly like base's inline rule in `Scheduler.starting_group`.
  The scheduler keeps the case where none means pending.
- **The own-idle read in relay fleets** (`discover_zones=False`). It uses `Client.count_connected()`,
  which counts every zone. That changes only which group is read and logged. Relay sizes are at
  least 1, so min-size decides, and the F4 probe shows the decisions unchanged.

### Human-gate triggers

- **Not triggered.** This cycle has no CONFIRMED finding.
  - Cycle 1's trigger, F1 in `cluster/remote.py`, was answered by the maintainer's 2026-11-12
    rulings.
  - The remediation edits three high-blast-radius files: `server.py`, `data/model.py` and
    `cluster/remote.py`. This pass found no defect in them.

### Open questions for the maintainer

1. **The zone-less case, when its clients idle out and a later group's work then arrives.**
   - **What happens.** Under the F2 ruling, the default own zone is read in the next group, so HEAD
     launches earlier than base:
     - 5 s earlier under fixed;
     - 4 s earlier under dynamic;
     - 1 s after the submit instead of 7 s, on a restart with every task complete.
   - **What does not change.** Launch counts and argv are the same.
   - **Why base is later.** Base launches only once the scheduler's back-off ends and the scheduler
     stages the group itself. The back-off is bounded by `-Q`, 30 s by default.
   - **How it was graded.** Base does not stall here, so the ruling's "everywhere else its
     decisions … stay exactly as today" could be read to cover this case. The reviewer and the
     orchestrator read the difference as timing only, so it is not filed. If the maintainer reads
     it as a decision change, the fix belongs in the ruling or in the own-idle read.
