# GOAL — Relay hardening for 2.9.0: no aborts on removed rows or a restarted upstream, a TLS mismatch that ends, zones that wait for their work, eviction by the server's clock

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay-hardening
- **kind:** fix
- **appetite:** big  ·  *Three unshaped seeds, each `small` on its own, promoted together for the
  2.9.0 final release: six separate fixes, one bounded investigation and one docs line. Two of the
  fixes are in `data/model.py` and two in `server.py` (high-blast-radius files), and two change
  every server's behaviour, not only a relay's. That is more than a small appetite's research and
  phase cap covers (see Clarifications).*

## Problem

`hs relay` (#82), its per-site topology (#100) and superseded-relay retirement (#101) are on
`develop`, and 2.9.0 is the release that makes them final. The relay's own reviews left a set of
defects that need not have blocked a pre-release but should not ship in a final one. None loses or
doubles a result; each makes a relay exit when it should carry on, carry on when it should exit,
leave work unrun, or evict a relay that is alive. Verified against `develop` at `2ffb5cc`
(2.9.0a8); line numbers below are that commit's.

**A relay exits 5 when its own server meets a row the relay removed** (primary seed,
`relay-abort-races`; `spec/relay/REVIEW.md` C7-1, C7-2). A relay deletes `task` rows under its
relay-local server: completed rows leave for the outbox (`collect_results`,
`src/hypershell/relay/store.py:363`, the DELETE at `:391`), and rows not yet scheduled at the site
are dropped when the upstream server has restarted (`drop_superseded`, `:242`, the DELETE at `:258`,
called from `insert_received` at `:233`). Two of the relay-local server's writes still raise on such
a row, end a server thread, and so end the relay with exit status 5 and no final heartbeat; the
upstream server then waits out `server.evict` (600 s by default) before it puts back what the relay
held.

- *The eviction revert.* `HeartMonitor.check_clients` (`src/hypershell/server.py:913`, the call at
  `:926`) runs `Task.revert_orphaned` (`src/hypershell/data/model.py:1224`), which reads the evicted
  client's in-flight rows (`select_orphaned`, `:1211`) and reverts them by id with `revert_all`
  (`:1002`), an unconditional per-row write that raises `StaleDataError` on a row gone since the
  read (`__write_rows`, `:937`, without `pending`). On a **primary** server the same gap does not
  raise but wipes a result the Receiver committed in it, and the task runs again unseen
  (`issues/orphan-revert-loses-result.md`, pre-existing): one conditional revert closes both.
- *The scheduler's dispatch stamp.* `Task.next` (`model.py:793`) selects unscheduled rows, sets
  `server_id`, `server_host` and `schedule_time` on the ORM objects (`:805-808`) and commits once
  (`:809`). A row deleted between the select and the flush makes the flush raise and ends the
  scheduler. `Task.next` is every server's dispatch path, and the same flush is the one
  `issues/hypertable-contended-scheduler-and-cli-writes.md` reports failing when contended on a
  TimescaleDB 2.29+ hypertable (an ORM flush for a task-row write is already against invariant §1).

Both were reproduced at unit level by forced interleaving; neither end to end. `drop_handed_back`
(`store.py:281`, added by #101) is a third deleter of unscheduled rows, but it runs only after a
retiring relay has stopped its scheduler, so it does not widen the dispatch-stamp window.

**A relay exits 5 when the first call of one of its link roles meets a restarted upstream**
(`upstream-link-restart-proxy`; `spec/relay-retire/REVIEW.md` O1). When the upstream server restarts
at the same address, a role's first call on a new worker thread, through a client the link created
before the restart, can dial the new server with a proxy id the old one minted; the new server
answers with a `RemoteError` wrapping `KeyError`, and `UpstreamLink.call`
(`src/hypershell/core/reconnect.py:258`) raises it as a non-transport error. In the puller it ends
the relay (exit 5); in the `handback` role (#101) the hand-back is refused and the tasks are left to
eviction, although the restarted server has already requeued them. A small harness reproduces it
every time; in a whole relay the window is narrow. `reconnect.py` is unchanged since #82.

**A TLS on/off mismatch with the upstream server is retried forever** (`relay-hardening`, C4-2).
The link counts every `OSError`, `ssl.SSLError` included, as a transport error a new connection may
cure (`TRANSPORT_ERRORS`, `reconnect.py:92`; `FATAL_ERRORS` at `:89`), and `attempt` (`:416`)
retries it. A TLS relay against a `--no-tls` upstream logs `SSLError: [SSL: WRONG_VERSION_NUMBER]`
and "trying again" without end; a `--no-tls` relay against a TLS upstream spends the full
`relay.upstream.deadline` (30 s) on every attempt. A plain `hs client` fails at once in both
directions, and a wrong key ends a relay in about 2 s. The relay never starts its site, and never
exits.

**An idle zone can end before taking rows staged for it** (`relay-hardening`, C5-1). In a relay
serving several zones, once one zone has had a shutdown reply, `finish_idle`
(`src/hypershell/relay/upstream.py:400`, called at `:378`) ends every zone with no site client and
nothing held, on the premise that "the upstream server finishes for every zone at once"
(`upstream.py:247`, repeated in `tests/test_relay_connector.py:559`). The dispatcher tells a request
to shut down only once its own zone's pool is empty (`Dispatcher._done`,
`src/hypershell/core/dispatch.py:313`). If upstream finishes with rows still staged for a zone (a
drain, a non-forever run stopped on a failed group, a server without a database) while that zone
has no site client (its fleet still launching, or its one client between exit and relaunch), the
zone ends and never takes them. With a database they go back unscheduled when the upstream server
exits; without one they never run in that run, with nothing logged. Reproduced at unit level
against the real `Dispatcher`. Next to it, documented but incomplete: a relay with no site client
in **any** zone never asks, so it never hears a shutdown (`docs/_include/relay_desc.rst`), and the
docs do not say that a non-forever upstream server waits for it as well.

**Eviction ages a heartbeat by the sender's clock** (`relay-hardening`; pre-existing for every
client). `check_clients` computes `age = self.last_check - hb.time` (`server.py:918`), and `hb.time`
is stamped by the sender (`src/hypershell/core/heartbeat.py`). A relay at another site whose clock
is behind the upstream server's by more than `server.evict` is evicted at every check while alive,
and everything it holds is put back; `docs/cli/relay.rst:116-118` lists this as a limitation.

**Some runs finish 6–9 s late, once about 60 s, for a reason not yet found** (`relay-hardening`,
`spec/relay/REVIEW.md` cycle 4). In the review's Linux throughput runs
(`spec/relay/drive/relay_throughput.py`, 2000 × 0.2 s, 32 executors), four of nine runs carried an
extra 6–9 s before the upstream server exited, and one about 60 s. Neither `bundlewait` nor the
upstream scheduler's backoff explained it, and no log survives. `develop` has changed since (#100,
#101), so it may or may not still occur.

The seed's last item, the man page's malformed `hs relay` synopsis, is **already fixed on
`develop`**: #89 (`7005186`) made the entry in `docs/manual.rst:24-25` a single line like its
neighbours, and `share/man/man1/hs.1:52-53` renders it so. It needs no work here.

## Outcome / vision

A relay that should keep running keeps running: neither its own server's eviction revert nor its
dispatch, nor a restarted upstream server, ends it; at worst a call is retried. A relay that cannot
work, because its TLS setting and its upstream's differ, says so and exits, as a client does. A zone
stops asking only when upstream has nothing left for it. A relay is evicted when it stops beating,
never because its clock differs from the server's. The late finish is either explained and fixed,
or bounded by recorded evidence. Every server, not only a relay's, gains the two shared fixes: an
eviction revert that never undoes a result that has landed, and a dispatch stamp that skips a row
it can no longer stamp. 2.9.0 ships with no relay follow-up from #82's review left open except
those named below as non-goals.

## Acceptance criteria (the contract)

- **R1** — WHEN a server (primary or relay-local) evicts a client and a row it read as that
  client's in-flight work has, before the revert writes it, completed or been removed, the server
  SHALL leave that row as it is (a completed row keeps its result and is not run again; a removed
  row is skipped), SHALL revert the client's other in-flight rows, and its heart monitor SHALL keep
  running; a relay SHALL NOT exit on that account.
- **R2** — WHEN a row the scheduler selected for dispatch is removed, or is no longer unscheduled,
  before its dispatch stamp is written, the scheduler (every server, primary and relay-local) SHALL
  skip that row, dispatch the other rows it selected, and keep running. WHEN that stamp waited on
  another writer's row lock on a TimescaleDB hypertable and the row still qualifies, the stamp SHALL
  land, as on PostgreSQL (invariant §1).
- **R3** — WHEN the upstream server restarts at the same address while a relay runs, the first call
  each of the relay's upstream link roles (puller, heartbeat, returner, hand-back) makes afterwards
  SHALL either succeed against the restarted server or count as a lost link (reconnect, then carry
  on as after any outage); it SHALL NOT end the relay, and SHALL NOT refuse a hand-back on that
  account.
- **R4** — IF the relay's TLS setting (`--no-tls` / `relay.upstream.tls`) and the upstream server's
  differ when the relay first connects, in either direction, THEN the relay SHALL exit non-zero
  within one `relay.upstream.deadline` of its first connection attempt, without launching any site
  client, with an error that names the relay's own TLS setting and says the two ends must agree. An
  upstream that is unreachable at start (refused, no route) SHALL still be retried as today, and a
  connection lost after the link has once connected SHALL still be retried as today.
- **R5** — WHILE the upstream server still holds rows staged for a zone a relay serves, the relay
  SHALL NOT end that zone's requests because another zone has heard the shutdown; those rows SHALL
  run at the site in that upstream run once the zone has a site client, whether or not the upstream
  server has a database. A zone with nothing staged upstream, no site client and nothing held SHALL
  still end with the others, so the relay and a non-forever upstream server still finish. The source
  comment and test docstring that state the upstream's finishing guarantee SHALL state it exactly.
- **R6** — The relay docs SHALL say that a relay with no site client in any zone never asks for
  work, so it never hears an upstream shutdown, and that a non-forever upstream server therefore
  waits for it too, until it is drained or stopped.
- **R7** — WHILE no more than `server.evict` has passed, by the server's own clock, since a
  client's last heartbeat arrived, the server SHALL NOT evict that client, whatever the offset
  between the client's clock and the server's; this holds for relays, relay site clients and plain
  clients alike. IF a client's heartbeats stop, THEN the server SHALL evict it once `server.evict`
  has passed since the last one arrived, as today. The clock-skew limitation in
  `docs/cli/relay.rst` SHALL be removed or restated to match.
- **R8** — The end-of-run tail SHALL be investigated within a fixed budget and end in one of two
  recorded outcomes (see Clarifications for the budget): (a) its cause is named from logs and
  fixed, and the post-fix runs show no tail over 5 s; or (b) `TECH.md` records the runs, the tail
  each showed, the logs kept as evidence, and the cause or the hypotheses ruled out, and the
  remaining work is left in a narrowed seed. The *tail* is the time from the last task's
  `completion_time` in the upstream database to the upstream server's exit.
- **R9** — Each defect SHALL have a regression test that fails on `2ffb5cc` and passes on the head:
  R1 and R2 by forcing the interleaving deterministically, with no threads or sleeps (engine events,
  as `tests/test_relay_return.py` does), including the primary-server case of R1 (a result committed
  between the read and the revert) and R2's contended case in `tests/test_contended_write.py` on
  SQLite, PostgreSQL and TimescaleDB; R3 with the restart harness (an `UpstreamLink` on a
  `QueueServer`, the server restarted on its port, then the first call of another role); R4 in both
  directions against a real upstream; R5 against the real `Dispatcher`, with and without an upstream
  database; R7 with a heartbeat stamped far from the server's clock.
- **R10** — Except for what R1, R2 and R7 state, a server's eviction, dispatch, retry and shutdown
  SHALL behave as today, and the existing suites SHALL pass unchanged, `tests/test_contended_recovery.py`,
  `tests/test_writeback.py` and `tests/test_contended_write.py` included. No wire change SHALL break
  a peer of an earlier 2.9 pre-release: a relay or client of 2.9.0a8 against this server, and this
  relay against a 2.9.0a8 server, keep today's behaviour.

## Non-goals (no-gos)

- **The rest of the contended-write seed.** This cycle moves the dispatch stamp in `Task.next` off
  the ORM flush for every server, gone-row and contended-row cases both (R2). It does **not** take
  the `retried` mark and its retry rows (`__schedule_next_failed_tasks`, `update_all`), `Task.cancel_all`
  / `Task.cancel`, or any `hs update` write: those stay in
  [`issues/hypertable-contended-scheduler-and-cli-writes.md`](../../issues/hypertable-contended-scheduler-and-cli-writes.md)
  (its R2–R4), for the next cycle. That seed's R1 (the stamp) is met here; `/hs-roadmap` narrows it
  when this cycle lands. The retry path is unreachable on a relay, whose local server never retries
  (`max_retries=0`, `src/hypershell/relay/site.py:130`).
- **No dispatch token on the eviction revert or Confirm.** Matching a revert or a confirmation to
  its dispatch stamp, and a confirmation replayed after an outage stamping a relay onto a reverted
  row, stay with [`issues/stale-confirm-hides-orphan.md`](../../issues/stale-confirm-hides-orphan.md).
- **No other use of the sender's clock changes.** `Client.connected_at` taken from a heartbeat,
  task timing columns and timing aggregates stay as they are
  ([`issues/docs-and-runtime-surfacing.md`](../../issues/docs-and-runtime-surfacing.md), L10).
- **No change to defaults or the reconnect policy**: `server.evict`, `relay.upstream.deadline`,
  `relay.upstream.evict_hint`, the backoff and its cap stay; only the TLS mismatch at first
  connection changes from retry to exit (R4). No TLS auto-negotiation, and no change to a plain
  client's or server's TLS behaviour.
- **No throughput requirement.** R8 is about an unexplained tail, not about the relay's steady
  overhead; `relay/`'s 1.5× rider is not a gate here.
- **No change for a relay with no site client in any zone**: it still never asks and waits for a
  drain or a signal; R6 documents it.
- **No man page work**: the synopsis is already fixed on `develop` (#89).
- **Not the neighbouring relay seeds**: [`issues/relay-test-strength.md`](../../issues/relay-test-strength.md),
  [`issues/advertised-host-ignores-bind.md`](../../issues/advertised-host-ignores-bind.md), and thread
  death turned into retry in general
  ([`issues/coordinator-resilience-retry-backoff.md`](../../issues/coordinator-resilience-retry-backoff.md)).
- **No 2.8-line backport.**

## Clarifications

- **Q:** One GOAL for three seeds? — **A:** Yes, `relay-abort-races` primary, with
  `upstream-link-restart-proxy` and `relay-hardening`, all three marked `adopted:relay-hardening`
  (foreman's brief, 2026-11-13, for the 2.9.0 final release). The size circuit-breaker (10 R-IDs,
  several deliverables) is therefore noted and not offered as a split, as in `relay-retire`
  (resolved 2026-11-13).
- **Q:** Appetite? — **A:** `big`: three seeds each `small` on their own, two shared fixes in
  high-blast-radius files that change every server, and an investigation; the seeds' own
  frontmatter is left as written (shaper's proposal, 2026-11-13; signed off by the foreman at the GOAL gate).
- **Q:** The eviction-revert fix: relay-local only, or every server? — **A:** Every server, as one
  conditional revert: a row is reverted only while it is still the evicted client's in-flight work,
  in the statement that writes it. That is R1 for a relay and the fix
  `issues/orphan-revert-loses-result.md` asks for on a primary server, and its replay test is part
  of R9. A relay-local variant would leave the primary server's lost-result window open while
  forking the eviction path. The relay review's build probe found the unit suite unchanged with the
  shared change. `orphan-revert-loses-result` is marked `adopted:relay-hardening` too, at
  the gate, as `writeback-scoped-conditional` and `relay-retire` did for a seed the GOAL subsumes;
  the GOAL-gate commit marks it (shaper's proposal, 2026-11-13; signed off by the foreman at the GOAL gate).
- **Q:** The dispatch stamp: every server, or only the relay-visible half? — **A:** Every server,
  and both the gone row and the contended row (R2), because `Task.next` is one shared function, a
  relay-only branch would fork the dispatch path, and any new per-row stamp must follow invariant §1
  on a 0-row match anyway. The `retried` mark and the rest of the contended-write seed are left to
  the next cycle (Non-goals) (shaper's proposal, 2026-11-13; signed off by the foreman at the GOAL gate).
- **Q:** TLS mismatch: fail fast, or a hint in the retry warning? — **A:** Fail fast at the first
  connection, in both directions (R4), as the seed's outcome asks ("says so and exits, as a client
  does"). If `hs-plan` finds the `--no-tls`-relay-against-TLS-upstream direction cannot be told
  from a stalled upstream within one deadline, it returns to the gate rather than weaken R4
  (shaper's proposal, 2026-11-13; signed off by the foreman at the GOAL gate).
- **Q:** Which rule keeps an idle zone asking (C5-1)? — **A:** Left to `hs-plan`: ending an idle
  zone only after a site period with no client, one more request per idle zone at the finish, or a
  shutdown reply that says whether anything is staged anywhere; R5 and R10 bind whichever is chosen
  (a wire addition must leave a 2.9.0a8 peer at today's behaviour) (shaping, 2026-11-13).
- **Q:** Clock skew: age by the server's clock, or warn? — **A:** Age by the server's own receive
  time, for every client (R7). It removes the dependency rather than surfacing it, needs no wire
  change, and only ever spares a client that is still beating. It changes eviction for every client,
  which R10 states (shaper's proposal, 2026-11-13; signed off by the foreman at the GOAL gate).
- **Q:** What bounds the tail investigation (R8)? — **A:** An investigation with a bounded outcome,
  not a guaranteed fix. Budget: at most 10 runs of `spec/relay/drive/relay_throughput.py` (2000 ×
  0.2 s, 32 executors) on the laptop with `HYPERSHELL_LOGGING_LEVEL=DEBUG` and scratch kept; if none
  shows a tail over 5 s, one cluster job of at most 10 such runs on Linux, where it was seen, and no
  more. Outcome (a) applies only if the fix fits this cycle's appetite; a cause whose fix does not
  is recorded under (b) and seeded. Either outcome closes R8; the release does not wait on a fix
  (shaper's proposal, 2026-11-13; signed off by the foreman at the GOAL gate).
- **Q:** The man page synopsis? — **A:** Already fixed on `develop` by #89; dropped from this
  cycle (shaping, 2026-11-13, verified in `docs/manual.rst` and `share/man/man1/hs.1` at `2ffb5cc`).

**Ruled at the GOAL gate** (2026-11-13): approved as written by the foreman, gate judge under the
maintainer's mandate. Confirmed: appetite big; the shared conditional eviction revert for every
server, with `orphan-revert-loses-result` adopted by this cycle; the dispatch stamp for every
server, both the gone and the contended row; TLS mismatch fails fast in both directions (a
direction that cannot be told apart within one deadline comes back to the gate); eviction ages by
the server's receive time for every client; and the R8 budget, whose one conditional cluster job the
foreman submits. The contract is locked; `hs-plan` designs against it.

## Evidence plan

- **Unit and integration** (CI and laptop): R9's regression tests; the full suite on SQLite, and
  `tests/test_contended_write.py` / `tests/test_contended_recovery.py` on local PostgreSQL and
  TimescaleDB (R2, R10).
- **Laptop drives**: a TLS-mismatch probe in both directions against a real `hs server` (R4: exit
  status, time to exit, the error, no site client launched); an upstream-restart run of a relay with
  a backlog, reusing `relay-retire`'s restart scenario (`spec/relay-retire/drive/`) (R1–R3: the
  relay never exits 5); a two-zone relay against a draining upstream with one zone's fleet not yet
  connected (R5); the `relay`, `relay-topology` and `relay-retire` laptop drives re-run on the head
  for R10; R8's throughput runs.
- **Cluster**: only R8's conditional Linux job. No other requirement needs real nodes.

## Related materials

- Seeds: [`issues/relay-abort-races.md`](../../issues/relay-abort-races.md) (primary),
  [`issues/upstream-link-restart-proxy.md`](../../issues/upstream-link-restart-proxy.md),
  [`issues/relay-hardening.md`](../../issues/relay-hardening.md), all `status: adopted:relay-hardening`.
- Subsumed or partly met: [`issues/orphan-revert-loses-result.md`](../../issues/orphan-revert-loses-result.md)
  (R1, see Clarifications);
  [`issues/hypertable-contended-scheduler-and-cli-writes.md`](../../issues/hypertable-contended-scheduler-and-cli-writes.md)
  (its R1 only, see Non-goals).
- Found in: [`spec/relay/REVIEW.md`](../relay/REVIEW.md) (cycle 4: C4-2 and the throughput note;
  cycle 5: C5-1; cycle 7: C7-1, C7-2; cycle 8, question 4), `spec/relay/PLAN.md` (risks:
  sender-clock eviction), [`spec/relay-retire/REVIEW.md`](../relay-retire/REVIEW.md) (O1). Current
  relay behaviour: [`spec/relay-topology/`](../relay-topology/GOAL.md) (#100),
  [`spec/relay-retire/`](../relay-retire/GOAL.md) (#101).
- Pattern: [`spec/hypertable-update-retry/`](../hypertable-update-retry/GOAL.md)
  (`CONTENDED_RERUNS`, `Task.revert_staged`'s conditional write); invariants §1, §3, §4, §6.
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/data/model.py`
  (`Task.next`, `revert_orphaned`, `select_orphaned`, `revert_all`, `__write_rows`);
  `src/hypershell/server.py` (`HeartMonitor.check_clients`, `switch_mode`);
  `src/hypershell/core/reconnect.py` (`UpstreamLink.call`, `attempt`, `FATAL_ERRORS`,
  `TRANSPORT_ERRORS`); `src/hypershell/relay/upstream.py` (`UpstreamPuller.gate`, `finish_idle`,
  `request`); `src/hypershell/core/dispatch.py` (`_done`, `finish`); `src/hypershell/relay/store.py`;
  `docs/cli/relay.rst`, `docs/_include/relay_desc.rst`.
- Drives: `spec/relay/drive/relay_throughput.py`, `spec/relay-retire/drive/`,
  `spec/relay-topology/drive/`.
