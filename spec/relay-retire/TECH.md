---
slug: relay-retire
title: Retire a superseded relay, and wake the main thread for signals
kind: fix
appetite: big
status: in_review
branch: fix/relay-retire
base: develop
current_phase: done
last_updated: '2026-11-13'
phases:
- id: P1
  name: 'Main-thread signal wake: one bounded-step wait for every long-running command'
  status: done
  satisfies:
  - R11
  - R12
  - R13
  - R14
  - R15
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_signal_wake.py tests/test_signal.py tests/test_relay_db.py
    tests/test_client.py tests/test_server.py tests/test_server_fsm.py tests/test_relay_fsm.py
    tests/test_relay_workload.py
- id: P2
  name: 'Server half of retire: the retired mark on the topology reply, the handback
    call, and the dispatch-matched requeue'
  status: done
  satisfies:
  - R1
  - R4
  - R5
  - R8
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_topology_plan.py tests/test_topology_server.py
    tests/test_dispatch.py tests/test_dispatch_version.py tests/test_server.py tests/test_server_fsm.py
    tests/test_relay_topology.py && for mode in founding fixed dynamic restart; do
    .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
    $mode || exit 1; done
- id: P3
  name: 'Relay half of retire: latch, stop pulling and launching, withdraw and hand
    back, finish and exit; docs; laptop evidence'
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  - R9
  - R10
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: for mode in retire retire-red pair-relay pair-server idle; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/relay-retire/drive/retire_drive.sh" $mode || exit 1; done && sh
    spec/relay/drive/relay_smoke.sh && for mode in founding fixed dynamic restart;
    do .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
    $mode || exit 1; done && uv run pytest && uv run --python 3.12 --group docs sphinx-build
    -W --keep-going -q -E -b html docs docs/_build/html
- id: P4
  name: 'Multi-node cluster run of the retire scenario (split gate: builder prepares
    and rehearses, foreman runs, gate judges)'
  status: done
  satisfies:
  - R3
  - R5
  - R6
  - R7
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_retire_judge.py && uv run python spec/relay-retire/drive/retire_judge.py
    spec/relay-retire/evidence/cluster --require retire
review:
  last_reviewed_commit: f98614f40768d0591fe529cf10cada7a946589af
  verdict: changes-requested
  blocked_reason: 'review cycle 1: RV1 retire strands a loading chunk (R4/R6/R7);
    RV2 restart order retires the newer relay (R1/R9/R7)'
  cycle: 1
