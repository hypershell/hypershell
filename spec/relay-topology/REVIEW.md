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

## Review cycle 3 — changes-requested (2026-11-13)

- **Reviewed commit:** b98e994e608ab354c8b7472c98f9fa43af64b83d  ·  **Base:** `develop` `22684fd`  ·  **Date:** 2026-11-13
- **Verdict:** changes-requested
- **Cycle:** 3 of ≤3 — mirrors `review.cycle` in `TECH.md`. The bounded loop is spent: this goes to the
  human (see *Escalation*).
- **Mode:** re-review scoped by the foreman's brief to the remediation `37e0a60..b98e994` (chiefly
  `relay/site.py` `SiteScaler.reconcile`/`planned_on`, `tests/test_relay_topology.py`, the drive's two
  stall modes, `docs/cli/relay.rst`, `AGENTS.md`, `invariants.md`), RV4 closed or reopened on an
  independent reproduction, RV1–RV3 and the lowered-target, floor, failing-launcher and new-version
  behaviour re-verified, and the zone-less founding case and wire compatibility checked for
  regression. One agent did the correctness pass and wrote this record. It read `GOAL.md` with its
  Clarifications, the rubric, `invariants.md`, the Findings of cycles 1–2, the remediation diff and
  the remediation's commit message, and only the frontmatter of `TECH.md`. It did not read
  `PLAN.md`, the body of `TECH.md`, `research/`, `META.md` or `evidence/`. Every scenario ran on a
  harness of its own (not the drive), and nothing it wrote is committed.

### Pre-flight

- **Contract drift.** `git log develop..HEAD -- spec/relay-topology/GOAL.md` is unchanged since
  cycle 2. The last entry is `7ddd16f` (the 2026-11-13 RV2 ruling), and the remediation does not
  touch `GOAL.md`.
- **Build state.** `TECH.md` is `status: in_review`, with `review.cycle` 2 before this cycle. The
  tree was clean at `b98e994`, and it was clean again before this record was written.
- **Source change in the remediation.** Of `src/`, only `relay/site.py` changes. `cluster/`,
  `server.py`, `core/` and `relay/upstream.py` are as cycle 2 reviewed them.

### Verification run

- **Full suite (sandboxed):** `uv run pytest -q tests -p no:randomly` → **1932 passed, 88 skipped**,
  2 warnings (the existing `fork()` deprecation warnings in `tests/test_logging.py`), 16 min 57 s,
  CPython 3.12.13.
- **Targeted:** `uv run pytest -q tests/test_relay_topology.py tests/test_topology_plan.py
  tests/test_topology_server.py` → **131 passed**.
- **The new tests are red on the cycle-2 code.** The branch's `tests/test_relay_topology.py` was run
  against an export of `7ddd16f`'s `src/` on `PYTHONPATH`, keyed to the new reconciler tests
  (`-k 'counted_exit or converges or lowered_target or failing_launcher'`). Result: **6 failed,
  6 passed**. The counted-exit test, two event-sequence rows and all three random-sequence plans
  fail there, which shows they encode RV4.
- **Docs:** `UV_PROJECT_ENVIRONMENT=<tmp> uv run --python 3.12 --group docs sphinx-build -W
  --keep-going -q -E -b html -D extensions=<conf.py's list without sphinx_sitemap> docs <tmp>` →
  **exit 0, no warnings**.
- **PostgreSQL suite:** not run. It is optional, and the remediation touches no database code.
- **Drive scripts:** `sh -n topology_drive.sh` and a parse of `topology_trigger.py` are clean. They
  were read, not run, because RV4 is graded on the harness below.
