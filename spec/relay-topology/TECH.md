---
slug: relay-topology
title: 'Relay site-topology autoscaling: fill and spread across sites'
kind: feature
appetite: big
status: done
branch: feature/relay-topology
base: develop
current_phase: done
last_updated: '2026-11-13'
phases:
- id: P1
  name: 'Topology core: bounds, advertisement wire, registry, and the pure planner'
  status: done
  satisfies:
  - R1
  - R3
  - R4
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_topology_plan.py tests/test_relay_roster.py
    tests/test_zone_autoscale.py tests/test_autoscale.py
- id: P2
  name: 'Server: hs server --autoscaling publishes a versioned topology from relay
    advertisements'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_topology_server.py tests/test_topology_plan.py
    tests/test_server.py tests/test_server_fsm.py tests/test_dispatch.py tests/test_dispatch_version.py
    tests/test_relay_roster.py tests/test_autoscale.py tests/test_zone_autoscale.py
    tests/test_completions.py tests/test_main.py && .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" founding
- id: P3
  name: 'Relay: per-zone bounds, advertisement and fetch, SiteScaler reconcile, zone
    exposure, fallback'
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R5
  - R6
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_topology.py tests/test_topology_plan.py
    tests/test_topology_server.py tests/test_relay_connector.py tests/test_relay_db.py
    tests/test_relay_fsm.py tests/test_relay_launch.py tests/test_relay_link.py tests/test_relay_outage.py
    tests/test_relay_return.py tests/test_relay_roster.py tests/test_relay_workload.py
    tests/test_completions.py tests/test_main.py && sh spec/relay/drive/relay_smoke.sh
- id: P4
  name: Laptop drive (two relays, three zones), evidence, full regression and docs
    gate
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: for mode in spread fill floor idle kill lower stale bounce stall3 stall-min2;
    do .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-topology/drive/topology_drive.sh"
    $mode || exit 1; done && for mode in founding fixed dynamic restart; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" $mode || exit 1; done && uv
    run pytest && uv run --python 3.12 --group docs sphinx-build -W --keep-going -q
    -E -b html docs docs/_build/html
- id: P5
  name: 'Multi-node test-cluster run (split gate): run A builds the batch script and
    judge, run B judges the foreman''s logs'
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_topology_judge.py && uv run python spec/relay-topology/drive/topology_judge.py
    spec/relay-topology/evidence/cluster --require spread-kill,fill
review:
  last_reviewed_commit: b98e994e608ab354c8b7472c98f9fa43af64b83d
  verdict: changes-requested
  blocked_reason: 'cleared by maintainer ruling (2026-11-13): review cycle 3 RV5 accepted
    as a known limitation of the duplicated-site case, fix deferred to a follow-up
    seed; ships at cycle 3 with no further review pass (GOAL Clarifications; REVIEW
    cycle 3, Maintainer ruling)'
  cycle: 3
---
# TECH.md — Relay site-topology autoscaling: fill and spread across sites