---
# TECH.md — Retire a superseded relay, and wake the main thread for signals

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay-retire/TECH.md`); the per-phase checklists
below are the work. `hs-build` executes the next actionable phase, runs its `verify:` command,
updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic code+state
commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract (locked at the
  GOAL gate, 2026-11-13).
- **Authoritative design:** [`PLAN.md`](PLAN.md). Section numbers below (§2.x) are PLAN's.
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–04.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work. The coupled core
  (`data/model.py`, `server.py`, `client.py`, `core/*`) is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` → `crest` → `downhill`. A phase stuck `uphill` across builds is a raised hand.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt`; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase; CLI drives run under
  `.agents/factory/bin/temp_site.sh`, never against the developer's real database.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md). This change touches invariants §1, §4, §5, §6, §8, §9, §11, §12
  (PLAN §3). High-blast-radius files touched: `core/thread.py` (P1), `server.py` (P1, P2),
  `client.py` (P1), `cluster/remote.py` and `cluster/ssh.py` (P1, one call each), `data/model.py`
  (P2): a CONFIRMED review finding in any of them is a human gate.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[fix] Build relay-retire P<n>: …`, signed, **no `Co-Authored-By`
  trailer**.
- A user-visible change updates its prose page, marked `.. versionchanged:: 2.9.0`, and any
  `docs/_include/*.rst` help snippet it changes, in the same commit (P3). No CLI option or config key
  changes in this cycle, so `share/` completions and `config_param_ref.rst` are untouched; a phase
  that finds it needs one stops and amends this file first.
- **Today's behaviour is the guard.** Every existing test stays green with its assertions unchanged,
  except the declared amendments:
  - `tests/test_dispatch_version.py::test_dispatcher_remote_surface_is_pinned` gains `'handback'`
    (P2; PLAN §3 deviation 1);
  - nothing else. A phase that needs another amendment records it here, with the reason, in its
    commit.
- **Generic names; this repo is public.** Docs, tests, drives, records and scripts name no real
  cluster, account, partition, host, user or local path. Sites are `A`/`A2`/`B`, zones `cpu`,
  `highmem`, `gpu`. Examples never put a key on a command line (`HYPERSHELL_SERVER_AUTH` or
  `server.auth`), and docs name top-level commands only.
- **Processes.** Every process a drive or test starts is stopped by its recorded PID (SIGTERM, then
  SIGKILL after a grace); `UV_CACHE_DIR` under `$TMPDIR`. No builder calls a cluster tool or
  submits a job (P4 is run by the foreman).

## Interfaces (built in P1–P3)

| Module | Name | Contract |
|--------|------|----------|
| `core/thread.py` | `WAKE_INTERVAL = 0.5`, `join_waking(thread, interval=WAKE_INTERVAL)` (in `__all__`) | `while thread.is_alive(): thread.join(timeout=interval)`, then `thread.join()` (re-raises the captured failure as today); `Thread.join` unchanged |
| `relay/upstream.py` | `log_drain()` | once per process (lock-guarded flag): WARNING `Drain requested - no more upstream requests`; called by the puller's gate and by `RelayThread.wait_site` |
| `core/topology.py` | `Topology(version, entries, checks=0, retired=frozenset())`, `TopologyReply(version, zones, checks=0, retired=False)` | `retired` = upstream identities of superseded instances; never part of the version decision |
| `core/topology.py` | `reply_text(topology, identities)`, `read_topology(text)` | adds top-level `"retired": true` only when some requested identity is retired; reads `data.get('retired') is True`; protocol stays 1 |
| `core/topology.py` | `handback_text(identity, tasks)`, `read_handback(text) -> (identity, [(id, schedule_time)])`, `handback_reply(accepted)`, `read_handback_reply(text) -> int`, `MAX_HANDBACK_TASKS = 1000`, `MAX_HANDBACK_REQUEST = 256 KiB`, `MAX_HANDBACK_FIELD = 64` | `{"protocol":1,"client":…,"tasks":[{"id":…,"schedule_time":…}]}` → `{"protocol":1,"accepted":n}`; identity and task ids must be UUIDs (returned canonical), schedule times ISO 8601 text (returned unchanged), no task twice; `ValueError` on anything malformed; errors answered with `error_text` |
| `cluster/topology.py` | `retired_identities(records) -> FrozenSet[str]`, `retiring_instances(records) -> Set[(site, instance)]` | pure; every identity of every older instance of a relay-wide `Duplicate`, and of every instance that says it is retiring; none for records with no instance key. `_arrange` picks the holder among instances not retiring only (review cycle 1, RV2) |
| `core/heartbeat.py` | `Heartbeat.retiring: bool = False` | packed as top-level `"retiring": true` only when set; read as `data.get('retiring') is True`; a server of the earlier build ignores the key (review cycle 1, RV2) |
| `core/topology.py` | `SiteRecord.retiring: bool = False`, `SiteRegistry.record(…, retiring=False)` | sticky per identity until the record is dropped; `HeartMonitor.record_site` passes `hb.retiring` |
| `core/dispatch.py` | `Dispatcher.handback(text) -> str`, `Dispatcher.take_handbacks() -> List[(identity, [(id, schedule_time)])]` | database-free; queues under the lock, `_signal_need()`; refuses in memory; `DISPATCH_EXPOSED = ('request', 'takes_submissions', 'topology', 'handback')` |
| `data/model.py` | `Task.revert_handed_back(client_id, assignments) -> List[str]` | one conditional UPDATE per row on `id`, `schedule_time` as dispatched, `completion_time IS NULL`, `client_id IN (client_id, NULL)`; §1 contention rule; returns ids reverted |
| `server.py` | `Scheduler.revert_handed_back()` | at the top of `load_bundle`, beside `revert_unstaged`; INFO `Requeued N tasks handed back by relay (identity)` |
| `relay/site.py` | `TopologyView.retired` (latched), `TopologyView.retire() -> bool` | set once by the heartbeat, never cleared (`retire()` is true only for the call that set it); `SiteScaler` launches none while set (`init`, `check_zones`, `reconcile`), and its `wait` never turns an unapplied version into a check once retired |
| `relay/upstream.py` | `UpstreamPuller(…, retired=callable)` (default `never`); `UpstreamHeartbeat.fetch` latches and logs once | GATE and REQUEST check `retired()`; WARNING `Superseded at site S by a newer relay - retiring: no more upstream requests, site clients finish what they hold` |
| `relay/store.py` | `unstarted(engine) -> List[(id, identity, schedule_time)]`, `drop_handed_back(engine, ids) -> int` | incomplete rows with no local `schedule_time` and an upstream stamp; delete matches `schedule_time IS NULL AND completion_time IS NULL`, stamps with them, one transaction under `_WRITE_LOCK` |
| `relay/__init__.py` | `RelayThread.retire()`, `RelayThread.withdraw() -> bool`, `RelayThread.hand_back()`, `RelayThread.superseded()`, `handback_due`, `withdrawn` | PLAN §2.4 order; `withdraw` runs once the relay-local scheduler has stopped (false while it runs): `server.revert_staged()` plus `Task.revert_staged` of the chunk in `scheduler.machine.loaded` (loaded, not staged); `hand_back` withdraws first and stays due while the scheduler runs; clears `handback_due` on success or refusal (`refuse_hand_back`, one WARNING) and leaves it set when the link loses a chunk; retried each check (in `wait_site` and `stop_site`) and once before `finish_upstream` |
| `relay/upstream.py` | `UpstreamHeartbeat.submit` | every identity's beat carries `retiring=True` once the view is retired, the final beat included (review cycle 1, RV2) |

---

## Phase P1 — Main-thread signal wake
**Satisfies:** R11, R12, R13, R14, R15 · **Depends on:** —
**Goal:** every long-running command's main thread waits on its worker in bounded steps, so a
SIGUSR1/SIGUSR2/SIGHUP taken by any thread is acted on within 2 s; nothing else changes. First
because it is small, independent of retire, and stops the CI hang every later phase's CI run risks.

- [x] `core/thread.py`: `WAKE_INTERVAL` and `join_waking()` (PLAN §2.5); unit tests in
      `tests/test_signal_wake.py`: a finished thread returns at once; a worker failure is re-raised
      unchanged; a handler for a signal `pthread_kill`ed at the worker runs on the main thread within
      `WAKE_INTERVAL` + slack while the main thread waits.
- [x] Switch the nine main-thread untimed joins named in PLAN §2.5 (relay, client, server ×2,
      `serve_with_sites`, cluster local/ssh/remote, submit) to `join_waking(thread)`; leave every
      `try`/`except`/`finally` as it is.
- [x] `relay/upstream.py`: `log_drain()` once-only; the puller's gate calls it in place of its
      direct WARNING, and `RelayThread.wait_site` calls it the first time `draining()` is true
      (R11's 1.5 s bound; same single line as today).
- [x] R15 tests (`@mark.integration`, in `tests/test_signal_wake.py`): `hs relay`, `hs client`,
      `hs server` each through the injector wrapper of PLAN §6 (a file named `hs`; its thread waits
      for a go-file and for the main thread parked in its join, then sends SIGUSR1 to its own thread
      with `signal.pthread_kill`); relay asserts exit 0 and "Drain requested"; client (beside a plain
      server, fired after "Registered client") exit 0 and "Signal interrupt (SIGUSR1)"; server (a
      plain client connected first) exit 0, "Signal interrupt (SIGUSR1)" and "Done". Run them once against `develop`'s source (a `git archive fb89670 src` export on
      `PYTHONPATH`, no stash) and record in the commit body that each fails there.
- [x] R12 coverage beyond R15: the same injector drives `hs cluster` (local) and `hs submit`; each
      acts on the self-`pthread_kill`ed SIGUSR1 as it does when the main thread takes it (no red
      requirement).
- [x] R13/R14: SIGTERM/SIGINT and worker-failure paths unchanged — existing tests stay green; add
      one test that a worker exception still surfaces through `.wait()` with today's exit status.
- **Verify:** `uv run pytest -v tests/test_signal_wake.py tests/test_signal.py tests/test_relay_db.py
  tests/test_client.py tests/test_server.py tests/test_server_fsm.py tests/test_relay_fsm.py
  tests/test_relay_workload.py` (the last guards the moved drain line).
- **Touches:** `src/hypershell/core/thread.py`, `relay/__init__.py`, `relay/upstream.py`, `client.py`, `server.py`,
  `cluster/topology.py`, `cluster/local.py`, `cluster/ssh.py`, `cluster/remote.py`, `submit.py`,
  `tests/test_signal_wake.py`.

## Phase P2 — Server half of retire
**Satisfies:** R1, R4, R5, R8, R9 (server side) · **Depends on:** P1
**Goal:** a server autoscaling with sites marks every identity of a superseded instance retired in
that instance's topology reply, accepts a hand-back of named assignments, and requeues exactly those
on its scheduler's next pass; every reply without a retired identity is byte-identical to today's.

- [x] `core/topology.py`: `Topology.retired`, `TopologyReply.retired`, `reply_text`/`read_topology`
      (PLAN §2.1); hand-back wire helpers and bounds (PLAN §2.2).
- [x] `cluster/topology.py`: `retired_identities`; `TopologyPublisher.check`/`publish` carry it on
      every check without a new version; INFO once per newly retired instance.
- [x] `core/dispatch.py` + `core/queue.py`: `Dispatcher.handback`, `take_handbacks`;
      `DISPATCH_EXPOSED` gains `'handback'` (declared amendment of the pinned-surface test).
- [x] `data/model.py`: `Task.revert_handed_back` (PLAN §2.3; invariants §1 contention rule, modelled
      on `__apply_result`).
- [x] `server.py`: `Scheduler.revert_handed_back()` at the top of `load_bundle`.
- [x] Tests: `tests/test_topology_plan.py` (`retired_identities` table: one relay, two instances,
      three instances, mixed with no-instance records; reply with/without mark; an 8403388-shaped
      reader — the `read_topology` of `fb89670` copied into the test as a fixture function — reads a
      marked reply exactly as an unmarked one); `tests/test_topology_server.py` (publisher carries
      `retired` with no version bump; `handback` over a real queue server requeues the named
      assignments; skips a row re-dispatched, completed or cancelled; reverts an unconfirmed one;
      the requeued rows are staged for another client); `tests/test_dispatch.py` (validation,
      bounds, in-memory refusal, wake of the scheduler).
- [x] (Added in build) `tests/test_contended_write.py`: the hand-back revert on PostgreSQL and
      TimescaleDB, matched on the stamp as a relay received it, and under contention (lands after the
      relay's own late confirmation; settled, with no warning, after a result or another client's
      confirmation), per invariants §1.
- **Review cycle 1 remediation (RV2, server side; GOAL "Ruled at review cycle 1").** Reopened.
  - [x] `core/heartbeat.py`: optional `retiring` field, packed only when true, read only as JSON
        `true` (shape chosen after reading the base's `Heartbeat.unpack_or_none`: it reads known keys
        with `.get` and ignores the rest, so a top-level boolean key is invisible to an fb89670 server).
  - [x] `core/topology.py`: `SiteRecord.retiring`, recorded sticky by `SiteRegistry.record`;
        `server.py` `HeartMonitor.record_site` passes `hb.retiring` (one argument; the scheduler is
        untouched).
  - [x] `cluster/topology.py`: `_arrange` chooses the holder among instances not retiring, emits no
        duplicate with a retiring holder, and `retired_identities` adds every identity of a retiring
        instance; `TopologyPublisher.note_retiring` logs once for one with no holder. Relays that
        never send the field are ordered by registration exactly as before.
  - [x] Tests: `tests/test_topology_plan.py` (`RETIRING_ROWS`: later registration, re-registration
        after eviction, one identity retiring, alone, two retiring, beside a no-instance record, and
        the residual restart-before-the-mark row pinned on registration order; the earlier table
        unchanged without the field; the registry keeps it; the beat wire against an fb89670-shaped
        reader); `tests/test_topology_server.py` (evicted and re-registered through a real
        `HeartMonitor` and publisher never retires the holder; alone at its site; a beat without the
        field handled as before).
  - Wire evidence beyond the suite: the fb89670 `HeartMonitor` itself (a `git archive` on
    `PYTHONPATH`) fed a beat with `"retiring": true` records the identical `SiteRecord` and beat,
    with no error.
- **Verify:** see frontmatter (unit/integration suites above + the four `zone_drive.sh` founding
  modes, R9).
- **Touches:** `src/hypershell/core/topology.py`, `cluster/topology.py`, `core/dispatch.py`,
  `core/queue.py`, `core/heartbeat.py`, `data/model.py`, `server.py`, tests.

## Phase P3 — Relay half of retire, docs, laptop evidence
**Satisfies:** R2, R3, R4, R5, R6, R7, R8, R9, R10 · **Depends on:** P2
**Goal:** a relay that reads the mark stops pulling and launching, hands back everything no site
client has received, lets its clients finish, sends its final heartbeat under every identity and
exits 0; proven red on `develop` and green here in the RV5 scenario, with the old/new pairings and
the founding case unchanged.

- [x] `relay/site.py`: `TopologyView.retired` latch; `SiteScaler` launches none while retired.
- [x] `relay/upstream.py`: `fetch` latches and logs the single WARNING (R2); `UpstreamPuller` checks
      `retired()` at GATE and REQUEST.
- [x] `relay/store.py`: `unstarted`, `drop_handed_back` (PLAN §2.4).
- [x] `relay/__init__.py`: `RelayThread.retire()` and `hand_back()` in PLAN §2.4's order; `wait_site`
      calls `retire()` once; `stop_site` loop and the step before `finish_upstream` retry a pending
      hand-back; module and class docstrings say what a retired relay does.
- [x] Docs (R10, same commit): remove `docs/cli/relay.rst:76-80`; add the retired behaviour there and
      one sentence in `docs/cli/server.rst`'s duplicate paragraph, `.. versionchanged:: 2.9.0`; one
      sentence in `APP_HELP` and its prose mirror `docs/_include/relay_desc.rst` (amended in build:
      `relay_help.rst` holds only the options, which do not change; the help prose lives in
      `relay_desc.rst`, which no tool generates).
- [x] Tests: `tests/test_relay_topology.py` (latch survives a reply without the mark; single log;
      `SiteScaler` launches none), `tests/test_relay_fsm.py` (puller ends at GATE and does not send
      from REQUEST once retired; an in-flight request is inserted and confirmed),
      a relay-store test (`unstarted` excludes delivered and unstamped rows; `drop_handed_back` never
      deletes a row that gained a local `schedule_time`), and an integration test of `retire()` against
      a real upstream queue server (handed-back rows requeued upstream; a delivered row runs once
      and its result lands; final heartbeat; exit path) and of the refusal/lost-link fallbacks
      (built as `tests/test_relay_retire.py`: the store tests, `hand_back` over a real queue server in
      chunks with the scheduler's requeue, a server without the call, an error reply, a link lost and
      back; and one end-to-end test, a server autoscaling with sites and relays A and A2 as processes).
- [x] `spec/relay-retire/drive/retire_drive.sh` (POSIX `sh`, built from
      `spec/relay-topology/drive/topology_drive.sh` and its shim; that cycle's files stay as its
      record) and `retire_judge.py`, modes (PLAN §6):
  - `retire` — RV5 on this branch: judge as PLAN §6, including R7's timed check (≤ 3 × heartrate
    from A's first "Superseded" line, relays logging in the system style) and non-vacuity;
  - `retire-red` — the same scenario with every role on `fb89670` (a `git archive` on `PYTHONPATH`):
    passes only if the judge reports the stranding (a row on A's identity > 20 s with A at zero
    clients), i.e. red on `develop` is reproduced deterministically;
  - `pair-relay` — relay A on `fb89670`, server and A2 on the branch: no topology read error at A,
    behaviour as today (A strands until stopped and evicted; eviction shortened for the mode);
  - `pair-server` — server on `fb89670`, relays on the branch: no relay retires, no hand-back call,
    behaviour as today;
  - `idle` — one relay per site stays connected at zero clients (R9 / relay-topology R5).
  Calibrate on `develop` ×3 before fixing the knobs (research 03 §4); keep the whole verify under
  ~10 minutes. `DRIVE_EVIDENCE=<dir>` writes scrubbed `MODE.txt` to `spec/relay-retire/evidence/laptop/`.
- [x] Record the red and green runs (`retire-red`, `retire`) and the pairings as evidence.
- **Build notes (P3, amendments).**
  - *Drive knobs, calibrated.* With `--autoscaling=dynamic` and A2 started once A ran 3 clients, A
    read the mark before it held anything its clients had not taken (every run INCONCLUSIVE): the
    dynamic plan replaces A's idle clients, which take what A pulls. The drive therefore runs the
    server `--autoscaling=fixed -P 1` (no replacement while a client runs), relays with `init = max`
    (A 3, A2 2), 12 tasks of 10 s, site-client timeout 3 s, heartrate 1 s, and starts A2 once A has
    run 3 clients and held a task no site client took (relay database, read-only) for 2 s running:
    RV5's "pulling for a client past its idle timeout". Eviction 120 s (`retire`, `retire-red`), 20 s
    (pairings). `retire-red` on `fb89670` reproduced the stranding 3 of 3 (+ the gate run), each in
    under 60 s; `retire` passes with the hand-back non-vacuous.
  - *Judge.* `retire_judge.py strand RUN` is the bounded red watch the drive polls each second (a
    task on A's identity > 20 s with A at zero clients); `retire_judge.py judge RUN MODE` judges a
    run (P4 extends it for the cluster record).
  - *The relay-topology `lower` laptop mode* (PLAN §5), run once on this branch: A reads the mark,
    retires and exits 0, so that committed mode fails three checks. Two are the ones PLAN §5 names:
    "every live relay still runs after its clients left" and "no relay identity left or was evicted
    while running" (A's final heartbeat logs `Relay site left`; nothing was evicted). The third is
    "every one of A's clients ended by idle exit" (0 of 3 seen): A's clients now leave on the
    relay-local server's shutdown reply after A stopped its fleets, so no autoscaler logs their
    disconnect; none was terminated ("no relay terminated a site client" passes), and every other
    check passes (60 of 60 tasks, A launched nothing after the warning, A2 launched within 10 s).
    Its successor is this cycle's `retire` mode; `spec/relay-topology/` and its committed evidence
    are left as that cycle's record.
- **Review cycle 1 remediation (RV1, RV2 relay side; GOAL "Ruled at review cycle 1").** Reopened.
  - [x] RV1, `relay/__init__.py`: `retire()` no longer withdraws while the relay-local scheduler may
        still run. `withdraw()` waits for it to have stopped (false until then, so the hand-back stays
        due and `wait_site`, `stop_site` and the step before `finish_upstream` try again), then takes
        back the staged rows (`server.revert_staged()`) and the chunk the scheduler had loaded but not
        staged (`scheduler.machine.loaded`, ids read with `sqlalchemy.inspect(...).identity` so no
        expired row is refreshed on another thread's session) with `Task.revert_staged`. Every row
        no site client has received is then unscheduled and handed back; a delivered row is never in
        a pool or in `loaded`, so it stays (R5). No change to `server.py`'s scheduler or `core/fsm.py`.
  - [x] RV1 tests (`tests/test_relay_retire.py`, from the reviewer's probe): a real
        `SchedulerThread` stopped between load and stage hands back the three loaded rows and the one
        never loaded, not the delivered one, then finishes upstream with the final heartbeat; a
        scheduler that outlives `STOP_TIMEOUT` hands back nothing until it has stopped, then all four.
        Both fail on `a1fcb8c` (one task handed back of four; the hand-back no longer due while the
        scheduler still runs) and pass here.
  - [x] RV2, `relay/upstream.py`: every beat says `retiring` once the view is retired.
        `tests/test_relay_topology.py`: no beat says it before the mark, every identity's beat says it
        from the next beat on, final beat included, even when later replies drop the mark.
  - [x] RV2 end to end (`tests/test_relay_retire.py`, from the reviewer's probe): A retiring, B holding,
        the upstream server restarted with A registering again after B: the new server retires A
        again (held by B), B is never superseded and runs what is left, A exits 0 with its final
        heartbeat, nothing evicted. Fails on `a1fcb8c` (the new server retires B) and passes here.
  - [x] Docs: `docs/cli/relay.rst` (a retiring relay says so on every heartbeat, so a restarted
        server or a re-registration keeps the newer relay) and `docs/cli/server.rst` (one sentence),
        under the existing `versionchanged:: 2.9.0`; `AGENTS.md` and `invariants.md` holder rule
        made true again.
- **Verify:** see frontmatter (the drive modes, `relay_smoke.sh`, the founding modes, the full test
  suite, and the docs build with `-W`).
- **Touches:** `src/hypershell/relay/__init__.py`, `relay/upstream.py`, `relay/site.py`,
  `relay/store.py`, `docs/cli/relay.rst`, `docs/cli/server.rst`, `docs/_include/relay_help.rst`,
  `spec/relay-retire/drive/`, `spec/relay-retire/evidence/laptop/`, tests.

## Phase P4 — Multi-node cluster run (split gate)
**Satisfies:** R3, R5, R6, R7 (on real nodes) · **Depends on:** P3
**Goal:** the retire scenario on real nodes: one node runs `hs server --autoscaling`, relay A and
then relay A2 advertise site A on other nodes, clients land through the scheduler's own job steps;
A retires, hands back, finishes and exits 0 with no eviction, and its unstarted tasks are requeued
within 3 × its heartbeat interval.

**Run A** (builder):
- [x] `spec/relay-retire/drive/retire_cluster.sh` in the `topology_cluster.sh` contract (configured by
      environment only, run inside an allocation the foreman holds, no `#SBATCH`, no submission, no
      site names, own key and deadline, cleanup by recorded PID): roles server, A, A2 (A2 on A's
      node is fine, recorder label `A2` with `site = "A"`), heartrate 2 s, eviction longer than the
      run, tasks ≫ site-client idle timeout, `$TASK_ID` run log, system-style logs, `TZ=UTC`.
- [x] Collector and `retire_judge.py` cluster mode: the laptop checks, the R7 bound with the clock-skew
      allowance, A's exit status from its role record; `tests/test_retire_judge.py` (unit) over
      synthetic records, including a broken record the judge must fail and an inconclusive one.
- [x] Local rehearsal through the stand-in `srun` (as `topology_rehearse.sh`), recorded under
      `spec/relay-retire/evidence/rehearsal/`; then set `hill: crest` and wait for the foreman.

- **Build notes (P4 run A, amendments).**
  - *Files.* `retire_cluster.sh` (the drive), `retire_role.py` (plan, status, server with its
    sampler, relay A or A2), `retire_launch.sh` and `retire_record.sh` (copied from the
    relay-topology drive's launcher and recorder, which stay as that cycle's record),
    `retire_collect.py` (the record), `retire_judge.py PATH [--require retire]` (cluster mode beside
    the laptop modes; `strand` and `follow` now share their sample-based core with it), and
    `retire_rehearse.sh`. The foreman's wrapper contract is `topology_cluster.sh`'s unchanged (`HS`,
    `HSD_WORK`, `HSD_RESULTS`, `HSD_NODE_TMP`, `HSD_SRUN_ARGS`, `HSD_LABEL`, `HSD_RUN_ID`): only the
    script path changes.
  - *Staging.* Node 0 runs the server and its read-only sampler of the task table (0.25 s); node 1
    runs relay A, and A2 once A's role cues it; site clients land round robin on nodes 1 to N-1 as
    overlapping job steps. Relay A's role watches its own relay database (read-only) for the P3
    trigger: A has launched its 3 clients and has held a task no site client took for 2 s.
  - *Knobs, calibrated from P3 for heartrate 2 s and a cluster's latencies.* Server
    `--autoscaling=fixed -P 1`; relays A `init = max = 3`, A2 `init = max = 2`, autoscale period 1 s;
    12 tasks of 40 s (P3: 10 s); site-client idle timeout 5 s (P3: 3 s); eviction 1200 s against a
    900 s run deadline (the script refuses an eviction not longer than the deadline); a run with no
    completion for 180 s once A2 has started ends early as a FAIL. In the rehearsal A's clients each
    took a second task when their first finished, and A held the tasks it pulled for them after that
    (the trigger fired 43 s after the submission in both rehearsals); the mark reached A 2 s after the
    cue, while A's clients had 36 s (first rehearsal) and 117 s (second) of what they held left. The 40 s tasks keep that margin for A2's
    start on a shared filesystem and the scheduler's step latency; a shorter margin risks A's clients
    finishing first, A relaunching under its old target, and an INCONCLUSIVE run.
  - *Clock skew.* A record's times keep the clock of the node that wrote them. Where the judge
    compares two nodes' times (A's mark against the server's samples and requeue line; A2's
    departure against its drain) it allows 1 s, the relay-topology judge's `SKEW`: R7's bound is
    6 s + 1 s, and the follow starts at the last sample at or before the mark less 1 s. The delay
    without the allowance is printed beside it.
**Run B** (after the foreman's run): the foreman commits the scrubbed records to
`spec/relay-retire/evidence/cluster/`; the gate runs the judge on them.
- Run B done (2026-11-13): one 4-node job at `29c9bc7`, scenario `retire`, PASS in 335 s; R7 +1.193 s
  of the mark against 6 s + 1 s. Record and README in `evidence/cluster/`.
- **Verify:** `uv run pytest -v tests/test_retire_judge.py && uv run python
  spec/relay-retire/drive/retire_judge.py spec/relay-retire/evidence/cluster --require retire`.
- **Touches:** `spec/relay-retire/drive/`, `spec/relay-retire/evidence/`, `tests/test_retire_judge.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
