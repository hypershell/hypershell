---
slug: relay-topology
title: 'Relay site-topology autoscaling: fill and spread across sites'
kind: feature
appetite: big
status: in_progress
branch: feature/relay-topology
base: develop
current_phase: P5
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
  verify: for mode in spread fill floor idle kill lower; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/relay-topology/drive/topology_drive.sh" $mode || exit 1; done &&
    for mode in founding fixed dynamic restart; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" $mode || exit 1; done && uv
    run pytest && uv run --python 3.12 --group docs sphinx-build -W --keep-going -q
    -E -b html docs docs/_build/html
- id: P5
  name: 'Multi-node test-cluster run (split gate): run A builds the batch script and
    judge, run B judges the foreman''s logs'
  status: pending
  satisfies:
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_topology_judge.py && uv run python spec/relay-topology/drive/topology_judge.py
    spec/relay-topology/evidence/cluster --require spread-kill,fill
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
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
- **No cluster tools, no submissions.** No builder calls a cluster tool or submits a job. P5's batch
  script is run by the foreman.
- **Plan gate:** approved by the foreman on 2026-11-12, all five deviation rows included. Q1 is ruled:
  relay launches always expose their zone (GOAL Clarifications, "Ruled at the plan gate"). The
  `scheduler_group` equivalence test (deviation 4) runs in P1's verify. `hs-build` may start P1.

## Interfaces (built in P1–P3)

