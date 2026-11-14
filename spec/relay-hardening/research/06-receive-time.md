# 06 — R7: eviction aged by the server's receive time

Research brief for `hs-plan` (relay-hardening). Line numbers are `develop` at `2ffb5cc` (2.9.0a8).

## Map (`src/hypershell/server.py`)

- `HeartMonitor` `:698`; `beats: Dict[str, Heartbeat]` `:716`, keyed by client uuid.
- `last_check: datetime` `:720`: **wall clock, tz-aware** (`datetime.now().astimezone()`), set at
  `:736` and in `switch_mode` `:907-909`, where it also paces checks (`wait_check` = evict/10).
- Receive: `get_next` `:767`, `self.queue.heartbeat.get(timeout=DEFAULT_REMOTE_TIMEOUT)` `:770`
  (2 s), unpack `:773`, `latest_heartbeat` `:777`.
- Store: `update_client` `:782`; first beat registers (`Client.from_heartbeat` `:793`, or
  `Client.reconnect` `:798`); `self.beats[hb.uuid] = hb` `:801`. FINISHED pops `:809-812`.
- Age: `check_clients` `:913`, `age = self.last_check - hb.time` `:918`, pop `:921`.
- `hb.time` is the sender's wall clock: `Heartbeat.new` (`core/heartbeat.py:~103`), packed as
  `'time'` `:117`. Senders: `client.py:1070`, the relay's identities `relay/upstream.py:820`.

Other readers of `hb.time` / `beats` — all stay as they are (Non-goal):
`Client.from_heartbeat` → `connected_at=hb.time` (`data/model.py:1404-1408`); a leaf's
`connected_at=hb.time` in `update_roster` (`server.py:879`). `beats` is read for membership by
`update_roster` `:864`, `switch_mode` `:904`, `signal_clients` `:937`; none reads its time. The
topology path does not read beat time: `SiteRegistry.record` (`core/topology.py:194`) keeps no
timestamp, and `cluster/topology.py`'s `last_check` (`:376/:403/:421`) is the publisher's own
cadence. Nothing re-packs a heartbeat onward: a relay forwards only `RosterEntry` (uuid, host,
state), and leaves never enter `beats`. No "last seen" column reaches `hs list`/`hs info` or the
client table (`Client` has only `connected_at`/`disconnected_at`). The relay-local server runs
the same `HeartMonitor` for site clients, so one change covers upstream (relay identities, plain
clients) and the relay-local server (site clients).

## Probes (`$TMPDIR/rh-06/probe.py`, candidate by monkeypatch, `evict_after=10`)

On develop: a beat stamped 2×evict behind is **evicted at the next check right after arriving**;
a beat stamped 1 h ahead whose client then goes silent is **still present after evict+1 s** (a
dead client with a fast clock keeps its tasks for skew + evict: the converse defect, also fixed).
Candidate (receive stamp `time.monotonic()`, injected clock): the lagging client beating every
5 s stays for 100 s while the silent ahead/in-sync clients are evicted; once it stops it stays
at age == evict (strict `>` kept) and is evicted at evict + 0.5 s. No wire change.

Existing tests under the candidate: `test_server_fsm.py`, `test_zone_dispatch.py`,
`test_relay_launch.py` pass; **4 fail** because they make a client stale by stamping its
sender time 3×EVICT back, i.e. they encode the behaviour R7 removes:
`test_relay_roster.py::test_relay_eviction_disconnects_roster` (`:223`),
`::test_failover_of_a_two_zone_relay_reverts_and_unstages_both_zones` (`:500`),
`test_topology_server.py::test_eviction_and_finish_drop_the_record` (`:104`),
`::test_a_retiring_relay_evicted_and_registered_again_never_retires_the_holder` (`:435`).
(`test_leaf_never_evicted_on_its_own` `:254` still passes.) Also green under the candidate
(SQLite, 502 passed, 12 skipped): `test_relay_retire`, `_fsm`, `_return`, `_connector`,
`_topology`, `_judge`, `test_topology_plan`, `_judge`, `test_retire_judge`, `test_writeback`,
`test_contended_recovery`, `test_contended_write`. Not run under it: the subprocess e2e suites
(`test_relay_workload.py` and kin), which the in-process monkeypatch does not reach.

## Options

- **A. Wall-clock receive stamp** (`datetime.now().astimezone()`), `age = last_check - received`.
  Smallest diff; immune to sender skew, but a server wall-clock step (NTP) still shifts ages.