- **Own harness** (Python, scratch, not committed). It runs one upstream `hs server --autoscaling`
  (or `hs server --forever`, or `hs cluster`) and relays as separate processes on loopback, each
  run in a fresh throwaway site under `$TMPDIR`. A launcher shim records every site-client launch,
  and liveness is read through libproc, so a zombie never counts as running. The cycle-2 head
  (`7ddd16f`) and the base (`22684fd`) ran from `git archive` exports of `src/` on `PYTHONPATH`,
  with the same venv. Relays used heartrate 1 s and an autoscale period of 1 s. The scenarios are
  below, with their numbers.

### RV1–RV4 status

| ID | Status | Evidence (own harness) |
|----|--------|------------------------|
| RV4 | **closed** | Independent reproduction by freezing: the relay is SIGSTOPped across the server's check, so the server publishes (target 3, seen 2) while the relay cannot read it. A counted client is SIGKILLed, and the relay gets SIGCONT right after that check. Its first beat then reports 1, it reads (3, 2) and launches 1, and its beats report 2 before the next check, so the server plans (3, 2) again, unchanged. *Fixed, `min = init = max = 3`, `-P 8`, 30 × `sleep 600`.* Branch: `:4 (target: 3, seen: 2, launched: 1) - launching 1`, then `:4 (target: 3, launched: 2): the server planned again on 2 running - applying again, launching 1`, back to 3 clients **17.3 s** after the resume. Cycle 2 (`7ddd16f`), same race line: it stayed at 2 under the unchanged `:4` for **61 s** (about 7 checks), below `min`, until an unrelated idle exit made the server see 1 and publish `:5`. *Dynamic, `max = 3`, `-P 8`, 300 × `sleep 2`.* Branch: back to 3 in **17.2 s**. Cycle 2: still at 2 when the run ended about 100 s later, with no version after `:4` (16 checks). |
| RV1 | **closed** | `rv1-slow`: server `-P 30`, `init = 2, max = 2`, idle timeout 2 s, no work, 145 s. **2 launches**, 0 re-applies. The server published (2, 0), then (0, 0) once it had seen the clients leave. `rv1-work`: `-P 20`, `max = 3`, 24 × `sleep 1`. 2 launches, none after the work drained, 0 failures. |
| RV2 | **closed** | `rv2`: server `-P 1`, evict 40 s, relay A `max = 2`, 40 × `sleep 2`. A and its 2 clients were SIGKILLed and A2 was started at site A at once. The two-relay warning came at +1.4 s, **A2's first launch at +2.5 s**, and A's eviction at +40.7 s. 40/40 done, 0 failed. |
| RV3 | **closed** | `relay.rst` (59–74), `AGENTS.md` §11 and `invariants.md` §11 now state the one rule: launch `target - max(basis, live)` at a new basis, either a new version's `seen` or `live` after two checks past a read that followed a beat. That matches `site.py:323-353` and every run above. No launch exceeded the target in hand, and none came on the relay's period alone. |

Also re-verified on the branch:
- **Lowered target, no termination (R5).** `lower`: relay A (`max = 3`) ran 3 busy clients, then A2
  joined site A with `max = 1`. A made 0 launches afterwards, and A2 peaked at 1. Nothing was
  terminated: no `Terminat` lines, no non-zero client exits, and A's 3 clients ended by idle exit.
  The same run surfaced RV5.
- **Floor while gated (R1).** `floor-gated`: `[relay.zone.gpu] min = 1, max = 2`, with gpu work only
  in group 1, behind cpu's group 0. While group 0 ran, gpu held exactly 1 (every gpu entry had
  target 1, peak 1). One idle exit at +45.5 s was replaced 1.6 s later by a new version. gpu grew to 2
  once group 0 completed. 30/30 done.
  - `rv1-slow-floor` (`min = init = max = 2`, `-P 30`, no work): after the clients idled out, the
    floor was relaunched at the second server check, under the unchanged version.
- **A launcher that always fails.** `failing-fixed` (`min = init = max = 2`, `-P 3`, 90 s): 24
  attempts in 12 rounds of 2, one round per 2–3 server checks (30 checks). `failing-dynamic`
  (`max = 3`, 20 tasks): 14 single attempts over 31 checks. The relay stayed up, and nothing ran
  away.
