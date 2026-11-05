---
slug: relay
title: 'HyperShell Relay: two-level scheduling across administrative sites'
kind: feature
appetite: big
status: blocked
branch: feature/relay
base: develop
current_phase: P7
last_updated: '2026-11-05'
phases:
- id: P1
  name: 'Late-confirm fence: a confirmation never rewrites a completed row''s attribution'
  status: done
  satisfies:
  - R19
  - R20
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: test -f src/hypershell/core/dispatch.py && grep -q _get_dispatch src/hypershell/core/queue.py
    && grep -q CONTENDED_RERUNS src/hypershell/data/model.py && uv run pytest -v tests/test_writeback.py
    tests/test_contended_recovery.py tests/test_contended_write.py tests/test_zone_dispatch.py
    tests/test_server_fsm.py
- id: P2
  name: 'hs relay process: [relay] config, private SQLite bound at import, local server
    up and down'
  status: done
  satisfies:
  - R1
  - R10
  - R18
  - R20
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_relay_db.py tests/test_main.py tests/test_completions.py
    tests/test_logging.py tests/test_initdb.py && uv run hs relay --help > /dev/null
    && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs
- id: P3
  name: 'Upstream pull: zone identity, backlog gate, durable insert before confirm,
    upstream TLS'
  status: done
  satisfies:
  - R2
  - R3
  - R8
  - R17
  - R20
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_relay_connector.py tests/test_relay_fsm.py tests/test_relay_db.py
    tests/test_dispatch_version.py
- id: P4
  name: 'Site run and return path: fleet, no local retry, outbox, leaf attribution,
    one-relay loopback'
  status: done
  satisfies:
  - R1
  - R4
  - R5
  - R6
  - R16
  - R20
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_return.py tests/test_relay_connector.py
    tests/test_relay_fsm.py && uv run pytest -v tests/test_relay_workload.py -k one_relay
- id: P5
  name: Credential fence and two-relay loopback with failover (delegation core complete)
  status: done
  satisfies:
  - R3
  - R6
  - R17
  - R20
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_launch.py tests/test_relay_workload.py
    && uv run pytest -v
- id: P6
  name: 'Roster forwarding: heartbeat site/roster, client site/relay columns, HeartMonitor
    cascade'
  status: done
  satisfies:
  - R7
  - R6
  - R20
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_relay_roster.py tests/test_server_fsm.py tests/test_server.py
    tests/test_upgrade_cli.py tests/test_zone_dispatch.py tests/test_autoscale.py
    tests/test_relay_workload.py
- id: P7
  name: 'R21 run A: relay drive, launcher wrapper, judge, local smoke, hand back (external
    wait follows)'
  status: pending
  satisfies:
  - R21
  - R20
  depends_on:
  - P5
  - P6
  parallel: true
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_judge.py && sh spec/relay/drive/relay_smoke.sh
- id: P8
  name: 'Outage ride-through and restart durability: UpstreamLink, backoff, redelivery'
  status: pending
  satisfies:
  - R9
  - R5
  - R20
  depends_on:
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_link.py tests/test_relay_outage.py tests/test_relay_fsm.py
    tests/test_relay_return.py tests/test_relay_workload.py