| Module | Name | Contract |
|--------|------|----------|
| `core/topology.py` | `Bounds(min, max, init)` | frozen; `to_dict`/`from_dict` (`{"min","max","init"}`), ints, `0 ≤ min ≤ init ≤ max`, `max ≥ 1` |
| `core/topology.py` | `Entry(zone, target, seen)`, `Topology(version, entries)` | `version` = `"<server instance>:<n>"`; `entries` by relay identity |
| `core/topology.py` | `SiteRegistry` | lock-guarded; `record(identity, *, site, zone, bounds, clients, host, instance=None)` overwrites per identity, stamps `registered` (monotonic) on first sight, `rank` per site name on first sight (kept), `init_held` until `clients ≥ init`; `drop(identity)`; `snapshot() -> List[SiteRecord]`; `SiteRecord.instance` is the advertised relay instance key (None when absent) |
| `core/topology.py` | `request_text(site, identities)`, `read_request(text) -> (site, identities)`, `reply_text(topology, identities)`, `read_topology(text) -> Optional[TopologyReply]` | JSON, `TOPOLOGY_PROTOCOL = 1` (its own number, apart from the work-request protocol); `{"protocol","version":null}` when unpublished; the reply names only identities that have an entry, by zone name; `TopologyReply(version, zones).assignment(zone)` gives `Entry(zone, 0, 0)` for a zone with no entry (target 0); `read_request`/`read_topology` raise `ValueError` on anything malformed |
| `core/heartbeat.py` | `Heartbeat.zone/bounds/clients/instance` | optional, packed only when set, read with `.get`; a malformed value (zone or instance not text, invalid bounds, a count that is not a whole number ≥ 0) decodes as None, so the identity is a non-participant rather than a crash in the heart monitor (an instance that is not text only drops the relay-wide rule) |
| `core/dispatch.py` | `Dispatcher.topology(text) -> str`, `Dispatcher.publish(topology)` | under the dispatcher lock; `DISPATCH_EXPOSED = ('request', 'takes_submissions', 'topology')` |
| `cluster/demand.py` | `scheduler_group(reported, *, attempts, eager) -> int` | additive; equals `AutoScaler.current_group` (pinned by a test) |
| `cluster/topology.py` | `Distribution`, `parse_distribution(value)`, `parse_sites(value)` | `spread`/`fill`; list or comma string |
| `cluster/topology.py` | `SiteZone`, `SiteView`, `Duplicate(site, holder, older, relay=False)`, `site_views(records) -> (views, duplicates)`, `plan_sites(loads, knobs, sites, *, distribution, order)`, `entries(targets, records)` | pure (PLAN §2.4); duplicates are held **relay-wide** (amended in P3, 2026-11-13): per site, the instance whose first identity registered last holds every zone it serves, every identity of an older instance gets `Entry(zone, 0, clients)` (a zone only it serves leaves the site's view), one `Duplicate(relay=True)` per older instance names the first identity of each; records with no instance key keep P1's per-zone rule among themselves and the holding instance's identity of that zone |
| `cluster/topology.py` | `TopologyPublisher`, `TopologyPublisherThread`, `AutoScalingServer`, `serve_with_sites(**options)` | FSM START→CHECK (first check at once)→PUBLISH→WAIT→CHECK, FINAL/HALT; publishes only on change; never launches; `AutoScalingServer` publishes an empty `<instance>:0` topology at construction, so a relay never reads null from it (P3); the duplicate warning is once per pair of relays (relay-wide) or of identities (per zone); `AutoScalingServer(address, auth, bundlesize, poll, max_retries, eager, redirect_failures, evict_after, tls, policy, factor, period, distribution=None, sites=None)` reads `[autoscale] distribution`/`sites` when not given (`ConfigurationError` naming the source); `ServerApp.run` calls `serve_with_sites` lazily |
| `core/topology.py` | `error_text(message)` | the reply to an unreadable topology request (`{"protocol","error":"request","message"}`); `read_topology` raises `ValueError` on it |
| `server.py` | `HeartMonitor(…, sites)`, `record_site(hb)`, `drop_site(uuid)`; `ServerThread(…, sites)` | a site beat lacking zone, bounds or clients is logged once at INFO and not recorded |
| `relay/__init__.py` | `zone_bounds(settings, relay_wide) -> (advertised: Bounds, fallback: Bounds)`, `BOUND_OPTIONS`; `RelayThread(…, min_size, max_size, init_size)` (relay-wide bounds, None = unset), `.view`, `.advertised`, `.zone_launched(name)`; `RelayApp.relay_bounds`, `.check_bounds()` | PLAN §2.2 table, with the fallback `min` and `init` held within `max`; ValueError → `ArgumentError` naming the zone |
| `relay/site.py` | `TopologyView(reply=None)` (`.reply`, `.set`), `SiteScaler(AutoScaler)`, `site_scaler(machine, launch, view)`, `launched_now(machine)`; `site_cluster`/`zone_fleet(…, init_size, min_size, max_size, view)` | `view.assignment(zone) -> Optional[(version, Entry)]`; `SiteScaler` launches `max(0, target − max(seen, launched))` once per version, else the parent's fixed fleet; it overrides `init`, `check_zones` and `wait` (a version not yet applied is checked at once, not at the period's end); `launched_now` counts launched processes still running, read by the heartbeat |
| `relay/upstream.py` | `UpstreamHeartbeat(…, zones, bounds, launched, instance, view)`, `.fetch()` | every beat of an identity in `zones` carries its zone, bounds, `launched(zone)` and `instance`; each running beat is followed by `fetch()`: a missing method or `version: null` clears the view (INFO once per change), `LinkDown` or any other failure keeps it |

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
**Goal:** a self-contained batch script the foreman submits shows the topology on a real
allocation. One node runs the server with no launcher, and two nodes run relays serving different
zones through the scheduler's own `srun`, with clients landing on the allocation's nodes. The gate is
split: **Run A** builds and rehearses, and the phase then waits (hill `crest`, `in_progress`) on the
foreman's run. **Run B** judges the collected records, and only then is the phase `done`.

**Run A** (builder):

