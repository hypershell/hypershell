# PLAN — Retire a superseded relay, and wake the main thread for signals

> **Status:** Draft for review · **Last updated:** 2026-11-13
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (locked at
> the GOAL gate, 2026-11-13); the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail
> is in [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every
> design element traces to a GOAL R-ID.

## 1. Summary

**Retire.** The topology publisher already decides, at each check, which relay instance holds a
duplicated site (`_arrange`). It now also carries the identities of every superseded instance on the
published `Topology` (`retired`), and `reply_text` adds a top-level `"retired": true` to the reply of
a relay whose identities are in that set. A relay of the previous build ignores the key (it reads
only `version`, `zones`, `checks`). A relay of this build latches the mark, stops pulling and
launching, stops its relay-local scheduler, withdraws the rows staged but not delivered, and hands
every row no site client has received back upstream through one new dispatcher call, `handback`,
that names each task with the upstream `schedule_time` it was dispatched under. The upstream
scheduler reverts exactly those assignments on its next pass, as it reverts unstaged zones today.
The relay-local server then finishes as in a drain: its clients get the shutdown reply once nothing
is staged, finish what they hold, and leave; the results go back; the final heartbeat goes out under
every identity; the relay exits 0.

**Wake.** One helper in `core/thread.py`, `join_waking(thread)`, joins a worker in bounded steps
(0.5 s), so the main thread returns to the interpreter twice a second and runs any pending
Python-level signal handler, whichever thread the kernel delivered the signal to. Every command named
in R11–R12 calls it in place of its untimed `join()`. Its final step is the existing re-raising
`Thread.join`, so failures, SIGINT, SIGTERM and exit statuses are what they are today. The relay's
"Drain requested" line is logged once by whichever of its relay thread or its puller sees the drain
first, so R11's 2 s holds even while a work request is outstanding upstream.

Four phases: the wake (small, independent, unblocks CI), the server half of retire (wire + revert),
the relay half (with the laptop evidence), and the multi-node run.

**Clarifications decided here** (the GOAL left them to `hs-plan`):

- **Keep feeding, or hand all back?** Hand back everything not delivered. On retirement the relay
  withdraws its staged rows and hands back every row a site client has not received; connected
  clients are then told to shut down once they ask again, so they finish what they hold and leave
  promptly (the maintainer's "send the shutdown request sentinel down to the old client"). This is
  the reading under which R7's timed half is deterministic: what the old relay held unstarted is
  upstream again within seconds of the mark, never after a client's long task.
- **What "started" means (R5).** A task is started at the relay once a site client's request has
  cut it from the relay-local pool (delivered). The relay cannot see which task inside a client's
  bundle is running, and a client never gives a task back, so every delivered task is that
  client's to run. Undelivered = no local `schedule_time` after the withdrawal (research 04 §2).
- **Wake interval.** 0.5 s (`WAKE_INTERVAL`). The handler runs within 0.5 s of the signal; the
  relay thread notices the drain within `STOP_CHECK_INTERVAL` (1 s) after that, so the relay logs
  "Drain requested" within 1.5 s even while its puller waits up to 2 s on a request upstream
  (research 02 §3). Client, server, cluster and submit act on the flag at their next poll, as they
  do today when the main thread takes the signal.

## 2. Design

### 2.1 The retired mark (R1, R8, R9) — `core/topology.py`, `cluster/topology.py`

- `Topology` gains `retired: FrozenSet[str] = frozenset()`: the upstream identities of every
  superseded relay instance. It is not part of the plan: the publisher carries it on every check, as
  it carries `checks`, so a change in it alone never makes a new version (an old relay reading the
  same entries sees the same version; R8).
- A pure function `retired_identities(records) -> FrozenSet[str]` beside `_arrange`: for each
  `Duplicate` with `relay=True`, every record whose `instance` equals `dup.older.instance` (all of the
  older instance's identities, not only its first). Records with no instance key are never retired
  (non-goal; research 01 §1 shows a 2.9.0a7 relay is never even recorded).
- `TopologyPublisher.check` computes it from the same `records` snapshot that feeds `site_views` and
  `entries`, so the duplicate warning (unchanged, R1), the zero entries and the mark come from one
  `_arrange` at one check. Both publish paths set it: `publish` builds `Topology(version, entries,
  checks, retired)`; the unchanged path does `replace(self.topology, checks=…, retired=…)`. A newly
  retired instance is logged once at INFO naming the instance, its site and the holder.
- `reply_text(topology, identities)` adds `"retired": true` only when some requested identity is in
  `topology.retired`; otherwise the reply is byte-identical to today's (R9). `read_topology` reads
  `data.get('retired') is True` into `TopologyReply.retired: bool = False`. Protocol stays 1; no
  reserved key in `zones`; never a mark on a `version: null` reply (research 01 §5).
- Latency: the mark reaches the old relay at most one publisher period (`[autoscale] period`, 60 s
  by default) plus one heartbeat after the newer instance registers. R7's bound runs from the old
  relay's first read, so this is outside it; the docs say so.

### 2.2 The hand-back call (R4, R5, R8) — `core/topology.py`, `core/dispatch.py`, `core/queue.py`

- Wire: `handback_text(identity, tasks)` / `read_handback(text)` in `core/topology.py` (the relay–server
  site protocol module), protocol `TOPOLOGY_PROTOCOL`:
  `{"protocol": 1, "client": "<identity>", "tasks": [{"id": "…", "schedule_time": "<as received>"}, …]}`.
  Bounded: at most `MAX_HANDBACK_TASKS` tasks (the relay chunks; 1,000 matches the upstream bundle
  default) and `MAX_HANDBACK_REQUEST` characters; malformed or oversized text gets `error_text`.
  Reply: `{"protocol": 1, "accepted": <n>}`.
- `Dispatcher.handback(text) -> str` (added to `DISPATCH_EXPOSED`). It stays database-free
  (`dispatch.py` module rule): it validates, appends `(client, [(id, schedule_time), …])` to a
  pending list under its lock, calls `_signal_need()` so the scheduler wakes at once, and replies
  with the count accepted. A dispatcher in memory (no database) refuses with an error reply; a
  server not autoscaling with sites never sends the mark, so no relay calls it there anyway.
- Old/new: a relay calls it only after reading the mark, which only this server sends, and through
  `getattr(client.dispatcher, 'handback', None)` like `fetch` does, so an older server is never
  asked and a server that lacks the method (one replaced mid-run) is treated as a refusal (§2.4).

### 2.3 The upstream revert (R4, R5, R7) — `data/model.py`, `server.py`

- `Task.revert_handed_back(client_id, assignments) -> List[str]` (a `Task` classmethod, invariants
  §1): one conditional UPDATE per task setting `revert_columns` to NULL, matching **`id` and
  `schedule_time` as dispatched** (the same dispatch match `apply_results` uses), **`completion_time
  IS NULL`**, and **`client_id` equal to the identity or NULL** (a confirmation the relay's link lost).
  A 0-row match is re-run only while a fresh read finds the row still under that dispatch with no
  `completion_time`, at most `CONTENDED_RERUNS` times, then a WARNING naming the task; a row that no
  longer qualifies (completed, cancelled, already reverted by eviction and re-dispatched, deleted) is
  skipped silently. One transaction per call. `schedule_time` text is decoded with the same column
  conversion results use (`Task.from_json` path), so the comparison is exact.
- `Scheduler.revert_handed_back()` runs beside `revert_unstaged()` at the top of `load_bundle`, takes
  the dispatcher's pending list, calls the classmethod per identity, and logs INFO
  `Requeued N tasks handed back by relay (identity)` (with the count skipped as settled). The
  reverted rows are unscheduled and are staged on the same or the next pass for whichever client of
  their zone asks (the newer relay). Nothing else in the server changes: eviction, orphan recovery,
  `revert_interrupted`, the FINISHED branch of `update_client` (non-goals).
- A hand-back accepted but not yet written when the server stops is reverted by
  `revert_interrupted()` at its next start, like every in-flight row.

### 2.4 The relay's retirement (R2, R3, R4, R5, R6) — `relay/upstream.py`, `relay/site.py`, `relay/store.py`, `relay/__init__.py`

- **Latch.** `TopologyView` gains a latched `retired` flag (an `Event`): `UpstreamHeartbeat.fetch`
  sets it the first time a reply reads `retired`, and logs once at WARNING `Superseded at site S by a
  newer relay - retiring: no more upstream requests, site clients finish what they hold` (R2). It
  never clears, even if a later reply drops the mark (the newer instance evicted; research 01 §5).
  The heartbeat keeps beating every identity throughout, so the upstream server evicts none (R7).
- **No further requests (R2).** `UpstreamPuller` takes a `retired` callable beside `drain`; the gate
  and the REQUEST state check it, so no request leaves after the mark is read; a request already sent
  is inserted and confirmed as today (its tasks are then held, and handed back).
- **No launches, no terminations (R3).** `SiteScaler` launches nothing while `view.retired` (checked in
  `reconcile` and `init`), and the retirement stops every zone's autoscaler as `stop_site` does; no
  path ends a site client (the reaper `reap_site_clients` keeps its current scope: only clients that
  never connected, after the clean finish).
- **`RelayThread.retire()`**, called from `wait_site` the first time `view.retired` is seen (checked
  every `STOP_CHECK_INTERVAL`):
  1. stop the zone autoscalers; stop the puller (`stop(wait=True, timeout=STOP_TIMEOUT)`);
  2. stop the relay-local scheduler (`stop(wait=True, …)`), so nothing more is staged;
  3. withdraw what is staged and undelivered: `server.queue.dispatcher.take_staged()` then
     `Task.revert_staged(ids)` on the relay database (exactly `ServerThread.revert_staged`'s pair);
  4. hand back: `store.unstarted(engine)` lists every incomplete local row with no local
     `schedule_time` and an upstream stamp, with its `relay_task.identity` and verbatim upstream
     `schedule_time`; per identity, in chunks, `handback` through the link (role `handback`); on each
     acknowledgement `store.drop_handed_back(engine, ids)` deletes those task rows (still unscheduled)
     and their stamps in one transaction under the store's write lock, and the relay logs INFO
     `Handed back N tasks no site client started (identity)`.
  A hand-back the link loses is tried again at each check while the site finishes, and once more
  before finishing upstream. A refusal (no method, an error reply) is logged as a WARNING and the
  rows are kept: `finish_upstream` then finds them held and leaves them to eviction, which is
  today's outcome, and the relay still exits.
- **Finish.** With the local scheduler stopped, `wait_site` sees `scheduling_over` and returns;
  `stop_site` runs unchanged: the relay-local server signals its clients (`dispatcher.finish()`:
  nothing staged, so the next request of each client is answered with shutdown), waits for them to
  leave, writes their results; the returner flushes; `finish_upstream` finds nothing held (handed-back
  rows are gone, started ones returned) and sends the final heartbeat under every identity; the
  relay exits 0 (R6). A drain signal during retirement changes nothing (both end pulling).
- **Rows held without an upstream stamp** (an upstream server with no database) are never handed
  back; a server autoscaling with sites always stamps (`--no-db` is refused there).

### 2.5 Main-thread signal wake (R11–R14) — `core/thread.py` and every named command

- `core/thread.py` gains `WAKE_INTERVAL: Final[float] = 0.5` and a function (exported)
  `join_waking(thread, interval=WAKE_INTERVAL)`: `while thread.is_alive(): thread.join(timeout=interval)`,
  then `thread.join()`. `Thread.join(timeout)` already re-raises a captured worker failure whether or
  not the timeout expired (`core/thread.py:52-56`), so the failure path is today's (R14). Each timed
  step returns to the interpreter, whose eval breaker runs a handler tripped by a signal on another
  thread (CPython runs Python handlers only on the main thread). A function rather than a change to
  `Thread.join`: worker-thread joins and `stop(wait=True)` stay untouched (invariants §5), and it
  works on any `threading.Thread`.
- The nine call sites (research 02 §1, confirmed; no other untimed wait on those main paths) swap
  `thread.join()` for `join_waking(thread)` and keep their `try`/`except`/`finally`: `hs relay`
  (`RelayApp.run`, `relay/__init__.py:1015`), `hs client` (`run_client`, `client.py:1518`),
  `hs server` (`serve_from` `server.py:1376`, `serve_forever` `:1567`), `hs server --autoscaling`
  (`serve_with_sites`, `cluster/topology.py:579`), `hs cluster` local/ssh/remote and autoscaling
  (`cluster/local.py:47`, `cluster/ssh.py:64`, `cluster/remote.py:85`), and `hs submit`
  (`submit_from`, `submit.py:1009`).
- **The relay's drain line (R11).** "Drain requested - no more upstream requests" is logged today by
  the puller at its gate, which an outstanding upstream request can hold for up to
  `DEFAULT_REQUEST_WAIT` (2 s). A once-only `log_drain()` in `relay/upstream.py` (a lock-guarded
  flag) is called both from the puller's gate and from `RelayThread.wait_site`, which checks
  `draining()` every `STOP_CHECK_INTERVAL`: the same single line, at most 1.5 s after the signal.
  Nothing is logged from a signal handler.
- SIGINT and SIGTERM taken by the main thread interrupt the timed wait at once, exactly as the
  untimed one (measured, research 02 §3); a worker taking SIGINT or SIGTERM is the kernel's default
  action or the relay's handler as today. The `except` paths are unchanged, so exit statuses are the
  same (R13). No `pthread_sigmask` (non-goal), no new handler, no `reset_signal()` (§8).

### 2.6 Docs (R10) — `docs/cli/relay.rst`, `docs/cli/server.rst`, `hs relay --help`

The known-limitation paragraph (`docs/cli/relay.rst:76-80`) goes. In its place: what a superseded
relay does (stops asking for work, launches nothing, hands back what no client has started, lets its
clients finish, then ends its registration and exits 0), that it happens within one server check of
the newer relay registering, and that a relay of an earlier build keeps the old behaviour. The
duplicate paragraph in `docs/cli/server.rst:68-73` gains the same in one sentence, with
`.. versionchanged:: 2.9.0` notes. `hs relay --help` (`APP_HELP`) gains one sentence, so
`docs/_include/relay_help.rst` is regenerated in the same commit; no option changes, so `share/`
completions are untouched. The wake is not user-visible (no docs beyond the PR note).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | §2.1 `retired_identities` from `_arrange`, `Topology.retired`, `reply_text` mark; warning unchanged (`warn_duplicates`) |
| R2   | §2.4 latch + single WARNING in `fetch`; puller `retired` check at GATE and REQUEST |
| R3   | §2.4 `SiteScaler` launches none while retired; autoscalers stopped; no termination path added; `stop_site` lets clients finish, results returned as today |
| R4   | §2.2 `handback` call; §2.3 `revert_handed_back` + scheduler pass; §2.4 withdrawal and hand-back |
| R5   | §2.4 "started" = delivered; only rows with no local `schedule_time` after withdrawal are handed back; §2.3 dispatch-matched revert never touches a re-dispatched row |
| R6   | §2.4 finish path: nothing held → final heartbeat under every identity → exit 0 |
| R7   | §2.4 timing (seconds after the mark; research 04 §6); heartbeat never stops → no eviction; proven by the `retire` drive (§6) |
| R8   | §2.1 mark is an ignored top-level key, version unchanged by it; §2.2 hand-back only after the mark, behind `getattr`; pairing drives (§6) |
| R9   | §2.1 replies without a retired identity are byte-identical; nothing changes for one relay per site, a non-publishing server, or `hs cluster --autoscaling`; `idle` drive keeps R5-at-zero |
| R10  | §2.6 |
| R11  | §2.5 `join_waking` in `RelayApp.run` (handler within 0.5 s); once-only drain line from the relay thread (≤ 1.5 s) |
| R12  | §2.5 `join_waking` at every client/server/cluster/submit main-thread join |
| R13  | §2.5 SIGINT/SIGTERM paths unchanged around each call site |
| R14  | §2.5 final step is the re-raising `Thread.join`; no output added without a signal |
| R15  | §6 `tests/test_signal_wake.py`: one subprocess test each for relay, client, server |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(sections §1, §5, §6, §8, §11, §12 touched; nothing at odds with the GOAL's intent) and again on
this design.

- **§1 Task lifecycle.** The upstream revert is a `Task` classmethod, one conditional UPDATE per row
  (no `bulk_update_mappings`, no ORM flush), checking its own count and re-running a 0-row match only
  while a fresh read says the row still qualifies, ≤ `CONTENDED_RERUNS`, then a WARNING; a completed
  or cancelled row is never reverted (`completion_time IS NULL` in the statement). The relay-side
  delete matches `schedule_time IS NULL AND completion_time IS NULL` in the statement, as
  `drop_superseded` does.
- **§2 / §3 exit_status, retries.** A handed-back task is reverted, not failed: no `exit_status`
  write, no `attempt` consumed, no retry row. No new sentinel.
- **§4 Server modes.** `handback` refuses in memory; site autoscaling is already refused with
  `--no-db`/`--no-confirm`. The scheduler pass is inside the scheduler, which does not exist in memory.
- **§5 Concurrency.** The puller's new check sits in states that already poll (`gate`, `request`);
  the relay's retirement runs on the relay thread with bounded waits (`STOP_TIMEOUT`,
  `STOP_CHECK_INTERVAL`); every `stop()` keeps its `machine.halt()`; the scheduler's new pass is a
  non-blocking take under the dispatcher lock. `join_waking` only adds a timeout loop before the
  existing re-raising `join`, outside `Thread` itself.
- **§6 Shutdown ordering.** The relay-local server's ordering (scheduler → `dispatcher.finish()` →
  heartbeat join → staged revert → receiver/confirm) is untouched: retirement stops the scheduler
  early and empties the pools first, the same calls in the same order. Staged rows are reverted with
  `Task.revert_staged`, never `revert_all`.
- **§8 Signals.** Handlers are untouched: stock handlers for USR1/USR2/HUP, relay carve-out as is,
  SIGINT/SIGTERM not captured outside the relay, no `reset_signal()`, no `pthread_sigmask`.
- **§9 Transport.** The new call is JSON text over the existing authenticated, TLS proxy; no live
  object on a queue; bounded request size.
- **§11 Cluster orchestration.** The publisher still launches and terminates nothing and still
  publishes a new version only when entries change; the relay fleets still never terminate.
- **§12 Conventions.** Docs, help snippet and `.. versionchanged::` in the same commit as the
  user-visible change (P3); tests tagged `@mark.unit` / `@mark.integration`; `cmdkit` exit statuses.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A new dispatcher method (`handback`) and a new `Task` revert that matches the dispatch | R4 needs the server to requeue tasks of a live identity, selectively (R5), within seconds (R7) | `revert_orphaned` on a final-heartbeat variant reverts the identity's started tasks too (R5) and only after the clients finish (R7); a new heartbeat state kills older servers' heart monitors; `revert_staged` by id alone could revert a row re-dispatched to another client (research 04 §3) |
| Retirement stops the relay-local scheduler before the site has drained | Without it the staged/unstaged split races the local scheduler, and R7's timed half would depend on a client's task length | Letting clients take the backlog (the GOAL allows it) makes R7 non-deterministic; it is the same stop `stop_site` performs, only earlier |

## 4. Rabbit holes (resolved)

- **Can an older relay read the mark without failing?** A top-level key is ignored by the 8403388
  `read_topology`; a key inside `zones` or a protocol bump fails it. Only develop builds from
  8403388 read a topology at all ([`research/01-wire-compat.md`](research/01-wire-compat.md)).
- **Can the old instance get a different reply from the new one?** Yes: replies are built per
  request from the identities named; the retired set must be computed at the publisher check and
  cover every identity of the older instance (research 01 §4).
- **Is there an existing revert path to reuse?** No path fits as is; the dispatch-matched revert
  follows `apply_results` and the scheduler-side write follows `revert_unstaged`
  ([`research/04-handback.md`](research/04-handback.md)).
- **What does "started" mean at the relay?** Delivered to a site client; withdrawal by
  `take_staged` after the local scheduler stops is atomic (research 04 §2).
- **Does a timed join wake for a signal taken by another thread?** Yes: an untimed join hung 12 of
  12 times on CPython 3.11–3.14 when a worker `pthread_kill`ed itself; a 1 s step loop ran the
  handler at +1.01 s every time; `hs client`/`server`/`relay` with an injected self-signal hung 3 of 3
  and exited 0 with the loop. Linux is expected to match (`tgkill`); CI confirms
  ([`research/02-signal-wake.md`](research/02-signal-wake.md)).
- **Can RV5 be driven fast and red/green on a laptop, and old/new paired?** Yes, with heartrate 1 s,
  task length well above the site-client idle timeout, and a `git archive` of the base on
  `PYTHONPATH` for one role ([`research/03-evidence.md`](research/03-evidence.md)).

## 5. Risks & open questions

- **A newer instance that dies right after superseding.** Retirement is one-way: the old relay
  retires even if the newer one is then evicted, leaving the site without a relay until one is
  started again. Accepted: the newer instance was started deliberately, and the old one's tasks go
  back upstream rather than stranding. (For the maintainer, if a "re-hold" is wanted later.)
- **Mark latency of one publisher period.** With the default 60 s period the old relay may pull for
  up to a minute after the newer one registers; everything it pulls is handed back. R7 is measured
  from the read.
- **The relay-topology `lower` laptop mode changes outcome.** With the fix, relay A in `lower` retires
  and exits, so that committed mode's "every live relay still runs" check no longer holds. Its
  successor is this cycle's `retire` mode; `spec/relay-topology/` stays as the record (research 03
  §2.5). The PR note says so.
- **Signal tests on CI.** Red on `develop` was measured on macOS (3 of 3 per command); Linux is
  expected to match but is confirmed only by CI. The tests assert the outcome within a deadline, not
  the hang, so a platform that happened to wake anyway would still pass green; the injector gates on
  the main thread being parked in the join (`sys._current_frames`), so the signal never lands early.
- **`hs server` and SIGUSR1 before any client.** A server that never had a client does not exit on
  SIGUSR1 today (`HeartMonitor.startup_phase`); unchanged, and the R15 server test connects a client
  before firing.
- **Cluster run.** Run by the foreman; the builder prepares the drive and a local rehearsal (P4).

## 6. Verification strategy

- **P1 (wake).** `tests/test_signal_wake.py` (`@mark.integration`): `hs relay`, `hs client` and
  `hs server` each run through an injector wrapper written as a file named `hs` (as `RECORDER` in
  `tests/test_relay_db.py`; a `python -c` wrapper would make the relay re-execute and lose the
  thread), whose thread waits for a go-file and for the main thread to be parked in its join, then
  sends SIGUSR1 to itself with `signal.pthread_kill`. Relay: the `test_relay_db` fixture pattern,
  assert exit 0 and "Drain requested". Client: beside a plain `hs server --forever`, fired after
  "Registered client", assert exit 0 and "Signal interrupt (SIGUSR1)". Server: with a plain client
  connected first, assert exit 0, "Signal interrupt (SIGUSR1)" and "Done". Deadlines 20–30 s, process
  groups stopped by PID. Red on `develop`, green on the branch. Unit tests for `join_waking`
  (re-raises a worker failure; returns at once for a finished thread; a handler tripped on a worker
  runs within the interval). The same injector drives `hs cluster` (local) and `hs submit` for R12. R13/R14 guard: `tests/test_signal.py`, `tests/test_relay_db.py`, the client/server/cluster
  suites unchanged and green.
- **P2 (server).** `tests/test_topology_plan.py` (`retired_identities`, reply/read round-trip with and
  without the mark, an 8403388-shaped reader ignores it), `tests/test_topology_server.py` (publisher
  carries `retired` without a new version; `handback` over a real queue server; scheduler requeue;
  dispatch match skips a re-dispatched, completed or cancelled row; unconfirmed rows revert),
  `tests/test_dispatch.py` (`handback` validation, bounds, in-memory refusal).
- **P3 (relay + laptop evidence).** `tests/test_relay_topology.py` and `tests/test_relay_fsm.py`
  (latch and single log; puller stops at GATE and REQUEST; `SiteScaler` launches none; `store.unstarted`
  and `drop_handed_back`; a lost hand-back retried; a refused one leaves rows to eviction). Laptop drive
  `spec/relay-retire/drive/retire_drive.sh` (built from `topology_drive.sh`, relays with system-style
  logs, a read-only sampler of the upstream task table, a `runs.log` of `$TASK_ID` per run):
  - `retire`: A (`cpu.max = 3`, site-client timeout 3 s, tasks ≫ timeout) then A2 at site A. Judge:
    every task once; A exits 0 by itself; no `Evicting client` for A's identities; A launches nothing
    after its "Superseded" line; no `Terminating site client`; A2 stays connected; the rows A held
    unstarted at the mark are requeued within 3 × heartrate of A's first "Superseded" line; at least
    one task was handed back (else INCONCLUSIVE). Red on `develop` (bounded: FAIL once a row stays on
    A's identity > 20 s with A at zero clients), green on the branch.
  - pairings (`DRIVE_BASE_SRC` = a `git archive` of `fb89670`, `DRIVE_BASE_ROLE` = `A` | `server`):
    base relay A vs this server behaves as today (no topology read error, strands until eviction);
    these relays vs the base server never retire.
  - R9: `idle` (one relay per site stays connected at zero clients), `spec/relay/drive/relay_smoke.sh`,
    and `spec/zone-autoscale/drive/zone_drive.sh founding|fixed|dynamic|restart`.
- **P4 (multi-node).** `spec/relay-retire/drive/` cluster scenario `retire` in the
  `topology_cluster.sh` style (a third relay role A2 at site A; collector and judge extended), a local
  rehearsal through the stand-in `srun`, judged with the same R7 check against heartrate 2 s and the
  harness's clock-skew allowance. The foreman runs the job and commits the scrubbed record; the gate
  judges it.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
