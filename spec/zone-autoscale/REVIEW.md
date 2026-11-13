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
