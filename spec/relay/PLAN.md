# PLAN — HyperShell Relay: two-level scheduling across administrative sites

> **Status:** Draft for review · **Last updated:** 2026-11-04
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

`hs relay` is one process holding three roles: an **upstream link** that behaves as one ordinary
Z2 client per served zone (request, confirm, return, heartbeat; no upstream dispatch change), a
**relay-local server** (the stock `ServerThread`, `max_retries = 0`, forever mode) on a private
SQLite database bound at import, and a **site fleet** brought up by the stock autoscaler with a
freshly minted site key. Received tasks are inserted durably before they are confirmed; the
upstream stamps travel in a relay-only side table; completed rows move at once to a durable outbox
and go back upstream carrying the leaf client's attribution and the upstream `schedule_time`, so
the existing eviction, orphan revert and #70 fence are the failover story. Upstream changes are
limited to R19 (a guarded confirm write) and R7's roster (optional heartbeat fields, two nullable
`client` columns). Zone code is not edited: multi-zone relays use one upstream identity per zone
(DQ1 (a)). The plan is ten phases, delegation core first.

## 2. Design

### 2.0 Base and sequencing

- The build starts from `develop` **after Z1 (#76) and Z2 have merged**. The first build step is to
  merge (or rebase) `feature/relay` onto that `develop`; this branch carries only spec commits, so
  nothing conflicts. P1's verify fails fast unless the zone code is present (`core/dispatch.py`,
  `_get_dispatch` in `core/queue.py`, `CONTENDED_RERUNS` in `data/model.py`).
- Z2's own merge obligations (for example, `revert_staged` through #75's contended-write helper)
  are Z2's. Relay neither does nor depends on them beyond the merged result.
- **Zone code is not edited.** Relay builds its own request text, imports `PROTOCOL` and
  `QueueVersionError`, and uses `ServerThread`'s `poll` instead of adding a dispatcher wake-up.
  The only additive change to a non-relay launcher file is in P9 (§2.6), after the security cycle.
- **The security-hardening cycle** (`fix/authkey-handling-hardening`) changes how launched clients
  receive the key in `cluster/remote.py`, `ssh.py` and `local.py`, and revises `docs/security.rst`
  and an `AGENTS.md` bullet. Relay's overlap and ordering are in §2.8.

### 2.1 Process, configuration and the relay-local database (R1, R10)

- **New package `src/hypershell/relay/`**: `__init__.py` (`RelayApp`, `RelayThread`), `store.py`
  (side tables, insert, outbox), `upstream.py` (puller, returner, heartbeat FSMs and identities),
  `site.py` (local server and fleet composition). Reusable link logic goes in
  `src/hypershell/core/reconnect.py` (§2.5).
- **Import-time binding** (`core/config.py`, additive). At the end of the config build, before
  `ACTIVE_CONFIG_VARS`, `RELAY_PROCESS = _relay_requested()`; when true, `config['database']` is
  replaced **wholesale** by `{provider: 'sqlite', file: <relay database>, pragmas: {synchronous:
  'FULL'}}`. `_relay_requested()` is true when a private marker variable (outside the
  `HYPERSHELL_` prefix, popped on read) is set, or when argv is `hs|hyper-shell relay …`.
  `RelayApp.run` asserts `RELAY_PROCESS`; when it is false (a programmatic `main(['relay', …])`
  after import) it re-executes the interpreter with the marker set. Env-var steering and
  post-import rebinding were both tested and rejected
  ([03 §1](research/03-local-server-db-fleet.md)). Consequence: one relay per process.
- **Default location**: `<site lib>/relay/<short host>.db` (host-scoped, since home directories are
  shared across login nodes), overridable by `[relay] database`. A sidecar `fcntl` lock makes a
  second relay on the same file refuse to start. No WAL by default (unsafe on network home
  directories); docs point to node-local disk.