- **New versions applied as before.** In every run, a version not yet applied was launched within
  about 1 s of its read (for example `:2 → :3 → :4` in `rv4-dynamic`).
- **Server restart (`checks` reset).** `restart`: relay at 1 of 2 (one client killed 1.5 s earlier).
  The server was SIGTERMed and a new one started on the same port and database. The relay read the
  new server's `:0` (no entry, launching 0), then `:1 (target: 2, seen: 1, launched: 1) - launching 1`.
  It was back to 2 in 2.6 s, with peak 2 and no re-apply.

### Requirement → evidence matrix (as the remediation touches it)

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | `rv4-fixed` restores `min = 3` after the counted exits. `floor-gated` holds gpu's `min` while gated and grows above it after. | ✅ |
| R2 | The relay reconciles to its entry under an unchanged version (`rv4-*`), applies each new version as read, and never exceeds the target in hand. | ✅ |
| R3 | `rv2`: the old relay is evicted at +40.7 s and its orphans are reverted, while the holder already runs. | ✅ |
| R4 / Q2 / 2026-11-13 ruling | The warning names both relays, and the newest instance holds the site at once (`rv2`, `lower`). The older instance, assigned zero, strands the tasks it holds once its last client leaves. | ❌ (RV5) |
| R5 | No client was terminated in any run, and the relays stay connected at zero clients. | ✅ (RV5: the older relay never regrows) |
| R6 | Branch relay against a base `hs server --forever`: `publishes no topology (an earlier release)`, 20/20 done, 5 launches. Against a branch `hs server --forever`: `(not autoscaling with relay sites)`, 20/20 done, 5 launches. | ✅ |
| R7 | Prose matches the code, and the `-W` docs build is clean. `relay.rst`'s section carries `versionadded:: 2.9.0`. | ✅ |
| Founding (zone-less) case | `hs cluster FILE --autoscaling=dynamic -P 2 -I 1 -Y 3 -T 3 --launcher=<shim>`, 40 × `sleep 1`, no zone. Base and branch were identical: launches at +0.0, +2.0 and +4.0 s, 40/40 done, 0 failed, 3 idle exits. Both logged `--autoscaling implies --forever` and kept serving until stopped. | ✅ |
| Wire compatibility | Base relay (`size = 2`) against the branch `--autoscaling` server: the server logged `advertises no zone or bounds: it takes no part`, the relay kept its fixed fleet, 20/20 done. Branch relay against a base server: the R6 row. | ✅ |
| Default-zone relay | A relay naming no zone (`min = 0, max = 2` in `[relay]`) against `--autoscaling`: it follows the entries for zone `default`, with `HYPERSHELL_ZONE=default`. 20/20 done. | ✅ |

Unmapped changes (possible scope creep): none.

### Findings

#### RV5 [MEDIUM/CONFIRMED] A superseded relay strands the tasks it holds once its last client leaves
- **Where:**
  - `src/hypershell/cluster/topology.py:256` (`entries`): every identity of a superseded instance
    gets `Entry(zone, 0, clients)`, whatever it holds.
  - `src/hypershell/relay/upstream.py:318-327` (`UpstreamPuller.bound`): the pull bound counts the
    zone's connected site clients and ignores the zone's assignment.
  - `src/hypershell/relay/site.py:323-342` (`SiteScaler.reconcile`): a target of 0 launches
    nothing.
  - Upstream, those tasks stay assigned to the older relay's live identity, so orphan recovery never
    reverts them. The puller is relay-cycle code. The zero assignment and the zero-client fleet
    under a topology are this branch's.
