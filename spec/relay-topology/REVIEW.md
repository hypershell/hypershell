# REVIEW — Relay site-topology autoscaling: fill and spread across sites

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-13)

- **Reviewed commit:** 0ddebf9fd88e8a52490b7ccb34ab2dcacc0c7781  ·  **Base:** `origin/develop` `22684fd`  ·  **Date:** 2026-11-13
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** one fresh blind reviewer (no `debate`, no `completeness` sub-pass). It got `GOAL.md`, the
  base `invariants.md` and the rubric as verbatim copies in a scratch directory, the spec-excluded
  diff command, and the base `AGENTS.md` (`git show origin/develop:AGENTS.md`). It was told not to
  read `PLAN.md`, `TECH.md`, `research/`, `META.md` or `evidence/`. It could run the laptop drive
  in `drive/` as a harness, judging from raw logs. The orchestrator ran the full suite, the
  PostgreSQL suite, the docs build and the cluster judge, and reproduced RV1 and RV2 in fresh
  throwaway sites from the reviewer's harness scripts.

### Pre-flight

- **Base.** `TECH.md` records `base: develop`, but the local `develop` is stale. The graded diff is
  `git diff origin/develop...HEAD -- . ':(exclude)spec/'`; `22684fd` is the merge base.
- **Build state.** `TECH.md` is `status: in_review`, with P1–P5 done.
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/relay-topology/GOAL.md` lists
  `b9e8e53` (shape), `d0c8fd7` (GOAL-gate rulings) and `f01dce0` (plan-gate approval and its Q1
  ruling). Both later commits record dated maintainer and foreman rulings in Clarifications and
  amend R1, R4, R5 and R6 as those rulings say. Both predate the first build commit (`d170337`), so
  the contract did not move mid-build. The review grades the amended contract.
- **Invariants handed to the reviewer.** The branch edits `AGENTS.md` and
  `.agents/factory/invariants.md` (§11: the publishing server, `SiteScaler`, relay launch
  exposure). The reviewer used the base `invariants.md` as its checklist and graded the branch's
  edits as part of the diff. RV3 comes from that.

### Verification run

- **Full suite (orchestrator, sandboxed):** `uv run pytest -q tests` → **1914 passed, 88 skipped**,
  2 warnings (the existing `fork()` deprecation warnings in `tests/test_logging.py`), 17 min 13 s,
  under CPython 3.12.13, the interpreter the worktree's `.venv` held (META F4).
- **PostgreSQL suite (orchestrator, sandbox off, local server):**
  `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community uv run pytest tests/test_upgrade_postgres.py
  tests/test_writeback_postgres.py tests/test_timescale.py tests/test_contended_write.py
  tests/test_zone_postgres.py` → **131 passed, 1 skipped, 29 failed**. Every failure is the
  expected missing template: 20 name `hs283` and 9 name `hs290a1` ("not listed in
  `HS_TEST_POSTGRES_TEMPLATES`"). No other failures.
- **Docs (orchestrator):** `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b
  html docs <tmp>`, with `sphinx_sitemap` dropped from `extensions` (sandbox) → **exit 0, no
  warnings**.
- **Cluster records (orchestrator):** `uv run python spec/relay-topology/drive/topology_judge.py
  spec/relay-topology/evidence/cluster --require spread-kill,fill` → **PASS (2 runs, 0 failed)**,
  every check passing in both scenarios. The records could not be regenerated; only their judge was
  re-run.
- **Targeted tests (reviewer):** two sets over the topology, relay, zone-autoscale, dispatch and
  server tests → **282 passed**, and **307 passed, 1 skipped**.
- **Laptop drive (reviewer):** `.agents/factory/bin/temp_site.sh sh
  "$PWD/spec/relay-topology/drive/topology_drive.sh" MODE` for `floor`, `lower` and `kill` → exit
  0, and the raw launch logs and topology lines agree with the checks.
- **Reviewer's own multi-process scenarios** (harness scripts, not committed; each a server and
  relays as separate processes on loopback in a throwaway site, every process stopped by PID):
  - `compat`: four pairings of base and branch relays and servers. All 12 tasks were done each
    time. A new relay against a base server logged "publishes no topology (an earlier release)"
    once and kept its fixed fleet. A base relay against an autoscaling server took no part and kept
    its fixed fleet.
  - `founding`: `hs cluster --autoscaling` with zones, on base and branch. Both made 7 launches
    (`default` 1, `cpu` 3, `gpu` 3) and completed 18 tasks.
  - `sigint`: SIGINT gives exit status 4 (`keyboard_interrupt`) for branch `--autoscaling`, branch
    `--forever` and base `--forever` alike.
  - `dup`: Q2 shared site, with an older relay serving two zones.
  - `failover`: R3, with a relay killed.
  - `vanish`: the topology disappears and returns.
  - `argv_probe` and `ompi_probe`: launch argv and environment, base vs branch.
  - `defzone`: the default zone with retries.
  - `stale` (RV1) and `bounce`/`bounce_kill` (RV2).

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/topology.py` (`plan_sites`, `_preference`, `configured_distribution`/`configured_sites`), `core/config.py`, `relay/__init__.py` (per-zone `min`/`max`/`init`) | Pure-planner probe: under `fill`, listed site first, then unlisted sites by first registration, and a returning site keeps its rank. The server logs advertised bounds with `min` defaulting to 0. Drive peaks stay ≤ `max` (`floor`, `lower`, `kill`). The `floor` drive holds `gpu` `min` 1 while gated, and holds `init` with no work (`stale`). Under `dynamic`, a zone grows by one per check (`dup` server log). | ✅ |
| R2 | `cluster/topology.py` (`AutoScalingServer`, `TopologyPublisher`), `server.py` (`--autoscaling`), `core/dispatch.py` (`publish`/`topology`) | No server launcher: every launch in the logs is relay-labelled. Versions are `<instance>:<n>`, published only on change. Each relay reconciles only its own zones. | ✅ (RV1 on the relay side) |
| R3 | `server.py` (`drop_site` on eviction/finish), planner over live records | `failover` (fixed): B evicted, orphans reverted, B left the topology, A's target grew. 40/40 done, 0 failed. `kill` drive (dynamic): A launched 0 before B's eviction and 11 after. The cluster judge passes `kill.*`. | ✅ |
| R4 | `core/heartbeat.py` (`zone`, `bounds`, `clients`, `instance`), `relay/upstream.py` (`advertisement` on every beat), `core/topology.py` (`SiteRegistry`), `cluster/topology.py` (`_arrange`) | The advertisement is on every beat and re-recorded on each new server (`vanish`). `dup`: the warning names both relays, the older relay launched 0 in both zones, and it held again after the newer one's eviction. A zone goes only to sites serving it. | ✅ (RV2 for a crashed relay) |
| R5 | `relay/site.py` (`SiteScaler`), planner (no launch above `max`) | No termination in any scenario other than the sanctioned relay SIGTERM stop. `lower` drive: 3 running against a `max` of 1, no launches, all 3 ended by idle exit. The relay stayed connected at 0 clients (`stale` at `-P 2`: 40 s at 0, no eviction). | ✅ |
| R6 | `relay/upstream.py` (`fetch`: no method or null version → `view.set(None)`), `relay/site.py` (fallback fixed fleet) | `compat` four pairings. `argv_probe`: base and branch argv and fleet sizes are identical apart from the sanctioned `{zone}` and `HYPERSHELL_ZONE`. | ✅ |
| R7 | `docs/_include/config_param_ref.rst`, `server_*`/`relay_*` includes, `docs/cli/server.rst`, `docs/cli/relay.rst`, `share/` completions | `versionadded` markers present. `bash -n`/`zsh -n` pass, and bash completion was exercised. The `-W` docs build is clean. | ✅ (RV3: one inaccurate sentence) |
| Q1 (spread default, join by default) | `core/config.py` default `spread` | The start log reads `distribution: spread`, and relays took part with no server site list. | ✅ |
| Q2 (shared site) | `cluster/topology.py` `_arrange` (relay-wide by instance key) | `dup`, `lower` drive | ✅ (RV2) |
| Zone floor | `plan_sites` base floor | `floor` drive | ✅ |
| Site naming | `relay_site` (unchanged from base) | By reading. A SIGTERM bounce reclaimed the site in 3.4 s; a crash bounce did not (RV2). | ✅ (RV2) |
| Advertise on connect | every beat carries it | `vanish` | ✅ |
| Plan-gate zone exposure | `relay/site.py::zone_launch` with `expose_zone=True` | `argv_probe`, `ompi_probe` (`HYPERSHELL_ZONE` not in the Open MPI forward list) | ✅ |

