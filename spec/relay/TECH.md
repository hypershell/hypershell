---
slug: relay
title: 'HyperShell Relay: two-level scheduling across administrative sites'
kind: feature
appetite: big
status: in_progress
branch: feature/relay
base: develop
current_phase: P7
last_updated: '2026-11-06'
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
  status: in_progress
  satisfies:
  - R21
  - R20
  depends_on:
  - P5
  - P6
  parallel: true
  hammerable: false
  hill: crest
  verify: uv run pytest -v tests/test_relay_judge.py && sh spec/relay/drive/relay_smoke.sh
- id: P8
  name: 'Outage ride-through and restart durability: UpstreamLink, backoff, redelivery'
  status: done
  satisfies:
  - R9
  - R5
  - R20
  depends_on:
  - P6
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_relay_link.py tests/test_relay_outage.py tests/test_relay_fsm.py
    tests/test_relay_return.py tests/test_relay_workload.py
- id: P9
  name: Multi-zone relays (one upstream identity per zone), per-zone fleets, relay
    docs and trust model
  status: done
  satisfies:
  - R2
  - R8
  - R18
  - R20
  depends_on:
  - P8
  parallel: false
  hammerable: false
  hill: crest
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
  last_reviewed_commit: d4a2ec8
  verdict: approved
  blocked_reason: ''
  cycle: 4
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
- **Landed and merged (2026-11-05).** `fix/authkey-handling-hardening` is #80 on `develop`
  (`d2a460f`), merged into this branch with the site-client launch moved onto it; see *Merge round —
  `origin/develop` `d2a460f`* after the review-2 round. It exposes one invocation function,
  `cluster/launch.py` `client_launch()`, so P9's extraction is dropped (P9 below), and the P9 gate on
  the hardening cycle is met.

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

## Review-3 rulings (maintainer, 2026-11-05)

- **C3-1 — sanctioned as a carve-out.** `hs relay` catches SIGTERM, cleans up (site clients ended,
  the site config copy removed) and re-delivers it, so the process still dies by SIGTERM; USR1/USR2
  drain; a second SIGTERM ends it at once. P9 adds this relay carve-out to `AGENTS.md` §Signals and
  to `invariants.md` §8.
- **C3-2 → P8.** The C2-1 process test is timing-dependent: raise the fault only when `results` is
  non-empty.