- **Failure scenario:**
  1. Two live relays advertise one site (Q2, and the 2026-11-13 ruling's "the older relay was in
     fact still alive"). The older one is assigned 0 and its clients wind down.
  2. A client whose scheduler has reached its idle timeout stays connected while it finishes what
     it holds. The superseded relay keeps pulling work upstream for it.
  3. When that last client leaves, the relay reverts the staged rows locally and holds them with no
     client.
  4. Its entry stays at 0, so it never launches. Upstream counts those tasks as remaining, and the
     holder is planned on its own clients, so the holder's fleet relaunches against work it cannot
     reach.

  The tasks never complete while the older relay lives. By the group-gating rule a later group
  would wait on them; this is by reading, not run.
- **Evidence (own harness, 3 of 3 runs on the branch, 1 of 1 on `7ddd16f`):**
  - Setup: server `--autoscaling=dynamic -P 2`. Relay A (`max = 3`, idle timeout 3 s) ran 3
    clients on 12 × `sleep 12`. Then A2 joined site A with `max = 1`.
  - In `strand-r1`, `strand-r2` and `strand-r1-cycle2`, 150 s after A2 joined, **2 tasks were still
    remaining, both assigned upstream to A's cpu identity**. A's log read `Reverted 1 staged tasks
    (zone has no client)` and `Task group 0 waiting on zone …: 2 tasks, no client serving it`. A
    made 0 launches, and its entry stayed `target: 0`.
  - A2 made **13 launches** in that time. Each client idled out after 3 s, so the alive count went
    0/1 about every 4 s.
  - The `lower` run shows the mechanism in A's log:
    - A's last client logged `Timeout reached` at 12:02:52 and ran its prefetched tasks until
      12:03:14.
    - A logged `Received 1 tasks` upstream at 12:03:02 and again at 12:03:14.
    - The client then left, and the two rows were stranded.
  - After A was stopped with SIGTERM, the 2 tasks were still not done 90 s later. Upstream eviction
    (120 s in the harness) had not yet come.
- **Touches:** R4 (the older relay "assigned zero (its clients retiring through idle exit)") and R5
  (scale-to-zero "ready to regrow": a superseded relay never regrows). The 2026-11-13 ruling accepts
  brief over-provisioning by a live older relay, not work that never runs.
- **Origin:** this predates the remediation. It reproduces identically on `7ddd16f`, and the cycle-1
  code assigns the older relay zero the same way. It was found while re-verifying the
  lowered-target rule the brief named, not in the one-rule reconciler, which behaves as specified
  here.
- **Not reproduced:** whether a non-duplicated site can strand the same way. That would need, for
  example, `fill` keeping a zone's demand at a preferred site while another site holds pulled
  tasks with no client.

### Human-gate triggers

- None by file or invariant. RV5 sits in `cluster/topology.py`, `relay/upstream.py` and
  `relay/site.py`, outside the high-blast-radius core, and touches no security or DB-lifecycle
  invariant.

### Escalation

- This is review cycle 3 of 3. The remediation closes RV4 and keeps RV1–RV3 closed: the one-rule
  reconciler is sound on every path exercised.
- RV5 is a separate, pre-existing defect in the duplicated-site path. The bounded loop is spent, so
  the maintainer decides:
  - authorise a fourth build↔review cycle for RV5;
  - or rule on it, for example by recording the stranding as a known limitation of a live
    superseded relay. That route is a GOAL change, since the rubric's deferral exception does not
    apply: the behaviour is not on `develop`.

### Processes

- Every server, relay, `hs cluster` and site client this review started was started by the harness.
  It stopped them by recorded PID with SIGTERM (SIGKILL only for one still running 20 s later), then
  checked each launched client's PID. Every run, including two early runs that aborted on a harness
  error before their scenario, reported none alive. The SIGKILLs above were the scenarios' own
  deliberate kills, of clients and of relay A.
- A process listing afterwards, run with the sandbox off because the sandbox hides other commands'
  processes, showed no process from this review. The only HyperShell processes listed belonged to
  another session's `relaydiag` stress run and were left alone.