This file is the **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth; read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay-topology/TECH.md`. The per-phase
checklists below are the work. `hs-build` runs the next actionable phase, runs its `verify:` command,
updates state with `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic code+state
commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). The R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). Section numbers below (§2.x) are PLAN's.
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and briefs 01–03.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`. `hs-publish` stamps `done`
  after confirmation, just before landing; it is the terminal state of the retained record.
- `appetite`: `small | big`. It caps the phase count and the build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: the GOAL R-IDs this phase delivers. It is the traceability anchor for `hs-review`.
- `depends_on`: phase ids that must be `done` first. A phase is actionable only when all are.
- `parallel`: `true` only for genuinely independent, non-coupled work (leaf `task.py` apps, docs,
  tests). The coupled core (`data/model.py`, `server.py`, `client.py`, `core/*`) is always `false`.
- `hammerable`: `false` marks a correctness or security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand: escalate to the human.
- `attempts`: a durable count of failed verifies (absent = 0). `set_phase.py --phase P<n>
  --record-attempt` bumps it on every red verify gate, and `next_phase.py` warns at ≥3. The circuit
  breaker runs on this file, not on session memory.
- `verify`: the exact command that proves the phase. Prefer driving the real CLI over tests alone,
  and wrap CLI drives in `.agents/factory/bin/temp_site.sh` so they hit a throwaway site, never the
  developer's real database.
- `review.cycle`: completed review passes, incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); it is the constitution. Consult
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) for the curated footgun
  checklist. This change touches §4, §5, §6, §9, §10, §11 and §12.
- One phase per `hs-build` invocation by default, and one atomic commit holding **both** the code and
  the `TECH.md` state change. Branch commit subjects follow the house style
  `[feature] Build relay-topology P<n>: …`; they are squashed at `hs-publish`.
- **No `Co-Authored-By` trailer.** This is the repo convention and overrides the AGENTS.md default.
  Commits are signed.
- A CLI or config change updates its `docs/_include/*.rst` snippets, `docs/_include/config_param_ref.rst`
  and the `share/` bash and zsh completions **in the same commit**. The man pages under
  `share/man/man1/` are generated from `docs/manual.rst` (which includes those snippets) and are
  rebuilt by `hs-release` at each release, as every earlier cycle left them; a build checks the
  `-b man` output builds, and never hand-edits the generated file (amended in P2). Mark it
  `.. versionadded:: 2.9.0` (or `versionchanged`). The user-facing note goes in the PR's squash body,
  which `hs-publish` drafts.
- **Founding-case rule (every phase).** The guarding tests named in PLAN §2.7 stay green with their
  assertions unchanged. The only allowed edits are the declared amendments:
  - `test_dispatch_version.py::test_dispatcher_remote_surface_is_pinned` (P2, deviation 1);
  - the exact beat key sets in `test_relay_roster.py::test_beat_with_site_and_roster_round_trips` and
    `::test_relay_beat_carries_site_and_roster` (P3, deviation 2);
  - the two env asserts at `test_relay_launch.py:334` and `:572` (P3, deviation 3, Q1).

  No phase edits `src/hypershell/cluster/remote.py`, `cluster/__init__.py` or `cluster/launch.py`.
- **Generic names; this repo is public.** Docs, tests, drives, records and the batch script name no
  real cluster, account, partition, QOS, host, user or path. Example sites are `A`/`B` (or `east`/`west`
  in docs), and example zones are `cpu`, `highmem` and `gpu`.
- **No cluster tools, no submissions.** No builder calls a cluster tool or submits a job. P5's drive
  is run by the foreman, inside an allocation the foreman holds.
- **Plan gate:** approved by the foreman on 2026-11-12, all five deviation rows included. Q1 is ruled:
  relay launches always expose their zone (GOAL Clarifications, "Ruled at the plan gate"). The
  `scheduler_group` equivalence test (deviation 4) runs in P1's verify. `hs-build` may start P1.

## Interfaces (built in P1–P3)

| Module | Name | Contract |
|--------|------|----------|
| `core/topology.py` | `Bounds(min, max, init)` | frozen; `to_dict`/`from_dict` (`{"min","max","init"}`), ints, `0 ≤ min ≤ init ≤ max`, `max ≥ 1` |
| `core/topology.py` | `Entry(zone, target, seen)`, `Topology(version, entries, checks=0)` | `version` = `"<server instance>:<n>"`; `entries` by relay identity; `checks` = the publisher's finished checks, advanced at every check, under the same version when the plan did not change (review cycle 1, RV1) |
| `core/topology.py` | `SiteRegistry` | lock-guarded; `record(identity, *, site, zone, bounds, clients, host, instance=None)` overwrites per identity, stamps `registered` (monotonic) on first sight, `rank` per site name on first sight (kept), `init_held` until `clients ≥ init`; `drop(identity)`; `snapshot() -> List[SiteRecord]`; `SiteRecord.instance` is the advertised relay instance key (None when absent) |
| `core/topology.py` | `request_text(site, identities)`, `read_request(text) -> (site, identities)`, `reply_text(topology, identities)`, `read_topology(text) -> Optional[TopologyReply]` | JSON, `TOPOLOGY_PROTOCOL = 1` (its own number, apart from the work-request protocol); `{"protocol","version":null}` when unpublished; the reply names only identities that have an entry, by zone name; `TopologyReply(version, zones, checks=0).assignment(zone)` gives `Entry(zone, 0, 0)` for a zone with no entry (target 0); a reply with a version carries `checks` (a whole number ≥ 0, required); `read_request`/`read_topology` raise `ValueError` on anything malformed |
| `core/heartbeat.py` | `Heartbeat.zone/bounds/clients/instance` | optional, packed only when set, read with `.get`; a malformed value (zone or instance not text, invalid bounds, a count that is not a whole number ≥ 0) decodes as None, so the identity is a non-participant rather than a crash in the heart monitor (an instance that is not text only drops the relay-wide rule) |
| `core/dispatch.py` | `Dispatcher.topology(text) -> str`, `Dispatcher.publish(topology)` | under the dispatcher lock; `DISPATCH_EXPOSED = ('request', 'takes_submissions', 'topology')` |
| `cluster/demand.py` | `scheduler_group(reported, *, attempts, eager) -> int` | additive; equals `AutoScaler.current_group` (pinned by a test) |
| `cluster/topology.py` | `Distribution`, `parse_distribution(value)`, `parse_sites(value)` | `spread`/`fill`; list or comma string |
| `cluster/topology.py` | `SiteZone`, `SiteView`, `Duplicate(site, holder, older, relay=False)`, `site_views(records) -> (views, duplicates)`, `plan_sites(loads, knobs, sites, *, distribution, order)`, `entries(targets, records)` | pure (PLAN §2.4); duplicates are held **relay-wide** (amended in P3, 2026-11-13): per site, the instance whose first identity registered last holds every zone it serves, every identity of an older instance gets `Entry(zone, 0, clients)` (a zone only it serves leaves the site's view), one `Duplicate(relay=True)` per older instance names the first identity of each; records with no instance key keep P1's per-zone rule among themselves and the holding instance's identity of that zone. Since review cycle 1 (RV2) a site-zone's `running` is the holder's own clients and the holder's entry is the site's whole target: superseded identities' clients do not count against the site |
| `cluster/topology.py` | `TopologyPublisher`, `TopologyPublisherThread`, `AutoScalingServer`, `serve_with_sites(**options)` | FSM START→CHECK (first check at once)→PUBLISH→WAIT→CHECK, FINAL/HALT; publishes only on change; never launches; `AutoScalingServer` publishes an empty `<instance>:0` topology at construction, so a relay never reads null from it (P3); the duplicate warning is once per pair of relays (relay-wide) or of identities (per zone); `AutoScalingServer(address, auth, bundlesize, poll, max_retries, eager, redirect_failures, evict_after, tls, policy, factor, period, distribution=None, sites=None)` reads `[autoscale] distribution`/`sites` when not given (`ConfigurationError` naming the source); `ServerApp.run` calls `serve_with_sites` lazily |
| `core/topology.py` | `error_text(message)` | the reply to an unreadable topology request (`{"protocol","error":"request","message"}`); `read_topology` raises `ValueError` on it |
| `server.py` | `HeartMonitor(…, sites)`, `record_site(hb)`, `drop_site(uuid)`; `ServerThread(…, sites)` | a site beat lacking zone, bounds or clients is logged once at INFO and not recorded |
| `relay/__init__.py` | `zone_bounds(settings, relay_wide) -> (advertised: Bounds, fallback: Bounds)`, `BOUND_OPTIONS`; `RelayThread(…, min_size, max_size, init_size)` (relay-wide bounds, None = unset), `.view`, `.advertised`, `.zone_launched(name)`; `RelayApp.relay_bounds`, `.check_bounds()` | PLAN §2.2 table, with the fallback `min` and `init` held within `max`; ValueError → `ArgumentError` naming the zone |
| `relay/site.py` | `TopologyView(reply=None)` (`.reply`, `.beat_at`, `.set(reply, beat_at=None)`, `.read()`), `SiteScaler(AutoScaler)` (`RECHECKS = 2`), `site_scaler(machine, launch, view)`, `launched_now(machine)`; `site_cluster`/`zone_fleet(…, init_size, min_size, max_size, view)` | `view.assignment(zone) -> Optional[(version, Entry)]`; each read is stamped with the build time of the beat before it; `SiteScaler` launches `max(0, target − max(basis, live))` each time it learns a new basis (a new version's `seen`; or, while `live < target` under an unchanged version, `live` once the server has finished `RECHECKS` checks past a read that followed a beat reporting it; review cycle 2, RV4), else the parent's fixed fleet; it overrides `init`, `check_zones` and `wait` (a version not yet applied is checked at once, not at the period's end); `launched_now` counts launched processes still running, read by the heartbeat |
| `relay/upstream.py` | `UpstreamHeartbeat(…, zones, bounds, launched, instance, view)`, `.fetch(built=None)` | every beat of an identity in `zones` carries its zone, bounds, `launched(zone)` and `instance`; each running beat is followed by `fetch()`: a missing method or `version: null` clears the view (INFO once per change), `LinkDown` or any other failure keeps it |

---

## Phase P1 — Topology core: bounds, advertisement wire, registry, and the pure planner
**Satisfies:** R1, R3, R4, R5 (their pure halves) · **Depends on:** —
**Goal:** the data shapes, the wire fields and the planner exist and are table-tested on their own.
Nothing is wired, and no behaviour changes.

- [x] `core/topology.py`: `Bounds`, `Entry`, `Topology`, `SiteRecord`, `SiteRegistry`, and the topology
      request/reply text helpers (Interfaces table). No import from `cluster/` and no DB.
- [x] `core/heartbeat.py`: optional `zone`, `bounds` (a `Bounds`, packed as its dict) and `clients`.
      Pack each only when set; `unpack_or_none` reads them with `.get`. Docstring: an older server
      ignores them, and a beat without them is valid.
- [x] `cluster/topology.py`: `Distribution`, `parse_distribution`, `parse_sites`, `SiteZone`,
      `SiteView`, `Duplicate`, `site_views`, `plan_sites` and `entries`, exactly as PLAN §2.4.
      `plan_sites` calls `zone_demand` per zone with
      `dataclasses.replace(knobs, init_size=0, min_size=0, max_size=Σmax)`; `demand.py`'s three seam
      functions are unchanged.
- [x] `cluster/demand.py`: the additive `scheduler_group(reported, *, attempts, eager)` (PLAN §2.4, D2):
      the reported group, else `Task.starting_group(Task.current_group())`. Deviation 4.
- [x] `tests/test_topology_plan.py` (`@mark.unit`). Table tests over `plan_sites` (parametrized rows:
      loads, knobs, sites, distribution, order → targets), plus `site_views` and `entries`:
  - spread across peers, with ties broken by order;
  - fill: listed sites in order, then unlisted ones by rank;
  - a zone only goes to sites serving it;
  - a gated zone (no load) keeps its `min` floor, and `min = 0` gets 0;
  - `init` applies while `starting`, then lapses;
  - `max` below running gives target = `max` (< running);
  - the cap is Σmax under dynamic;
  - fixed gives one client when none runs and the zone has work, else none;
  - a floor lift counts toward growth;
  - a site that is gone regrows the survivors under dynamic;
  - with no sites the result is `{}`;
  - a duplicate site: the newest holds, the older gets `Entry(zone, 0, …)`, the holder's target less
    the older's running, and one `Duplicate` naming both.
- [x] Wire tests in the same file:
  - `Heartbeat` round-trips with `zone`/`bounds`/`clients`;
  - a plain beat still packs exactly its four keys;
  - a beat without the new keys decodes with them None;
  - the topology reply for null, entries, and an identity with no entry;
  - `SiteRegistry`: overwrite in place, `registered` and `rank` order, `drop`, and `init_held` clearing.
- [x] Equivalence test in the same file (`@mark.integration`, on a real SQLite file, since the start
      rule reads the database; deviation 4, kept in P1's verify):
      `scheduler_group` equals `AutoScaler.current_group` over the `TestSchedulerGroup` cases (a
      reported group; None → the start rule).
- **Verify:** see frontmatter (new tests plus the beat, Z3 and autoscaler suites unchanged).
- **Touches:** `src/hypershell/core/topology.py` (new), `src/hypershell/core/heartbeat.py`,
  `src/hypershell/cluster/topology.py` (new), `src/hypershell/cluster/demand.py`,
  `tests/test_topology_plan.py` (new).

## Phase P2 — Server: `hs server --autoscaling` publishes a versioned topology
**Satisfies:** R1, R2, R3, R4, R7 (server half) · **Depends on:** P1
**Goal:** a server started with `hs server --autoscaling` records the relays' advertisements and
plans per site and zone from Z3's loads. It publishes a version only when the plan changes and
serves it through `Dispatcher.topology`, and it launches nothing. Every other server and `hs cluster`
mode is unchanged.

- [x] `core/dispatch.py`:
  - `published: Optional[Topology] = None`;
  - `publish(topology)` under the lock;
  - `topology(text)` replies per PLAN §2.4, with `version: null` when nothing is published.

  `core/queue.py`: add `'topology'` to `DISPATCH_EXPOSED`. Work-request handling is untouched.
- [x] `server.py`:
  - `HeartMonitor(…, sites: Optional[SiteRegistry] = None)`. In `update_client`, a beat with `site` and
    `zone` is recorded every time. The record is dropped on the FINISHED path and in `check_clients`
    on eviction, beside `end_roster`/`forget`.
  - `ServerThread(…, sites=None)` passes it through.
  - `ServerApp`: `-A/--autoscaling [MODE]` (`nargs='?'`, `const=config.autoscale.policy`),
    `-F/--factor` and `-P/--period`. `--autoscaling` implies forever. It is refused with FILE,
    `--restart`, `--no-db` and `--no-confirm` (`ArgumentError`). It keeps every key check. `run()`
    imports `cluster.topology.AutoScalingServer` lazily.
- [x] `cluster/topology.py` (the publisher reads its group through P1's `scheduler_group`):
      `TopologyPublisher`/`TopologyPublisherThread` and `AutoScalingServer` (PLAN §2.1, §2.4):
  - publish only on change, with the version bumped then;
  - INFO lines per site and zone at each version;
  - the duplicate WARNING once per pair;
  - DEBUG `Autoscale check (zone: …)` per zone per check;
  - stop the publisher before the server.
- [x] `core/config.py`: `[autoscale] distribution = 'spread'`, `sites = PARAM_UNSET`. Validate the
      distribution at `AutoScalingServer` construction (`ConfigurationError` naming the source).
- [x] `tests/test_topology_server.py`:
  - `@mark.integration`, on a real `QueueServer` and `HeartMonitor`: advertised beats populate the
    registry; a new identity registers newer; eviction and FINISHED drop the record; an old relay's
    beat (no `zone`) and an ordinary client's are ignored; `dispatcher.topology` returns null before
    publishing, entries after, and 0 for an older duplicate's zone.
  - `@mark.integration`, on a temp DB with tasks in `cpu` (group 0) and `gpu` (group 1): a publisher
    check with two fake sites publishes per the planner. A second check with the same inputs publishes
    nothing new. The `gpu` floor holds while group 0 runs.
  - `@mark.unit`: `parse_distribution`/`parse_sites` (list, comma string, unknown value).
- [x] `tests/test_server.py`: the new refusals and the mode's help text (usage and help still equal
      `ServerApp.interface`). `tests/test_dispatch_version.py`: amend the pinned surface (deviation 1),
      and add `old server → hasattr(dispatcher, 'topology')` false through a proxy built from the old
      tuple.
- [x] Docs and assets, same commit:
  - `docs/_include/server_usage.rst`, `server_help.rst` and `server_desc.rst`, plus `APP_USAGE`/`APP_HELP`;
  - `docs/cli/server.rst`: a section on autoscaling with relay sites, `.. versionadded:: 2.9.0`. It
    covers what the server plans with, that the server's own `size.*`/`launcher` do not apply, `fixed`
    vs `dynamic` meaning, `spread`/`fill`, `sites`, and the environment forms;
  - `config_param_ref.rst`: `[autoscale] .distribution`, `.sites`, and notes on `.size.*`/`.launcher`;
  - `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`: the `hs server` options and
    `autoscale.distribution` values (the man page is regenerated at release; `-b man` builds clean);
  - AGENTS.md cluster section and invariants.md §11: "a server autoscaling with sites launches
    nothing; it publishes", in lockstep.
- **Verify:** see frontmatter (server, dispatch, roster, autoscaler and completion suites; the
  founding drive for `hs cluster`).
- **Touches:** `src/hypershell/core/{dispatch,queue,config}.py`, `src/hypershell/server.py`,
  `src/hypershell/cluster/topology.py`, `tests/test_topology_server.py`, `tests/test_server.py`,
  `tests/test_dispatch_version.py`, `docs/`, `share/`, `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P3 — Relay: bounds, advertisement and fetch, `SiteScaler`, zone exposure, fallback

**Amendments (2026-11-13, at build):**

- **Reopened at review cycle 1 (2026-11-13)** for RV1–RV3; see "Review cycle 1 remediation" below.
- **Reopened at review cycle 2 (2026-11-13)** for RV4; see "Review cycle 2 remediation" below, which
  restates `SiteScaler`'s rule.
- **Foreman ruling, 2026-11-13: duplicate sites are held relay-wide, not per zone.** P1 held a
  duplicated site per zone, which let an older (possibly gone) relay keep a zone the newer one does
  not serve, absorbing that zone's demand until eviction. Each relay process now advertises one
  instance key (its first upstream identity: stable for the process, new each time a relay starts)
  as one more optional key, `instance`, on every beat of every zone identity. The server groups
  identities by instance; per site, the instance whose first identity registered last holds every
  zone it serves, and every identity of every other instance at that site gets target 0, with one
  warning naming both relays. A zone only an older instance serves leaves the site, so it is planned
  over the other sites that serve it. Beats without the key fall back to P1's per-zone rule. This
  extends deviation row 2 by the one key (PLAN §3). Built in `core/heartbeat.py`,
  `core/topology.py`, `server.py` and `cluster/topology.py`; table-tested in
  `test_topology_plan.py` (`INSTANCE_ROWS`) and `test_topology_server.py`.
- **Deviation 2's test amendment was not needed.** The two exact-key tests in `test_relay_roster.py`
  build beats with no advertisement (`relay_beat`, and an `UpstreamHeartbeat` given no zones), so
  their key sets are unchanged and the tests are untouched. The advertisement on the relay's own
  beats is asserted in `test_relay_topology.py`.
- **An autoscaling server publishes before it listens.** `AutoScalingServer` publishes an empty
  `<instance>:0` topology at construction: a relay that connected before the publisher's first
  check read `version: null` and started its fallback fleet (seen in the first integration run).
- **`SiteScaler` also overrides `wait`,** so a version read mid-period is applied at once; the relay
  site period defaults to `[autoscale] period` (60 s).
- **The advertised count is `launched_now`** (launched processes still running, by `poll`), not
  `machine.launched()`, which drops exited clients only at each period's clean.
- **The integration test runs the server and the relay as processes** (`hs server --autoscaling`,
  `hs relay`), as every relay test does (AGENTS.md: nothing in a relay dials its relay-local server
  in-process, and tests run the upstream server and each relay as separate processes).
- **The probe tolerates a failing call.** Any failure of the topology read leaves the view as it
  was (a warning), so the topology can never fail a relay. `test_relay_launch.py`'s `HeldLink`
  raises on every call; its start-up tests pass unchanged, the probe logging that warning.
**Satisfies:** R1, R2, R4, R5, R6, R7 (relay half) · **Depends on:** P2
**Goal:** a relay advertises each zone's bounds and launched count on every beat. It fetches the
topology at heartbeat cadence and reconciles its own entries through its own launchers, which see
`{zone}`, `HYPERSHELL_ZONE` and `--zone`. It never terminates a client. Against a server that publishes
nothing, it runs today's fleets.

- [x] `relay/__init__.py`:
  - `ZONE_OPTIONS` gains `min`, `max` and `init`;
  - `[relay] min`/`max`/`init` defaults go in `core/config.py` (`PARAM_UNSET`);
  - `zone_bounds` per the PLAN §2.2 table;
  - validation in `RelayApp.zone_settings`/`check_args` (whole numbers, `0 ≤ min ≤ init ≤ max`,
    `max ≥ 1`, `size ≥ 1` kept);
  - `RelayThread` builds fleets with the fallback bounds and the heartbeat with the zone map,
    advertised bounds and `launched`;
  - probe the topology once after `wait_link()`, before `cluster.start()`.
- [x] `relay/site.py`:
  - `site_cluster`/`zone_fleet` take `init`/`min`/`max` (`size` is kept as shorthand);
  - `zone_launch(..., expose_zone=True)`;
  - `site_cluster` sets `cluster.launch = zone_launch(cluster, zone, …)`;
  - `TopologyView`;
  - `SiteScaler(AutoScaler)`, assigned as `cluster.autoscaler.machine` and each fleet's `machine`
    (PLAN §2.5), with the delta logged: `Topology <version> (zone: Z, target: T, seen: S, launched: L)
    - launching N`.
- [x] `relay/upstream.py`: `UpstreamHeartbeat` puts `zone`/`bounds`/`clients` on each identity's beat
      and fetches after each successful submit through the link (`hasattr` guard). A missing method or
      `version: null` sets the view to None, logged once at INFO. `LinkDown` keeps the view.
- [x] `tests/test_relay_topology.py`:
  - `@mark.unit`: the `zone_bounds` table, including `size`-only = today's fleet and `min = 0`
    fallback floor 1, plus refusals.
  - `@mark.unit`, with a fake actuator: the `SiteScaler` delta table (target vs seen vs launched; the
    same version twice launches once; a lowered target launches none; a missing entry gives 0;
    None → the parent's fixed fleet, with decisions equal to an `AutoScaler` with the same knobs).
  - `@mark.unit`: beats carry the advertisement on every beat and after a reconnect, and fetch
    results set the view (stub link: method missing, null, entries, `LinkDown`).
  - `@mark.unit`: every launch exposes its zone (word, env, `--zone`) and is fenced.
  - `@mark.integration`: an in-process `hs server --autoscaling` `AutoScalingServer` with one relay
    serving two zones and a recording launcher. The relay's identities appear in the registry; work
    in one zone launches only there; nothing is terminated.
- [x] Declared amendments only (deviations 2 and 3): the beat key sets in `test_relay_roster.py`, and
      `ZONE_ENV` in `test_relay_launch.py:334`/`:572`. Every other relay test is unchanged.
- [x] Docs and assets, same commit:
  - `docs/_include/relay_help.rst`, `relay_desc.rst`, `APP_HELP`;
  - `docs/cli/relay.rst`: a "Topology" section with `.. versionadded:: 2.9.0`. It covers the bounds,
    their defaults, `size` as shorthand, `{zone}`/`HYPERSHELL_ZONE` on relay launchers, the fallback,
    and that scale-down is idle exit only;
  - `config_param_ref.rst`: `[relay] .min/.max/.init` and `[relay.zone.<name>]`;
  - completions for the new `relay.*` keys (the man page is regenerated at release);
  - AGENTS.md and invariants.md §11: "relay launches expose their zone; the relay's fleets reconcile a
    published topology and never terminate".
- **Verify:** see frontmatter (the whole relay suite, which is the R6 guard, and the relay cycle's
  loopback smoke).
- **Touches:** `src/hypershell/relay/{__init__,site,upstream}.py`, `src/hypershell/core/config.py`,
  `tests/test_relay_topology.py`, `tests/test_relay_roster.py`, `tests/test_relay_launch.py`, `docs/`,
  `share/`, `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P4 — Laptop drive, evidence, full regression and docs gate

**Amendments (2026-11-13, at build):**

- **Reopened at review cycle 2 (2026-11-13)** to add the `stall3` and `stall-min2` modes to the gate
  and refresh the evidence; see "Review cycle 2 remediation" below.
- **Reopened at review cycle 1 (2026-11-13)** to refresh the evidence and add the `stale` and
  `bounce` modes to the gate; see "Review cycle 1 remediation" below, which also supersedes the
  retry rule of the next item (RV1).
- **Fix found by the drive: launches the server never counted are applied again.** A site client
  that left before any heartbeat counted it (the first drive run: a launcher that could not find
  `hyper-shell`) changes nothing the server sees, so the entry stayed `target 1, seen 0`, no new
  version came, and a relay that applies each version once never launched again. `SiteScaler` now
  applies an unchanged version again when its launches are short and the zone has no more than
  `seen` (none was counted), at most once every `SiteScaler.RETRY_PERIODS` (3) periods; a client the
  server counted is never replaced this way. Table-tested in `test_relay_topology.py`; P1–P3's
  verifies re-ran after it.
- **Drive modes, as built** (PLAN §6, with the laptop's own numbers):
  - B starts and registers before A in every two-relay mode, so `fill`'s preference order holds
    from the first plan.
  - `kill` runs under `fill` (`sites = B`, factor 10, B's `cpu.max` 20, A's 3, 40 tasks of 2 s),
    and B is killed once 10 tasks are complete. Under `spread` the survivor reaches its own `max`
    before the dead relay is evicted (the dead relay keeps its room until eviction), so regrowth
    after eviction cannot be told apart. Here A launches no `cpu` client before B's eviction and
    must launch after it: the zone's share regrows at the survivor.
  - `floor`: the held floor (B's `gpu.min = 1`) keeps a client after the work too (the Zone-floor
    ruling), so that zone is left out of the idle checks and the floor's relaunch after the work is
    checked instead.
  - `lower` starts A2 (`site = A`, `cpu.max = 1`) once A runs 3 clients; the server log is in the
    system style so the drive reads the warning's and the eviction's times.
  - The "nothing is launched again" window starts 6 s after the last task completes and every client
    has left: a relay returns results up to `bundlewait` (5 s) after its clients ran them, and until
    then the server counts them as work and may plan one more client (seen once in `lower`: A2
    launched one 2 s after the drive's snapshot).
  - The site clients' 2 s idle timeout makes some clients exit between bundles while work remains,
    and they are launched again by the next version; peaks stay within each `max` and nothing is
    terminated.
- **Docs gate, sandbox form:** `sphinx_sitemap` cannot bind a socket in the build sandbox, so the
  gate runs with every other extension (`-D extensions=…`, as zone-autoscale P4 recorded); the
  unmodified command is the foreman's to run.
**Satisfies:** R1–R7 (end to end) · **Depends on:** P3
**Goal:** one server with no launcher and two relays, as separate processes on loopback, show spread,
fill, the gated floor, per-zone idle exit, failover regrowth and the lowered-`max` handoff in live
runs. Logs are kept as evidence, the full suite is green, and docs build with `-W`.

- [x] `spec/relay-topology/drive/topology_shim.sh LOG SITE {zone}`, built on `zone_shim.sh`. It logs
      `launch t=<epoch> site=<SITE> pid=<pid> word=<{zone}> env=<HYPERSHELL_ZONE> client=<--zone or ->`,
      rewrites `-H` to `127.0.0.1`, and `exec`s the client.
- [x] `spec/relay-topology/drive/topology_drive.sh MODE`, run under `temp_site.sh`, bounded by its own
      poll deadline (`TD_DEADLINE`; macOS has no `timeout`).
  - **Server:** `hs server --autoscaling=dynamic -P 1` with `HYPERSHELL_SERVER_EVICT=10`, the operator
    key from the environment, a DEBUG log, and `[autoscale] distribution`/`sites` per mode.
  - **Relay A:** `site = A`, zones `cpu` and `highmem`. **Relay B:** `site = B`, zones `cpu` and `gpu`.
    Each has its own config file with `[relay.zone.<z>]` bounds and the shim as launcher, its own
    relay database and port, heartrate 1 and `[relay] timeout = 2`.
  - Tasks are submitted with `hs submit --zone`/`-g`.
  - Modes and checks are those of PLAN §6.
  - The script never deletes anything; it prints its scratch path and ends every process it started.
- [x] `TD_EVIDENCE=<dir>` writes scrubbed `MODE.txt` files: the shim log, the publisher's version
      lines, the relays' topology lines, and pass/fail per check, with host, user and paths replaced.
      Run all six modes once with `TD_EVIDENCE="$PWD/spec/relay-topology/evidence/laptop"` and commit
      the files with a `README.md` giving the commit, the kind of host and the PASS lines.
- [x] Fix anything the drive, the full suite or the `-W` docs build finds. Any source change re-runs
      P1–P3's verifies before this phase is marked done.
- **Results (2026-11-13):** six topology modes PASS (evidence in `evidence/laptop/`); Z3's
  `founding`, `fixed`, `dynamic`, `restart` PASS; full suite 1852 passed, 91 skipped (environmental:
  PostgreSQL/Timescale unset, fish absent, a fixture regeneration, two Python-version GC cases);
  relay smoke PASS; PostgreSQL suites against a local server (sandbox off): 131 passed, 1 skipped
  (needs a server without TimescaleDB), 29 failed, every one "Template hs283/hs290a1 not listed";
  `test_zone_postgres.py` 5/5. Docs: the sandbox form of the gate, exit 0, no warnings; `-b man`
  builds.
- **Verify:** see frontmatter (six topology modes, four Z3 modes as the `hs cluster` guard, full
  suite, docs). If the sandbox blocks `sphinx_sitemap`'s socket, use the sandbox form zone-autoscale
  P4 recorded, and run the unmodified command outside the sandbox before review.
- **Touches:** `spec/relay-topology/drive/`, `spec/relay-topology/evidence/laptop/`, and, only for
  fixes, any file P1–P3 touched.

## Phase P5 — Multi-node test-cluster run (split gate)
**Satisfies:** R2, R3, R4, R5 (on real nodes) · **Depends on:** P4
**Goal:** a drive the foreman runs inside a held allocation shows the topology on real
nodes. One node runs the server with no launcher, and two nodes run relays serving different
zones through the scheduler's own `srun`, with clients landing on the allocation's nodes. The gate is
split: **Run A** builds and rehearses, and the phase then waits (hill `crest`, `in_progress`) on the
foreman's run. **Run B** judges the collected records, and only then is the phase `done`.

**Amendment (2026-11-13, run A; the foreman's contract, superseding PLAN §6's `INSTALL SCRATCH`
batch script).** The foreman's scheduler wrapper builds a venv at the commit and runs the drive
inside an allocation it already holds (4 nodes, 1 task per node, 8 CPUs each, at most 1 hour), the
way `relay_drive.sh` is run: configured only by environment variables, with the account and
partition added by the wrapper at submit time. So the drive is not a batch script: it has no
`#SBATCH` lines and no positional arguments, never submits, and names no site. Further changes, as
built: tasks are submitted only once both relays are registered (B first), so that the first plan
with work sees both sites and `fill`'s listed order (A first) is shown over registration rank (the
first rehearsal had B launch alone before A registered); the gated floor (`gpu.min 1`) is left out,
since P4 covered it on the laptop and a held floor keeps a client past the work, which the idle and
drain checks need gone; the kill comes once group 0 (highmem, gpu) is done and each site has
launched 2 `cpu` clients, and regrowth is judged as A's `cpu` peak after B's eviction above its
peak between the kill and the eviction (spread places growth at the smallest base, and the dead
relay keeps its last reported count until eviction, so A holds); eviction is 20 s, the server's
floor being over 10 s; there is no TLS on the allocation's network (the key still authenticates;
TLS was the relay cycle's evidence); the server log uses the system style and every process runs
with `TZ=UTC`, so the times of every node's logs compare.

**Run A** (builder) — done 2026-11-13:

- [x] `spec/relay-topology/drive/topology_cluster.sh` (POSIX `sh`), the drive. Environment contract:
      `HS` (the `hs` under test; `python` and `hyper-shell` beside it), `HSD_WORK` (shared work
      directory), `HSD_RESULTS` (results directory), `HSD_NODE_TMP` (node-local temp, the same path
      on every node), `HSD_SRUN_ARGS` (extra flags for every job step, client steps included),
      `HSD_LABEL`, `HSD_RUN_ID` (prefix; run ids are `<prefix>-spread-kill`, `<prefix>-fill`).
      Every inherited `HYPERSHELL_*` is unset. Nodes come from `scontrol show hostnames
      "$SLURM_JOB_NODELIST"`: node 0 the server, node 1 relay A, node 2 relay B; A's clients land
      on nodes 1 and 3+, B's on 2 and 3+. Every role is `srun --overlap --nodes=1 --ntasks=1
      --nodelist=NODE --export=ALL $HSD_SRUN_ARGS`. Rehearsal and tuning knobs (`HSD_SRUN`,
      `HSD_NODES`, `HSD_LOCAL`, timings, sizes) have the cluster values as defaults. Each run has
      its own key (0600), deadline (`HSD_TIMEOUT`, 900 s) and cleanup: roles and recorders stop
      their processes on the run's `abort` flag or deadline, and leftover steps get SIGTERM, then
      SIGKILL, by recorded pid, on exit, error or signal; a run that could end past `HSD_BUDGET`
      (2400 s) is skipped and fails.
- [x] `topology_role.py` (stdlib): `plan`, `status`, `server` (node 0: `hs server
      --autoscaling=dynamic -P 5 -F 1`, evict 20, DB in node-local temp, submits on the drive's cue,
      writes progress, exports the tasks on stop) and `relay SITE` (`hs relay` with `[relay.zone.*]`
      bounds, heartrate 2, site-client idle timeout 10 s, 2 threads, DEBUG log; SIGKILL, SIGUSR1
      drain or SIGTERM on the drive's flags).
- [x] `topology_launch.sh {zone}` (the relays' launcher: next client node round robin, then
      `$HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist=NODE --export=ALL $HSD_SRUN_ARGS sh
      topology_record.sh …`) and `topology_record.sh` (on the client's node: one record per client
      with the relay-side launch time, start time, node, `{zone}` word, `HYPERSHELL_ZONE`, `--zone`,
      whether the upstream key is in its environment or argv; runs the client as a child and records
      its exit status, a passed-on signal, or the kill it delivered on the drive's `kill-B` flag).
- [x] `topology_collect.py` writes `HSD_RESULTS/<run id>.json`: plan (bounds, distribution, sites,
      workload, settings), task counts and per-group first schedule and last completion, one entry
      per launch (site, zone words, node index, launch/start/end times, status, killed), each
      relay's registrations, evictions, reverts and departures (from the server log), idle exits,
      non-zero exits and `Terminating site client` lines (from its log) and how it ended, the
      drive's events (submitted, kill, completed, quiet, observed, drain), times in seconds from the
      run's start. It scans itself for node names, host, user, paths and key (`scrub`, all 0).
- [x] `topology_judge.py PATH [--require spread-kill,fill]` (stdlib) and
      `tests/test_topology_judge.py` (59 `@mark.unit` tests: a good record of each scenario passes,
      51 bad records each fail their named check, and the CLI over a tree, one record, a failing
      run, no runs and `--require`). Checks: common — schema, deadline, tasks complete and none
      failed, group order, launches, zone named alike, placement (highmem only at A, gpu only at B),
      nodes other than the server's, peak ≤ max per site-zone, no key, every exit recorded, every
      exit idle (status 0) or by the kill, no `Terminating site client`, each live relay saw an idle
      exit per launch, no launch after all clients left, live relays stayed and drained with status
      0, record scrubbed; spread-kill — cpu at both sites before the kill, group 0 done first, B
      killed, both B identities evicted and reverted after the kill, site B left the topology, A's
      cpu regrew after the eviction; fill — listed order over rank, the preferred site launched its
      max before the other's first cpu launch, no kill or eviction.
- [x] `topology_rehearse.sh`: the whole drive on one machine with a stand-in `srun` written to
      scratch (ignores resource flags, names the node from `--nodelist`, points a client's `-H` at
      loopback), four named nodes, scaled down (1 s checks, 12 s evict, 1 s tasks), then the judge
      with `--require spread-kill,fill`. Evidence: `evidence/rehearsal/` (`.txt`, scrubbed).
- [x] **Run A gate (2026-11-13):** `uv run pytest -v tests/test_topology_judge.py` 59 passed; the
      rehearsal PASS (spread-kill 60 s, fill 46 s): B killed at 20.4 s, evicted and reverted at
      32.4 s, A's cpu peak 2 between kill and eviction, 5 (max) after; fill: A launched 3 (max)
      before B's first. The judge with one assertion broken on purpose (`launch.peak` counting a
      peak equal to max as over) failed both runs, exit 1; restored, it passed again. No process
      of the rehearsal was left (every relay, server and client recorded its exit).
- **Hand-back to the foreman (run A).**
  - *Run:* inside the 4-node allocation, `HS=<venv>/bin/hs HSD_WORK=<shared> HSD_RESULTS=<results>
    HSD_NODE_TMP=<node-local> HSD_SRUN_ARGS=<site step flags> HSD_LABEL=<short sha>
    HSD_RUN_ID=topology-<short sha> sh spec/relay-topology/drive/topology_cluster.sh`. Expected
    wall time about 8–10 min (spread-kill about 5, fill about 3); worst case 30 min plus
    collection, within its own 900 s deadline per run. Exit 0 only if both scenarios pass.
  - *Copy:* `HSD_RESULTS/*.json` only, unchanged, into `spec/relay-topology/evidence/cluster/`;
    never the work directory (key, logs, launch records).
  - *Watch for:* nested job steps from inside the relay step (as the relay drive's launcher did);
    steps that wait with "Requested nodes are busy" need more step flags in `HSD_SRUN_ARGS`; site
    clients resolve relay nodes by host name and relays the server by its node name; the server's
    `rc` is 4 at the drive's SIGINT (keyboard interrupt of a forever server) and is recorded, not
    judged.

**Run B** (after the foreman's run):

- [x] Copy the records unchanged and re-scrub them: no host, user, cluster, account, partition or path.
      Run the frontmatter verify and write `README.md` (amended from `SUMMARY.txt`, matching
      `evidence/laptop/`). The phase is `done` only when it passes.
- [x] **Run B gate (2026-11-13):** the foreman's run of `793d386` in a 4-node allocation (1 task of
      8 CPUs per node), records `topology-793d386-45729057-{spread-kill,fill}.json`, checked by the
      foreman for host names, users and paths and copied unchanged. `uv run pytest -v
      tests/test_topology_judge.py` 59 passed; the judge over `evidence/cluster` with `--require
      spread-kill,fill` PASS (2 runs, 0 failed). Drive 479 s (spread-kill 322 s, fill 157 s).
      spread-kill: B killed at 149.1 s, both B identities evicted and reverted at 169.2 s, A's cpu
      peak 2 between kill and eviction, 6 (max) after; fill: A launched 4 (max) cpu before B's first
      at 32.9 s. An earlier attempt on the same commit failed `relay.idle_seen` in both scenarios:
      on one node the relay's nested client steps were refused ("Unable to satisfy cpu bind
      request", status 192), inheriting the outer step's CPU binding; the rerun added
      `--cpu-bind=none` to `HSD_SRUN_ARGS` (a site argument, no drive change). The judge was right
      to fail it. Notes in `evidence/cluster/README.md`.
- **Verify:** see frontmatter (judge tests, then the judge over the delivered records with both runs
  required).
- **Touches:** `spec/relay-topology/drive/`, `spec/relay-topology/evidence/rehearsal/`,
  `spec/relay-topology/evidence/cluster/`, `tests/test_topology_judge.py`.
- **State (2026-11-13, after run B):** `done`; the cycle is `in_review`.

---

## Review cycle 1 remediation (2026-11-13)

`hs-review` cycle 1 (`REVIEW.md`, record `b8bb537`, reviewed `0ddebf9`) requested changes: RV1,
RV2, RV3. P3 (which satisfies every R-ID the findings touch) and P4 (the laptop evidence, gate
retuned with the two new drive modes) are reopened by script; P1, P2 and P5 stay done. Every source
change below re-ran P1–P5's verifies (results at the end).

**Amendments (2026-11-13, foreman rulings at review cycle 1; RV2's is the maintainer's, also
recorded in `GOAL.md` Clarifications).**

- **RV1 — a relay applies an unchanged version again only on the server's evidence.** The P4 fix
  ("launches the server never counted are applied again") read the server's silence for three
  relay periods as "my clients left before it counted them". A server whose `-P` exceeds three relay
  periods is silent that long between checks, so the relay relaunched clients the server had
  already counted (12 launches against a target of 2 in the review's reproduction). *Design chosen:*
  the topology carries the publisher's count of finished checks (`checks`), advanced at every
  check and republished under the same version when the plan did not change; each topology read is
  stamped with the build time of the heartbeat sent just before it (`TopologyView.set(reply,
  beat_at)`, from `UpstreamHeartbeat.submit`). A fleet that finds its launches short notes when
  (`short_at`), takes the check count from the first read after a beat built later (`mark`: that
  beat reported the zone short), and applies the version again only once `checks ≥ mark + 2` with
  the version unchanged: the server has then planned with that report and still asks for the same
  entry. Two checks, not one: the check after `mark` may have read the registry before that beat
  arrived. *Why this over the alternatives the ruling named:* the server's period on the reply
  still infers from elapsed time (a slow check, a slow beat, or a link outage breaks it), and the
  reply's planning time against the client's exit time compares two hosts' clocks. A check counter
  is clock-free and is the evidence itself; the beat stamp keeps a stale view (one read before the
  exit, or kept through an outage) from counting. It adds one field to the reply, required with a
  version (the topology protocol is unreleased, so `TOPOLOGY_PROTOCOL` stays 1), and no dispatcher
  method. Re-applies are now spaced by the server's own period, so `RETRY_PERIODS` is gone.
  *Consequence:* a client that leaves uncounted is replaced after two server checks rather than
  three relay periods (P4's bug stays fixed); a counted client that idles out under a slow server is
  never replaced unless the server, having planned again, still asks.
- **RV2 — superseded relays' clients stop counting against the site** (maintainer ruling at review
  cycle 1). `site_views` takes a site-zone's `running` from the holder's own clients and `entries`
  gives the holder the site's whole target (no subtraction of older identities' clients). A relay
  restarted after a crash is planned at once; if the older relay was in fact alive, its clients
  over-provision briefly and retire by idle exit. The `site_views` docstring that called the old
  counting deliberate is rewritten. The Q2 rule (newest holds, older gets zero, one warning) is
  unchanged.
- **RV3 — the text matches the code.** `AGENTS.md` (cluster section), `invariants.md` §11,
  `docs/cli/relay.rst` (Topology) and `docs/cli/server.rst` (duplicated site) now say when a relay
  replaces an exit (a later version asks, or the server finished two checks past a beat that
  reported it and still publishes the same version) and that a newer relay is planned on its own
  clients.
- **Drive:** two modes, `stale` (server `-P 20`, relay period 1 s, `cpu` `init`/`max` 2, no work:
  exactly 2 launches, no version applied again once the server checks again) and `bounce` (A
  SIGKILLed with its clients once it runs 2, restarted at once as A2 at site A with its own relay
  database, eviction 30 s: A2 launches within 10 s of its start and before A's eviction). `lower`
  gains a check that A2 launches within 10 s of the duplicate warning. The drive's server period
  and eviction became per-mode settings; a killed relay is skipped by the liveness checks in both
  `kill` and `bounce`.

**Red on the reviewed head, green after.** The tests and drive modes were written first and run
against the unchanged source:

| Check | Reviewed head | After |
|-------|---------------|-------|
| `test_relay_topology.py::test_site_scaler_does_not_take_a_slow_server_for_uncounted_exits` (RV1 reproduction, unit) | fail: 2 relaunched | pass |
| `…::test_site_scaler_applies_again_once_the_server_planned_again` (P4 bug, unit) | error (no `checks`) | pass |
| `…::test_site_scaler_counts_only_checks_after_the_beat_that_reported_the_exit`, `…::test_site_scaler_follows_a_new_version_over_applying_again`, `…::test_fetch_sets_the_view` (beat stamp) | error | pass |
| `…::test_relay_launches_again_while_the_server_asks` (P4 bug, processes; either path) | pass | pass |
| `test_topology_plan.py::test_a_restarted_relay_is_planned_at_once` (3 rows, RV2) | fail: target 0 | pass |
| `test_topology_plan.py` duplicate tests, `INSTANCE_ROWS` (amended per the RV2 ruling) | fail | pass |
| `test_topology_plan.py::test_topology_reply_null_entries_and_missing` (`checks` on the wire) | fail | pass |
| `test_topology_server.py::test_publisher_plans_from_the_database_and_publishes_once_per_change` (`checks`), `::test_publisher_warns_once_per_duplicate` (RV2) | fail | pass |
| drive `stale` (RV1 reproduction, `-P 20` against 1 s) | FAIL: 12 launches, 5 versions applied again | PASS: 2 launches, none again |
| drive `bounce` (RV2, SIGKILL bounce) | FAIL: A2's first launch 32 s after its start, after A's eviction | PASS: 2 s after its start, 28 s before the eviction |
| drive `lower` (RV2 with the older relay alive) | FAIL: A2's first launch 21 s after the warning | PASS: 1 s after |

The process-level P4 test passes on both: a beat sometimes catches a short-lived client, so which
path relaunches it (a new version, or the same version applied again) varies; the unit tests pin
the second path.

**Cluster evidence (`evidence/cluster/`, not re-run).** The judged scenarios, `spread-kill` and
`fill`, have one relay per site, so RV2's change is not exercised there: with one identity per
site-zone, the holder's own clients are the sum the planner used before. RV1's change touches only
the path that applies an unchanged version again, and only makes it later or absent (two server
checks of 5 s past a post-exit beat, against three relay periods of 2 s before); it never adds a
launch. The records carry no relay log lines, so whether a re-apply happened on the cluster cannot
be read from them. Every judged check (peaks within `max`, every exit idle or by the kill, no
launch after every client left, a live relay seeing an idle exit per launch, regrowth after the
eviction through new versions, the `fill` order) depends on new versions or on fewer launches, not
on re-applies. So no behaviour the cluster scenarios judged changes; a rerun would re-confirm the
same checks. The added `checks` field is on both sides of the same commit. Whether to rerun is the
foreman's call.

**Verification (2026-11-13).** Targeted (P1–P5 verify test files, the relay suite and the
judge tests): 688 passed; relay smoke PASS. The three changed files alone: 120 passed. Drive: all
eight topology modes PASS (`spread`, `fill`, `floor`, `idle`, `kill`, `lower`, `stale`, `bounce`;
evidence in `evidence/laptop/`, every file regenerated, no version applied again in any run), and
Z3's `founding`, `fixed`, `dynamic`, `restart` PASS. Full suite: 1921 passed, 88 skipped, 2
warnings (the existing `fork()` deprecations), under the worktree's CPython 3.12. PostgreSQL suites
against a local server (sandbox off): 131 passed, 1 skipped, 29 failed, every one "Template
hs283/hs290a1 not listed" (20 and 9). Docs: the sandbox form of the `-W` gate (every extension but
`sphinx_sitemap`, in its own environment) exit 0 with no warnings; `-b man` builds. The unmodified
docs command is the foreman's to run outside the sandbox.

## Review cycle 2 remediation (2026-11-13)

`hs-review` cycle 2 (`REVIEW.md`, record `37e0a60`, reviewed `7ddd16f`) requested changes: RV4. P3
(the relay's `SiteScaler`) and P4 (the laptop gate, retuned with two new drive modes) are reopened by
script; P1, P2 and P5 stay done. This is the last review cycle the factory allows, so the foreman
ruled that the fix restate the reconciler as one rule, not add a case.

**RV4.** The cycle-1 `SiteScaler` applied each version once and kept a second, special path that
applied it again. That path measured its shortfall against `expected`, the count the version's
launches should have reached (`launched + count` when applied), and reset whenever the zone ran at
least that many. When a client the server had counted exited before the relay read the plan made
with it, the relay launched `target − seen`, reached `expected`, and the server's next plan was the
same entry: no new version, and the re-apply path reset, though the zone ran one short of the
target. It stayed short, below its `min` too, until the plan changed for another reason.

**The rule (foreman ruling at review cycle 2).** A fleet following a topology launches
`target − max(basis, live)` clients (none when that is not positive) each time it learns a new
*basis*, and at no other time. `live` is the zone's clients launched here and still running (the
relay's own count, the one its beats advertise), never a count of launches. The *basis* is the count
of the zone's clients the server's current plan is known to rest on:

- a version not yet applied gives its `seen`, the count the server planned from;
- otherwise, while `live < target`, the server's own evidence gives `live`: the server has finished
  two checks (`RECHECKS`, the cycle-1 `checks` counter) since a topology read that followed a beat
  reporting `live`. One check may have read the registry before that beat was recorded; the second
  starts after the first finished, which is after the read, so it planned with `live`, and its entry
  (the same one, or the relay would hold a new version) still asks for `target`.

The fleet tracks `held` (the `live` it last saw), `since` (when it first saw it) and `mark` (the
`checks` of the first read whose beat was built after `since`); a change in `live`, or a launch,
starts that afresh, and a `checks` lower than `mark` (a new server) re-marks. Why it converges: once exits
stop, a fleet below its target either reads a new version (applied at once) or, the plan standing,
its `live` stands too until it launches, so two server checks later the evidence holds and it launches
`target − live`, reaching the target in hand. Why it never over-launches: every launch stops at the
target in hand, and only on a basis the server stated or planned on, so nothing is launched on the
relay's own period alone. Implemented in `relay/site.py` (`SiteScaler.reconcile`, `.planned_on`);
`expected`, `short_at` and `uncounted` are gone.

What the earlier cycles fixed still holds, by the same rule:

| Case | How the rule keeps it |
|------|-----------------------|
| RV1: `-P 20` against a 1 s relay launches exactly its target | clients that idle out after the server counted them change `live`, but no basis comes until the server checks twice on the new count, and by then its plan (init lapsed, no work) asks for none: a new version with target 0 |
| A lowered target terminates nothing and replaces nothing | `target ≤ live` gives no launch on either basis; the excess retires by idle exit |
| A client that exits before any beat counted it is replaced (P4) | the plan stays `target, seen` unchanged; two checks after a beat reports the lower `live`, the basis is `live` |
| A restarted relay is planned at once (RV2) | server-side, unchanged; the relay applies the first version it reads |
| A launcher that always fails relaunches only while the server asks | each failed launch leaves `live` unchanged, so another needs two more server checks on it and a target still above it |
| The explicit floor holds while gated | the floor is the server's target; an exit below it is replaced by a new version or, unchanged, by the evidence |

**Tests, red on the reviewed head (`37e0a60`) and green after.** The tests and drive modes were
written first and run against the reviewed head's `relay/site.py`, swapped in for the run and
restored before the fix:

| Check | Reviewed head | After |
|-------|---------------|-------|
| `test_relay_topology.py::test_site_scaler_replaces_a_counted_exit_the_unchanged_plan_still_asks_for` (RV4, unit) | fail: 0 launched after two checks | pass |
| `…::test_site_scaler_converges_over_event_sequences[a-counted-exit-during-growth]` (`stall3`'s sequence) | fail: 3 launches, 2 running of 3 | pass: 4, 3 |
| `…[two-counted-exits-at-a-floor]` (`stall-min2`'s sequence) | fail: 4 launches, 2 running of 3 | pass: 5, 3 |
| `…[an-exit-before-any-beat]`, `…[a-slow-server-is-not-a-lost-count]`, `…[exits-under-a-standing-target]` | pass | pass |
| `…::test_site_scaler_never_replaces_under_a_lowered_target`, `…::test_site_scaler_relaunches_a_failing_launcher_only_while_asked` | pass | pass |
| `…::test_site_scaler_converges_over_random_event_sequences[growth, floor, starting]` (300 seeds each) | fail: seeds 1, 92, 62 end short (2 of 3, 1 of 2, 1 of 2) | pass |
| drive `stall3` | FAIL: race reproduced, A never ran 3 again (peak 2 after the kill), no plan applied again | PASS: 3 again 9 s after the kill, one plan applied again |
| drive `stall-min2` | FAIL: race reproduced, A sat at 2 under a floor of 3, no plan applied again | PASS: 3 again 10 s after the second kill, one plan applied again |

The guard rows (`an-exit-before-any-beat`, `a-slow-server-is-not-a-lost-count`, the lowered target,
the failing launcher) pass on both: they pin what cycle 1 fixed. The random-sequence test drives a
model server (planning from the last recorded beat, publishing only on change, counting every
check, recording a beat no later than the second check after the read that followed it) and the
real `SiteScaler` through 300 random orders of 60 events per plan (dynamic growth to 3, a floor of
3, an `init` of 2 that lapses), asserting at every launch that the fleet stays within the target in
hand, that launches never exceed the highest target plus exits, and that once exits stop the fleet
ends at the server's target.

**Drive.** Two modes, after the reviewer's reproductions, run with relay A alone, the server
checking every 4 s and site clients idle for 30 s (so no client leaves between bundles and hides the
stall behind a new version); the relay's site server evicts a killed client after 20 s, reverting its
task. `topology_trigger.py` follows the server log and kills a client the instant the server
publishes `target 3, seen 2`, before the relay's next beat and read.

- `stall3`: `cpu` `max` 3, 90 tasks of 2 s, under growth.
- `stall-min2`: `cpu` `min = init = max = 3`, 60 tasks of 2 s; one client killed once the server
  has seen 3, a second as it publishes `target 3, seen 2`.

Each checks that the race happened (A logged `target: 3, seen: 2, launched: 1) - launching 1`) and
that A ran 3 clients again within 30 s of the kill. `stall-min2`'s zone is a held floor, so it is
left out of the idle-exit checks, as `floor`'s `gpu` is.

**Cluster-judged behaviour.** No behaviour the cluster scenarios judged changes, so the
records in `evidence/cluster/` stand; a rerun would re-confirm the same checks, and whether to rerun
is the foreman's call. A new version is applied exactly as before (`target − max(seen, live)`, the
cycle-1 formula). The rule differs from cycle 1 only under an unchanged version: it launches where
cycle 1 stalled (a counted client that left before the relay read the plan made with it), and in
the uncounted case it still waits for the same two server checks. Both scenarios have one relay
per site, idle timeout 10 s and `-P 5`; every judged check rests on new versions or on fewer
launches: peaks within `max` (every launch stops at the target in hand, itself within `max`), every
exit idle or by the kill (nothing is terminated), no launch after every client left (the target is
then 0, no floor being set), a live relay seeing an idle exit per launch, regrowth after the
eviction, and the `fill` order (a preferred site that the race had left one short would now reach
its `max` sooner, which only strengthens that check). The records carry no relay log lines, so
whether the race occurred on the cluster cannot be read from them.

**Verification (2026-11-13).** Targeted (P1–P5 verify test files, the relay suite, the
Z3 autoscaler suites and the judge tests): 611 passed; relay smoke PASS; the judge over
`evidence/cluster` with `--require spread-kill,fill` PASS (2 runs, 0 failed). Drive: all ten topology
modes PASS (`spread`, `fill`, `floor`, `idle`, `kill`, `lower`, `stale`, `bounce`, `stall3`,
`stall-min2`; evidence in `evidence/laptop/`, every file regenerated; only the two stall runs applied
a plan again, once each), and Z3's `founding`, `fixed`, `dynamic`, `restart` PASS. Full suite: 1932
passed, 88 skipped, 2 warnings (the existing `fork()` deprecations), CPython 3.12.13, 16 min 42 s.
PostgreSQL suites against a local server (sandbox off): 131 passed, 1 skipped, 29 failed, every one
"Template hs283/hs290a1 not listed" (20 and 9). Docs: the sandbox form of the `-W` gate (every
extension but `sphinx_sitemap`, in its own `UV_PROJECT_ENVIRONMENT`) exit 0 with no Sphinx
warnings. The unmodified docs command is the foreman's to run outside the sandbox.

## Review cycle 3 — maintainer ruling and publish (2026-11-13)

Review cycle 3 closed RV1–RV4 and found RV5 (a superseded relay strands the tasks it holds once
its last client leaves), with the bounded loop spent. The maintainer accepted RV5 as a known
limitation of the duplicated-site case and ruled to ship at cycle 3; the fix goes to a follow-up
seed. Recorded in `GOAL.md` (Clarifications, "Ruled at review cycle 3"), `REVIEW.md` (cycle 3,
"Maintainer ruling") and the frontmatter's `review.blocked_reason`; `review.verdict` stays
`changes-requested` and `review.cycle` 3, since no further review pass ran. No `src/` change
follows `b98e994`. After it, the branch adds only spec records and one user-docs paragraph
(`docs/cli/relay.rst`, the limitation), on the maintainer's ruling.

**Cluster rerun at `b98e994`.** The foreman reran the multi-node drive on the final reconciler
(same drive and judge as P5): `topology-b98e994-45755911-{spread-kill,fill}.json` in
`evidence/cluster/`, both PASS; the judge over the folder with `--require spread-kill,fill` prints
PASS (4 runs, 0 failed). The `793d386` records stay as P5's; `evidence/cluster/README.md` says which
records are current.

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase. Phase statuses are authoritative, and the
   `current_phase` pointer is reconciled against them.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase, consulting `PLAN.md` and `research/` for detail.
4. Run the phase's `verify:` command. Never advance on a checkbox alone. For P5, Run A stops at
   `crest` (see the phase).
5. Amend this file freely if reality diverges: regenerate frontmatter with `set_phase.py` and note the
   amendment in the commit body. STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`, make one `[feature]` commit, then stop
   and report.