- [ ] `spec/relay-topology/drive/topology_cluster.sh INSTALL SCRATCH`:
  - `#SBATCH --nodes=4`, `#SBATCH --time=01:00:00`, and nothing site-specific (no account, partition
    or QOS; the foreman adds them at submit time).
  - `INSTALL` is the prefix whose `bin/hs` and `bin/python` are under test, and `SCRATCH` is a shared
    directory for logs and records. It reads node names from `scontrol show hostnames
    "$SLURM_JOB_NODELIST"` and unsets every inherited `HYPERSHELL_*`.
  - Every role is an `srun --overlap --nodes=1 --ntasks=1 --nodelist=<node> --export=ALL` step:
    - node 0 runs `hs server --autoscaling=dynamic -P 5` with `evict` 30 and a key minted into a 0600
      file under `SCRATCH`;
    - node 1 runs relay A (`cpu`, `highmem`);
    - node 2 runs relay B (`cpu`, `gpu`);
    - each relay's launcher is `srun --overlap --nodes=1 --ntasks=1 --exclude=<node 0> --export=ALL sh
      <recorder> <log> {zone}`, so clients land on nodes 1–3.
  - Two runs within the hour, each bounded by its own deadline:
    - `r1-spread-kill`: about 600 tasks of 2 s in group 0 (`cpu`), plus `highmem` and `gpu` in group 1;
      bounds `cpu.max` 3 per site, `gpu.min` 1, `highmem.min` 0; SIGKILL relay B once a third of the
      tasks are complete.
    - `r2-fill`: `sites = B,A` and the same workload, with no kill.
- [ ] `spec/relay-topology/drive/topology_record.sh LOG ZONE_WORD -- CLIENT_ARGV`. It appends `t, node,
      word, env zone, --zone, pid, key_present (bool)` and then `exec`s the client.
- [ ] A collector, run at the end of each run (`topology_collect.py`, stdlib plus the venv's python),
      writes `SCRATCH/results/<run>.json`. It holds counts and booleans only:
  - tasks per group, completed once, failed;
  - launches per site-zone with their timestamps relative to start;
  - peak launched vs advertised max;
  - the gated-zone launches before group 0 ended;
  - the fill order;
  - evictions and orphan reverts (server-log regexes, as `relay_probe.py:52-58`);
  - A's `cpu` launches after B's eviction;
  - `Terminating site client` lines before the drain, and idle exits seen;
  - key present in any launch;
  - the scrub check.
- [ ] `spec/relay-topology/drive/topology_judge.py DIR [--require spread-kill,fill]` (stdlib) and
      `tests/test_topology_judge.py` (`@mark.unit`): a good record of each run passes, each bad record
      fails its named check, and the CLI handles a tree, one run, no runs and `--require`.
- [ ] Local rehearsal, `TC_MODE=local sh spec/relay-topology/drive/topology_cluster.sh INSTALL SCRATCH`.
      It runs on one machine with stand-in `srun`/`scontrol` from `TC_STANDIN` (scratch, not
      committed), loopback, and both runs shortened. The judge passes on its records.
- [ ] **Run A gate** (recorded in the phase notes, not the frontmatter verify):
      `uv run pytest -v tests/test_topology_judge.py` plus the local rehearsal PASS. Then set `hill:
      crest`, leave the phase `in_progress`, and hand back to the foreman:
  - the exact submit line: `sbatch [<foreman's account/partition/QOS flags>]
    spec/relay-topology/drive/topology_cluster.sh <INSTALL> <SCRATCH>`;
  - what to copy: `SCRATCH/results/*.json` only, never the key, logs or identities;
  - where to copy it: `spec/relay-topology/evidence/cluster/`.

**Run B** (after the foreman's run):

- [ ] Copy the records unchanged and re-scrub them: no host, user, cluster, account, partition or path.
      Run the frontmatter verify and write `SUMMARY.txt`. The phase is `done` only when it passes.
- **Verify:** see frontmatter (judge tests, then the judge over the delivered records with both runs
  required).
- **Touches:** `spec/relay-topology/drive/`, `spec/relay-topology/evidence/cluster/`,
  `tests/test_topology_judge.py`.

---

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