Also checked and clean:
- **`hs server --autoscaling` refusals.** FILE, `--restart`, `--no-db`, `--no-confirm` and an
  unknown mode are refused; tests pass. A `:memory:` database exits 3. An unknown distribution
  exits 3, naming its source.
- **Thread safety, by reading.** `SiteRegistry` is lock-guarded with frozen records. The published
  topology sits under the dispatcher's lock. The heart monitor alone touches `ignored_sites`.

Unmapped changes (possible scope creep): none. `tests/test_topology_judge.py` tests a `spec/`
drive script, as `tests/test_relay_judge.py` does on the base.

### Findings

#### RV1 [MEDIUM/CONFIRMED] A relay re-applies a stale topology version and relaunches clients the server already counted
- **Where:** `src/hypershell/relay/site.py:288-309` (`SiteScaler.reconcile`, `uncounted`)
- **Failure scenario:** the server's `-P` period exceeds three of the relay's own autoscale
  periods. Clients launched for a version idle-exit before the server's next check. `uncounted()`
  sees the version unchanged, `launched` below `expected`, `launched <= seen` and
  `RETRY_PERIODS * self.period` elapsed. It takes the server's silence for "left before the server
  counted them" and launches the full count again, every three relay periods, until the server
  checks again. The server had counted those clients on their beats. Example: an off-cluster
  server at `-P 300` with relays on the default 60 s. On a scheduler, every relaunch is a new job
  or step.
