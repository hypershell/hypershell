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