- **`[relay]` config section** (`core/config.py` defaults): `database`, `bind`, `port` (a default
  distinct from the server's 50001; proposed 50002, the builder checks for collisions), `bundlesize`,
  `bundlewait`, `zone` (one name in P2–P8, a list from P9), `size` (fleet size), `launcher`,
  `launcher_args`, `poll` (default 1 s), client knobs (`num_threads`, `template`, `timeout`), and
  `[relay.upstream]`: `host`, `port`, `auth` (with cmdkit `_env`/`_eval`), `bundlesize` (the
  advertised size, default 1000), `heartrate`, `evict_hint` (default 600 s), and `tls`
  (`enabled`, `cert`, `cafile`, `fingerprint`, same meaning as `server.tls`). Upstream material is
  never read from `[server]`, so site clients keep resolving `[server.tls]` to the relay's own
  certificate.
- **CLI** (`hs relay`): `-H/--upstream-host`, `-p/--upstream-port`, `--zone` (repeatable from P9),
  `-N/--size`, `--launcher`, `--launcher-args`, `--bind`, `--port`, `--database`, `--no-tls`
  (upstream hop only). There is **no upstream `-k` option**: the key comes from config or the
  environment, never the relay's own argv. `--no-confirm` does not exist and is refused if passed.
  Registered in `__init__.py` (`commands`, `APP_HELP`, `__all__`); `'relay'` joins
  `DISTRIBUTED_ROLES` in `core/logging.py` (D11), giving `relay-<host>.log`.

### 2.2 Upstream pull: identities, durable insert, confirm, backlog bound (R2, R3, R8, R17)

- **Identities.** For each served zone, `uuid5(relay instance, zone name)`, logged at start as
  `Upstream identity <id> for zone <name>`. Single-zone relays use the same rule (one identity).
- **Puller FSM** (`relay/upstream.py`): `START → GATE → REQUEST → INSERT → CONFIRM → GATE`, plus
  `WAIT_LINK` (§2.5) and `FINAL`. Every blocking step has a finite timeout and re-enters;
  `check_signal()` is polled in GATE.
  - **GATE (R8).** `backlog` = local `task` rows not complete (pending or running at the site).
    `bound = connected site clients × local bundlesize`, where *connected* is the local server's
    live `Client` count. While `backlog ≥ bound` (including a fleet of zero), no request is sent;
    the puller waits `poll` and re-enters.
  - **REQUEST (R2).** Request text: `{"protocol": PROTOCOL, "client": <zone identity>, "host":
    HOSTNAME, "zone": <name>, "size": min(bound − backlog, upstream bundlesize), "wait": <short>}`.
    Each reply is bounded by the upstream `server.bundlemax`; the puller simply asks again. An
    error reply, or `QueueVersionError` at connect, exits non-zero with the client's single
    version error. A `shutdown: true` reply moves the relay into drain (§2.7).
  - **INSERT (R3).** In one SQLite transaction: upsert each record into `task` by id with
    `schedule_time`, `server_id`, `server_host`, `client_id`, `client_host` cleared (so the local
    scheduler takes it), and upsert `relay_task(id, schedule_time TEXT verbatim, server_id,
    server_host, zone identity, received_at)`. Aware datetimes are normalised to relay-local time
    (SQLite drops tzinfo). Commit with `synchronous=FULL`.
  - **CONFIRM.** Only after the commit: `ClientInfo(client_id=<zone identity>, client_host,
    task_ids).pack()` on `confirmed`. A failed insert is never confirmed.
- **Upstream TLS (R17).** The link dials with a `TLSConfig` built from `[relay.upstream.tls]`
  (`cafile` or `fingerprint` pinning across the WAN hop). Z1's in-process server keeps its context
  on its listener, so the upstream trust (process context) and the local `<auto>` server coexist;
  hence nothing in the relay dials the local server in-process
  ([03 §3](research/03-local-server-db-fleet.md)).

### 2.3 Site run and return path (R4, R5, R6, R16, R17)

- **Local server and fleet** (`relay/site.py`). The single-zone relay composes the stock
  `AutoScalingCluster(source=None, max_retries=0, poll=[relay] poll, bind=([relay] bind, port),
  launcher, launcher_args, policy='fixed', init_size=min_size=max_size=N, tls=<local>,
  zone=<name>)`. It already embeds a forever-mode `ServerThread`, mints a fresh
  `secrets.token_hex(64)` site key and launches site clients with it, and relaunches exits under
  the fixed policy (R16's local half). Relay edits nothing in `cluster/`; it inherits whatever key
  delivery the security cycle lands. The local server's evict stays at its default (the class does
  not forward one), which only delays local re-runs of a dead leaf's rows.
- **Credential fence (R17).** Before the fleet starts, `os.environ` is scrubbed: every
  `HYPERSHELL_RELAY_*` name, `HYPERSHELL_CONFIG_FILE` when it names relay config, and **any
  variable whose value contains the upstream key**. Launchers copy `os.environ` at `Popen` time,
  and tasks inherit their client's environment, so the scrub covers clients and tasks. The
  upstream key never appears on any argv.
- **No local retry (R4).** `max_retries = 0` is passed explicitly, whatever
  `server.attempts`/env says. A local failure is a completed row like any other.
- **Outbox.** The returner FSM (`START → COLLECT → SEND → MARK → COLLECT`, plus `WAIT_LINK`,
  `FINAL`) runs one transaction per cycle: completed local rows are **moved** out of `task` into
  `relay_result(id, record JSON, state, returned_at)`. This keeps a failed local row from holding
  the local group (local `max_retries = 0` would otherwise stall `increment_group` in forever mode)
  and is R9's durable buffer. The record is the local row's `to_json()` with `schedule_time`,
  `server_id`, `server_host` restored from `relay_task` (verbatim text, so it equals the
  dispatched stamp exactly) and naive local timestamps given the relay's tz. `client_id`,
  `client_host`, `command`, timing, `exit_status`, outputs and resources are the leaf's (R6).
  Relay-local stamps never leave: they are overwritten in the record before the put.
- **Return and prune (R5).** Bundles follow `bundlesize`/`bundlewait`, like the client collector.
  A row becomes `returned` only after `completed.put` returns; a `QueueFull`, timeout or link error
  leaves it `pending` and it is sent again. `returned` rows are deleted after a short retention
  (default 300 s) in batched deletes, so the file is size-stable (R10). A replay after a crash
  arrives DUPLICATE or STALE and writes nothing upstream. **DEFERRED reading:** a relay cannot
  observe outcomes (no per-result acknowledgement; the upstream Receiver marks the item done before
  applying and keeps DEFERRED results in its own custody), so "not pruned as if it had landed"
  is met by recording *handed over*, never *landed* (§5, ruling N2).

### 2.4 R19 — the late-confirm fence (`data/model.py`)

- The confirm statement gains `completion_time IS NULL`. In the contended-write helper
  (`__row_statements` / `__write_rows`), the zero-row re-read for the confirm path reads
  `completion_time` and classifies three outcomes: **missing** (raise `StaleDataError`, as today),
  **complete** (settled: no re-run, no WARNING, DEBUG line), **still open** (contended: re-run up to
  `CONTENDED_RERUNS`, as today). The revert statement is unchanged. Ordinary confirm-before-result
  is unchanged. A confirm after a cancel no longer stamps `client_id` (benign).

### 2.5 DQ2 — outage ride-through and restart durability (R9)

- **`core/reconnect.py::UpstreamLink`**, liftable by `issues/coordinator-resilience-retry-backoff.md`:
  a factory for `QueueClient`, a state (`UP`/`DOWN`), an `epoch`, `last_ok`, a condition, and
  injected `clock`/`rng`/`sleep`.
  - `call(role, fn, timeout)` runs `fn(client)` on that role's **link worker thread** and waits at
    most `timeout` (default 30 s, well above the request `wait`). Transport errors (`EOFError`,
    `ConnectionError`, `TimeoutError`, non-verification `OSError`) or an overrun mark the link down
    for that epoch and raise `LinkDown`; the worker is retired (abandoned if stuck in a half-open
    socket). Fatal errors (`QueueVersionError`, `AuthenticationError`, `TLSError`,
    `SSLCertVerificationError`) raise `LinkFatal`, and the relay exits non-zero; the outbox is
    durable, so exiting loses nothing.
  - Fresh worker threads after every reconnect also avoid the per-thread proxy connection cache
    that otherwise returns `BrokenPipeError` after a reconnect in the same process
    ([02 §2](research/02-upstream-connector.md)). No `core/queue.py` change.
  - **Reconnect:** full-jitter backoff `uniform(0, min(60, 1 · 2ⁿ))` s, waiting on the halt event.
    Logging: one WARNING on going down, INFO on power-of-two attempts and at least every 5 min, one
    WARNING when the outage passes `evict_hint` ("buffered results will likely be refused as
    stale"), one WARNING on reconnect with the duration.
- **While down:** the puller parks in `WAIT_LINK` (1 s re-entry); the local server, fleet and local
  Receiver keep running, so completions land in the outbox; returner and heartbeat park.
- **On up, in order:** heartbeat every identity (the #29 re-registration branch); replay pending
  confirms only if the outage was shorter than `evict_hint` (otherwise drop and log: results still
  land, since the fence is `schedule_time`, and a stale confirm could stamp a row another site now
  runs); drain the outbox oldest first; resume pulling.
- **Upstream restart** is proven by a received record whose `server_id` differs from the one in
  `relay_task`: unstarted local rows with the old `server_id` are deleted (upstream already
  reverted them); started ones run and return STALE. The relay never drops local tasks on an
  inferred eviction.
- **Restart durability:** a relay restart re-opens the same file; `pending` outbox rows are sent,
  local in-flight rows are reverted and re-run by the local server's `revert_interrupted`
  (at-least-once), and the upstream stamps survive in `relay_task`.

### 2.6 DQ1 — multi-zone relays (R2 multi-zone half)

- **One upstream identity per zone (option (a)), multiplexed over one connection set.** Nothing
  upstream binds an id to a connection; each identity requests, confirms and heartbeats under its
  own id, so `demand[id] = (zone, size)` holds one entry per zone, exactly as for single-zone
  clients. The puller cycles the zones with a short `wait`, so the upstream connection count is
  the number of relay link threads (puller, returner, heartbeat), not the number of zones.
- **Failover**: a dead relay's identities each stop beating, are each evicted, and each
  `revert_orphaned(id)` + `dispatcher.forget(id)` runs: every zone it held is reverted, and each
  zone it was the last client of is unstaged. No Z2 code, test or `PROTOCOL` changes. Option (b)
  was rejected because `tests/test_zone_dispatch.py:197` assigns `dispatcher.demand[...]` directly
  ([01 §5](research/01-upstream-surfaces.md)).
- **Per-zone gate:** the R8 bound and backlog are computed per zone (connected site clients serving
  that zone × local bundlesize).
- **Per-zone fleets.** Each served zone has its own site clients (`--zone <name>`; zone ids are
  deterministic, so local and upstream ids match). One local server serves them all (two servers on
  one database is unsupported). The first zone's fleet comes from the `AutoScalingCluster` (§2.3);
  further zones need an `AutoScalerThread` with the same client invocation and `--zone` changed.
  That needs the client-launch build (argv and key delivery) available as a function: P9 makes a
  **minimal, behaviour-preserving extraction** of `AutoScalingCluster`'s client-invocation build
  into a module function in `cluster/remote.py`, used by `AutoScalingCluster` itself and by relay,
  **after** the security cycle has landed (§2.8). Config: `[relay.zone.<name>]` may override
  `launcher`, `launcher_args`, `size`.

### 2.7 DQ3 — the forwarded roster (R7, R6)

- **Heartbeat** (`core/heartbeat.py`): optional `site: str` and `roster: [{uuid, host, state}]`,
  packed only when set and read with `.get` (an old server ignores extra keys; the current decoder
  raises on a missing one, so defaults are required). Each beat carries the full roster (snapshot,
  idempotent). The first zone identity is the **roster holder**; every relay identity's beat
  carries `site`.
- **`client` columns** (`data/model.py`, through #69's upgrade step, both nullable): `site TEXT`,
  `relay UUID`. Relay identity rows self-reference (`relay = id`); leaf rows name the holder.
- **HeartMonitor** (`server.py`):
  - On a beat with `site`, the relay identity's row is written with `site` and `relay = id`.
  - On a beat with a roster, diff against `self.rosters[holder]`: new leaves are added (or
    re-registered through the #29 branch) with `site` and `relay = holder`; leaves that dropped out
    or report FINISHED get `disconnected_at`. **Leaves never enter `self.beats`**, so the monitor
    cannot evict one and they do not hold shutdown.
  - When a holder is evicted or finishes, one more statement: `UPDATE client SET
    disconnected_at = now WHERE relay = :holder AND id <> :holder AND disconnected_at IS NULL`.
    Leaves own no in-flight rows (those carry relay identities), so they need no revert or forget.
  - The #29 re-registration also refreshes `server_id`/`server_host`, so after an upstream restart
    a surviving relay and its roster count as connected again.
  - One ERROR line when a beat with `site` arrives under `no_confirm` or `in_memory` (failover is
    off there).
- **Counting:** `Client.count_connected` excludes relay identity rows (`relay = id`) and counts
  leaves, so it counts executing clients. `effective_rate_by_client` and `task_pressure` join
  completed rows (leaf ids, via R6 and R19) to connected leaf rows: coherent while the relay lives.
  `latest_server` is untouched: results cannot write `server_id`.
- **Relay side:** the roster is the local server's `Client` table (connected rows, their host and
  state), read by the heartbeat FSM each beat.

### 2.8 Overlap with the security cycle, and ordering

| Relay touch | Overlap | Ordering |
|---|---|---|
| `cluster/remote.py`: **consume** `AutoScalingCluster` (P4) | its constructor and key delivery change in the hardening cycle | P4 merges `develop` first if hardening has landed; otherwise P4 builds against the current API and the call is re-checked at the merge (a call, not an edit) |
| `cluster/remote.py`: **extract** the client-invocation build (P9) | the very code hardening rewrites | P9 only, and only after hardening has landed and been merged; if it has not, P9 stops for a foreman ruling |
| `docs/security.rst` relay trust model (P9) | hardening's R8 rewrites the key section | P9, after hardening; relay adds a new section, edits none of hardening's text |
| `AGENTS.md` (relay notes, D11 role location) (P9) | hardening revises the *Queue transport & security* bullet | P9, after hardening; relay edits other sections |
| `cluster/ssh.py`, `cluster/local.py`, `client.py` | hardening | not touched by relay |

The relay's site fleet is, in effect, a fifth client-launching mode (AGENTS §11: no shared argv
builder). Using `AutoScalingCluster` whole for the core, and the extracted function for extra zones,
keeps relay on whichever key delivery hardening lands, with no second copy of it. Recommendation
to the foreman: ask the hardening plan to put the autoscaling client invocation behind one
function; if it does, P9's extraction is already done.

### 2.9 Shutdown (R20 "halting on shutdown")

Relay order: puller stops (on a `shutdown: true` reply in every zone, a signal, or Ctrl-C) → wait
for the local backlog to drain → stop the cluster (local server `finish()`, site clients get
`shutdown` and exit; the autoscaler never terminates clients) → returner flushes the outbox →
FINISHED heartbeat for every identity → link closed → exit 0. USR1/USR2 (sticky) start the same
drain. With an upstream `--forever` server, the relay runs until signalled.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `hs relay` = upstream link (§2.2) + `AutoScalingCluster` local server and fleet (§2.3) in one process; private DB (§2.1) |
| R2 | Stock work-request text per identity, bounded by `bundlemax`, re-request; `QueueVersionError` exit (§2.2); per-zone identities, demand held independently (§2.6) |
| R3 | Upsert task + `relay_task` in one `synchronous=FULL` transaction, confirm after commit under the zone identity (§2.2) |
| R4 | `max_retries = 0` forced; failures go upstream as completed rows (§2.3) |
| R5 | Outbox, bundlesize/bundlewait, mark after put, retention prune, DEFERRED reading (§2.3) |
| R6 | Leaf result columns + verbatim upstream `schedule_time`/`server_*` restored; local stamps never leave (§2.3); counting rules (§2.7) |
| R7 | Heartbeat `site`/`roster`, `client.site`/`client.relay`, HeartMonitor roster diff and cascade (§2.7) |
| R8 | GATE: backlog ≥ connected site clients × local bundlesize → no request (§2.2), per zone (§2.6) |
| R9 | `UpstreamLink`, link workers, backoff, down/up order, restart detection, durable outbox (§2.5) |
| R10 | Wholesale database swap at import, SQLite always, host-scoped default, lock, prune keeps size stable (§2.1, §2.3) |
| R16 | Fixed-policy fleet of `[relay] size` via `AutoScalingCluster` (§2.3) |
| R17 | `[relay.upstream]` auth/TLS pinning; site key minted by `AutoScalingCluster`; env scrub; no upstream `-k` (§2.1–2.3) |
| R18 | CLI snippets/completions in each CLI commit; `docs/cli/relay.rst`; `docs/security.rst` trust model and non-goals in P9 (§2.8) |
| R19 | Guarded confirm + "complete" outcome in the contended-write helper (§2.4) |
| R20 | Test inventory §6 |
| R21 | `spec/relay/drive/` run script, local smoke, judge; external wait (§6, TECH P7/P10) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design was drafted. Sections touched: §1, §2, §3, §4, §5, §6, §8,
§9, §10, §11, §12. §7 (resource accounting) is untouched: no in-process clients.

- **§1 Task lifecycle.** Upstream: the only new task-row write is R19, which keeps
  `CONTENDED_RERUNS` and adds a settled outcome rather than a crash or stranded row. Relay-local:
  inserted rows are unscheduled (`schedule_time IS NULL`); completed rows move to the outbox in
  the same transaction that reads them, so no local row is left stamped without completion except
  in flight, which local `revert_interrupted` covers. State logic for upstream rows stays in `Task`
  classmethods; relay-local outbox logic lives in `relay/store.py` against relay-only tables.
- **§2 `exit_status`.** No new sentinel. Leaf exit statuses travel unchanged.
- **§3 Retry model.** The true server stays the only retrier: local `max_retries = 0`, no local
  retry rows, ids and `previous_id`/`next_id` preserved verbatim. Results go through
  `apply_results` and its fence unchanged.
- **§4 Server modes.** The relay refuses `--no-confirm`; the upstream logs one ERROR when a relay
  beats under `no_confirm`/`in_memory`. The relay-local server is never `in_memory`.
- **§5 Concurrency.** New FSMs (puller, returner, heartbeat) each have `HALT`, finite waits that
  re-enter, `check_signal()` in their gate states, and `stop()` overrides that call
  `machine.halt()` first. `UpstreamLink` waits on events with timeouts.
- **§6 Shutdown ordering.** Relay order is new (§2.9) and is defined once; the embedded
  `ServerThread`'s own order is unchanged. Every upstream payload is JSON (`request` text,
  `ClientInfo.pack`, `serialize_tasks`, `heartbeat.pack`).
- **§8 Signals.** No new handler, no `reset_signal()`. USR1/USR2 drive the relay's drain.
- **§9 Transport.** Wire format, handshake, `PROTOCOL`, key rules and the fingerprint check are
  unchanged. Two auth domains: the upstream key stays in the relay process; the site key is minted
  per run. No mTLS is added.
- **§10 Configuration.** New `[relay]` section. The import-time singleton gains one branch that
  swaps the database section for the relay process only (a deviation, below).
- **§11 Cluster.** No launcher argv is changed in P2–P8; P9 extracts one invocation build without
  changing it. JSON-mode template handling stays in `AutoScalingCluster`. The relay host must be
  routable from site nodes (it advertises `HOSTNAME`, as `AutoScalingCluster` does).
- **§12 Conventions.** Docs and completions with each CLI change; tests tagged; exit codes from
  `exit_status`; no hardcoded version.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `core/config.py` swaps `config['database']` for the relay process at import | R10: SQLite whatever the configured provider, without touching the primary; `data/core.py` binds at import and other modules freeze copies | Env steering cannot delete keys (fails over a PostgreSQL config); rebinding after import leaves frozen copies, the baked `schema` and the pragma listener on the primary ([03 §1](research/03-local-server-db-fleet.md)) |
| Relay-only tables `relay_task` and `relay_result` on their own `MetaData` | The upstream stamp must survive local re-stamping, local revert and SQLite's tz loss; outbox for R5/R9 | A column on `Task` is re-stamped/cleared by the local server; an `Entity` subclass would be created in every user's primary database by `upgrade_schema` |
| Completed relay-local rows are moved out of `task` | Local `max_retries = 0` makes a failed row hold the local group forever in forever mode | Marking them `retried` locally works but leaves the scheduler's tables growing and needs a second buffer for R9 |
| Upstream HeartMonitor gains roster handling; `client.site`/`client.relay`; `count_connected` excludes relay rows; #29 re-registration refreshes `server_id` | R7 and R6's "remain correct"; DQ3 | Per-leaf heartbeats (more WAN traffic, leaves evicted independently, no cascade, still needs the same server change) |
| R19 adds a "complete" outcome to the shared contended-write helper | Without it every late confirm costs four UPDATEs and a false WARNING | A separate confirm path duplicates `CONTENDED_RERUNS` handling |
| Relay builds its own request text instead of reusing `ClientScheduler.request_text` | That method is Z2 code and hard-codes `INSTANCE`; refactoring it edits approved zone code | — (the duplication is six JSON fields) |
| Upstream calls run on link worker threads with a deadline; stuck workers are abandoned | A half-open upstream blocks a proxy call forever; a reconnect in-process hits the per-thread connection cache | Opt-in socket timeouts in `core/queue.py`: a Z1 file and the security cycle's declared no-touch file |
| P9 extracts `AutoScalingCluster`'s client-invocation build into a function | Extra zone fleets must launch exactly as the first, with the same key delivery | Copying the argv block makes relay a sixth place that must track key delivery by hand |
| Ten phases, above the ~8-phase soft breaker | Scope ruled by the maintainer (C1) knowing it is tight; phases kept small so review can grade incrementally; two are evidence bookkeeping (P7, P10) | Merging phases makes review checkpoints coarser exactly where the calendar needs them early |
| R21 run script under `spec/relay/drive/` | The foreman's brief places it there | `tests/cluster/` (brief 04's preference) is where cluster harness code lives; `drive.sh` is left untouched either way |

## 4. Rabbit holes (resolved)

- Can one relay serve several zones against Z2's demand map without touching Z2? Yes: one identity
  per zone, multiplexed ([01 §5](research/01-upstream-surfaces.md)).
- How does the relay bind a private SQLite before import wins? Wholesale section swap in
  `core/config.py`, tested over a PostgreSQL config with no driver import
  ([03 §1](research/03-local-server-db-fleet.md)).
- Where does the upstream `schedule_time` survive? A verbatim-text side table; a DATETIME column
  would lose tz and turn every result STALE ([03 §2](research/03-local-server-db-fleet.md)).
- Why does a relay site stall on a failed local task, and how to avoid it? Local
  `max_retries = 0` + forever mode; move completed rows to the outbox
  ([03 §2](research/03-local-server-db-fleet.md)).
- What happens to an upstream call in an outage? Errors or a forever hang; in-process reconnects
  fail on the per-thread connection cache; resolved by link workers with deadlines
  ([02 §2](research/02-upstream-connector.md)).
- What can a relay observe about a return? Only that the put returned
  ([01 §2](research/01-upstream-surfaces.md)).
- Do leaf credentials leak? Launchers and tasks inherit `os.environ`; scrub by name and value
  before the fleet starts ([03 §4](research/03-local-server-db-fleet.md),
  [04 §2](research/04-evidence-and-tests.md)).
- Can the R21 checks fit one 4-node hour? About 3–4 min a run; three repeats in about 20 min
  ([04 §4](research/04-evidence-and-tests.md)).

## 5. Risks & open questions

- **Calendar.** Serial build estimate ≈ 7–8.5 builder-days (TECH, per phase) against the 5–6 days
  of build and review available; the delegation core (P1–P5) is ≈ 4. **Rulings for the foreman**
  (cuts are the maintainer's, per C1): accept the slip for P8/P9, or ship the pre-release at P7
  with P8/P9 following in the next pre-release. P7 runs in parallel with P8.
- **N1 — R5's DEFERRED clause reading** (§2.3): the relay records *handed over*; it cannot observe
  DEFERRED. Options: (i) accept this reading in TECH (recommended); (ii) amend R5's wording in
  GOAL; (iii) add a general server→client result acknowledgement (outside R2's no-change line and
  the appetite).
- **N2 — R9 against an upstream restart.** An upstream *restart* reverts every in-flight row at
  start, so buffered pre-restart results are STALE whatever the outage length. R9's "returns
  buffered results on reconnection" yields landed results only when the upstream process survived
  (a partition). The relay delivers them either way; recommend recording this reading.
- **N3 — The R21 evidence build.** R21 needs core + roster (P1–P6). P7 hands back after P6 so the
  run can happen while P8/P9 build; P8 changes the link. Options: accept evidence from a head ≥ P6
  (recommended under calendar pressure), or re-run on the final head (about 20 min of allocation).
- **N4 — The fleet API under the security cycle.** P4 consumes `AutoScalingCluster`; P9 extracts
  its client-invocation build after hardening lands. If hardening has not landed when P9 starts,
  P9 stops for a ruling. Ask the hardening plan to expose one invocation function.
- **Sender-clock eviction.** Upstream eviction ages heartbeats by the sender's clock
  (`last_check − hb.time`). Cross-site clock skew beyond `evict_after` evicts a live relay every
  check. Not fixed here (it changes eviction for every client); docs require synchronised clocks;
  candidate seed.
- **Cut-to-confirm window**, larger by the relay's bundle size and likelier in outages (GOAL
  non-goal). Documented.
- **Confirm replay** after an outage shorter than `evict_hint` can, after an unseen upstream
  restart, stamp the relay onto a reverted row; the worst case is an extra at-least-once re-run.
  A confirm conditioned on `schedule_time` would close it (wire change, out of scope).
- **Abandoned link workers** hold a socket until TCP gives up; bounded by one per role per stall.
- **Local re-run of a dead leaf's rows** waits for the local default evict (600 s), since
  `AutoScalingCluster` does not forward one. Acceptable; noted in docs.
- **A PostgreSQL primary with no driver** exits `hs` at import before relay code runs. Docs note it.
- **`count_connected`** is only an autoscaler debug figure today; the exclusion keeps it honest.

## 6. Verification strategy

- **Unit** (`@mark.unit`), deterministic, no sleeps as synchronisation:
  - `tests/test_writeback.py::test_late_confirm_keeps_leaf_attribution` (the genuine red-first) and
    `tests/test_contended_recovery.py::test_late_confirm_on_complete_row_is_not_contention`; a PG
    companion in `tests/test_contended_write.py`.
  - `tests/test_relay_db.py`: subprocess with a PostgreSQL config (closed port) and with a second
    SQLite primary; a `do_connect` recorder asserts only the relay file is opened, the primary is
    not created, `psycopg` is not imported, `Task.__table__.schema is None`; plus the pure
    section-builder.
  - `tests/test_relay_connector.py`: confirm follows a durable insert; a failed insert is not
    confirmed; no request while backlog ≥ bound; upsert refreshes stamps; version error exits;
    each zone requests independently under its own identity (P9).
  - `tests/test_relay_return.py`: the returned record restores the upstream stamp and is APPLIED
    with the leaf id; a replay is DUPLICATE; a return after revert is STALE; a put failure leaves
    the row pending; retention prune.
  - `tests/test_relay_fsm.py`: each relay machine re-enters on timeout, halts on halt and on
    signal, finals on upstream shutdown after drain.
  - `tests/test_relay_link.py`: `UpstreamLink` backoff sequence, classification, logging cadence,
    epoch, worker retirement on overrun (injected clock/rng).
  - `tests/test_relay_roster.py`: leaf registered with site, leaf disconnect forwarded, relay
    eviction disconnects its roster, leaves never evicted on their own, `count_connected` with
    roster, missing roster fields tolerated.
  - `tests/test_relay_launch.py`: the scrubbed environment and the built argv contain no upstream
    key.
- **Integration** (`@mark.integration`, `temp_site`, separate processes, `until()` polls with
  deadlines, done = `COUNT(exit_status = 0) == M`):
  - `tests/test_relay_workload.py`: one relay end to end (P4); two relays, grouped workload with a
    failing-and-retried task, every task completed once upstream, leaf attribution, key scan clean,
    primary sentinels absent (P5); a failover case with evict 10 s (P5); a multi-zone relay taking
    only its zones (P9).
  - `tests/test_relay_outage.py`: a pass/close/blackhole TCP forwarder between relay and upstream:
    partition then results land; SIGKILL during a partition and restart keeps unreturned results
    (P8).
- **Drive the CLI** in a throwaway site:
  `.agents/factory/bin/temp_site.sh sh -c "…"` with `hs server --forever` and `hs relay` (launcher
  `env`), then `hs list`.
- **Multi-node (R21, foreman-run)**: `spec/relay/drive/relay_drive.sh` on a 4-node, 1-hour test
  cluster allocation: node 0 upstream (evict 30 s); site A = nodes 1–2 (survives); site B = node 3
  (relay killed at 40 % completion); shared zone; step-launch launcher. Checks (a)–(e) as in
  [04 §2](research/04-evidence-and-tests.md), judged by `relay_judge.py`; evidence under
  `spec/relay/evidence/cluster/<run_id>/`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