- **Evidence:** the `stale` scenario, with server `--autoscaling=dynamic -P 20`, relay period 1 s,
  `[relay.zone.cpu] init = 2, max = 2`, idle timeout 2 s and no work. Reproduced by the
  orchestrator in a fresh throwaway site. The server published version `:2` once (target 2, seen
  0). The relay logged `Topology …:2 (zone: cpu): 2 clients left before the server counted them -
  applying again` five times and made **12 launches**. The server's next check, version `:3`, read
  `target: 2, launched: 2`. Control: the same scenario at `-P 2` makes 2 launches and no
  re-apply.
- **Touches:** R2/R5 (the relay launches what the server asks; it stops replacing exiting clients),
  and the `docs/cli/relay.rst` promise below (RV3).

#### RV2 [MEDIUM/CONFIRMED] A crashed relay's last-advertised clients hold its site's target at zero for its restarted successor until eviction
- **Where:** `src/hypershell/cluster/topology.py:184` (`site_views` sums `clients` over every
  identity at the site), `:258` (`entries` subtracts the older identities' clients from the
  holder's target)
- **Failure scenario:** relay R at site S runs 2 clients. R and its clients die (SIGKILL, or the
  node is lost). R is restarted at S as R2. R2 becomes the holder under Q2, but R's record still
  advertises 2 clients until R is evicted, so R2's target is `site target − 2 = 0`. A single-site
  zone stalls for the whole eviction window, 600 s by default (`server.evict`). On the base, a
  restarted relay launches its fleet at once. The site-naming ruling relies on a bounced relay
  "reclaiming its own site"; R2 reclaims the holder role, but with nothing to launch.
- **Evidence:** the `bounce_kill dynamic` scenario, with server evict 40 s and 80 two-second `cpu`
  tasks. Reproduced by the orchestrator in a fresh throwaway site. R2's first launch came 43.7 s
  after the bounce, with 79 tasks remaining throughout. The server logged the two-relay warning,
  then `Evicting client` for R at +41 s, then the topology regrew R2 from 0. Control: in
  `bounce`, R is stopped with SIGTERM, its final beat reports 0 clients, and R2 launches within
  3.4 s.
- **Touches:** R4 (Q2, site naming), R3 (failover capacity). The `site_views` docstring makes the
  counting deliberate for a *live* older relay whose clients are still idling out. The crash case
  is the gap. **Triage:** whether this is accepted as a trade-off is the maintainer's call.

#### RV3 [LOW/CONFIRMED] The branch's new invariant and docs text says the relay never replaces an exit on its own; the code does
- **Where:** `AGENTS.md:436-438`, `.agents/factory/invariants.md:205-207` ("applies each version
  once … never replaces an exit by itself"), `docs/cli/relay.rst:65` ("a *client* that exits is
  launched again only if the server asks for one")
- **Failure scenario:** `SiteScaler.uncounted()` re-applies an unchanged version and replaces
  exited clients without a new request from the server, both by design (failed launches) and as in
  RV1. The checklist that later reviews grade against is false of the code.
- **Evidence:** the five `applying again` lines on one version in the RV1 run. The `SiteScaler`
  docstring itself names the re-apply case.
- **Touches:** invariants §11 text accuracy, R7.

### Human-gate triggers

- None. No CONFIRMED finding touches the high-blast-radius core (`relay/site.py` and
  `cluster/topology.py` are outside it; `server.py`, `core/queue.py` and `core/heartbeat.py`
  changes came out clean) or a security/DB-lifecycle invariant. RV2 carries a triage question for
  the maintainer (above).

### Processes

Every server, relay and site client the reviewer and the orchestrator started was started inside
a harness script, which stopped them by recorded PID (SIGTERM, then SIGKILL to the process group)
and reported none still alive. A process listing after the review showed no HyperShell process from
this review.

## Review cycle 2 — changes-requested (2026-11-13)

- **Reviewed commit:** 7ddd16f3d4b57c5410eeabd9d3f52a08c60a418b  ·  **Base:** `origin/develop` `22684fd`  ·  **Date:** 2026-11-13
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** re-review scoped to the remediation `b8bb537..7ddd16f`, as the foreman's brief set it, rather
  than the default fresh pass over the whole diff. One fresh blind reviewer got verbatim copies of
  `GOAL.md`, the base `invariants.md` and `AGENTS.md`, the rubric, and the Findings section of cycle 1.
  Its primary diff was `git diff b8bb537..7ddd16f -- . ':(exclude)spec/'`, with the whole spec-excluded
  branch diff for context. It was told not to read `PLAN.md`, `TECH.md`, `research/`, `META.md`,
  `REVIEW.md`, `evidence/` or `drive/`, nor the branch's commit bodies, and it built its own harnesses.
  The orchestrator ran the full suite, the PostgreSQL suite and the docs build, and reproduced RV1
  and RV2 with a harness of its own.

### Pre-flight

- **Contract drift.** `7ddd16f` adds one dated Clarification to `GOAL.md`, "Ruled at review cycle 1
  (2026-11-13)". It is the maintainer's RV2 ruling: a superseded relay's clients stop counting
  against its site. Nothing else in the contract moved. The foreman's brief names it, and this
  cycle grades against it.
- **Build state.** `TECH.md` is `status: in_review`, with `review.cycle` 1 before this cycle.
- **Invariants handed to the reviewer.** The base copy (META F6). The remediation edits `AGENTS.md`
  and `invariants.md` §11 again, and those edits were graded as part of the diff.

### Verification run

- **Full suite (orchestrator, sandboxed):** `uv run pytest -q tests` → **1921 passed, 88 skipped**,
  2 warnings (the existing `fork()` deprecation warnings in `tests/test_logging.py`), 16 min 51 s,
  CPython 3.12.13.
- **PostgreSQL suite (orchestrator, sandbox off, local server):**
  `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community uv run pytest tests/test_upgrade_postgres.py
  tests/test_writeback_postgres.py tests/test_timescale.py tests/test_contended_write.py
  tests/test_zone_postgres.py` → **131 passed, 1 skipped, 29 failed**. Every failure is the
  expected missing template: 20 name `hs283` and 9 name `hs290a1`. No other failures.
- **Docs (orchestrator):** `UV_PROJECT_ENVIRONMENT=<tmp> uv run --python 3.12 --group docs
  sphinx-build -W --keep-going -q -E -b html -D extensions=<conf.py's list without sphinx_sitemap>
  docs <tmp>` → **exit 0, no warnings**. The shared `.venv` was left as it was.
- **Targeted tests (reviewer):** `tests/test_relay_topology.py tests/test_topology_plan.py
  tests/test_topology_server.py` → **120 passed**.
- **Orchestrator's RV1 harness:** server `--autoscaling=dynamic -P 20`, evict 60, and one relay with
  autoscale period 1 s and heartrate 1 s. Settings: `[relay.zone.cpu] init = 2, max = 2`,
  `timeout = 2`, no work, 135 s.
  - Results: **2 launches**, 0 `applying again` lines, 6 server checks.
  - The server published `:2` (target 2, seen 0), then `:3` (target 0, seen 0) once a beat had
    counted the two clients. The relay launched 2 at `:2` and 0 at `:3`.
  - Cycle 1 made 12 launches in the same scenario.
- **Orchestrator's RV2 harness:** server `-P 1`, evict 40, relay A with `max = 2`, and 40 cpu tasks
  of 2 s.
  - Once A ran 2 clients, A and its clients were SIGKILLed and A2 was started at once at site A.
  - **A2's first launch came 3 s after the kill.** The server logged the two-relay warning 1 s
    after A2 registered.
  - A was evicted 41 s after the kill, and its orphaned tasks were reverted then. Cycle 1 measured
    43.7 s to A2's first launch.
- **Reviewer's harness runs (18):** `rv1`, re-apply probes (a launcher that exits at once, the
  first launches failing, every launch failing with `min = 2`), `rv2` and `rv2-init0`, `twolive`
  (two live relays at one site), `stall*` (RV4 below), `founding-base`/`founding-branch`, four
  `compat-*` pairings of base and branch relays and servers, and `vanish-work`.

### RV1–RV3 status

| ID | Status | Evidence |
|----|--------|----------|
| RV1 | **closed** | Both harnesses made exactly 2 launches over 6 server checks at `-P 20`, with no re-apply. The re-apply path the fix keeps still works. In the reviewer's run of a launcher that exits at once, at `-P 5`, there was one re-apply about two server checks after the mark. With every launch failing and `min = 2`, the relay made 2 launches per two server checks, which is the server still asking. No over-launch, and no churn beyond what the server asks. |
| RV2 | **closed** | The restarted relay launched 3 s after the kill (orchestrator) and 3.4 s after the restart (reviewer, `-P 2`), long before the old relay's eviction (+41 s and +43 s). `twolive` checked two live relays at one site. The older relay was assigned 0 at once, and its 2 clients kept working and exited idle (status 0) when the work ran out. They were never relaunched or terminated, and no relay logged `Terminating`. The site ran 4 clients against a `max` of 2 for about 27 s, as the cycle-1 ruling accepts. |
| RV3 | **closed for its two claims** | `checks` advances at every check (`cluster/topology.py:392-397`), and the relay never re-applies on its own period alone. The holder is planned on its own clients (`cluster/topology.py:186`, `:255`), and the `server.rst` text matches both runs. The new `relay.rst` sentence on replacing an exited client describes a case that RV4 shows the code missing. |

### Requirement → evidence matrix (as the remediation touches it)

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | The floor holds in steady state (`rv1`, `twolive`). It is not held after RV4's exit: `stall-min2` sat at 2 clients against `min = init = max = 3` for 16 checks. | ❌ (RV4) |
| R2 | The server publishes without launching. The versioned topology carries `checks`, under the same version when the plan is unchanged. In RV4's case the relay does not reconcile to its entry. | ❌ (RV4) |
| R3 | `rv2`: the old relay's eviction logs `Relay site left`, its orphaned tasks are reverted, and the holder regrows. | ✅ |
| R4 / Q2 / 2026-11-13 ruling | The warning names both relays. The restarted relay is planned at once, and the older one is assigned 0. | ✅ |
| R5 | Nothing terminated in any run. Clients retire by idle exit, and nothing is replaced on the relay's own period. | ✅ |
| R6 | A branch relay against a base `hs server --forever` logged `publishes no topology (an earlier release)` and ran its fixed fleet: 20/20 tasks, 13 launches, against 12 for a base relay and base server. With `min 1 / init 2 / max 2` it runs the fixed fallback fleet. | ✅ |
| R7 | The remediation changes prose only. The `-W` build is clean. | ✅ (RV4 touches one sentence) |
| Founding case | `hs cluster --forever --autoscaling=dynamic -P 2 -I 1 -Y 3 -T 3` with cpu 40×1 s and gpu 15×2 s. Base and branch launched identically: default@0.0, cpu@2.1, gpu@2.1, cpu@4.1, cpu@6.1, gpu@6.1, gpu@8.2, gpu@16.3. | ✅ |
| Wire compatibility | A base relay against the branch server logs `advertises no zone or bounds: it takes no part` and keeps its fixed fleet: 20/20 tasks, 0 errors. A branch relay against a base server is the R6 row. `read_topology` requires `checks` (`core/topology.py:279-280`) under the same protocol number, but no released server publishes a topology, so no released pair is affected. | ✅ |

Unmapped changes (possible scope creep): none. The `server_and_relay` test helper serves the new
RV1 integration test.

### Findings

#### RV4 [MEDIUM/CONFIRMED] A relay stays short of its entry for good when a counted client exits before the relay reads the next version
- **Where:** `src/hypershell/relay/site.py:320` (`count = target - max(seen, launched)`) and `:337`
  (`uncounted` resets when `launched >= self.expected`). The same guard was in the cycle-1 code
  (`0ddebf9`, `site.py:308`), and the remediation kept it.
- **Failure scenario:**
  1. The server plans `(target T, seen S)`. Before the relay reads that version, one of the clients
     the server counted exits, so the relay runs `L < S`.
  2. The relay launches `T − S` and reaches `L + T − S`, which is also `expected`.
  3. When that equals `S` (one exit during a +1 dynamic growth step, or two exits around one check
     at a floor), the server's next plan is the same entry, so no new version comes.
  4. `uncounted` never fires, because `launched == expected`, although `count` is 1 or more.

  The zone stays below the server's target until the plan changes for another reason, including
  below its advertised `min`. The new `docs/cli/relay.rst:64-68` sentence describes the relaunch
  that this case needs (the plan unchanged and two more checks since a beat reported the exit),
  and it does not happen.
- **Evidence (reviewer, re-read by the orchestrator from the run logs):**
  - `stall3`, dynamic: server `-P 4`, `max = 3`, 300 two-second tasks. One client was SIGKILLed as
    the server published `:4`. The relay logged `:4 (zone: cpu, target: 3, seen: 2, launched: 1) -
    launching 1`. For the next 77 s (about 19 checks) the server logged `clients: 2` with pressure
    about 100 and published nothing new (4 versions in all). Control without the kill: 1→2→3 by
    17 s.
  - `stall-min2`, fixed: `min = init = max = 3`, 30 tasks of `sleep 600`. Two clients were killed
    around one check. The relay logged `:4 (target: 3, seen: 2, launched: 1) - launching 1`, and
    the server then logged `clients: 2` for 16 checks with no new version and no `applying again`.
  - The precondition also appeared 3 times in the `rv2` run, where the target fell at the next
    check and no stall followed.
- **Touches:** R2 (the relay reconciles to its entry) and R1 (a zone's `min` is held). It also
  touches the new `relay.rst` sentence (R7) and the §11 text in `AGENTS.md:440-443` and
  `invariants.md:207-210`. The §11 text says "only when", so it is not strictly false, but it
  should describe the fixed behaviour.
- **Severity:** MEDIUM. The trigger is an exit in roughly one relay heartbeat after a server check,
  but once the stall happens it lasts.

### Human-gate triggers

- None. RV4 is in `relay/site.py`, outside the high-blast-radius core, and touches no security or
  DB-lifecycle invariant.

### Processes

- The reviewer ran 18 harnesses, about 154 PIDs in all, and the orchestrator ran 2. Each started
  and stopped its server, relays and site clients inside one invocation, by recorded PID (SIGTERM,
  then SIGKILL). Each reported no survivors.
- A process listing afterwards showed no HyperShell server, relay or site client from this review.
