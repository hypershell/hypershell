---
slug: relay
title: "HyperShell Relay: two-level scheduling across administrative sites"
kind: feature
appetite: big
status: in_progress
branch: feature/relay
base: develop
current_phase: P1
last_updated: "2026-11-04"
phases:
  - id: P1
    name: "Late-confirm fence: a confirmation never rewrites a completed row's attribution"
    status: pending
    satisfies: [R19, R20]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: "test -f src/hypershell/core/dispatch.py && grep -q _get_dispatch src/hypershell/core/queue.py && grep -q CONTENDED_RERUNS src/hypershell/data/model.py && uv run pytest -v tests/test_writeback.py tests/test_contended_recovery.py tests/test_contended_write.py tests/test_zone_dispatch.py tests/test_server_fsm.py"
  - id: P2
    name: "hs relay process: [relay] config, private SQLite bound at import, local server up and down"
    status: pending
    satisfies: [R1, R10, R18, R20]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_db.py tests/test_main.py tests/test_completions.py tests/test_logging.py tests/test_initdb.py && uv run hs relay --help > /dev/null && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs"
  - id: P3
    name: "Upstream pull: zone identity, backlog gate, durable insert before confirm, upstream TLS"
    status: pending
    satisfies: [R2, R3, R8, R17, R20]
    depends_on: [P2]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_connector.py tests/test_relay_fsm.py tests/test_relay_db.py tests/test_dispatch_version.py"
  - id: P4
    name: "Site run and return path: fleet, no local retry, outbox, leaf attribution, one-relay loopback"
    status: pending
    satisfies: [R1, R4, R5, R6, R16, R20]
    depends_on: [P3]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_return.py tests/test_relay_connector.py tests/test_relay_fsm.py && uv run pytest -v tests/test_relay_workload.py -k one_relay"
  - id: P5
    name: "Credential fence and two-relay loopback with failover (delegation core complete)"
    status: pending
    satisfies: [R3, R6, R17, R20]
    depends_on: [P4]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_launch.py tests/test_relay_workload.py && uv run pytest -v"
  - id: P6
    name: "Roster forwarding: heartbeat site/roster, client site/relay columns, HeartMonitor cascade"
    status: pending
    satisfies: [R7, R6, R20]
    depends_on: [P5]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_roster.py tests/test_server_fsm.py tests/test_server.py tests/test_upgrade_cli.py tests/test_zone_dispatch.py tests/test_autoscale.py tests/test_relay_workload.py"
  - id: P7
    name: "R21 run A: relay drive, launcher wrapper, judge, local smoke, hand back (external wait follows)"
    status: pending
    satisfies: [R21, R20]
    depends_on: [P5, P6]
    parallel: true
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_judge.py && sh spec/relay/drive/relay_smoke.sh"
  - id: P8
    name: "Outage ride-through and restart durability: UpstreamLink, backoff, redelivery"
    status: pending
    satisfies: [R9, R5, R20]
    depends_on: [P6]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_relay_link.py tests/test_relay_outage.py tests/test_relay_fsm.py tests/test_relay_return.py tests/test_relay_workload.py"
  - id: P9
    name: "Multi-zone relays (one upstream identity per zone), per-zone fleets, relay docs and trust model"
    status: pending
    satisfies: [R2, R8, R18, R20]
    depends_on: [P8]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs"
  - id: P10
    name: "R21 run B: judge the external multi-node evidence"
    status: pending
    satisfies: [R21]
    depends_on: [P7, P9]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run python spec/relay/drive/relay_judge.py spec/relay/evidence/cluster"
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
---

# TECH.md — HyperShell Relay: two-level scheduling across administrative sites