- **B. Monotonic receive stamp** (`time.monotonic()`), `now = time.monotonic()` taken in
  `check_clients`; `last_check` stays wall and keeps pacing checks untouched. Immune to sender skew
  and to server wall steps. Two clocks in one class, each with one job.
- C. Add a non-wire `received` field to the `Heartbeat` dataclass: touches a core shared type for
  a server-only concern. Rejected.
- D. `age = min(sender age, receive age)`: spares a lagging client but still waits skew + evict
  for a dead fast-clock client (fails R7's second clause). Rejected.

## Recommendation: B

A `received: Dict[str, float]` beside `beats` (init `:737`), stamped with `time.monotonic()` in
`get_next` right after the `get` (`:770`, kept as `latest_received` next to `latest_heartbeat`) and
stored at `:801` with the beat; popped wherever `beats` pops (`:810`, `:921`); `check_clients`
computes `age = timedelta(seconds=now - self.received[uuid])` from one `now` per check, and keeps
`> self.evict_after`. Invariant: `received.keys() == beats.keys()`. Reason: R7 says "by the
server's own clock" and "whatever the offset"; monotonic is the only clock no host can skew,
and it changes nothing but the age (R10). `hb.time`, `connected_at`, `disconnected_at` (server
wall), `switch_mode`'s cadence and the wire stay.

Ordering: the queue keeps no enqueue time, so dequeue is the earliest receive the server can
know. A backlog (monitor busy in `revert_orphaned` or a slow DB write) stamps a beat late, which
only ages it less, i.e. spares a live client. A fresh beat still unread when a check runs is
judged on the previous one, exactly as today. Start/restart: an empty `beats` evicts no one;
a client is aged only from its first beat to this server (unchanged: `startup_phase`, Issue #29
re-registration).

## R9 test shape (unit, `test_server_fsm.py::TestHeartMonitor` style: `in_memory=True`, stand-in queues, no sleeps)

1. Beat stamped `now - 3*EVICT`, then `check_clients()` → still in `beats` (fails on `2ffb5cc`).
2. Beat stamped `now + 1 h`, then back-date its receive stamp past evict → evicted (fails on
   `2ffb5cc`: not evicted).
3. A beat back-dated to exactly evict is kept; past it, evicted (R7's second clause, as today).
One DB-backed case (`test_relay_roster.py` style) with a relay beat skewed 3×EVICT: not
evicted, its roster stays connected, its tasks are not reverted. Back-date by editing
`machine.received[uuid]` (no new constructor parameter, so `HeartMonitorThread` `:956` is untouched).

## R10

The 4 tests above cannot pass with any R7 implementation; their sender-time staleness must
become a back-dated receive stamp, assertions unchanged. TECH.md should record this as R7's own
consequence (R10's "except what R7 states") so review does not read it as drift; a gate nod is
prudent. Must pass unchanged: everything else, incl. `test_server_fsm.py`, the e2e kill-relay
evictions (`test_relay_workload.py:436/:798/:1123`, same host, so arrival ≈ send), and
`test_relay_outage.py` (no eviction at default 600 s). Wire: no pack/unpack change; a 2.9.0a8 peer
still sends `time`, which is now ignored for aging only.

## Docs

`docs/cli/relay.rst:116-118` — remove the bullet:

> - **Tolerance of clock skew.** The upstream *server* ages a relay's heartbeats by the relay's own
>   clock, so a relay host whose clock is far off the upstream *server*'s is evicted while alive (and
>   its tasks put back). Keep every relay host's clock synchronised with the upstream *server*'s.

The page is new in 2.9.0 (`versionadded` at its title), so removal needs no marker. The change
reaches plain clients too, which the stable line ages by sender clock, so mark
`server.evict` in `docs/_include/config_param_ref.rst:269-274` (wrapped in `.. only:: html`, as at
`:222-225`): restate "If the server receives no heartbeat from a client for this period of time
(by its own clock) it is considered defunct…" and add `.. versionchanged:: 2.9.0` — "a heartbeat
is aged from when the server received it, not by the client's clock." No other doc mentions the
clock (`relay_desc.rst:29-30,54,66`, `server.rst:67`, `alternatives.rst:43` describe eviction
only; correct as is). Timing columns' sender clocks stay with `issues/docs-and-runtime-surfacing.md` L10.

## Risks

- `check_clients` is in a high-blast-radius file: keep the change to the age line plus the dict.
- A `KeyError` if `received` and `beats` drift: pop both in one place each, or use `.get` with a
  fallback to now (never evict on a missing stamp).
- Tests that set `machine.last_check` to force a check keep working (B ignores it for age).