- id: P9
  name: Multi-zone relays (one upstream identity per zone), per-zone fleets, relay
    docs and trust model
  status: pending
  satisfies:
  - R2
  - R8
  - R18
  - R20
  depends_on:
  - P8
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs
- id: P10
  name: 'R21 run B: judge the external multi-node evidence'
  status: pending
  satisfies:
  - R21
  depends_on:
  - P7
  - P9
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python spec/relay/drive/relay_judge.py spec/relay/evidence/cluster
review:
  last_reviewed_commit: d6e9d7d
  verdict: changes-requested
  blocked_reason: 'review cycle 1: remediation round (A1-A4, B1-B8) before P7'
  cycle: 1
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
- **Met 2026-11-05.** `origin/develop` `c6869f2` (Z1 #76, Z2 #78) is merged in and every deferred P1
  item is green; see *Merge round* after P2. P3 is unblocked.

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

**Outcome (2026-11-05, merge round):** the three P1 items ran green on Python 3.12 and 3.14. The P3
item runs with P3's gate; it already passes on the merged tree. The P6 items run with P6.

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

- [x] Build precondition above (as ruled: P1 builds on the current base; its zone items are deferred).
- [x] Red first: `tests/test_writeback.py::test_late_confirm_keeps_leaf_attribution` (submit,
      dispatch one, receive a result attributed to a leaf, then confirm under another id; assert
      the leaf attribution survives). It fails on the unmodified tree for the right reason.
- [x] `data/model.py`: the confirm statement gains `completion_time IS NULL`; the zero-row
      re-read on the confirm path reads `completion_time` and classifies missing / complete /
      open. *Complete* is settled: no re-run, no WARNING, one DEBUG line. Revert is unchanged.
- [x] `tests/test_contended_recovery.py::test_late_confirm_on_complete_row_is_not_contention`
      (`contention.lose(id, 1)`; exactly one UPDATE and one SELECT; no WARNING).
- [x] PG companion in `tests/test_contended_write.py` (skipped without a PostgreSQL URL): the
      confirm waits on a holder that sets `completion_time`, and classifies *complete*
      (`test_contended_late_confirm_on_complete_row_is_settled`).
- [x] Existing confirm tests pass unmodified — **amended:** all but one. The PG-only
      `test_contended_confirm_records_client` had its holder set `completion_time` and asserted the
      confirm still stamped the client, which is exactly what R19 forbids (its scenario is the new
      companion's). It now holds the open row with a concurrent client write and asserts the
      waited confirm stamps the client, so the open-row path keeps its PostgreSQL coverage.
- [x] `AGENTS.md` (*Task lifecycle contract*) and `invariants.md` §1 gain one line each on the
      confirm fence (the code moved; AGENTS is ground truth).
- **Verify:** see frontmatter, minus the deferred zone items (Build precondition).
- **Verified 2026-11-04** on the base `bd2faf9` (Python 3.12, a frozen venv first on `PATH` in place
  of `uv run`): `grep -q CONTENDED_RERUNS` ok; `pytest -v tests/test_writeback.py
  tests/test_contended_recovery.py tests/test_contended_write.py` 37 passed, 6 skipped (the
  PostgreSQL tests; no server here). Deferred: the presence guard, `tests/test_zone_dispatch.py`,
  `tests/test_server_fsm.py`. CLI drive: `seq 50 | hsx -t 'echo {}' -N4` in a throwaway site gave
  50 output lines, 50 tasks, 50 succeeded, 0 failed, 0 with no `client_id`.
- **Touches:** `src/hypershell/data/model.py`, `tests/test_writeback.py`,
  `tests/test_contended_recovery.py`, `tests/test_contended_write.py`, `AGENTS.md`,
  `.agents/factory/invariants.md`.

## Phase P2 — `hs relay` process and the private database
**Satisfies:** R1, R10, R18, R20 · **Depends on:** P1 · PLAN §2.1, §2.9
**Goal:** `hs relay` starts a forever-mode local server on a private SQLite file whatever the
configured provider, and stops cleanly; nothing upstream yet.

- [x] `core/config.py`: `[relay]` and `[relay.upstream]` defaults; `RELAY_PROCESS` and the
      wholesale `config['database']` swap (marker or `hs|hyper-shell relay` argv), with
      `synchronous=FULL`; host-scoped default path under the site lib dir.
- [x] `src/hypershell/relay/__init__.py`: `RelayApp` (cmdkit `App`, shared exception mapping,
      `exit_status` constants), re-exec when `RELAY_PROCESS` is false, sidecar `fcntl` lock,
      refuses `--no-confirm`; `RelayThread` composing (for now) a local `ServerThread`
      (`forever_mode=True`, `max_retries=0`, small `poll`) with a defined stop order.
- [x] `src/hypershell/relay/store.py`: `RelayBase` (own `MetaData`), `relay_task`, `relay_result`,
      created with `upgrade_schema(engine, metadata=RelayBase.metadata)`.
- [x] `__init__.py` (`commands`, `APP_HELP`, `__all__`); `core/logging.py` `DISTRIBUTED_ROLES`
      gains `relay`.
- [x] Docs in the same commit: `docs/_include/relay_{usage,help,desc}.rst`, `docs/cli/relay.rst`
      (+ `docs/cli/index.rst`, and `docs/manual.rst`'s synopsis and usage section),
      `docs/_include/config_param_ref.rst` (`[relay]`); completions in
      `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`.
- [x] `tests/test_relay_db.py` (subprocess, `do_connect` recorder): PostgreSQL config on a closed
      port and a second SQLite primary — only the relay file is opened, primary not created,
      `psycopg` not imported, `Task.__table__.schema is None`; the side tables never appear in a
      primary initialised by `hs initdb`; the lock refuses a second relay; the section builder over
      sqlite/postgres/timescale/url configs.
- **Amendments (2026-11-04, build):**
  - **CLI grows with the phases.** P2's `hs relay` takes only the options it implements:
    `--bind`, `--port`, `--database` (and refuses `--no-confirm`, a hidden option). The upstream
    options (`-H`, `-p`, `--no-tls`) arrive with P3 and the fleet options (`--zone`, `-N`,
    `--launcher`, `--launcher-args`) with P4, each with its help snippet and completions in that
    commit. The `[relay]`/`[relay.upstream]` config defaults are all in place now, as planned.
  - **`--database` re-executes once.** The database is bound at import, so a `--database` naming
    another file re-executes the relay with `HYPERSHELL_RELAY_DATABASE` set (an env-layer value,
    which P5's scrub of `HYPERSHELL_RELAY_*` removes before the fleet starts). A private
    `_HYPERSHELL_RELAY_REEXEC` marker stops a second re-exec.
  - **P2's stop order.** A stock server that never had a client waits for its first one, so a
    drained relay (USR1/USR2) would never exit. `RelayThread` stops the relay-local server itself
    once its scheduler has finished. P4 replaces this with the cluster's own shutdown (§2.9).
  - Default `[relay] bind` is `0.0.0.0` (site clients run on other nodes), default port `50002`.
- **Verify:** see frontmatter.
- **Verified 2026-11-04** on the base `bd2faf9` + P1 (Python 3.12, a frozen venv first on `PATH`
  in place of `uv run`): `pytest -v tests/test_relay_db.py tests/test_main.py
  tests/test_completions.py tests/test_logging.py tests/test_initdb.py` 140 passed; `hs relay
  --help` exit 0; `bash -n` and `zsh -n` on the completions ok. No deferred items. CLI drive:
  `hs relay --bind 127.0.0.1 --port <free>` in a throwaway site came up, exited 0 on SIGUSR1 within
  the deadline, left the relay database (`task`, `client`, `source`, `relay_task`, `relay_result`)
  under `<site lib>/relay/<host>.db`, and created no primary database. The PostgreSQL-config
  test passes on a host with no `libpq`, so the relay process never needs the driver.
- **Full suite at the end of P2** (Python 3.12, `pytest -v`): 713 passed, 80 skipped (PostgreSQL and
  other server-backed tests), no failure, no rerun.
- **Touches:** `src/hypershell/core/config.py`, `src/hypershell/relay/`, `src/hypershell/__init__.py`,
  `src/hypershell/core/logging.py`, `docs/`, `share/`, `tests/test_relay_db.py`,
  `tests/test_main.py`, `tests/test_logging.py`.

## Merge round — `origin/develop` `c6869f2` (2026-11-05)
**Not a phase.** It brings Z1 (#76), Z2 (#78), #77 and the 2.8.4 back-merge onto relay and runs the
deferred P1 verifies. P3 is not started here.

- **Merge base** `ab16902` (2.9.0a3), not `bd2faf9` as the precondition records. The trees are
  identical: `bd2faf9` is the 2.8.4 back-merge resolved wholly to develop's side. The merge commit
  is signed; develop's `spec/` directories come in verbatim, and outside `spec/relay/` the merged
  tree differs from `c6869f2` only by relay's own P1/P2 changes.
- **Conflicts (2).** `ROADMAP.md`: relay's count-free Part II lead, `adopted:relay` status and
  relay-topology entry kept; develop's #70 wording and its note on the two unshaped seeds taken.
  `data/model.py`: the contended-write helper had grown two fences on one UPDATE, relay's
  `open_only` (confirm) and Z2's `pending` (staged revert).
- **The fence on develop's paths.** One `pending` fence now serves both. Under `pending` the UPDATE
  matches only while `completion_time IS NULL`, and the zero-row look-up reads `completion_time` by
  id, not `id` under the same filter, so one SELECT separates *gone* / *complete* / *still
  pending*. *Complete* is settled (one DEBUG line, no re-run, no warning). *Pending* re-runs up to
  `CONTENDED_RERUNS` (#75). *Gone* is skipped under `pending` and raises `StaleDataError` under
  `pending, strict`. `confirm_all` passes `pending=True, strict=True`. `revert_staged`'s call is
  unchanged (`pending=True`), and so are its outcomes and statement counts, which Z2's tests
  pin. #70's `(id, schedule_time)` result writeback and #73's prebuilt statements are a separate
  path and are not touched. Confirmations still reach the database only through `Confirm` →
  `confirm_all`. Z2's staging and dispatch write no `client_id`, so no second confirm path exists.
- **P2 against the new server.** No code change. `ServerThread`'s constructor kept every argument
  relay passes. Zones arrive with clients, not the server, and Z1's queue server is built inside
  `ServerThread`. The P2 stop order still holds: `HeartMonitor` keeps its start-up wait for a
  first client. The relay database now also carries Z2's `zone` table, as every primary does.
- **Gates (Python 3.12 / 3.14, venvs from `uv sync --frozen` on `PATH` in place of `uv run`):**
  - Full suite: 925 passed, 85 skipped / 922 passed, 88 skipped; no failure.
  - Relay, zone, dispatch, server-FSM, contended-write and writeback files: 180 passed, 35
    skipped on 3.12 and the same on 3.14. The skips need a PostgreSQL server.
  - P1 full verify, including the deferred items: guard ok, 78 passed, 8 skipped on each.
  - P2 verify: 140 passed on each; `hs relay --help` ok; `bash -n`/`zsh -n` ok.
  - `unsupported_internals()` returns `[]` on both.
  - Loopback smoke, P2's shape: in a throwaway site on both versions, `hs relay --bind 127.0.0.1
    --port <free>` listened, exited 0 on SIGUSR1 within 30 s, and left only the relay database
    (`client, relay_result, relay_task, source, task, zone`). No primary was created.
- **Touches:** `ROADMAP.md`, `src/hypershell/data/model.py` (both in the merge commit),
  `spec/relay/TECH.md`.

## Phase P3 — Upstream pull
**Satisfies:** R2, R3, R8, R17, R20 · **Depends on:** P2 · PLAN §2.2
**Goal:** the relay requests work as one ordinary client for its zone, inserts it durably, then
confirms; it never pulls past the backlog bound.

- [x] `relay/upstream.py`: identity `uuid5(relay instance, zone name)`, logged at start; the
      puller FSM (`START, GATE, REQUEST, INSERT, CONFIRM, FINAL, HALT`) with finite waits and
      `check_signal()` in GATE; request text built here with `PROTOCOL` from `core/dispatch.py`;
      an error reply or `QueueVersionError` exits non-zero with the client's error; `shutdown:
      true` begins drain.
- [x] GATE: backlog vs `connected site clients × local bundlesize`; request size
      `min(bound − backlog, upstream bundlesize)`.
- [x] `relay/store.py` insert: upsert task (stamps and client fields cleared) + `relay_task`
      (verbatim `schedule_time` text, `server_id`, `server_host`, identity) in one transaction;
      aware datetimes to relay-local time; confirm only after commit.
- [x] Upstream `QueueClient` built from `[relay.upstream]` (host, port, auth, TLS `cafile` /
      `fingerprint`); `--no-tls` affects the upstream hop only. CLI `-H/--upstream-host`,
      `-p/--upstream-port`, `--no-tls` with help snippets and completions (P2 amendment).
- [x] Heartbeat FSM: one `Heartbeat.new(uuid=<identity>)` per beat at `[relay.upstream] heartrate`.
- [x] Tests (`tests/test_relay_connector.py`, `tests/test_relay_fsm.py`) against a fake upstream:
      confirm follows the insert commit; a failed insert is never confirmed; no request at or
      above the bound, one below it; a re-dispatched id upserts and refreshes its stamp; version
      error exit; re-enter on timeout, halt on halt and on signal.
- **Amendments (2026-11-05, build):**
  - **"Connected" is the relay-local server's own registrations.** The gate counts `client` rows
    with `server_id` = this relay instance and no `disconnected_at` (`store.count_connected`), not
    `Client.count_connected`, which keys on `Task.latest_server()` and reads zero until the
    relay-local server has scheduled a task, so the gate would never open. Rows left by an earlier
    relay run on the same file carry another `server_id` and do not count.
  - **Insert failures.** A failed insert (a SQLAlchemy error, e.g. a locked file) is rolled back
    whole, logged, and tried again after `poll`, up to `INSERT_ATTEMPTS` = 3; then the puller
    raises `RelayInsertError` and the relay exits non-zero. The bundle is never confirmed either
    way (upstream it stays in the cut-to-confirm window, a GOAL non-goal).
  - **Local `part` is 0.** The partition index is the relay database's own; the received value
    is not kept.
  - **The connect comes first.** `RelayThread` connects upstream before the relay-local server
    starts, so a version mismatch, a wrong key or a refused connection ends the relay before the
    site is touched (one CRITICAL line, as for a client). Until P8 an unreachable upstream at start
    is fatal. Consequently P2's process tests in `tests/test_relay_db.py` now run against a stand-in
    upstream (a queue server in the test process, configured through `HYPERSHELL_RELAY_UPSTREAM_*`
    with TLS off); their assertions are unchanged.
  - **Ending upstream.** On an upstream `shutdown` reply the puller ends and the relay waits for its
    local backlog to finish before it stops the relay-local server (§2.9); a drain signal ends the
    puller and the local scheduler together, as in P2. The FINISHED heartbeat is sent only when
    every received task's result has been handed over (`store.count_held` = 0, keyed on
    `relay_result.state = returned`, P4's outbox). Otherwise the relay logs a WARNING and stops
    beating, leaving its identity to upstream eviction, which reverts the tasks it still holds: a
    FINISHED beat would end the registration without that revert and strand them. In P3 nothing is
    returned yet, so a relay that pulled anything never sends FINISHED.
  - **Zone from configuration.** P3 reads `[relay] zone` (unset → default zone, which requests with
    no `zone`, as a client does); `--zone` arrives with P4 as planned.
  - The relay's exception map gains the client's transport entries (`EOFError`,
    `ConnectionResetError`, `ConnectionRefusedError`, `AuthenticationError`).
- **Verify:** see frontmatter.
- **Red first (2026-11-05):** the two new test files failed at collection on the unmodified tree
  (`ModuleNotFoundError: hypershell.relay.upstream`); after the build, P2's process tests in
  `tests/test_relay_db.py` failed 6 of 27 with `ConnectionRefusedError` (no upstream) until they
  were given the stand-in upstream above.
- **Verified 2026-11-05** (venvs from `uv sync --frozen` on `PATH` in place of `uv run`):
  - P3 verify: 64 passed on 3.12, and inside the 3.14 set below.
  - Full suite, 3.12: 957 passed, 85 skipped (PostgreSQL and other server-backed tests), no failure.
  - Relay, zone, dispatch, server-FSM, contended-write and writeback files, 3.14: 206 passed, 11
    skipped.
  - `tests/test_main.py`, `tests/test_completions.py` pass on both; `hs relay --help` ok;
    `bash -n`/`zsh -n` ok; `unsupported_internals()` is `[]` on both.
  - Loopback smoke, both versions, throwaway site, upstream `hs server --forever` with TLS
    (`<auto>` material shared in the site), 20 tasks: (A) `hs relay -H … -p …` with no site
    client registered its logged identity upstream (`client` row connected), was dispatched and
    confirmed 0 tasks, exited 0 on SIGUSR1, and its FINISHED beat disconnected the row (not
    evicted); the relay file held no task row and no primary was touched. (B) a relay run from a
    small driver (to read the minted site key) with one `hs client` on the relay-local port: the
    upstream dispatched 20 requests of 1 task each, all 20 rows confirmed upstream to the relay
    identity and none completed upstream (no return path until P4); the relay file holds 20 rows
    completed with exit 0 by the site client, and all 20 `relay_task` stamps equal the upstream
    `schedule_time` text and `server_id`.
- **Touches:** `src/hypershell/relay/` (`__init__.py`, `store.py`, new `upstream.py`),
  `tests/test_relay_connector.py`, `tests/test_relay_fsm.py`, `tests/test_relay_db.py` (stand-in
  upstream), `docs/_include/relay_{usage,help,desc}.rst`, `docs/_include/config_param_ref.rst`,
  `docs/manual.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`. No zone code,
  `client.py`, `server.py` or `core/queue.py` edit.

## Phase P4 — Site run and return path
**Satisfies:** R1, R4, R5, R6, R16, R20 · **Depends on:** P3 · PLAN §2.3
**Goal:** pulled tasks run on a site fleet launched with a fresh site key and come back upstream
attributed to the leaf with the upstream `schedule_time`.

- [x] Security-cycle overlap rule for P4 (above). `fix/authkey-handling-hardening` has not landed on
      `develop` (`origin/develop` is `02b292a`, a version bump past the merged `c6869f2`), so P4
      builds against the current `AutoScalingCluster` constructor; the foreman re-runs P4's and P5's
      verify after the later merge.
- [x] `relay/site.py`: `site_cluster()` builds `AutoScalingCluster(source=None, max_retries=0, poll,
      bind, launcher, launcher_args, policy='fixed', init_size=min_size=max_size=N, tls=<local>,
      zone=<name>)`, replacing P2's bare `ServerThread`; no edit to `cluster/`.
- [x] CLI `--zone`, `-N/--size`, `--launcher`, `--launcher-args` with help snippets and
      completions (P2 amendment).
- [x] Returner FSM (`START, COLLECT, SEND, MARK, FINAL, HALT`): move completed rows into
      `relay_result` in one transaction; restore `schedule_time`/`server_id`/`server_host` from
      `relay_task`; give naive timestamps the relay tz; bundle by bundlesize/bundlewait; mark
      `returned` only after `completed.put` returns; prune `returned` after retention.
- [x] Tests (`tests/test_relay_return.py`): restored record APPLIED upstream with the leaf id;
      replay DUPLICATE; return after revert STALE; put failure leaves the row pending; a failed
      local row does not hold the next local group; retention prune keeps the file size-stable.
- [x] `tests/test_relay_workload.py::test_one_relay_end_to_end`: upstream `hs server` and one
      `hs relay` (launcher `env`, 2 site clients) as separate processes; a grouped workload with
      a failing-then-retried task; every task completed once upstream; no `attempt` beyond the
      upstream retry; every completed `client_id` is a site client, never the relay identity.
- [x] Drive once by hand in a throwaway site and record it in the commit body:
      `.agents/factory/bin/temp_site.sh sh -c "…hs server --forever… & …hs relay… & …; hs list"`.
- **Foreman rulings on P3's open decisions (2026-11-05), recorded as accepted:**
  1. *Gate count.* The gate counts the relay-local server's own client registrations
     (`server_id` = this relay instance). **Note for P9:** a multi-zone relay needs this count per
     zone (site clients of each zone's fleet); not built here.
  2. *FINISHED only when nothing is held* (`count_held` = 0). P4's returner marks rows `returned`
     once their put upstream returns, so a drained relay sends FINISHED; the one-relay workload
     asserts the final beat is FINISHED and upstream records a clean disconnect with no eviction.
  3. *An unreachable upstream at start is fatal* until P8.
  4. *Insert failure:* 3 attempts, then exit non-zero without confirming.
  5. *Smaller points:* local `part` = 0; the transport exception map; `[relay] zone` from
     configuration, now overridden by `--zone` (wired in this phase).
- **Amendments (2026-11-05, build):**
  - **Site lifecycle.** `RelayThread` starts the cluster thread and waits for its server to listen.
    It finishes when pulling is over (an upstream `shutdown` reply or a drain signal) and the local
    backlog is empty, or when the local scheduler has ended (a drain signal). The site is then
    stopped in order: the autoscaler is halted first (under the fixed policy it would relaunch the
    clients as they exit), then the local scheduler, and the local server finishes the stock way:
    it tells its clients to finish and waits for their FINISHED beats, so every leaf result reaches
    the relay database. A local server that never had a client is stopped outright (its heart
    monitor would wait for a first one). The returner then flushes and ends, and the FINISHED rule
    above applies. This replaces P2's stop order, as planned (§2.9).
  - **Outbox writes are serialised.** The puller's insert and the returner's move, mark and prune
    take one process lock in `relay/store.py`, so a re-dispatched id can never be moved or marked
    against a stamp it no longer has. A re-received id drops any result still held for its earlier
    assignment in the insert transaction, and a mark matches the exact record that was sent, so a
    result from a superseded assignment is never marked returned and never counts as handed over.
    The relay-local server writes only incomplete rows, which these writers never touch.
  - **Return bundles** use the upstream bundle size (`[relay.upstream] bundlesize`, the size the
    relay advertises) and `[relay] bundlewait`, as a client's collector uses its own `-b`/`-w`.
  - **New `[relay] retention`** (default 300 s, PLAN §2.3) in `core/config.py` and the config
    reference. Pruning runs from the returner at most once a minute (sooner with a shorter
    retention) and deletes each pruned result's `relay_task` stamp with it.
  - **`-N 0`** launches no site client; with no connected client the gate never opens, so the
    relay asks upstream for nothing. P3's process tests in `tests/test_relay_fsm.py` and
    `tests/test_relay_db.py` now pass `-N 0` (or `HYPERSHELL_RELAY_SIZE=0`); their assertions are
    unchanged.
  - **Site clients relaunch period** is `[autoscale] period` (the class's own knob).
  - **Loopback launcher in the tests.** Site clients dial the relay by host name
    (`AutoScalingCluster` builds `-H HOSTNAME`, unchanged). On the build host the name resolves to
    an address whose firewall completes TCP but never delivers it to `accept()`, and inside the
    sandbox it does not resolve at all. The workload test therefore launches site clients through a
    small wrapper around `env` that replaces the `-H` value with `127.0.0.1`; the relay binds
    loopback. The real dial-by-name path is the stock autoscaler's.
- **Verify:** see frontmatter.
- **Red first (2026-11-05):** on the unmodified tree `tests/test_relay_return.py` and
  `tests/test_relay_fsm.py` failed at collection (`ImportError: UpstreamReturner`), and
  `test_one_relay_end_to_end` failed on its deadline (the relay refused `-N`, so the upstream server
  never finished).
- **Verified 2026-11-05** (venvs from `uv sync --frozen` on `PATH` in place of `uv run`):
  - P4 verify, 3.12: 51 passed, then the one-relay workload 1 passed (about 18 s, TLS on both hops).
  - Full suite, 3.12: 977 passed, 85 skipped (PostgreSQL and other server-backed tests), no failure.
  - Relay, zone, dispatch, server-FSM, contended-write, writeback, main and completion files, 3.14:
    297 passed, 35 skipped.
  - `hs relay --help` ok; `bash -n`/`zsh -n` on the completions ok; `unsupported_internals()` is
    `[]` on both.
  - Loopback smoke and hand drive (`temp_site.sh`), 3.12 and 3.14: upstream `hs server --restart -r 1
    -b 4` with TLS (`<auto>`), 50 tasks in 2 groups with one failing on its first attempt, then
    `hs relay -N 2` through the loopback launcher. Both exited 0. Upstream: 51 rows, all completed
    (49 exit 0 at attempt 1, the failure exit 3 and retried, its retry exit 0 at attempt 2), every
    `client_id` one of the 2 site clients, never the relay identity; the only upstream `client`
    row is the relay identity, disconnected and not evicted; no stale or duplicate result warning
    and no eviction in the upstream log; group 1 scheduled after group 0 completed. Relay: final
    FINISHED beat, no task row left, 51 results `returned`. The same with `--zone gpu` (tasks
    submitted to that zone) on 3.12.
- **Touches:** `src/hypershell/relay/` (`__init__.py`, `store.py`, `upstream.py`, new `site.py`),
  `src/hypershell/core/config.py` (`[relay] retention`), `tests/test_relay_return.py`,
  `tests/test_relay_workload.py`, `tests/test_relay_fsm.py`, `tests/test_relay_db.py`,
  `docs/_include/relay_{usage,help,desc}.rst`, `docs/_include/config_param_ref.rst`,
  `docs/manual.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`. No zone code,
  `client.py`, `server.py`, `core/queue.py` or `cluster/` edit.

## Phase P5 — Credential fence, two relays, failover (core complete)
**Satisfies:** R3, R6, R17, R20 · **Depends on:** P4 · PLAN §2.3, §2.6 (failover)
**Goal:** the upstream key reaches no site client or task; two relays share a grouped workload;
killing one relay fails its work over to the other with no task lost.

- [x] Env scrub before the fleet starts: `HYPERSHELL_RELAY_*` names and any variable whose value
      contains the upstream key; no upstream key on any argv.
- [x] `tests/test_relay_launch.py`: the scrubbed environment and the built client invocation
      exclude the key even when it is placed in the relay's environment on purpose.
- [x] `tests/test_relay_workload.py::test_two_relays_grouped_workload_with_retry`: one upstream
      (`hs server -r 1 -b 4 --restart`), two relays with two site clients each; 3 groups, one
      failing-then-retried task in a shared zone; all complete once; both sites ran work; group
      order holds; psutil `cmdline`/`environ` scan of site clients and their tasks finds no
      upstream key; primary sentinels absent; every process exits 0 within the deadline (halt on
      upstream shutdown).
- [x] `::test_failover_reverts_and_completes_elsewhere` (evict 10 s, short heartrate): SIGKILL relay
      B once rows are confirmed to it; its identity is evicted, its rows revert and complete at
      site A; no task lost.
- [x] Full suite passes.
- **Foreman rulings on P4's open points (2026-11-05), recorded as accepted:**
  1. *Loopback launcher in tests.* Tests and local smokes launch site clients through the `env`
     wrapper that swaps `-H` to `127.0.0.1`. The real dial-by-name path is exercised by the R21
     multi-node runs (P7/P10), which the foreman runs. A `--advertise ADDR` option stays deferred;
     if P9 or the R21 run shows it is needed, it is a P9 item.
  2. *Stop order* (autoscaler, then scheduler, then drain): accepted. **Note for P8:** a site client
     killed without a FINISHED beat delays the relay's shutdown until the relay-local server evicts
     it (local evict, default 600 s).
  3. *Return bundles* (upstream bundle size, `[relay] bundlewait`) and *the `-N 0` leftover-task
     launch*: accepted. **Note for P8:** both are revisited with the outage work.
- **Security-cycle overlap, restated.** A concurrent hardening cycle
  (`fix/authkey-handling-hardening`, not yet on `develop`) changes how `AutoScalingCluster` launches
  clients: one `client_launch()` builds the invocation and the site key is handed over in the
  environment, not on `-k`. Relay does not depend on that branch now. When it lands, relay merges
  `develop` and re-runs P4's and P5's verify. `fence_site` reads the built argv from
  `cluster.autoscaler.machine.launcher`; if hardening moves it (or adds launch-time environment),
  the fence follows the new place, and the site key then legitimately sits in each site client's
  environment (it differs from the upstream key, which the fence and the scan check).
- **Amendments (2026-11-05, build):**
  - **The fence is `relay/site.py::fence_site`,** called by `RelayThread` once the upstream
    configuration (and so the key) is resolved and before the upstream connect, so a refusal ends
    the relay before anything starts. It (1) refuses (`CredentialFenceError`, exit
    `runtime_error`, one CRITICAL line that never names the key) a site client invocation carrying
    the key in any argument (launcher, launcher arguments, template); (2) refuses an exported task
    variable (`[export]`, merged into every launch by `AutoScaler.scale` from `load_task_env()`)
    carrying it, since that merge happens in `cluster/` and is not scrubbable from relay; (3)
    scrubs `os.environ` of every `HYPERSHELL_RELAY_*` name, every variable whose name or value
    contains the key (an empty key matches nothing), and `HYPERSHELL_CONFIG_FILE` when the file it
    names holds a relay table or dotted relay key, holds the key, or cannot be read. Removed names
    (never values) are logged at DEBUG. No `cluster/` edit.
  - **Read the key before the fence.** cmdkit expands `auth_env` lazily from the environment, so
    after the scrub `config.relay.upstream.auth` may no longer resolve. The relay holds the key in
    its upstream `QueueConfig`, built before the fence; P8's reconnect must reuse that object, never
    re-read the configuration.
  - **Out of R17's reach (argv and environment only), for the P9 docs:** a site client on a host
    that shares the relay's user or system configuration file reads whatever `[relay.upstream]`
    holds there. The P9 trust-model section should advise `auth_env`/`auth_eval` or a relay-only
    `HYPERSHELL_CONFIG_FILE` rather than a literal key in a shared file.
  - **Gated workload.** The two-relay and failover tests put a barrier file in front of group 0, so
    each site holds work at once before anything completes (both sites run work; relay B is
    killed while its tasks are running) without sleeps as synchronisation. The waiting tasks are
    small Python processes: macOS withholds the environment of its own system programs (`/bin/sh`,
    `sleep`) from psutil, returning an empty mapping, which would make the task half of the scan
    vacuous. The scan counts only processes whose environment it read, and requires at least two
    site clients and one task per site. **Note for P7:** research 04 §2 assumes psutil `environ()`
    works for every own process on macOS; `relay_smoke.sh` and the judge need the same guard
    (count only readable environments; make the scanned task a non-system program).
  - **Expected double execution under failover.** Tasks relay B held when it died may have started
    at site B; they run again at site A after the revert. The failover test asserts that only
    snapshot ids ran at both sites and that site A ran each task once. No stale or duplicate
    result is expected under the kill variant (relay B never returns anything).
  - Help text and `docs/_include/relay_desc.rst` describe the fence.
- **Verify:** see frontmatter.
- **Red first (2026-11-05):** on the unmodified relay source `tests/test_relay_launch.py` failed at
  collection (`ImportError: fence_site`), and `test_two_relays_grouped_workload_with_retry` failed
  on the key scan (`upstream key seen in 4 site processes`: both site clients and both running
  tasks at relay A). The failover test passed on the unmodified source, as expected: failover is
  P3/P4's confirm-and-evict mechanics, and P5 adds its test, not new code for it.
- **Verified 2026-11-05** (venvs from `uv sync --frozen` on `PATH` in place of `uv run`):
  - P5 verify, 3.12: `test_relay_launch.py` + `test_relay_workload.py` 22 passed (about 64 s), then
    the full suite 997 passed, 85 skipped (PostgreSQL and other server-backed tests), no failure.
  - Relay, zone, dispatch, server-FSM, contended-write, writeback, main and completion files, 3.14:
    284 passed, 11 skipped.
  - `unsupported_internals()` is `[]` on both.
  - Loopback failover smoke (`temp_site.sh`, a scratch driver), 3.12 at INFO and 3.14 at DEBUG:
    upstream `hs server --restart -r 1 -b 4` with TLS (`<auto>`) and evict 10 s, 121 tasks in 3
    groups of 40 (`sleep 0.3`) plus one failing on its first attempt, all in zone `shared`; relays A
    and B (`--zone shared -N 2`, loopback launcher, heartrate 1 s, the key also placed in a second
    variable). Relay B and its site were SIGKILLed once at least 40 % of rows had completed and B
    held confirmed rows (50 and 53 rows done; 2 and 1 rows held). Both runs: server and relay A
    exit 0, B -9; 122 rows, all completed, 121 exit 0, the one failure `(3, attempt 1, retried)`;
    both sites completed work (A 96–98, B 24–26), none attributed to a relay identity; every
    held row completed at site A; B's identity evicted (one `Evicting client`, one `Reverting
    orphaned tasks`), A's disconnected by its final beat, not evicted; zero stale and zero duplicate
    result lines (none is expected under the kill variant); site A ran each task once, and the
    only task run at both sites (3.14 run: 1) was one B held when killed; group order held; no key
    in either relay's log or output; no primary sentinel created.
- **Touches:** `src/hypershell/relay/` (`__init__.py`, `site.py`), `tests/test_relay_launch.py`,
  `tests/test_relay_workload.py`, `docs/_include/relay_desc.rst`. No zone code, `client.py`,
  `server.py`, `core/queue.py` or `cluster/` edit.

## Phase P6 — Roster forwarding
**Satisfies:** R7, R6, R20 · **Depends on:** P5 · PLAN §2.7
**Goal:** leaf clients appear upstream attributed to their site, are never evicted on their own,
and disconnect with their relay; `count_connected` and the rate math stay correct.

- [x] `core/heartbeat.py`: optional `site` and `roster`, packed only when set, read with `.get`.
- [x] `data/model.py`: `Client.site` (TEXT) and `Client.relay` (UUID), nullable, through #69's
      upgrade step; `count_connected` excludes `relay = id`.
- [x] `server.py` HeartMonitor: relay identity rows written with `site` and `relay = id`; roster
      diff adds/re-registers/disconnects leaves without entering `beats`; holder eviction or
      FINISHED disconnects its roster in one statement; #29 re-registration refreshes
      `server_id`/`server_host`; one ERROR when a `site` beat arrives under `no_confirm` or
      `in_memory`.
- [x] Relay heartbeat FSM sends `site` on every identity and the roster (local `Client` table) on
      the holder.
- [x] `tests/test_relay_roster.py` (HeartMonitor on stand-in queues + `task_db`): leaf registered
      with site; leaf disconnect forwarded; relay eviction disconnects the roster; a leaf is never
      evicted on its own; `count_connected` counts leaves, not relays; a beat with no roster fields
      decodes; an existing pre-column database upgrades in place.
- [x] Extend the two-relay workload: upstream `client` rows per site equal the leaves launched.
- **Amendments (2026-11-05, build):**
  - **Site name.** Nothing named where `site` comes from. New `[relay] site` (config only, no
    CLI flag; default unset, which means the relay's host name, `HOSTNAME`), resolved by
    `relay/upstream.py::relay_site` and logged on the identity line (`Upstream identity … for zone
    … at site …`). Documented in the config reference, `relay_desc.rst` and `hs relay --help`.
    A `--site` option is left for P9 or a foreman ruling.
  - **The roster** is `store.roster(engine, INSTANCE)`: the relay-local server's connected `client`
    rows (its own `server_id`), as `RosterEntry(uuid, host, state=RUNNING)`, ordered by id. Only
    id, host and state go upstream (credential fence: nothing the site was given to connect).
    Leaves that leave the site drop out of the snapshot; upstream treats that as a disconnect.
  - **Roster changes beat early.** A roster read at heartrate alone (default 10 s) would miss leaves
    that come and go between beats, and the first beat goes before the fleet registers. The
    heartbeat FSM re-reads the roster at each wait step (≤ 1 s) and beats at once when it differs
    from the last one sent. A roster that cannot be read (SQLAlchemy error) is left out of that
    beat; upstream then keeps the last roster (a beat without `roster` changes no leaf, an empty
    roster disconnects them all). The FINISHED beat carries `site` but no roster.
  - **Upstream monitor shape.** `HeartMonitor.rosters` holds `{relay: {leaf: RosterEntry}}`.
    Leaves with state FINISHED, the relay itself, and any id that beats on its own are not taken
    as leaves. New leaves are added (or reconnected through `Client.reconnect`) with `site` and
    `relay = holder`; dropped ones are disconnected with one `Client.disconnect_all` statement;
    eviction or FINISHED of a relay runs `Client.disconnect_roster` (one UPDATE, `relay = holder
    AND id <> holder AND disconnected_at IS NULL`). Leaves are disconnected, never marked evicted.
    In memory the rosters are tracked but nothing is written.
  - **#29 re-registration** (`Client.reconnect`) now refreshes `server_id`/`server_host` for every
    re-registered client, not only relays, as PLAN §2.7 states it.
  - **ERROR line:** once per relay registration (its first beat, or its first after an eviction)
    under `--no-confirm` or `--no-db`.
  - **Test harness touch.** `tests/__init__.py::snapshot` (#69's helper) read `SELECT * FROM
    client`, so the new nullable columns broke `test_upgrade_cli.py::test_rows_preserved`. It now
    snapshots the 2.8.3 client columns (`ORIGINAL_CLIENT_COLUMNS`, as it already did for `task`),
    and the test asserts the new columns read NULL. The PostgreSQL snapshot is task-only and is
    unchanged.
  - **Workload tests.** All three relay workload tests asserted the relay identity was the only
    upstream client; they now assert the forwarded roster: per site, the upstream rows with that
    `site` are the relay plus exactly the leaves in the relay database, `relay` = the identity,
    all disconnected at the end and none evicted (in failover, B's leaves were disconnected by
    B's eviction). Relays in the two-relay tests set `HYPERSHELL_RELAY_SITE` to their name.
- **Verify:** see frontmatter.
- **Red first (2026-11-05):** on the unmodified tree `tests/test_relay_roster.py` failed at
  collection (`ImportError: RosterEntry`). The extended `tests/test_relay_workload.py`, run against
  the P5 source (`d6e9d7d`, a scratch export), failed its one-relay and two-relay tests on the
  roster assertions (`no such column: site` upstream). During the build
  `test_count_connected_counts_leaves_not_relays` and `test_upgrade_cli.py::test_rows_preserved`
  (the snapshot amendment above) were red until fixed.
- **Verified 2026-11-05** (venvs from `uv sync --frozen` on `PATH` in place of `uv run`):
  - P6 verify, 3.12: 129 passed (19 of them `test_relay_roster.py`), no skip.
  - Full suite, 3.12: 1017 passed, 85 skipped (PostgreSQL and other server-backed tests), no failure.
  - Relay, zone, dispatch, server-FSM, contended-write, writeback, upgrade, autoscale and initdb
    files, 3.14: 348 passed, 64 skipped.
  - `unsupported_internals()` is `[]` on both; `hs relay --help` shows the roster paragraph.
  - Loopback smoke (`temp_site.sh`, a scratch driver), 3.12 at DEBUG and 3.14 at INFO: upstream
    `hs server --restart -r 1 -b 4` with TLS (`<auto>`), 41 tasks plus one failing on its first
    attempt; a raw four-key heartbeat as 2.9.0a4 packs it (no `site`, no `roster`) put on the
    upstream heartbeat queue; then `hs relay -N 2` (loopback launcher, `[relay] site` = `site-a`).
    Mid-run the upstream `client` table held the relay (`site-a`, `relay` = itself), its 2 site
    clients (`site-a`, `relay` = the relay) and the old-style client (`site`/`relay` NULL), all
    connected, and `Client.count_connected()` was 3 (the leaves and the old-style client, not the
    relay). Both processes exited 0; 42 rows, all completed, the failure `(3, attempt 1, retried)`;
    every `client_id` (and `hs list client_id client_host`) names one of the 2 leaves, which equal
    the relay database's `client` rows; at the end every upstream client row was disconnected and
    none evicted; no eviction, stale, duplicate, ERROR or CRITICAL line upstream; no key in the
    relay's log or output.
- **Touches:** `src/hypershell/core/heartbeat.py`, `src/hypershell/data/model.py`,
  `src/hypershell/server.py` (`HeartMonitor` only), `src/hypershell/core/config.py` (`[relay]
  site`), `src/hypershell/relay/` (`__init__.py`, `store.py`, `upstream.py`),
  `tests/test_relay_roster.py`, `tests/test_relay_workload.py`, `tests/__init__.py`,
  `tests/test_upgrade_cli.py`, `docs/_include/relay_desc.rst`, `docs/_include/config_param_ref.rst`.
  No zone code (`Scheduler`/staging, `core/dispatch.py`, `core/queue.py`), `client.py` or `cluster/`
  edit. Wire: two optional heartbeat keys, packed only when set; a beat without them decodes as
  before, and an older server ignores them.

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