The **context engine and finite-state machine** for building relay. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay/TECH.md`); the per-phase checklists
below are the work. `hs-build` executes the next actionable phase, runs its `verify:` command,
updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). Section references below (§) point there.
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and briefs 01–04.

## Frontmatter field reference

See [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md). This cycle uses
the zone cycles' **external wait** convention for P10:

- When `spec/relay/evidence/cluster/` is absent, `hs-build` marks P10 `blocked` with
  `blocked_reason: "awaiting multi-node evidence (run B)"` and stops. That does not count against
  `attempts`.
- The foreman may run `hs-review` on P1–P9 while P10 is blocked.
- **Review checkpoint after P5.** P1–P5 are the delegation core (R1–R6, R8, R10, R17, R19); the
  foreman may start `hs-review` on them while P6–P9 build.
- P7 is `parallel: true` (scripts and a judge under `spec/relay/drive/` plus one test file, no
  coupled core); it may run beside P8.
- No phase is `hammerable`: the maintainer ruled the whole first phase in scope (GOAL C1). Any cut
  is the maintainer's.

## Conventions (apply to every phase)

- Commit conventions, code style and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md). Consult [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md)
  §1, §3–§6, §8–§12 (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state
  change. Subjects: `[feature] Build relay P<n>: …`. **No `Co-Authored-By` trailer.**
- A CLI change updates `docs/_include/*.rst` and `share/` completions **in the same commit**. Man
  pages are left to `/hs-release`.
- No feature-scoped ids (`R#`, `P#`, `DQ#`) in source comments.
- Public repo: name infrastructure only by kind; name CLI commands only at top level (`hs list`,
  `hs info`, `hs update`, `hs wait`, `hs run`).
- **Zone code is not edited** (`core/dispatch.py`, the Z2 parts of `core/queue.py`, `client.py`,
  `server.py`'s `Scheduler`/staging), nor are Z2's tests. The only planned edit to a launcher file
  is P9's extraction (PLAN §2.6, §2.8).
- Tests are deterministic: fakes, injected clocks, `until()` polls with deadlines; never a sleep
  as synchronisation. Integration tests run the upstream server and each relay as **separate
  processes** (one relay per process; no in-process dial of the local server). Done means
  `COUNT(exit_status = 0) == M`, never "no incomplete rows".

## Build precondition (amended 2026-11-04)

- **As planned:** relay builds from `develop` after Z1 (#76) and Z2 (`feature/zone-dispatch`) have
  merged, by bringing this branch onto that `develop` (`git merge origin/develop`, signed).
- **As ruled (foreman, 2026-11-04):** zones are not on `develop` yet; Z1 is held by a Linux-only CI
  slowdown under diagnosis. To use the time, **P1 and P2 build now on the current base**
  (`origin/develop` `bd2faf9`, 2.8.4 merged back; it carries #75's `CONTENDED_RERUNS`). Neither
  phase needs zone code: P1 is a `data/model.py` change on the confirm path, which Z1 and Z2 do not
  touch; P2 composes a plain `ServerThread`. If either could not be built honestly without zone
  code, the builder stops and reports.
- **Blocking precondition, now before P3.** P3 is the first phase that needs zone code (`PROTOCOL`,
  the work-request reply, `test_dispatch_version.py`). Before P3 starts, this branch merges a
  `develop` that carries Z1 and Z2 and runs every deferred verify item below; any red one is a red
  gate for the phase it belongs to. Until then `hs-build` marks P3 `blocked` with
  `blocked_reason: "awaiting develop with Z1 and Z2"` and stops (not counted against `attempts`).
- Z2's own merge obligations (among them `revert_staged` through #75's contended-write helper) are
  Z2's; relay builds on the merged result and does not redo them.

### Deferred verify items (need zone code; run after the zone merge, before P3)

The frontmatter `verify:` strings stay as planned: they are the full gates, re-run once zones land.
Until then each phase runs its verify **minus** these items, and records the substitution:

| Phase | Deferred item | Why |
|---|---|---|
| P1 | `test -f src/hypershell/core/dispatch.py && grep -q _get_dispatch src/hypershell/core/queue.py` (the zone-code presence guard) | Z2 code, not on the base |
| P1 | `tests/test_zone_dispatch.py` | Z2 test, not on the base |
| P1 | `tests/test_server_fsm.py` | Z1 test, not on the base (the ruling's list names the guard and the two dispatch files; this file is Z1's, so it is deferred on the same ground) |
| P3 | `tests/test_dispatch_version.py` | Z2 test; P3 itself waits for the zone merge |
| P6 | `tests/test_server_fsm.py`, `tests/test_zone_dispatch.py` | as P1; P6 runs after the zone merge in any case |

`grep -q CONTENDED_RERUNS src/hypershell/data/model.py` is #75, present on the base, and runs.

## Security-cycle overlap (binding in every phase; PLAN §2.8)

- **P4** consumes `AutoScalingCluster` (a call, no edit). If `fix/authkey-handling-hardening` has
  landed on `develop` when P4 starts, merge `develop` first and build on the hardened launchers.
  If not, build against the current constructor; the foreman re-runs P4's and P5's verify after the
  later merge.
- **P9** extracts the autoscaling client-invocation build in `cluster/remote.py` and adds the relay
  section to `docs/security.rst` and relay notes to `AGENTS.md`. **P9 starts only after the
  hardening cycle has landed and been merged into this branch.** If it has not, `hs-build` marks P9
  `blocked` with `blocked_reason: "awaiting authkey-handling-hardening on develop"` and stops for a
  foreman ruling (N4). If hardening already exposes one invocation function, P9 uses it and the
  extraction is dropped.
- Relay never touches `cluster/ssh.py`, `cluster/local.py` or `client.py`.

## Plan-gate rulings (foreman, 2026-11-04; PLAN §5)

- **N1 — R5's DEFERRED clause.** Accepted as proposed: the *handed over* reading. A `relay_result`
  row is marked `returned` only after `completed.put` returns; never on `QueueFull`, a timeout or a
  link error.
- **N2 — R9 across an upstream restart.** Accepted: R9 promises **landed** results only when the
  upstream process survived the outage (a partition). Across an upstream restart the relay still
  delivers its buffered results, and the restarted server refuses them `STALE` by design (it
  reverted every in-flight row at start).
- **N3 — R21's evidence build.** Evidence from any head at or after P6 is accepted; it is re-run on
  the final head if the allocation allows.
- **N4 — Fleet API under the security cycle.** The foreman asks the hardening cycle's plan to put the
  autoscaling client invocation behind one function. P9 stays gated as planned (starts only after
  hardening has landed and been merged here).
- **N5 — Calendar.** Maintainer: *"Everything, accept the risk."* All ten phases target the last
  pre-release before about 2026-11-13. Review starts after P5 (the delegation-core checkpoint).
- **N6 — Ten phases.** Accepted.

## Build-day estimates

| Phase | Estimate | Note |
|---|---|---|
| P1 | 0.5 | small model change, red-first test exists in shape |
| P2 | 1.0 | highest-risk plumbing (import-time binding, re-exec) |
| P3 | 1.0 | new FSM, insert transaction, upstream TLS |
| P4 | 1.0–1.5 | outbox, stamp restore, tz, fleet composition, first loopback |
| P5 | 0.5–0.75 | env scrub, two-relay loopback, failover case, full suite |
| P6 | 0.75–1.0 | coupled core (`server.py`, `model.py`, `heartbeat.py`) |
| P7 | 0.5 | parallel with P8 |
| P8 | 1.0–1.25 | link workers, backoff, forwarder tests |
| P9 | 0.75–1.0 | per-zone identities and fleets, docs |
| P10 | 0.25 | after the external run |

---

## Phase P1 — Late-confirm fence
**Satisfies:** R19, R20 · **Depends on:** — · PLAN §2.4
**Goal:** a confirmation applied to a completed row leaves `client_id`/`client_host` as the
result wrote them, keeping the contended-write discipline.

- [ ] Build precondition above (as ruled: P1 builds on the current base; its zone items are deferred).
- [ ] Red first: `tests/test_writeback.py::test_late_confirm_keeps_leaf_attribution` (submit,
      dispatch one, receive a result attributed to a leaf, then confirm under another id; assert
      the leaf attribution survives). It fails on the unmodified tree for the right reason.
- [ ] `data/model.py`: the confirm statement gains `completion_time IS NULL`; the zero-row
      re-read on the confirm path reads `completion_time` and classifies missing / complete /
      open. *Complete* is settled: no re-run, no WARNING, one DEBUG line. Revert is unchanged.
- [ ] `tests/test_contended_recovery.py::test_late_confirm_on_complete_row_is_not_contention`
      (`contention.lose(id, 1)`; exactly one UPDATE and one SELECT; no WARNING).
- [ ] PG companion in `tests/test_contended_write.py` (skipped without a PostgreSQL URL): the
      confirm waits on a holder that sets `completion_time`, and classifies *complete*.
- [ ] Existing confirm tests pass unmodified.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/data/model.py`, `tests/test_writeback.py`,
  `tests/test_contended_recovery.py`, `tests/test_contended_write.py`.

## Phase P2 — `hs relay` process and the private database
**Satisfies:** R1, R10, R18, R20 · **Depends on:** P1 · PLAN §2.1, §2.9
**Goal:** `hs relay` starts a forever-mode local server on a private SQLite file whatever the
configured provider, and stops cleanly; nothing upstream yet.

- [ ] `core/config.py`: `[relay]` and `[relay.upstream]` defaults; `RELAY_PROCESS` and the
      wholesale `config['database']` swap (marker or `hs|hyper-shell relay` argv), with
      `synchronous=FULL`; host-scoped default path under the site lib dir.
- [ ] `src/hypershell/relay/__init__.py`: `RelayApp` (cmdkit `App`, shared exception mapping,
      `exit_status` constants), re-exec when `RELAY_PROCESS` is false, sidecar `fcntl` lock,
      refuses `--no-confirm`; `RelayThread` composing (for now) a local `ServerThread`
      (`forever_mode=True`, `max_retries=0`, small `poll`) with a defined stop order.
- [ ] `src/hypershell/relay/store.py`: `RelayBase` (own `MetaData`), `relay_task`, `relay_result`,
      created with `upgrade_schema(engine, metadata=RelayBase.metadata)`.
- [ ] `__init__.py` (`commands`, `APP_HELP`, `__all__`); `core/logging.py` `DISTRIBUTED_ROLES`
      gains `relay`.
- [ ] Docs in the same commit: `docs/_include/relay_{usage,help,desc}.rst`, `docs/cli/relay.rst`
      (+ `docs/cli/index.rst`), `docs/_include/config_param_ref.rst` (`[relay]`); completions in
      `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`.
- [ ] `tests/test_relay_db.py` (subprocess, `do_connect` recorder): PostgreSQL config on a closed
      port and a second SQLite primary — only the relay file is opened, primary not created,
      `psycopg` not imported, `Task.__table__.schema is None`; the side tables never appear in a
      primary initialised by `hs initdb`; the lock refuses a second relay; the section builder over
      sqlite/postgres/timescale/url configs.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/core/config.py`, `src/hypershell/relay/`, `src/hypershell/__init__.py`,
  `src/hypershell/core/logging.py`, `docs/`, `share/`, `tests/test_relay_db.py`.

## Phase P3 — Upstream pull
**Satisfies:** R2, R3, R8, R17, R20 · **Depends on:** P2 · PLAN §2.2
**Goal:** the relay requests work as one ordinary client for its zone, inserts it durably, then
confirms; it never pulls past the backlog bound.

- [ ] `relay/upstream.py`: identity `uuid5(relay instance, zone name)`, logged at start; the
      puller FSM (`START, GATE, REQUEST, INSERT, CONFIRM, FINAL, HALT`) with finite waits and
      `check_signal()` in GATE; request text built here with `PROTOCOL` from `core/dispatch.py`;
      an error reply or `QueueVersionError` exits non-zero with the client's error; `shutdown:
      true` begins drain.
- [ ] GATE: backlog vs `connected site clients × local bundlesize`; request size
      `min(bound − backlog, upstream bundlesize)`.
- [ ] `relay/store.py` insert: upsert task (stamps and client fields cleared) + `relay_task`
      (verbatim `schedule_time` text, `server_id`, `server_host`, identity) in one transaction;
      aware datetimes to relay-local time; confirm only after commit.
- [ ] Upstream `QueueClient` built from `[relay.upstream]` (host, port, auth, TLS `cafile` /
      `fingerprint`); `--no-tls` affects the upstream hop only.
- [ ] Heartbeat FSM: one `Heartbeat.new(uuid=<identity>)` per beat at `[relay.upstream] heartrate`.
- [ ] Tests (`tests/test_relay_connector.py`, `tests/test_relay_fsm.py`) against a fake upstream:
      confirm follows the insert commit; a failed insert is never confirmed; no request at or
      above the bound, one below it; a re-dispatched id upserts and refreshes its stamp; version
      error exit; re-enter on timeout, halt on halt and on signal.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/relay/`, `tests/test_relay_connector.py`, `tests/test_relay_fsm.py`,
  `docs/_include/relay_*` and `share/` if options change.

## Phase P4 — Site run and return path
**Satisfies:** R1, R4, R5, R6, R16, R20 · **Depends on:** P3 · PLAN §2.3
**Goal:** pulled tasks run on a site fleet launched with a fresh site key and come back upstream
attributed to the leaf with the upstream `schedule_time`.

- [ ] Security-cycle overlap rule for P4 (above).
- [ ] `relay/site.py`: replace P2's bare `ServerThread` with `AutoScalingCluster(source=None,
      max_retries=0, poll, bind, launcher, launcher_args, policy='fixed',
      init_size=min_size=max_size=N, tls=<local>, zone=<name>)`; no edit to `cluster/`.
- [ ] Returner FSM (`START, COLLECT, SEND, MARK, FINAL, HALT`): move completed rows into
      `relay_result` in one transaction; restore `schedule_time`/`server_id`/`server_host` from
      `relay_task`; give naive timestamps the relay tz; bundle by bundlesize/bundlewait; mark
      `returned` only after `completed.put` returns; prune `returned` after retention.
- [ ] Tests (`tests/test_relay_return.py`): restored record APPLIED upstream with the leaf id;
      replay DUPLICATE; return after revert STALE; put failure leaves the row pending; a failed
      local row does not hold the next local group; retention prune keeps the file size-stable.
- [ ] `tests/test_relay_workload.py::test_one_relay_end_to_end`: upstream `hs server` and one
      `hs relay` (launcher `env`, 2 site clients) as separate processes; a grouped workload with
      a failing-then-retried task; every task completed once upstream; no `attempt` beyond the
      upstream retry; every completed `client_id` is a site client, never the relay identity.
- [ ] Drive once by hand in a throwaway site and record it in the commit body:
      `.agents/factory/bin/temp_site.sh sh -c "…hs server --forever… & …hs relay… & …; hs list"`.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/relay/`, `tests/test_relay_return.py`, `tests/test_relay_workload.py`.

## Phase P5 — Credential fence, two relays, failover (core complete)
**Satisfies:** R3, R6, R17, R20 · **Depends on:** P4 · PLAN §2.3, §2.6 (failover)
**Goal:** the upstream key reaches no site client or task; two relays share a grouped workload;
killing one relay fails its work over to the other with no task lost.

- [ ] Env scrub before the fleet starts: `HYPERSHELL_RELAY_*` names and any variable whose value
      contains the upstream key; no upstream key on any argv.
- [ ] `tests/test_relay_launch.py`: the scrubbed environment and the built client invocation
      exclude the key even when it is placed in the relay's environment on purpose.
- [ ] `tests/test_relay_workload.py::test_two_relays_grouped_workload_with_retry`: one upstream
      (`hs server -r 1 -b 4 --restart`), two relays with two site clients each; 3 groups, one
      failing-then-retried task in a shared zone; all complete once; both sites ran work; group
      order holds; psutil `cmdline`/`environ` scan of site clients and their tasks finds no
      upstream key; primary sentinels absent; every process exits 0 within the deadline (halt on
      upstream shutdown).
- [ ] `::test_failover_reverts_and_completes_elsewhere` (evict 10 s, short heartrate): SIGKILL relay
      B once rows are confirmed to it; its identity is evicted, its rows revert and complete at
      site A; no task lost.
- [ ] Full suite passes.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/relay/`, `tests/test_relay_launch.py`, `tests/test_relay_workload.py`.

## Phase P6 — Roster forwarding
**Satisfies:** R7, R6, R20 · **Depends on:** P5 · PLAN §2.7
**Goal:** leaf clients appear upstream attributed to their site, are never evicted on their own,
and disconnect with their relay; `count_connected` and the rate math stay correct.

- [ ] `core/heartbeat.py`: optional `site` and `roster`, packed only when set, read with `.get`.
- [ ] `data/model.py`: `Client.site` (TEXT) and `Client.relay` (UUID), nullable, through #69's
      upgrade step; `count_connected` excludes `relay = id`.
- [ ] `server.py` HeartMonitor: relay identity rows written with `site` and `relay = id`; roster
      diff adds/re-registers/disconnects leaves without entering `beats`; holder eviction or
      FINISHED disconnects its roster in one statement; #29 re-registration refreshes
      `server_id`/`server_host`; one ERROR when a `site` beat arrives under `no_confirm` or
      `in_memory`.
- [ ] Relay heartbeat FSM sends `site` on every identity and the roster (local `Client` table) on
      the holder.
- [ ] `tests/test_relay_roster.py` (HeartMonitor on stand-in queues + `task_db`): leaf registered
      with site; leaf disconnect forwarded; relay eviction disconnects the roster; a leaf is never
      evicted on its own; `count_connected` counts leaves, not relays; a beat with no roster fields
      decodes; an existing pre-column database upgrades in place.
- [ ] Extend the two-relay workload: upstream `client` rows per site equal the leaves launched.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/core/heartbeat.py`, `src/hypershell/data/model.py`,
  `src/hypershell/server.py`, `src/hypershell/relay/`, `tests/test_relay_roster.py`,
  `tests/test_relay_workload.py`.

## Phase P7 — R21 run A: relay drive, smoke, hand back
**Satisfies:** R21, R20 · **Depends on:** P5, P6 · PLAN §6; [04 §2](research/04-evidence-and-tests.md)
**Goal:** a committed, site-neutral run script the foreman can run on a 4-node, 1-hour test
cluster allocation, proven by a local smoke, and an exact run matrix handed back.

- [ ] `spec/relay/drive/relay_drive.sh` (roles `upstream`, `relay`, `leaf`; `HSD_*` environment
      only; reuses `tests/cluster/build_archive.sh`; `drive.sh` untouched): node 0 upstream
      `hs server --forever -r 1` with evict 30 s; site A = nodes 1–2 (survives); site B = node 3
      (relay killed at `HSD_KILL_AT`, default 40 % of tasks completed; `HSD_FAILOVER=kill|pause|none`);
      shared zone; upstream key in a 0600 transport file, exported only into the `hs relay`
      process; relays log their identities to a transport file (never evidence).
- [ ] `spec/relay/drive/relay_launch.sh`: the launcher wrapper (step launch by default, which
      forwards the environment so check (e) bites; `ssh` and local modes); environment passed
      through untouched.
- [ ] Checks recorded as counts and booleans only (no host, user or path): (a) rows, retries,
      group order, executions vs kill snapshot; (b) completed rows name leaves of the site that ran
      them, upstream `client` rows per site; (c) upstream connections from relays only, zero from
      any leaf process; (d) relay B identity evicted, snapshot completed at site A; (e) no upstream
      key in any leaf process's `cmdline`/`environ` (`procs_scanned > 0`); R10 sentinel and
      R8 peak backlog.
- [ ] `spec/relay/drive/relay_judge.py` (stdlib only) + `tests/test_relay_judge.py` (synthetic
      records, loads the judge by path).
- [ ] `spec/relay/drive/relay_smoke.sh`: one host, one upstream, two relays (own ports, own site
      dirs), M = 300, 3 groups, one retry, evict 10 s, kill variant, then the judge and a host/user
      grep. It never deletes: it prints its scratch path for the foreman.
- [ ] Hand-back note in this file: run matrix (r1 kill, r2 pause, r3 no-failover control), the
      exact `HSD_*` values, the 54-minute guard, evidence layout
      `spec/relay/evidence/cluster/<run_id>/{upstream,relay-A,relay-B,leaf-*}.json`.
- **Verify:** see frontmatter.
- **Touches:** `spec/relay/drive/`, `tests/test_relay_judge.py`.

## Phase P8 — Outage ride-through and restart durability
**Satisfies:** R9, R5, R20 · **Depends on:** P6 · PLAN §2.5
**Goal:** while upstream is unreachable the site keeps running and results stay durable; on
reconnection they are handed over; a relay restart loses nothing.

- [ ] `core/reconnect.py::UpstreamLink`: link worker per role with a call deadline; transport vs
      fatal classification; full-jitter backoff (1 s → 60 s) on the halt event; epoch; logging
      cadence and the `evict_hint` warning; injected clock/rng/sleep. No `core/queue.py` change.
- [ ] Puller, returner, heartbeat: `WAIT_LINK` state (1 s re-entry); on up: heartbeat all
      identities → replay confirms only for an outage shorter than `evict_hint` → drain outbox →
      pull.
- [ ] Upstream restart detection by `server_id` change: delete unstarted local rows with the old
      `server_id`.
- [ ] `tests/test_relay_link.py` (unit, injected clock): backoff sequence, classification,
      cadence, worker retirement on overrun, fatal exit.
- [ ] `tests/test_relay_outage.py` (integration, pass/close/blackhole TCP forwarder between relay
      and upstream, evict 600 s): partition after local completion, results land after restore;
      blackhole stall detected within the deadline; SIGKILL during a partition, restart on the same
      file, restore: unreturned rows land (APPLIED or DUPLICATE), none lost.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/core/reconnect.py`, `src/hypershell/relay/`,
  `tests/test_relay_link.py`, `tests/test_relay_outage.py`.

## Phase P9 — Multi-zone relays and relay docs
**Satisfies:** R2, R8, R18, R20 · **Depends on:** P8 · PLAN §2.6, §2.8
**Goal:** one relay serves several zones, each as its own upstream identity and its own site fleet;
failover reverts and unstages every zone it held; the docs carry the relay trust model and
non-goals.

- [ ] Security-cycle overlap rule for P9 (above): starts only after hardening has landed and been
      merged.
- [ ] `[relay] zone` becomes a list (`--zone` repeatable); `[relay.zone.<name>]` overrides
      `launcher`, `launcher_args`, `size`.
- [ ] Puller cycles zones with a short `wait`; per-zone gate; confirm under the requesting zone's
      identity; heartbeat every identity, roster on the holder.
- [ ] `cluster/remote.py`: minimal, behaviour-preserving extraction of `AutoScalingCluster`'s
      client-invocation build into a module function, used by `AutoScalingCluster` and by relay
      for extra zone fleets (`AutoScalerThread`); skipped if hardening already exposes one.
      `tests/test_autoscale.py` and `tests/test_cluster.py` pass unmodified.
- [ ] Tests: `test_relay_connector.py::test_each_zone_requests_independently` (demand holds one
      entry per identity; Z2 dispatcher untouched); `test_relay_workload.py::
      test_multizone_relay_takes_only_its_zones` (a zone with no client stays unscheduled);
      failover of a two-zone relay reverts and unstages both zones.
- [ ] Docs: `docs/security.rst` gains a relay section (two auth domains, TLS pinning across the WAN
      hop, key exposure confined within each site); `docs/cli/relay.rst` states the non-goals (no
      throughput gain, no cross-site cancellation or output retrieval, no site-topology
      autoscaling, synchronised clocks needed for eviction); `docs/database.rst` (relay-local file,
      node-local disk advice); help snippets and completions for the multi-zone options.
- [ ] `AGENTS.md`: relay in the repository map and footguns (one relay per process; import-time
      database swap; relay-only tables; no in-process dial of the local server); `DISTRIBUTED_ROLES`
      location (D11). `invariants.md` §10/§11 notes in lockstep.
- [ ] Full suite passes.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/relay/`, `src/hypershell/cluster/remote.py` (extraction only),
  `src/hypershell/core/config.py`, `docs/`, `share/`, `AGENTS.md`, `.agents/factory/invariants.md`,
  tests above.

## Phase P10 — R21 run B: judge the evidence
**Satisfies:** R21 · **Depends on:** P7, P9
**Goal:** the foreman's multi-node records pass every check (a)–(e).

- [ ] External wait: blocked until `spec/relay/evidence/cluster/` exists (see field reference).
- [ ] Re-scrub the delivered records (no host, user, path, key); run the judge; write
      `spec/relay/evidence/cluster/SUMMARY.txt` with each run's verdict and the build commit it ran.
- **Verify:** see frontmatter.
- **Touches:** `spec/relay/evidence/`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable; the build precondition and the
   security-cycle overlap rules above.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and
   report.