- **C3-3 → P9.** Docs: a second SIGTERM also leaves the site config copy behind.
- None of the three is done in P7; each is an item in its phase below.

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
- **Known property (review cycle 2 note, recorded 2026-11-05):** any holder of the upstream key can
  heartbeat a roster, and so re-attribute or disconnect client rows. That is no weakening of the trust
  model: a key holder already has the full RPC. A cheap guard inside `HeartMonitor.update_roster` (the
  scope this phase already touches) now applies a roster entry only to a client whose `relay` is NULL
  or the sender; an entry naming another relay's site client is ignored, with a WARNING, and never
  enters the sender's tracked roster, so that roster can neither re-attribute nor later disconnect it.
  What the guard leaves: a forged roster can still adopt a row with no relay (an ordinary client not
  beating at the time) and later disconnect what it adopted. *Test:*
  `test_relay_roster.py::test_roster_never_takes_another_relays_leaf` (relay B's roster and an
  ordinary client's both name relay A's leaf: it keeps A's site and relay, stays connected when it
  drops out of theirs, and is disconnected only by A's). *Red:* `(None, <relay B>) == ('site-a', <relay A>)`.
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

## Review-1 remediation round (2026-11-05)
**Not a phase.** It fixes the findings of the interim review (`REVIEW.md`, cycle 1, at `d6e9d7d`),
red first, on the P6 head `9810598`, under the foreman's rulings recorded there. P7 is not started.
No zone code (`core/dispatch.py`, `core/queue.py`, `client.py`, `server.py`) or `cluster/` edit.

- **A1 (`--no-db` upstream).** `relay_task.schedule_time` is nullable: an in-memory upstream stamps
  nothing, the task is kept with no stamp, and its result goes back with none (`server_id` /
  `server_host` none too), as a stock client returns it. An in-memory upstream has no result fence
  and no failover (its HeartMonitor logs its one ERROR for a relay). *Test:*
  `test_relay_workload.py::test_relay_against_a_server_without_a_database` (8 tasks, each run once,
  all returned, FINISHED). *Red:* the relay never ran a task (insert refused on the NULL stamp).
  A relay database created before this round keeps the old `NOT NULL` (the upgrade step only adds
  columns); relay databases are disposable (R10).
- **A2 (halt mid-request).** `UpstreamPuller.halt` no longer halts the machine wherever it is: it
  marks it stopping, and the machine ends at its gate (or before sending a request). A request
  already sent is inserted and confirmed first; insert retries wait their full interval even when
  stopping. `finish_upstream` stops the puller (bounded), and decides FINISHED only once it has
  stopped, reading `count_held` then; a puller that does not stop in time gets no FINISHED.
  *Tests:* `test_relay_fsm.py::test_puller_halted_mid_request_inserts_and_confirms_the_reply` (the
  fake upstream holds the request on an event; `stop()` lands, then the reply: 3 inserted, held,
  one confirmation) and `::test_puller_halt_ends_at_the_gate`, which replaces
  `test_puller_halts_on_halt` (that test pinned the defect: HALT from INSERT and CONFIRM).
  *Red:* `inserted 0 == 3`.
- **A3 (size 0).** `hs relay` refuses a size below 1 (`ArgumentError`, exit `bad_argument`: "Site size
  must be at least 1 …"); help, `relay_help.rst` and the config reference say so. *Test:*
  `test_relay_fsm.py::test_relay_refuses_a_site_of_no_clients[0,-1]`. *Red:* `-N 0` started.
  The P3/P4 process tests that ran `-N 0` to have no site client now run `-N 1` with a launcher that
  starts none (`true`; `HYPERSHELL_RELAY_LAUNCHER` takes its path, since the word alone reads as a
  boolean); their assertions are unchanged.
- **A4 (leaf times).** `store.store_utc(engine)`, called by `RelayApp.run` before the engine's first
  statement, binds SQLite's `DateTime` on the relay engine's dialect (instance `colspecs`, a
  documented SQLAlchemy hook) to `UTCDateTime`: every datetime is stored as UTC and read back aware
  (UTC); a naive one is taken as local time, as before. It refuses an engine that already settled on
  the stock type. `result_record` returns each time as that instant in the relay host's zone.
  **Deviation from the ruling's "return them as aware UTC", for the foreman:** the instant is exact
  either way, but a SQLite upstream keeps wall clock only, so `+00:00` results would sit hours off
  the upstream's own local stamps (`hs list`, and the workload tests' group-order checks, which
  compare upstream `completion_time` with the next group's `schedule_time`). Returning the instant in
  the relay's zone is what a stock client on the relay host sends. Flipping to UTC is one line in
  `result_record`. *Tests:* `test_relay_return.py::test_leaf_times_return_as_the_instants_reported`
  (the first and the second 01:30 of 2026-11-01, stored under `America/New_York`, returned under
  `America/New_York`, `UTC` and, stored under `UTC`, `Asia/Kolkata`: stored text is UTC, returned
  instants equal, one hour apart), `::test_relay_database_matches_an_instant_in_any_offset` (the
  relay-local server's stamp match), `::test_store_utc_refuses_an_engine_already_in_use`. *Red:*
  stored wall clock `01:30`/`01:30`. Rows a relay database held before this round were local wall
  clock; disposable, as above.
- **B1 (never exits).** The relay's failure path is one idempotent `RelayThread.abort` (first call
  only; `RelayThread.stop` calls it): puller, autoscaler, then the site clients (B2), then the site
  cluster only if alive, stopped once from a helper thread joined with `STOP_TIMEOUT` (10 s), since
  the stock server's stop can block on its one-slot queue; then returner and heartbeat (no FINISHED).
  Each wait is bounded and a part's own failure is not re-raised there. `RelayApp.run` stops the
  thread on any exception (including `KeyboardInterrupt`) and re-raises, so the exit is non-zero.
  The clean path now watches the heartbeat and returner while it waits for the site to stop and
  the returner to flush, so an upstream lost during a drain also takes the failure path. *Test:*
  `test_relay_workload.py::test_relay_exits_and_ends_its_site_when_upstream_dies` (upstream SIGKILLed
  with two site tasks running; the relay exits non-zero within 30 s; in B's lifecycle harness it
  exited with status 5 between 10 and 15 s after the kill, the default 10 s heartrate being most of it). *Red:* still running at 30 s.
- **B2 (orphaned site clients).** `site.end_site_clients(cluster, grace, patience)`: the autoscaler's
  own `Popen` handles, SIGTERM, then SIGKILL after `CLIENT_GRACE` (5 s); `client.py` unedited. Every
  exit but a clean drain ends them in `abort` ("Ended N site clients"); after a clean drain any still
  running after the grace (one that never connected) is ended too. Their tasks are not chased (a
  stock client's SIGTERM leaves its tasks as well; the non-finding below). *Test:* the B1 test asserts
  both site clients are gone when the relay has exited. *Red:* both alive.
- **B3 (`[relay] port`).** `DEFAULT_RELAY_PORT` and `RelayApp.port` read `config.relay.port`, as
  `ServerApp` reads `config.server.port`. *Test:* `test_relay_db.py::test_relay_port_from_configuration`
  (environment and file). *Red:* bound 50002.
- **B4 (`auth_env`/`auth_eval`).** `relay.upstream.upstream_key()` resolves the key layer by layer, from
  the top: the first configuration layer setting any of `auth`, `auth_env`, `auth_eval` decides, two
  forms in one layer are refused (`ConfigurationError`, naming the forms, never the key), and with
  none the built-in default applies. `upstream_config` and `check_args` use it. It lives in
  `relay/upstream.py` and touches `[relay.upstream]` only; the built-in `auth` default stays. The same
  layered lookup would fix `[server]` (the follow-up seed below). *Tests:*
  `test_relay_launch.py::test_upstream_key_forms`, `::test_upstream_key_refuses_two_forms_in_one_place`,
  `::test_relay_takes_the_upstream_key_from_a_variable_or_a_command[auth_env,auth_eval]`. *Red:* "'auth'
  has more than one variant".
- **B5 (config-file scrub).** `scrub_environment` no longer drops `HYPERSHELL_CONFIG_FILE` for relay
  settings: `site.site_config_copy` parses the file (`tomllib`), and if it holds `[relay]` (tables or
  dotted keys) or the key, writes a copy without the `relay` table and without any string value (or
  name) holding the key, via `tomlkit`, mode 0600 (`os.open` + `fchmod`, so an older copy is made
  private again), at `<relay database>.site.toml`, and points the variable there. A file holding
  neither is passed on unchanged. A file that cannot be read or parsed, or a copy that cannot be
  written, keeps the old behaviour (dropped) with a WARNING. `RelayThread.release` removes the copy
  when the relay ends (clean or not). *Tests:*
  `test_relay_launch.py::test_scrub_gives_the_site_a_copy_without_relay_settings` (four files),
  `::test_scrub_drops_an_unreadable_config_file` (now with the WARNING),
  `::test_site_config_copy_replaces_an_existing_copy_privately`, and B's scenario end to end,
  `test_relay_workload.py::test_site_clients_keep_the_relay_config_file_without_relay_settings`
  (explicit `[server.tls]` cert: the site client's variable names the 0600 copy, which has the TLS
  table and no key or relay table; 4 of 4 tasks complete; the copy is gone after exit). *Red:* no site
  task ever ran (certificate verification failed in a relaunch loop).
- **B6 (drain signal). Went the first way: behaviour now matches the doc.** `RelayApp.run` installs
  its own SIGUSR1/SIGUSR2 handler (`install_drain_handlers`, main thread), which sets the relay's
  `DRAIN` event and nothing else, so the stock process-wide flag the relay-local scheduler reads is
  never set and the scheduler keeps going. The puller takes a `drain` callable (the relay passes
  `DRAIN` or the stock flag) and ends at its gate ("Drain requested - no more upstream requests").
  The site then finishes every task the relay holds, started or not, the results go back, and
  FINISHED goes out. No zone-code edit. `relay_desc.rst` and `hs relay --help` say exactly this, and
  that SIGINT/SIGTERM end the relay at once. *Test:*
  `test_relay_workload.py::test_drain_signal_finishes_everything_the_relay_holds` (16 gated tasks,
  bound 8, SIGUSR1 while the site runs 2 and the relay holds more; every received task completes
  upstream, the rest were never pulled, FINISHED, no eviction). *Red:* no drain line; the relay ended
  "not finishing upstream".
- **B7 (`[export]` test gap).** `test_relay_launch.py::test_relay_refuses_an_exported_task_variable_carrying_the_key`
  drives the real `[export]` path (from the environment and from a file) through a relay process with
  a recording launcher: refused with exit `runtime_error`, no launch, the key in no output. It passes on
  the fixed and the unfixed source (the check was right; the test was missing). *Mutation:* with the
  `check_exports` call replaced by `pass`, both cases fail ("a site client was launched with the key
  exported") while the other fence tests still pass.
- **B8 (`evict_hint`).** P8 reads it, as planned (the `UpstreamLink` warning and the cut-off for
  replaying confirmations); noted under P8. The config reference marks it "Not read yet: used from P8".
- **A5 (site evict fixed at 600 s): deferred to P8**, noted there. No `cluster/` edit this round.
- **Follow-up public seeds, for the foreman to file (not filed here):**
  1. *`[server] auth_env` / `auth_eval` collide with the built-in `auth`* ("more than one variant"),
     on `develop` before this branch; `upstream_key`'s layered lookup is one fix.
  2. *The advertised host ignores the bind address:* `AutoScalingCluster` gives clients `-H HOSTNAME`
     whatever `bind` is, which matters to a future `--advertise`.
- **Non-findings (recorded, not changed):** a `--no-tls` relay against a TLS upstream hangs silently at
  connect, and SIGTERM/SIGINT orphan running tasks; a stock client does both.
- **Verify / gates (2026-11-05;** venvs from `uv sync --frozen` on `PATH` in place of `uv run`, each
  run under a process-group kill timer):
  - **Red first:** every new test above failed on the P6 source (`9810598`, a scratch export on
    `PYTHONPATH`) for its finding's reason, except B7's, which passes there by design and is
    mutation-checked instead. Unit and process tests whose module imports the new names ran red
    from a scratch copy with those imports stubbed.
  - Full suite, 3.12: 1042 passed, 85 skipped (PostgreSQL and other server-backed tests), no failure.
  - Relay, zone, dispatch, contended-write, writeback, upgrade, server-FSM and autoscale files, 3.14:
    343 passed, 64 skipped.
  - `unsupported_internals()` is `[]` on both; `hs relay --help` shows the new paragraph and `-N` text.
  - Loopback smokes (`temp_site.sh`, a scratch driver), 3.12 and 3.14, TLS on the upstream hop
    (`<auto>`): **one relay** (P4's shape: 50 tasks in 2 groups plus a first-attempt failure, `-N 2`):
    51 rows completed, the failure `(3, attempt 1, retried)`, every `client_id` a leaf, FINISHED,
    nothing stale, duplicate or evicted, 51 results returned. **Two relays plus failover** (P5's shape:
    121 tasks in 3 groups plus the failure, zone `shared`, evict 10 s, heartrate 1 s, B SIGKILLed at
    ≥ 40 % while holding confirmed rows): 122 rows completed, A 83 / B 39, B's held rows completed at
    A, B evicted once, no task run twice, no stale or duplicate, no key in any output. **Roster** (P6's
    shape: 41 tasks plus the failure, a raw four-key heartbeat, `[relay] site` = `site-a`): mid-run the
    relay and its 2 leaves at `site-a` (`relay` = the identity) beside the old-style client,
    `count_connected` 3; 43 rows completed; every row disconnected, none evicted; no ERROR upstream.
    One earlier failover run on 3.12 killed B inside the cut-to-confirm window (two rows cut for B and
    never confirmed) and upstream then waited on them, as GOAL's non-goal says it will; the driver
    now kills B only when no row is cut and unconfirmed, and the runs above passed.
  - B's lifecycle, drain, config-file, port and auth scenarios re-run against the fix: upstream killed
    → relay exit 5 within 15 s, no site client left; SIGUSR1 with 8 held → exit 0 after 20 s, 8
    completed, 0 stranded, final beat; explicit `[server.tls]` → 0 client exits, 4 of 4 completed;
    `auth_env` / `auth_eval` (re-run with `-N 1`, since the scenario passed `-N 0`, now refused) →
    the relay reaches its upstream connect. The port scenario also passed `-N 0`; its test covers it.
- **FSM:** top status `in_review` for the scoped review cycle 2 of the reviewed scope (verdict and
  cycle left as cycle 1 set them; cycle 2's verdict moves them); `current_phase` stays P7.
- **Touches:** `src/hypershell/relay/` (`__init__.py`, `site.py`, `store.py`, `upstream.py`),
  `src/hypershell/core/config.py` (`DEFAULT_RELAY_PORT`, a size comment), `docs/_include/relay_desc.rst`,
  `docs/_include/relay_help.rst`, `docs/_include/config_param_ref.rst`, `tests/test_relay_fsm.py`,
  `tests/test_relay_db.py`, `tests/test_relay_launch.py`, `tests/test_relay_return.py`,
  `tests/test_relay_workload.py`. No completion change (no new option).

## Review-2 remediation round (2026-11-05)
**Not a phase.** It fixes the findings of review cycle 2 (`REVIEW.md`, at `e84ff17`), red first, on
`a9d82e7`, under the foreman's rulings. P7 is not started. No `dispatch.py`, `queue.py`, `client.py` or
`cluster/` edit; `server.py` only inside `HeartMonitor.update_roster` (P6's scope, for the optional
roster guard). The key reaches no new place.

- **C2-1 (dead relay-local receiver).** `RelayThread.check_site(stopping)` watches every thread of the
  relay-local server and the site's autoscaler, from the relay's existing supervisor loops (period
  `STOP_CHECK_INTERVAL`, 1 s): `wait_site` and `stop_site`. A thread that ended with a failure raises
  it. On a clean stop the server's threads end in turn (scheduler, heart monitor, receiver, confirm,
  the server thread), each only after the one before it, so one that has ended while an earlier one
  still runs has died: `RelayPartEnded`. The autoscaler must run until the site is told to stop. In
  `wait_site`, a returner or upstream heartbeat that ends without a failure is `RelayPartEnded` too.
  Either way the relay takes the existing bounded failure path (B1/B2): `abort` stops every part once,
  ends the site clients, sends no FINISHED, and the relay exits non-zero; upstream evicts it and
  reverts. No `server.py` edit. *Tests:*
  `test_relay_workload.py::test_relay_exits_and_ends_its_site_when_its_receiver_dies` (a relay process
  whose `Task.apply_results` raises, marked a relay before import so it does not re-execute; two site
  tasks running, then released: the relay exits non-zero within 30 s with the injected error, no site
  client survives, no FINISHED; upstream, evict 10 s, evicts the identity and reverts every task it
  held); unit tests in `test_relay_fsm.py` on stand-in threads (`test_site_check_*`,
  `test_relay_wait_raises_a_part_that_died[receiver,returner,heartbeat]`,
  `test_relay_stop_raises_a_site_thread_that_died_meanwhile`). *Red:* the process test, "relay still
  running 30 seconds after its site returned results". *Mutations* (scratch copies of `src/`, all
  killed): no `check_site` in `wait_site` (process and unit), no out-of-turn check, no failure join, no
  `check_site` in `stop_site`, no autoscaler check, no `RelayPartEnded` for a clean returner/heartbeat end.
- **C2-2 (`store_utc` unpinned).** `test_relay_workload.py::test_leaf_times_land_upstream_as_the_instants_they_were`:
  the real `hs relay` start path, upstream and relay in `Asia/Kolkata`, site clients in
  `America/Los_Angeles` (a launcher wrapper sets `TZ`); every upstream row has
  `schedule_time <= start_time <= completion_time <= schedule_time + 5 min`. Passes on the source as
  is. *Mutation:* without `store_utc(engine)` in `RelayApp.run` it fails, the leaf times 13.5 h before
  the schedule time.
- **C2-3 (drain handler unpinned).** `test_drain_signal_finishes_everything_the_relay_holds` now holds a
  backlog the relay-local server has not staged: zone `shared`, site clients asking one task at a time
  (a launcher wrapper rewrites `-b`), relay bundle size 6, so the pool stages one or two ahead; SIGUSR1
  goes once both site tasks run and the relay database has received rows with no local
  `schedule_time`. It asserts every received task completes upstream and is returned
  (`relay_result` all `returned`), FINISHED, no eviction. *Mutation:* without
  `install_drain_handlers()` it fails (received tasks left incomplete).
- **C2-4 (config copy after SIGTERM).** Removed through the exit path, the signal's semantics kept:
  `RelayApp.run` installs `install_terminate_handler()` (main thread); SIGTERM raises
  `RelayTerminated` (a `BaseException`) there, so the relay ends as on SIGINT (`thread.stop()` →
  `abort`: parts stopped once, site clients ended, no FINISHED; `release()` removes the copy), and
  `RelayApp.main` then re-delivers SIGTERM with its default action (`end_by_signal`), so the process
  still dies by SIGTERM (status −15). The handler resets SIGTERM to default first, so a second SIGTERM
  ends the process at once. `relay_desc.rst` says when the copy is removed (finish, failure, SIGINT,
  SIGTERM; only SIGKILL leaves it, and the next relay on that database to need one writes over it),
  and that SIGINT/SIGTERM end the relay at once without draining, with its site clients; `hs relay
  --help` says the latter. *Test:* `test_relay_ended_by_a_signal_removes_the_site_config_copy[SIGTERM,SIGINT]`
  (status −15 / 4, copy gone, no site client left). *Red:* SIGTERM left the copy. *Mutations:* no
  handler installed, and no re-delivery (status 0), both fail.
- **C2-5 (internal phase in public docs).** `relay.upstream.evict_hint` is out of
  `config_param_ref.rst`; the default stays in `core/config.py`; P8 has the item to document it.
- **Roster note.** Recorded under P6 with the guard (added: trivial, inside `update_roster`).
  *Mutation:* without the guard the new roster test fails.
- **Verify / gates (2026-11-05;** venvs on `PATH`, each run under a process-group kill timer):
  - **Red first:** on the source at `a9d82e7`, the C2-1 process test (relay alive at 30 s), the
    SIGTERM case of the C2-4 test (copy left) and the roster-guard test failed for their finding's
    reason; the C2-2, C2-3 and SIGINT tests pass there by design and are mutation-checked instead.
  - Full suite, 3.12: 1063 passed, 85 skipped. 3.14: 1060 passed, 88 skipped (PostgreSQL and other
    server-backed tests skip). A first 3.14 run failed the new C2-1 test once: it read the upstream
    rows between the eviction mark and the revert (two statements); the test now waits for both. The
    amended test passed on 3.12 alone and in the 3.14 re-run above.
  - The seven relay test files three runs in a row on 3.14: 165 passed each time, no flake.
  - `unsupported_internals()` is `[]` on 3.12 and 3.14.
  - Loopback smokes (`temp_site.sh`, the review-1 scratch driver), 3.12 and 3.14: **one relay** (51 rows,
    the failure `(3, attempt 1, retried)`, FINISHED, nothing stale, duplicate or evicted); **two relays
    plus failover** (122 rows; B killed at 74 / 78 done holding 4 / 2; held rows completed at A; B
    evicted once; no stale or duplicate; no key in output); **roster** (relay and 2 leaves at `site-a`
    beside an old-style client, `count_connected` 3; 43 rows; all disconnected, none evicted).
  - Docs: a Sphinx build outside the sandbox was not attempted (denied to the last reviewer). Inside
    the sandbox the full build fails at `sphinx_sitemap` (it needs an AF_UNIX listener). With every
    other configured extension (`-D extensions=…` without `sphinx_sitemap`), `-W --keep-going` gives
    the same three warnings as `a9d82e7` (`manual.rst`, `cli/task_submit.rst`) and none from the
    changed files; both changed includes parse under docutils (only Sphinx's `:ref:` role unknown).
- **FSM:** top status `in_review` for the scoped review cycle 3; verdict and cycle left as cycle 2 set
  them; `current_phase` stays P7.
- **Touches:** `src/hypershell/relay/__init__.py`, `src/hypershell/server.py` (`HeartMonitor.update_roster`),
  `docs/_include/relay_desc.rst`, `docs/_include/config_param_ref.rst`, `tests/test_relay_workload.py`,
  `tests/test_relay_fsm.py`, `tests/test_relay_roster.py`, this file.

## Merge round — `origin/develop` `d2a460f`, authkey hardening (2026-11-05)
**Not a phase.** It brings #80 (`fix/authkey-handling-hardening`), the 2.9.0a4 bump and #79's roadmap
changes onto relay, and moves the relay's site-client launch onto `client_launch()`. P7 stays
`in_progress`, waiting on the foreman's external run.

- **Merge** (`72cb1bb`, signed): no textual conflict. `AGENTS.md`, `invariants.md`, `ROADMAP.md` and
  `issues/relay.md` auto-merged; develop's `spec/authkey-handling-hardening/` and new seeds come in
  verbatim.
- **Semantic conflict (1).** `AutoScaler` now holds a `ClientLaunch` (`launch`) in place of its argv
  (`launcher`), so `fence_site`, which read `cluster.autoscaler.machine.launcher`, raised
  `AttributeError` and every relay start exited 5 on the merge commit alone (4 relay-launch tests red
  there). Nothing else in relay read the argv.
- **The relay's one launch path is the stock one.** The relay starts site clients only through its
  site cluster's `AutoScaler` (fixed policy), which launches every client with the cluster's one
  `ClientLaunch` from `client_launch()` (`cluster/remote.py` `AutoScalingCluster`, `AutoScaler.scale`).
  Relay builds no argv and starts no other process (only the re-exec of itself), and `cluster/` is not
  edited. So, with the site key: never on a site client's command line (handed over as
  `HYPERSHELL_SERVER_AUTH` in the launched environment; for an Open MPI launcher also named in
  `OMPI_MCA_mca_base_env_list`); never in a task's environment or a `{% %}` command's (the client
  strips it). The relay supports `--launcher`/`--launcher-args` only; `--ssh`/`--mpi` are `hs cluster`
  modes it does not have. A launcher that does not pass the environment through (`srun
  --export=NONE`, a bare `ssh host` prefix) now leaves site clients without a key: they fail with the
  client's no-key hint rather than authenticating. That is #80's behaviour for `hs cluster
  --launcher` too, and the relay docs now say the launcher must pass the environment.
- **Rewired.** `relay/site.py`: `site_launch(cluster)` returns `cluster.launch`; `check_invocation`
  takes the `ClientLaunch` and refuses the upstream key in its argv or in any variable it adds to a
  client's environment; `fence_site` checks `site_launch(cluster)`. The scrub is unchanged: the launch
  builds each client's environment at launch time from the scrubbed `os.environ`, the `[export]`
  variables (still checked) and the site key, in that order, so the site key wins over any
  `HYPERSHELL_SERVER_AUTH` the relay host sets, and an upstream key set there is scrubbed by value
  first.
- **Site configuration copy against the new path.** Unchanged and still right: the copy drops the
  `[relay]` table and every value holding the upstream key. A `[server] auth` left in the copy (or in
  a file passed on unchanged) cannot shadow the site key, because the environment layer outranks every
  file (checked: `HYPERSHELL_SERVER_AUTH` beats `[server] auth` in `HYPERSHELL_CONFIG_FILE`). If a
  launcher drops the environment, the client falls back to that file or the default and fails
  authentication; the upstream key is in neither.
- **Upstream key (R17, R21 (e)).** Unchanged: it reaches no site client by argv, environment, the
  configuration copy or the launch's added variables, and the relay never sets
  `HYPERSHELL_SERVER_AUTH` to it (the site key is minted by the site cluster).
- **Tests (red first).** `tests/test_relay_launch.py`: `test_client_invocation_excludes_the_key` (the
  launch's argv holds no key option and neither key; its environment additions are exactly the site
  key; the autoscaler launches with that object), `test_open_mpi_launcher_forwards_the_site_key_by_name_only`,
  `test_invocation_check_covers_what_the_launch_adds_to_the_environment`, and
  `test_relay_launches_site_clients_without_the_key` (a recording launcher: no `-k`/`--auth`, one
  128-character site key in each launch's environment, on no argument, in no relay output).
  `tests/test_relay_workload.py::test_site_key_reaches_site_clients_in_their_environment_only`: one
  relay, two site clients and a gated task whose environment can be read; psutil finds the site key in both clients'
  environments, on no command line, in no task's environment; the site template
  `: {% printenv HYPERSHELL_SERVER_AUTH || echo unset %}; {}` records `: unset;` in every command
  upstream. *Red:* on the pre-merge source (`b532c65`, a scratch export on `PYTHONPATH`, the new
  imports stubbed) the process tests failed for the reason (`-k` on the recorded argv; site clients
  without the key in their environment) and the unit test at its first line (no `launch`).
  *Mutations* (scratch copies of the merged source): tasks inheriting `os.environ`, and `{% %}`
  expanding without `shell_env`, both fail the workload test (`site key in 2 task environments`; the
  second through `TASK_COMMAND`, which carries the expanded command).
- **P7 drive.** Still captures R21 (e) as written: the watcher scans site clients and tasks for the
  upstream key, and the site key now in each client's environment is not the upstream key. It now
  also records, per leaf node (counts only; the site keys stay in the watcher's memory), site clients
  holding `HYPERSHELL_SERVER_AUTH`, site processes with `-k`/`--auth` or a site key on their command
  line, and tasks holding the variable or a site key (`relay_probe.py`, record schema 2). The judge
  checks them as `e.site_key.handoff` / `.argv` / `.tasks` on schema 2 records and judges schema 1
  records (an earlier head's drive) without them (`relay_judge.py`; 8 new judge tests, red against the
  old judge: 10 failed). `relay_launch.sh` already passes `--export=ALL`; it is now what lets site
  clients authenticate, not only what makes (e) non-vacuous.
- **Verified 2026-11-05** (venvs from `uv sync --frozen` on `PATH` in place of `uv run`, the sandbox
  off only for that sync; each run under a process-group kill timer; no process of these runs left):
  - Full suite, 3.12, `-n auto`: 1251 passed, 86 skipped (PostgreSQL and other server-backed tests).
  - Relay files and `tests/test_cluster_launch.py`, 3.14, `-n auto`: 353 passed, 1 skipped, twice in a
    row. Two earlier runs on a loaded host (other worktrees' suites running) failed 2 each:
    `test_relay_exits_and_ends_its_site_when_its_receiver_dies` both times (review cycle 3's C3-2, the
    fault armed before any result: ruled to P8), `test_failover_reverts_and_completes_elsewhere` once
    (passed serially at once; reason not captured), and the new site-key test once, which waited for
    two running tasks where one client may hold the whole local bundle; it now waits for one readable
    task and both clients, and passed 6 of 6 run concurrently.
  - P4 verify, 3.12: 75 passed; one-relay workload 1 passed. P5 verify, 3.12: 44 passed (serial), then
    the full suite above.
  - P7 verify (project `.venv`, Python 3.15): judge tests 75 passed; `relay_smoke.sh` with
    `HSD_SMOKE_RUNS="kill pause none"`: PASS (41, 41, 39 checks; 160 s); every leaf record shows both
    site clients holding the site key, 0 on a command line, 0 of 33–89 scanned tasks holding it, 0
    upstream-key hits. *Red:* the same smoke (variant none) on the pre-merge source fails exactly
    `e.site_key.handoff` (0 of 4 clients) and `e.site_key.argv` (4 processes).
- **Touches:** merge commit (develop's files); `src/hypershell/relay/site.py`, `src/hypershell/relay/__init__.py`
  (help and docstring), `docs/_include/relay_desc.rst`, `docs/_include/relay_help.rst`,
  `tests/test_relay_launch.py`, `tests/test_relay_workload.py`, `tests/test_relay_judge.py`,
  `spec/relay/drive/` (`relay_probe.py`, `relay_judge.py`, `relay_drive.sh`, `relay_launch.sh`), this
  file. No `cluster/`, `client.py`, `server.py` or zone-code edit.

## Phase P7 — R21 run A: relay drive, smoke, hand back
**Satisfies:** R21, R20 · **Depends on:** P5, P6 · PLAN §6; [04 §2](research/04-evidence-and-tests.md)
**Goal:** a committed, site-neutral run script the foreman can run in one Slurm allocation, proven by
a local smoke, and an exact run matrix handed back.
**Status:** built and smoke-tested (2026-11-05); `in_progress`, **waiting on the foreman's external run**
(not done). P10 judges the delivered records.

- [x] `spec/relay/drive/relay_drive.sh` — one script that performs R21 (a)–(e) inside one Slurm
      allocation of ≥ 3 nodes (`HSD_MODE=slurm`, the default) or on one machine (`HSD_MODE=local`).
      Node 0 (from `scontrol show hostnames "$SLURM_JOB_NODELIST"`) runs the upstream server; the
      rest split into site A (first `HSD_SITE_A_SIZE`, default half rounded up) and site B. Every role
      is one job step, `$HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist=NODE --export=ALL
      $HSD_SRUN_ARGS`: the upstream role on node 0, a relay on each site's first node, a watcher on
      every site node. Runs the matrix `HSD_RUNS` (default `kill pause none`), judges each run, skips a
      run that could end past `HSD_BUDGET` (default 840 s). `relay_drive.sh submit` wraps itself in
      `sbatch` (`HSD_ACCOUNT`, `HSD_PARTITION`, `HSD_SUBMIT_NODES`, `HSD_TIME_LIMIT`,
      `HSD_SBATCH_ARGS`). Isolation: every `HYPERSHELL_*` variable is unset, and every process reads
      only the run's own `HYPERSHELL_CONFIG_FILE` (logging only); each role sets its own
      `HYPERSHELL_SITE`. Site-specific values come only from variables with neutral defaults
      (`HS`, `HSD_SETUP` for module loads, `HSD_SRUN`, `HSD_SRUN_ARGS`, `HSD_LEAF_SRUN_ARGS`, the
      `submit` ones); the script names no site, cluster, account, partition, path, host or user.
- [x] `spec/relay/drive/relay_launch.sh` — the relays' `--launcher`: in slurm mode, one overlapping
      job step per site client on the next node of the site (round robin under a `mkdir` lock),
      `--export=ALL` so the environment reaches the client whatever the site default, so (e) tests the
      relay's own scrub; in local mode, the client runs here, dialing loopback (P5 ruling 1). The
      environment passes through untouched.
- [x] `spec/relay/drive/relay_probe.py` — the roles (psutil, run with the venv's `python` beside
      `HS`): `upstream` (submit, server, sampler, failover director, collector), `relay SITE`
      (the relay and its supervisor), `watch SITE NODE R` (per-node process scan), `workload`,
      `sweep` (local mode: ends anything of the run left). `relay_task.py` is the task: a Python
      process (readable environment on every platform, P5 note) that appends `<site> <task id>` to a
      node-local execution log, sleeps, and fails once when asked.
- [x] Records, counts and booleans only (no host, user, path or key), under `HSD_RESULTS/<run_id>/`:
      `upstream.json` (a)–(d) and the connection series; `relay-A.json`, `relay-B.json` (exit,
      identity count, leaves, outbox state, peak backlog vs bound, upstream connections, primary
      sentinel, key in logs); `leaf-<site>-<n>.json` (processes scanned: clients, tasks, launchers;
      key hits; `HYPERSHELL_RELAY_*` names; the key-carrying probe variable; clients without the
      relay's environment; upstream connections from site processes; executions). Transport
      (`HSD_WORK/<run_id>/`: key 0600, token, flags, identities, leaf ids, snapshot, logs) is never
      evidence.
- [x] `spec/relay/drive/relay_judge.py` (stdlib only; `DIR [--require kill,pause,none]`, DIR one run
      or a directory of runs) + `tests/test_relay_judge.py` (67 unit tests: a good run of each variant
      passes; 59 bad runs each fail the named check; CLI over a tree, one run, no runs, `--require`).
- [x] `spec/relay/drive/relay_smoke.sh` — `HSD_MODE=local` on one machine: three local "nodes" with
      their own node-local directories, 300 tasks in 3 groups, one failing once, evict 10 s, relay
      heartbeat 1 s, 2 clients × 2 threads per site, kill variant (`HSD_SMOKE_RUNS` for more); then the
      judge, and a grep of the records for the host name, the user name and every run's key. It never
      deletes; it prints its scratch path.
- [x] Hand-back note (below).
- **Amendments (2026-11-05, build), for the foreman:**
  - *One orchestrating script per allocation* (the foreman's brief), not per-role invocations from a
    foreman-written batch script as the zone drives and the plan had it. It installs nothing: `HS` names
    the venv under test (no `tests/cluster/build_archive.sh`); `drive.sh` is untouched.
  - *3 nodes by default* (one per site), not 4; any count ≥ 3 works. Site A is no longer multi-zone:
    multi-zone relays are P9, and R21 does not ask for them. One shared zone (`HSD_ZONE`, `shared`).
  - *Non-`--forever` upstream* (`hs server --restart -r 1 -b 8 -Q 2`, evict `HSD_EVICT`, DEBUG log):
    it ends when every task is done and tells the relays to shut down, so a clean run also shows
    "halt on upstream shutdown"; the plan's `--forever` would need the drive to stop it.
  - *Failover at a settled moment.* Killing relay B inside the cut-to-confirm window strands rows
    (GOAL non-goal; it bit P5's scratch driver once). The director, once `HSD_KILL_AT` of the rows are
    complete and B holds confirmed rows, has B's supervisor SIGSTOP the relay, waits `HSD_SETTLE`
    (longer than a request's upstream wait), and acts only if the upstream DEBUG log shows every task
    dispatched to B's identity confirmed: then it snapshots B's held rows and releases B as killed
    (SIGKILL) or paused (`HSD_PAUSE`, then SIGCONT); otherwise B continues and it tries again (up to
    `HSD_FAILOVER_ATTEMPTS`, 10). The snapshot is taken while B is frozen, so it is exactly what B
    held. A killed relay's site clients are ended by its supervisor once the run is over.
  - *Relay bundle size 4 × threads* (`HSD_RELAY_BUNDLESIZE`). With it equal to the thread count, R8's
    bound (connected clients × local bundle size) holds only what runs, and each refill pays a
    request round trip: the first smoke took 123 s for 300 short tasks. Four times the threads gives
    the site a buffer; the bound is still R8's and is recorded (`peak_backlog ≤ peak_bound`).
  - *Defaults for 3 nodes:* `HSD_TASKS=2000`, `HSD_SLEEP=1.0`, `HSD_FAILING=3` (one per group),
    `HSD_THREADS=8`, `HSD_CLIENTS_PER_NODE=2`, `HSD_EVICT=30`, `HSD_HEARTRATE=5`, `HSD_TIMEOUT=300`.
  - *Check (c)'s address half* (upstream peers within the relay nodes' addresses) is recorded but only
    judged in slurm mode; with one node per site every site node hosts a relay, so the ownership half
    (no upstream connection from any site process, per node) is what decides it.
- **Maintainer approvals (2026-11-05).** The four deviations above are approved: (1) 3 nodes by
  default with one shared zone (no multi-zone site A); (2) relay bundle size 4 × threads; (3) a
  non-`--forever` upstream that ends the run and tells the relays to shut down; (4) META F6: P7 stays
  `in_progress` (hill `crest`) after a green verify, waiting on the foreman's external run, and P10's
  judging of the delivered records closes R21.
- **Red first (2026-11-05):** `tests/test_relay_judge.py` written before the judge: 66 errors
  (`FileNotFoundError`, no judge). With the judge: 67 passed (one test case corrected: the address
  check is judged only when the record says it applies, so the bad case sets it).
- **Verified 2026-11-05:**
  - Verify as written (`uv run pytest -v tests/test_relay_judge.py && sh spec/relay/drive/relay_smoke.sh`,
    sandbox off for the uv cache; `uv run` built the project `.venv`): 67 passed; smoke PASS (kill:
    38 checks, 66 s).
  - Smoke with venv 3.12 (`HS=.venv-312/bin/hs`), all three variants: **kill** — 301 rows, B frozen
    once and killed holding 16 confirmed rows, B evicted and reverted once, all 16 completed at site A,
    7 ran at both sites (all in the snapshot), 0 stale or duplicate, relay exits 0 / −9; **pause** —
    B held 16, evicted once, every one of its late results refused stale (16), exits 0 / 0;
    **none** — every task ran once, no eviction. Each: key hits 0 over 28–98 scanned site processes
    per node, no `HYPERSHELL_RELAY_*` name or probe variable at the site, no upstream connection from
    any site process, upstream peak 6 connections (3 per relay), backlog peak 16 of bound 16, no
    primary sentinel, the key in no log or record, 0 processes left for the sweep.
  - Slurm path rehearsed locally with stand-in `srun`/`scontrol` (scratch, not committed; the
    stand-in dials loopback, since this sandbox cannot resolve its own host name): 5 nodes, sites of
    2 nodes each, round-robin client steps on both nodes of each site, every watcher reporting; every
    check passed except `b.site`, as expected there (fake node names are not the host's name).
  - No process of these runs left afterwards (psutil scan).
- **Hand-back: how the foreman runs it (run A; P10 judges run B's records).**
  - *Prerequisites:* the repository checkout (for `spec/relay/drive/`) and the venv under test on the
    shared filesystem, readable from every node; `HS` = that venv's `bin/hs` (beside it, `python`
    with psutil and `hyper-shell`, both installed with HyperShell). Run from a directory on a shared,
    coherent filesystem (scratch); `HSD_WORK` defaults to `./relay-drive` there.
  - *Submit (3 nodes, ≤ 20 min wall):*
    ```
    HS=<venv>/bin/hs HSD_LABEL=<short sha> HSD_RUN_ID=relay-<short sha> \
    HSD_ACCOUNT=<account> HSD_PARTITION=<partition> HSD_SRUN_ARGS='--gres=none' \
    HSD_SETUP='<module loads, if the venv needs them>' \
        sh spec/relay/drive/relay_drive.sh submit
    ```
    (Inside an existing allocation instead: the same variables without the `submit` ones, then
    `sh spec/relay/drive/relay_drive.sh`.) Matrix: r1 kill, r2 pause, r3 none, about 8–10 min in
    all; a run that could end past 840 s is skipped and counted as a failure.
  - *Output:* the job log `relay-drive/relay-drive-<jobid>.out` prints each run's verdict and the
    judge's failing checks. Evidence: `relay-drive/results/<run_id>/{upstream,relay-A,relay-B,
    leaf-A-1,leaf-B-2}.json` with `<run_id>` = `relay-<sha>-r1-kill`, `-r2-pause`, `-r3-none`. Copy
    **only** `results/*` into `spec/relay/evidence/cluster/`; never the transport
    (`relay-drive/<run_id>/`: key, logs, ids). P10's verify then is
    `uv run python spec/relay/drive/relay_judge.py spec/relay/evidence/cluster` (add
    `--require kill,pause,none` for the full matrix).
  - *Watch for:* (1) nested job steps: every step uses `--overlap`; if steps wait with "Requested
    nodes are busy", the site needs more step flags in `HSD_SRUN_ARGS`. (2) Environment forwarding:
    the launcher passes `--export=ALL`; if site clients still lack the relay's environment,
    `e.forwarded`/`e.scanned` fail rather than pass vacuously. (3) Host names: `b.site` compares each
    leaf's `gethostname()` (short) with the Slurm node names; if they differ on that cluster, the
    check fails while attribution is right; look at the transport `upstream.log`. (4) `HSD_NODE_TMP`
    (default `TMPDIR` or `/tmp`) must be the same node-local path on every node. (5) The failover
    handshake polls flag files across nodes; on a filesystem with slow attribute caching it can take
    long enough for the 30 s eviction to come first (`d.triggered` fails); raise `HSD_EVICT` then.
    (6) Relays must reach node 0 by its Slurm name (in its certificate), and site nodes the relay
    node by its host name. (7) If the run is slow, lower `HSD_TASKS` before raising the budget.
    (8) *Since the authkey merge (2026-11-05):* a site client's key reaches it only in the environment,
    so the launcher's `--export=ALL` is now what lets site clients authenticate; an `--export` in
    `HSD_SRUN_ARGS`/`HSD_LEAF_SRUN_ARGS` comes after it and wins, so do not put one there. A client
    that got no key logs the client's no-key hint (`HYPERSHELL_SERVER_AUTH is not set`) and exits;
    the relay relaunches it each `[autoscale] period`. Records are now schema 2 with three more
    checks (`e.site_key.*`); records from a drive before the merge (schema 1) are judged without them.
- **Touches:** `spec/relay/drive/` (`relay_drive.sh`, `relay_launch.sh`, `relay_probe.py`,
  `relay_task.py`, `relay_judge.py`, `relay_smoke.sh`), `tests/test_relay_judge.py`, this file.

## Phase P8 — Outage ride-through and restart durability
**Satisfies:** R9, R5, R20 · **Depends on:** P6 · PLAN §2.5
**Goal:** while upstream is unreachable the site keeps running and results stay durable; on
reconnection they are handed over; a relay restart loses nothing.

- [x] `core/reconnect.py::UpstreamLink`: link worker per role with a call deadline; transport vs
      fatal classification; full-jitter backoff (1 s → 60 s) on the halt event; epoch; logging
      cadence and the `evict_hint` warning; injected clock/rng/sleep. No `core/queue.py` change.
- [x] Puller, returner, heartbeat: `WAIT_LINK` state (1 s re-entry); on up: heartbeat all
      identities → replay confirms only for an outage shorter than `evict_hint` → drain outbox →
      pull.
- [x] Upstream restart detection by `server_id` change: delete unstarted local rows with the old
      `server_id`.
- [x] `tests/test_relay_link.py` (unit, injected clock): backoff sequence, classification,
      cadence, worker retirement on overrun, fatal exit.
- [x] `tests/test_relay_outage.py` (integration, pass/close/blackhole TCP forwarder between relay
      and upstream, evict 600 s): partition after local completion, results land after restore;
      blackhole stall detected within the deadline; SIGKILL during a partition, restart on the same
      file, restore: unreturned rows land (APPLIED or DUPLICATE), none lost.
- [x] Document `relay.upstream.evict_hint` when implemented (`docs/_include/config_param_ref.rst`,
      under `[relay.upstream]`). Review cycle 2 (C2-5) took it out of the public docs until then;
      its default stays in `core/config.py`.
- [x] *C3-2 (review cycle 3, ruled 2026-11-05):* make
      `test_relay_exits_and_ends_its_site_when_its_receiver_dies` deterministic: the injected
      `apply_results` fault fires only when `results` is non-empty.
- **From review cycle 1 (deferred here):**
  - [x] *A5.* The site server's eviction period is fixed at 600 s: `cluster/remote.py` builds
    `AutoScalingCluster`'s `ServerThread` with no `evict_after`, so `[server] evict` never reaches the
    relay's site (a site client killed without a FINISHED beat holds up a drain that long; P5 ruling 2's
    note). It predates this branch. Fix it here, after the hardening cycle's `cluster/remote.py`
    changes have merged (pass the evict period through, or compose the site server in relay).
    They merged on 2026-11-05 (merge round above); #80 left `AutoScalingCluster`'s `ServerThread`
    call as it was.
  - [x] *B8.* `relay.upstream.evict_hint` is read here (the `UpstreamLink` warning and the replay cut-off
    below). Since review cycle 2 the public config reference does not list it: document it when
    implemented (the checklist item above).
- [x] *Failover test that failed once under load (merge round, 2026-11-05):* investigate; fix if a test or
      timing defect.
- [x] *Throughput rider (foreman, 2026-11-05; below):* measure both sides of the relay, fix the
      bottlenecks with defaults that need no tuning, add a local relay-vs-direct check (target ≤ 1.5×
      direct wall time for 2000 × 0.2 s tasks at 32 executors), and answer the R21 run A
      `a.group_order` / `d.completed` question.
- **Amendments (2026-11-05, build):**
  - **The link.** `UpstreamLink` (`core/reconnect.py`) holds one connected `QueueClient` and runs each
    role's calls (`puller`, `returner`, `heartbeat`) on that role's worker thread with a deadline
    (`[relay.upstream] deadline`, default 30 s). A transport error (`EOFError` or any `OSError`: resets,
    refusals, timeouts, non-verification TLS errors) or an overrun takes the link down for that
    connection and raises `LinkDown`; the worker is retired (a stuck one is abandoned and ends with its
    socket). A fatal error (`QueueVersionError`, `AuthenticationError`, `TLSError`,
    `SSLCertVerificationError`) fails the link: it is raised unchanged to every caller and waiter, so the
    relay exits as a client would (one CRITICAL line, from the app). Any other error (`QueueFull`) is the
    caller's. A reconnector thread dials again: each attempt on a throwaway thread with the same
    deadline, waits `uniform(0, min(cap, 2ⁿ))` s between attempts (cap `[relay.upstream] backoff`,
    default 60 s) on the halt event. Each connection is a new epoch with new worker threads, which is
    what avoids the per-thread proxy connection cache (research 02 §2); the old client's manager is
    marked shut down so its proxy finalizers never dial an unreachable peer. Logging as planned: one
    WARNING down, INFO at attempts 2, 4, 8, … and at least every 5 min, one WARNING past `evict_hint`,
    one WARNING on reconnect with the outage length. `FixedLink`/`as_link` wrap a connected client (calls
    in place, always up), so the machines also take a plain client (the existing unit tests' fakes).
  - **Order after a reconnection, by link stages.** Each connection has two stages, `beat` (passed by
    the first heartbeat on it) and `drain` (passed when the returner finds nothing pending, or sends a
    partial bundle, on it). The heartbeat beats first; the returner sends only after `beat` and sends
    at once while catching up (not at the bundle wait); the puller replays a lost confirmation only after
    `beat`, and requests only after `beat` and `drain`. The first connection follows the same order.
  - **Confirm replay.** A confirmation lost with the link (`LinkDown` from its put) is replayed once the
    link is back if `link.outage` (last success before the outage to the reconnection) is below
    `evict_hint`; otherwise it is dropped with a WARNING and the tasks still run (their results land
    if upstream kept the assignment, else they are refused stale). A puller told to stop while it
    waits for the link ends there (a pending confirmation is dropped with a WARNING). A request lost
    with the link goes back to `WAIT_LINK` (a reply cut for it upstream is the cut-to-confirm non-goal).
  - **An unreachable upstream at start is no longer fatal (deviation for review, the conservative
    reading).** The relay keeps P3's order: it connects before the site starts, so a server that refuses
    it for good still ends it before anything is launched; a server it cannot reach yet is retried with
    backoff ("Cannot reach upstream server …"), and the site starts once it answers. A relay restarted
    during a partition therefore waits for its upstream server before running anything; it then returns
    the results its earlier run held. The other reading, starting the site at once, would run held tasks
    through a long outage after a restart but give up "a refused relay never touches its site".
  - **Upstream loss is no longer a relay failure (R9).** Review cycle 1's B1 scenario (upstream killed →
    relay exits non-zero within 30 s) contradicts R9 now. Its process test is replaced by
    `test_relay_rides_through_its_upstream_server_dying_and_restarting` (upstream SIGKILLed with tasks held;
    the site keeps running and holds every result; a server restarted on the same port refuses the 4 held
    results as stale (N2), the relay runs the tasks again under their new assignments, and all complete
    once) and `test_relay_exits_and_ends_its_site_when_its_upstream_server_comes_back_unusable` (a server
    with another key on the same port: the relay exits non-zero within 30 s with `AuthenticationError`
    and ends both site clients), which keeps B1's bounded-exit contract for real failures.
  - **Restart detection** (`store.drop_superseded`, inside `insert_received`'s transaction): a received
    record whose `server_id` differs from a held task's drops the held tasks of the other instance that
    the relay-local server has not scheduled (local `schedule_time IS NULL`, re-checked in the DELETE),
    with their stamps; started ones run on and return STALE; ids in the bundle are kept. One INFO line.
  - **New settings** `[relay.upstream] deadline` (30) and `backoff` (60), documented with `evict_hint` in
    the config reference and `relay_desc.rst`; `hs relay --help` gains one outage paragraph. They are
    single words on purpose: a variable cannot set a key with an underscore (see the rider's finding 4).
  - **A5.** `AutoScalingCluster` gains `evict_after` (default `DEFAULT_EVICT`, behaviour unchanged for
    every other caller), passed to its `ServerThread`; the relay passes `[server] evict`
    (`site_cluster(evict_after=…)`, `RelayThread(evict=…)`). This is the one `cluster/` edit, as A5
    allowed. `relay_desc.rst` says a silent site client is evicted after `server.evict` seconds.
  - **C3-2.** The injected receiver fault fires only on a non-empty `results`; the original
    `apply_results` handles an empty one. The test's "put back" check now reads rows with no client and
    no completion: with the larger bound the relay holds all four tasks, and the upstream scheduler may
    stage the reverted rows again at once (stamping `schedule_time`).
  - **P5 ruling notes for P8, revisited.** (2) A site client killed without a FINISHED beat now holds a
    drain up for `[server] evict` (A5), not a fixed 600 s. (3) Return bundles keep the upstream bundle
    size and `[relay] bundlewait`, except that after a reconnection the outbox goes back at once; `-N 0`
    is refused since review cycle 1 (A3), so the leftover-task launch no longer arises. The key-reuse rule
    (merge-round note: "P8's reconnect must reuse that object") holds: the link's factory is
    `QueueClient(config=<the upstream QueueConfig built before the fence>)`; configuration is never read
    again.
  - **Drain with the link down** waits for the link: the returner holds what it has until it can hand
    it over (SIGINT/SIGTERM still end the relay at once).
- **The failover test (`test_failover_reverts_and_completes_elsewhere`).** It passed 6 of 6 run
  concurrently on the P7 head, so the one failure under load was not reproduced; its reason was found
  by reading the test against the dispatch path. It killed relay B as soon as B held one confirmed,
  running task. At that moment B could still be (a) inside a request whose reply upstream had cut
  (rows stamped, no client: the cut-to-confirm window, a GOAL non-goal), which strands those rows and the
  run never ends, or (b) about to confirm more rows than the snapshot holds, so a task B ran outside the
  snapshot fails `dup ⊆ snapshot`; and (c) its roster might not yet be upstream (the larger P8 bound made
  this one fire: the leaves B launched were missing from the upstream `client` table). A test timing
  defect, fixed in the test: B is killed only once it is *settled* (`settled()`): both site clients
  connected and listed upstream, everything the upstream log dispatched to B confirmed and equal to B's
  local backlog, and either B above its low-water mark (it asks for nothing) or no group-0 task upstream
  without a client (a request finds nothing). The snapshot is then exact and no reply can be cut for B.
- **Throughput rider (foreman, 2026-11-05).** R21 run A at `24176a6` (3 nodes, kill variant) completed 947
  of 2000 one-second tasks in its 300 s deadline, about 3 tasks/s, with upstream dispatching 1–2 tasks
  per request after the first bundle.
  - *Measured* with a local driver (2000 × `sleep 0.2`, 4 site clients × 8 threads, upstream `hs server
    --restart`, every other setting at its default), on both sides of the relay:
    1. **Upstream: a trickle.** The gate asked for `bound − backlog`, so once the backlog neared the bound
       each request was for the 1–2 tasks just finished, each a round trip through the upstream
       dispatcher (upstream log: 589 dispatches, sizes 1–4).
    2. **Bound too small.** `bound = connected × [relay] bundlesize` = 4 × 1 = 4 tasks for 32 executors.
    3. **Site clients one task per request.** They were launched with `-b [relay] bundlesize` = 1, and a
       client holds at most one bundle in waiting, so every task was its own request (relay log: 2000
       dispatches of 1).
    4. **The relay-local scheduler idles after each insert.** With no task in its database it backs off
       up to `[relay] poll` (1 s), and a relay insert does not wake it (only a cut or new demand does).
       After the fixes above, each cycle still showed ~0.7 s between `Received 40 tasks` and the next
       `Scheduled 8 tasks` (relay DEBUG log), so the site ran ~40 tasks/s against 160 possible.
    5. **The drive's site clients ran one executor each.** `HYPERSHELL_RELAY_NUM_THREADS` never reached
       `[relay] num_threads`: a variable cannot set a key with an underscore (cmdkit reads it as
       `relay.num.threads`; checked: `config.relay.num_threads` stays 1). R21 run A's sites had 2
       executors per node, not 16. The same holds for every underscored key (`launcher_args`,
       `evict_hint`, …): `HYPERSHELL_RELAY_LAUNCHER_ARGS` even replaces `relay.launcher` with a table.
  - *Fixed* (defaults, no tuning): (1) **low-water mark**: the gate asks only when the backlog is at or
    below half the bound, and then for the whole gap (at most the upstream bundle size), so requests are
    few and large; the gate re-checks every 0.1 s (`GATE_WAIT`). (2) **bound** = connected clients ×
    local bundle size × 2 (`BACKLOG_FACTOR`), so a second round is ready while the first runs. (3) **site
    bundle size** = `max([relay] bundlesize, [relay] num_threads)` (`site_bundlesize`), used for the site
    clients' `-b`, the relay-local server and the bound. (4) **`[relay] poll` default 1 → 0.1 s**, so a
    received bundle waits at most 0.1 s before the relay-local scheduler takes it (the plan's "use
    `ServerThread`'s poll, no dispatcher wake-up"; zone code untouched). (5) **drive**: `relay_probe.py`
    gives each relay a configuration file (the run's file plus `[relay] num_threads`) instead of the
    variable; the relay hands its site a copy without `[relay]`. No evidence-schema change; no other
    drive default changed.
  - *Numbers* (same machine, Python 3.12, `spec/relay/drive/relay_throughput.py`, ideal 12.5 s):
    | run | direct `hs cluster -N 32` | relay | ratio |
    |---|---|---|---|
    | `24176a6`, driver (DEBUG; site clients at 1 thread, finding 5) | 31.3 s | 657.9 s | 21 |
    | `24176a6`, `relay_throughput.py` (8 threads; stopped, see below) | — | 362 tasks in 125 s (≈ 690 s) | ≈ 33 |
    | fixes 1–3, `[relay] poll` 1 s (driver, DEBUG) | — | 71.5 s | — |
    | P8 (driver, INFO, twice) | 21.0 / 20.9 s | 25.2 / 25.2 s | 1.20 |
    | P8 (`relay_throughput.py`) | 21.1 s | 26.6 s | **1.26** (PASS ≤ 1.5) |
    Upstream dispatch after the fix: 63 replies, mostly 32 tasks; relay-local: 250 replies of 8. The
    `relay_throughput.py` run at `24176a6` was stopped at the semaphore alert after 362 tasks (2.9 tasks/s,
    as on the cluster); the driver run at `24176a6` completed.
  - *R8 reading (deviation for review).* R8 asks for a bound "on the order of connected clients × local
    bundle size"; it is now twice that, with the bundle size floored at the executor threads. The relay
    still never pulls while the bound is met, and the duplicate/cancel windows stay bounded by it.
  - *R21 run A's `a.group_order` and `d.completed`.* Both are artifacts of the incomplete run, not
    failover bugs. `relay_probe.py` sets `group_order` false whenever an earlier group has an incomplete
    row (`None in done`) or a later group was never scheduled (`not out`), so any run cut off by the
    deadline fails it by construction (1053 rows were incomplete). Upstream gating cannot start group
    g+1 while group g has an incomplete row, and a failover only reverts B's rows of the current group
    to unscheduled, so it cannot break the order. The one way a complete run could fail the check is
    clock skew: `completion_time` is the leaf's clock and `schedule_time` the upstream server's, so a site
    clock ahead of the upstream's by more than a result's return latency (≥ the relay's bundle wait)
    would read as overlap. `d.completed` (18 of 64 held tasks completed at A) is the same deadline:
    site A was running at ~1.5 tasks/s with one executor per client (finding 5).
- **Follow-up public seed, for the foreman to file (not filed here):** a `HYPERSHELL_*` variable cannot set
  a configuration key with an underscore in it (`num_threads`, `launcher_args`, `evict_hint`, `bundle…`
  in other sections), and one whose prefix names another key replaces that key with a table
  (`HYPERSHELL_RELAY_LAUNCHER_ARGS` turns `relay.launcher` into a table). Present on `develop` (cmdkit's
  variable mapping), not relay-specific.
- **Red first (2026-11-05),** new and changed tests against the `24176a6` source (a scratch export on
  `PYTHONPATH`): `test_relay_link.py` and `test_relay_fsm.py` fail at collection
  (`ModuleNotFoundError: hypershell.core.reconnect`), `test_relay_launch.py` too (`site_bundlesize`);
  `test_no_request_above_the_low_water_mark_then_the_whole_gap` fails (`no attribute 'bound'`; the old
  gate holds at backlog 4 of bound 4); `test_upstream_restart_drops_held_tasks_not_yet_started` fails
  (the superseded task is kept). The process tests (`test_relay_outage.py`, the two new workload tests)
  were not run against the old source: the build host ran out of POSIX semaphores before that step (see
  *Gates*). By construction they fail there: the old relay exits on its first transport error (B1), so
  it never logs "Lost connection" and never holds a result through an outage.
- **Verify:** see frontmatter.
- **Gates (2026-11-05): partial, the rest handed to the foreman.** Mid-build the build host ran out of named
  POSIX semaphores (`kern.posix.sem.max`; `multiprocessing.Lock()` fails with ENOSPC), so nothing that starts
  a server can run here until a reboot (foreman's operations alert). P8 is therefore left `in_progress`
  with its verify not yet run as written; the foreman runs the commands below on a Linux test cluster at
  this commit. Run here before the host ran out (venvs on `PATH` in place of `uv run`, each under a
  process-group kill timer; none of these processes left):
  - All relay test files, Python 3.12, `-n 4`: 289 passed, 2 failed, both fixed and re-run green (the
    real-dispatcher connector test pinned the old bound; the version-error test saw two CRITICAL lines,
    one from the link, now DEBUG).
  - Concurrency (META F5): `test_relay_outage.py` plus the failover, ride-through and receiver tests, 6
    copies at once: 6 × 6 passed (66–75 s each). `test_failover_reverts_and_completes_elsewhere` alone, 6
    copies at once on the P7 head: 6 of 6 passed.
  - The two new workload tests (`…comes_back_unusable`, `…rides_through…`): passed.
  - Unit tests of the relay files (no server), 3.12: 236 passed; 3.14: 141 passed (link, FSM, connector,
    return, launch).
  - Sphinx `-W --keep-going` (sandbox off): only the three known warnings (`manual.rst` ×2,
    `cli/task_submit.rst`).
  - Throughput: the table above (PASS, 1.26×).
  - Not run here: the full suite on 3.12, the relay files on 3.14, the P8 verify as written, the P7
    verify and its smoke (all start servers).
- **Linux gates (foreman, test cluster, at `1393af6`, 2026-11-05) — P8 done.**
  - Full suite, Python 3.12, `-n auto` on 32 CPUs: 1295 passed, 4 failed under load. Three are known
    timing flakes (`test_busy_port_raises_from_start[no-tls]`, `test_waits_for_slow_peer_upgrade`,
    `test_server_keeps_its_files_at_the_open_file_limit`); the fourth,
    `test_two_relays_grouped_workload_with_retry`, timed out at 205 s under that load and passed in the
    serial P8 verify below.
  - Relay test files plus `test_cluster_launch.py`, Python 3.14, `-n 8`: 401 passed.
  - P8 verify as written: 115 passed.
  - P7 judge (`tests/test_relay_judge.py`): passed. P7 smoke: every check passed but `relay.backlog`.
  - R21 on 3 nodes at `1393af6`: every functional check passed in all three variants (kill 139 s,
    pause 119 s, none 99 s for 2000 × 1 s tasks on 32 executors; run A at `24176a6` did 947 in 300 s);
    only `relay.backlog` failed (peak 128 of a recorded bound 64).
  - **`relay.backlog` was the probe, not the relay (fixed at P8 closure).** P8 doubled the relay's bound
    (`BACKLOG_FACTOR`, two site bundles per connected client) but `relay_probe.py` still recorded
    `connected × RELAY_BUNDLESIZE`, so every real run read as twice its bound (cluster 128/64, smoke
    32/16), i.e. exactly at the true bound. The probe now records `relay_bound(connected)` =
    connected × `max(1, RELAY_BUNDLESIZE, THREADS)` × `BACKLOG_FACTOR`, **mirrored** from
    `hypershell.relay.site_bundlesize` and `hypershell.relay.upstream.BACKLOG_FACTOR`, not imported
    (importing HyperShell would build its configuration and database engine inside the drive's role
    processes). `tests/test_relay_judge.py` checks the mirror against the code
    (`test_probe_records_the_bound_the_relay_enforces`, red first: 4 failed, `no attribute 'relay_bound'`)
    and that a relay at its full bound passes while one above it fails
    (`test_relay_holding_two_bundles_per_client_passes`). `peak_bound` keeps its meaning (the bound the
    relay enforces; it was mis-computed), so the evidence schema stays 2.
- **Touches:** `src/hypershell/core/reconnect.py` (new), `src/hypershell/relay/` (`__init__.py`, `upstream.py`,
  `store.py`, `site.py`), `src/hypershell/core/config.py` (`[relay] poll`, `[relay.upstream] deadline`,
  `backoff`), `src/hypershell/cluster/remote.py` (`AutoScalingCluster(evict_after=…)`, A5),
  `docs/_include/config_param_ref.rst`, `docs/_include/relay_desc.rst`, `tests/test_relay_link.py` (new),
  `tests/test_relay_outage.py` (new), `tests/test_relay_fsm.py`, `tests/test_relay_connector.py`,
  `tests/test_relay_return.py`, `tests/test_relay_launch.py`, `tests/test_relay_workload.py`,
  `spec/relay/drive/relay_probe.py`, `spec/relay/drive/relay_throughput.py` (new). No zone code
  (`core/dispatch.py`, `core/queue.py`, `server.py`), `client.py` or completion change (no new option).

## Phase P9 — Multi-zone relays and relay docs
**Satisfies:** R2, R8, R18, R20 · **Depends on:** P8 · PLAN §2.6, §2.8
**Goal:** one relay serves several zones, each as its own upstream identity and its own site fleet;
failover reverts and unstages every zone it held; the docs carry the relay trust model and
non-goals.
**Status:** done (2026-11-06). Built 2026-11-05 with unit gates green here; the foreman's Linux run of
the verify at `d4a2ec8` passed (this host cannot start servers, as in P8). Review cycle 4 approved it;
its remediation follows the phase.

- [x] Security-cycle overlap rule for P9 (above): starts only after hardening has landed and been
      merged. (Met 2026-11-05; see the overlap section.)
- [x] `[relay] zone` becomes a list (`--zone` repeatable); `[relay.zone.<name>]` overrides
      `launcher`, `launcher_args`, `size`.
- [x] Puller cycles zones with a short `wait`; per-zone gate; confirm under the requesting zone's
      identity; heartbeat every identity, roster on the holder.
- [x] ~~`cluster/remote.py`: minimal, behaviour-preserving extraction of `AutoScalingCluster`'s
      client-invocation build into a module function~~ **Dropped (merge round, 2026-11-05):**
      hardening exposes `cluster/launch.py` `client_launch()`. Extra zone fleets build their
      `ClientLaunch` with it and run an `AutoScalerThread(launch, …)`; each zone's fleet goes through
      the credential fence (`check_invocation` on its launch). No `cluster/` edit.
- [x] Tests: `test_relay_connector.py::test_each_zone_requests_independently` (demand holds one
      entry per identity; Z2 dispatcher untouched); `test_relay_workload.py::
      test_multizone_relay_takes_only_its_zones` (a zone with no client stays unscheduled);
      failover of a two-zone relay reverts and unstages both zones.
- [x] Docs: `docs/security.rst` gains a relay section (two auth domains, TLS pinning across the WAN
      hop, key exposure confined within each site; since #80 no key is on any site process's
      command line, so state the site key's handoff in the site client's environment as #80's
      section does, not process-table exposure); `docs/cli/relay.rst` states the non-goals (no
      throughput gain, no cross-site cancellation or output retrieval, no site-topology
      autoscaling, synchronised clocks needed for eviction); `docs/database.rst` (relay-local file,
      node-local disk advice); help snippets and completions for the multi-zone options.
- [x] `AGENTS.md`: relay in the repository map and footguns (one relay per process; import-time
      database swap; relay-only tables; no in-process dial of the local server); `DISTRIBUTED_ROLES`
      location (D11). `invariants.md` §10/§11 notes in lockstep.
- [x] *C3-1 (ruled 2026-11-05):* the relay SIGTERM carve-out in `AGENTS.md` §Signals and
      `invariants.md` §8 (catch, clean up, re-deliver; USR1/USR2 drain; a second SIGTERM ends at once).
- [x] *C3-3 (ruled 2026-11-05):* `relay_desc.rst` / `hs relay --help`: a second SIGTERM also leaves
      the site config copy behind (not only SIGKILL).
- [x] *Foreman, Linux concurrency gate at `1393af6`:* `test_relay_ended_by_a_signal_removes_the_site_config_copy
      [SIGINT]` failed 6 of 6 as background jobs (SIGINT inherited as SIG_IGN; harness, not relay).
      Fixed in the test: the relay is started inside `interruptible()`, which installs Python's SIGINT
      handler in the test process while it spawns, so the child gets SIGINT at its default action
      (an ignored signal survives exec; a handled one is reset); off the main thread, with SIGINT
      ignored, it skips with that reason.
- [x] *Foreman:* `spec/relay/drive/relay_throughput.py` hung when run outside `uv run` (site clients:
      `env: 'hyper-shell': No such file or directory`, relaunched forever). It now puts `dirname(HS)`
      first on `PATH` for every process (as `relay_drive.sh` does), fails with a message naming the
      logs when no client is live within `--live-wait` (120 s), when the relay exits before the
      upstream server, or past `--timeout`, and prints progress lines with `flush=True`.
- [x] Full suite passes (the foreman's Linux run at `d4a2ec8`, 2026-11-06: serial on 3.12, 1331
      passed, 87 skipped, 0 failed; the relay files on 3.14 and the multi-zone tests pass; review
      cycle 4 records it). The cycle-4 remediation below adds unit tests only.
- **Amendments and deviations (2026-11-05, build; the conservative reading each time):**
  - **`[relay] zone` forms.** The plan's `[relay] zone` list and `[relay.zone.<name>]` tables cannot
    both be one TOML key (`relay.zone` is either an array or a table). `relay.zone` therefore takes
    one name, a list of names, or one table per zone (`[relay.zone.gpu]`, possibly empty), the table
    form carrying that zone's `launcher`, `launcher_args`, `size` (`relay/__init__.py::zone_options`).
    Any other per-zone key, a repeated zone (after `zone_name` normalisation), or no zone is refused
    (`ArgumentError`). `--zone` (repeatable) names the zones in place of the configuration; each still
    takes its table's settings. A zone's own setting wins over the relay-wide value for that zone,
    whether the relay-wide value came from the command line or configuration.
  - **Per-zone client count.** The relay-local `client` table has no zone column, and the model is not
    changed for it, so a multi-zone gate counts a zone's site clients from the relay-local server's
    dispatcher (`Dispatcher.clients(zone_id)`: clients that have asked for that zone and have not
    finished or been evicted; read in process, no dial). A client counts from its first request, so a
    zone's first pull can come one request later than with the `client` table. **A single-zone relay
    is unchanged:** the `client`-table count and the total backlog, exactly as in P8.
  - **Per-zone backlog** is `count_backlog(zone=zone_id(zone))` for a multi-zone relay.
  - **Short wait.** With more than one zone, requests wait upstream at most `ZONE_REQUEST_WAIT` (1 s)
    so an empty zone does not hold up the others; one zone keeps `DEFAULT_REQUEST_WAIT`. The gate takes
    zones in turn from the one after the last asked; a full zone (or one with no client) is skipped.
  - **Shutdown per zone** (PLAN §2.9 "in every zone"): the upstream dispatcher answers `shutdown` per
    pool, so a zone that gets one is asked no more, and pulling ends once every zone has had one.
  - **Fleets.** The first zone's fleet is the `AutoScalingCluster`'s; each further zone's is
    `relay/site.py::zone_fleet` (a fixed-policy `AutoScalerThread`, named `hypershell-autoscaler-<zone>`)
    over `zone_launch`, which calls `client_launch()` with the same values `AutoScalingCluster` uses
    and the cluster's site key and port. A unit test pins that, for the same zone and launcher, the
    argv and environment equal the cluster's own, and that another zone differs only in `--zone`.
    No `cluster/` edit. Every fleet is fenced (`fence_site(..., launches=)`), supervised
    (`check_site`), stopped (`stop_site`, `abort`) and reaped (`end_site_clients(..., fleets=)`).
  - **Heartbeat.** One heartbeat machine beats every identity in turn on its one link role (no new
    upstream connection per zone); the holder (first zone) carries the roster, every beat the site.
    The `beat` link stage is passed once all identities have beaten. FINISHED goes out under each.
  - **Drive untouched.** The R21 drive and judge keep the approved 3-node shared-zone matrix; the
    single-zone path they exercise is unchanged.
  - **Not added:** a `--site` option (P6 left it to P9 or a ruling; config `[relay] site` stays the
    only way) and `--advertise` (P4 ruling 1; nothing here needed it).
  - **`test_one_relay_end_to_end` under 6x load** (one copy 452 s, timed out once in 6): not changed.
    Its 240 s `DEADLINE` is shared by the file; a scale knob would be new test configuration for a load
    artefact. For the foreman.
- **Red first (2026-11-05),** new tests against the `8a0f3b3` source (a scratch export on `PYTHONPATH`):
  `test_relay_connector.py` and `test_relay_launch.py` fail at collection (`cannot import name
  'served_zones'` / `'zone_options'`); in `test_relay_roster.py`
  `test_every_identity_beats_and_the_holder_carries_the_roster` and
  `test_failover_of_a_two_zone_relay_reverts_and_unstages_both_zones` fail (`unexpected keyword argument
  'identities'`). The two new workload tests and the SIGINT fix start servers: not run here.
- **Gates here (2026-11-05), no servers (POSIX semaphores still exhausted):**
  - Relay unit tests (`-m unit`: connector, roster, launch, fsm, link, return, judge, db, main,
    completions): 289 passed on 3.12 and on 3.14.
  - `tests/test_main.py tests/test_completions.py` (venv on `PATH`): 65 passed. `bash -n`/`zsh -n` on
    the completions: clean. `hs relay --help` shows the new text.
  - Sphinx `-W --keep-going` (sandbox off): only the three known warnings.
  - `relay_throughput.py` with an `HS` that does not exist: one line naming it, `FAIL`, exit 1.
- **Touches:** `src/hypershell/relay/` (`__init__.py`, `upstream.py`, `site.py`), `src/hypershell/core/config.py`
  (comment), `docs/` (`security.rst`, `cli/relay.rst`, `_include/database.rst`, `relay_desc.rst`,
  `relay_help.rst`, `relay_usage.rst`, `config_param_ref.rst`), `share/zsh/site-functions/_hs`,
  `AGENTS.md`, `.agents/factory/invariants.md`, `tests/test_relay_connector.py`, `tests/test_relay_roster.py`,
  `tests/test_relay_launch.py`, `tests/test_relay_workload.py`, `spec/relay/drive/relay_throughput.py`.
  No `cluster/`, zone code, `client.py`, `server.py` or `data/model.py` change.
- **Verify:** see frontmatter.

## Review-4 remediation round (2026-11-06)

Review cycle 4 approved P7–P9 with follow-ups. The foreman's rulings: fix C4-1 now; kill the C4-3
survivors where a test is cheap (M41 first, then M23, M19, M18, M36); fix C4-5; C4-2 and C4-4 stay
follow-ups.

- [x] **C4-1: a zone with no site client never heard the upstream shutdown.** The gate passes over a
      zone whose bound is 0, so such a zone never asked, never had a shutdown reply, and pulling never
      ended: the relay sent no FINISHED beats and a non-forever upstream server never exited. Now,
      once any zone has had a shutdown reply, the gate ends every zone with no site client and no task
      held (`UpstreamPuller.finish_idle`); a zone with site clients, or with tasks still held, asks for
      itself and ends on its own reply. Safe because the upstream dispatcher finishes for every zone
      at once: a non-forever upstream server finishes only once no task is incomplete, and a drained
      one puts back whatever is still staged when it exits.
      - *Residual (stated in `relay_desc.rst`):* a relay with no site client in **any** zone never asks
        (asking would take tasks its site cannot run, against R8's bound of 0), so it cannot hear a
        shutdown; it ends on a drain or a stop, as before. No request form asks "are you finishing?"
        without taking tasks, and adding one is an upstream protocol change.
      - *Red first:* against the unfixed puller, the scripted upstream that answers every request with
        `shutdown` left clients `{'cpu': 1, 'gpu': 0}` at GATE after 20 actions, in both zone orders,
        and a `gpu` zone holding tasks never ended once they were done (3 failed). With the fix: pass.
        The safety tests passed both before and after: a single zone (and two zones) with 0 clients
        never asks; a zone with clients asks for itself after another's shutdown; a zone holding
        tasks is not ended until they are done; no zone is ended before any shutdown.
      - Mutants of the fix (the call dropped; ending idle zones before any shutdown; dropping the
        no-client or the nothing-held condition) are each killed.
- [x] **C4-3: tests for the survivors**, each shown killing its mutant on a copy of `src/` here (all
      unit tests; none starts a server):
      - M41 (`relay/__init__.py`, the wait for the first upstream connection):
        `test_relay_starts_its_site_only_once_the_upstream_server_answers`. The link is a stand-in that
        is not up until told, and starting the site raises: for 2.5 s nothing is started, then the site
        starts once the link is up.
      - M23 (further zones' launches not fenced): `test_relay_fences_the_launch_of_every_zone_before_connecting`
        (a further zone's launcher holding the key ends `run_with_exceptions` with the fence error before
        any link is made; the fence runs on stand-ins, so the test process's environment is untouched).
      - M19 (further zones' autoscalers not supervised): `test_site_check_needs_every_zone_fleet_until_the_site_stops`.
      - M18 (zone `size` validation): `test_zone_table_size_must_be_a_whole_number_of_at_least_one`.
      - M36 (`store.count_backlog`, the default zone's `IS NULL` match):
        `test_default_zone_backlog_is_its_own_in_a_relay_serving_several_zones`.
- [x] **C4-5:** `relay_drive.sh`'s header states the doubled bound (clients × bundle × 2).
- **Follow-ups (not fixed here, by ruling):**
  - *C4-2:* a TLS on/off mismatch with the upstream server is retried as a transport error (the
    `ssl.SSLError` of `WRONG_VERSION_NUMBER`, or the connect deadline), not failed fast. Candidate:
    fail fast on that class at the first connection, or add a hint to the "Cannot reach" warning.
  - *C4-4:* the judge's four vacuity cases: require `env_unreadable == 0`, the presence of
    `key_in_server_log`, at least 2 groups, and schema 2 under `--require`. Run B's records show none
    of the four.
  - *C4-3, rest:* M26 (the epoch guard in `mark_down`) and M28 (a non-transport connect error failing
    the link) in `core/reconnect.py` were not in the ruling's list.
  - *Throughput tail (review-4 note):* 4 of 9 Linux throughput runs had a 6–9 s end-of-run tail (once
    about 60 s); rerun `relay_throughput.py` at DEBUG with the scratch kept until an outlier occurs.
- **Gates here (no servers):** relay unit tests with `test_main.py`, `test_completions.py` and
  `test_cluster_launch.py` (`-m unit`): 304 passed on 3.12, 3.14 and 3.15. The full suite is the
  foreman's Linux run.
- **Touches:** `src/hypershell/relay/upstream.py`, `docs/_include/relay_desc.rst`,
  `tests/test_relay_connector.py`, `tests/test_relay_launch.py`, `tests/test_relay_fsm.py`,
  `spec/relay/drive/relay_drive.sh`, this file.

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
