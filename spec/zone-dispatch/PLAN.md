# PLAN — Zone-aware, demand-driven dispatch (zone, cycle 2)

> **Status:** Draft for review · **Last updated:** 2026-11-02
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

A client stops pulling from the shared `scheduled` queue. It calls one new manager-served callable,
`request`, with its id, zone and advertised size. That call runs on the server's handler thread
for the client's own connection and returns, on that connection, up to that many tasks cut from
its zone's staging pool. The scheduler fills one FIFO pool per zone that has demand, with the same
`Task.next` stamping and the same footprint bound as today. A `Zone` table and a nullable
`task.zone` column arrive through #69's declarative upgrade step. Zone ids are derived from the
name, so get-or-create has no race. In the homogeneous case (every task and client in the default
zone, no advertised size) the server cuts the same bundles in the same order as today, so R1 holds
by construction and is then measured against 2.8.3.

## 2. Design

### 2.0 Base and sequencing

This branch carries only spec commits on top of Z1's `aa55e86`. Before `hs-build` starts, it is
rebased onto `develop` once `develop` holds Z1, #69 (`data/upgrade.py`) and
`writeback-scoped-conditional` (§3.2). The code is therefore written **against** the writeback
result rather than merged with it. All line references below are to `origin/develop` (`d996579`)
or `b54d685`, as each brief states; they move with the rebase.

### 2.1 The request callable (`core/queue.py`, new `core/dispatch.py`)

- **Registry.** `QueueServer.start`'s per-instance registry gains
  `'_get_dispatch': (_provider(self.dispatcher), ('request',), None, AutoProxy)`. `self.dispatcher`
  is a `Dispatcher` built in `QueueServer.__init__` (pure Python, no DB access). `QueueClient`
  registers `_get_dispatch` and creates it **first** in `connect()`, wrapped by `_defer_decref`.
  The client calls `request` only from its scheduler thread, so a client keeps three persistent
  connections and Z1's descriptor reserve sizing (C3) is unchanged
  ([01 §1](research/01-request-callable.md)).
- **`request(text: str) -> str`**, both JSON text (R3).
  - Request: `{"protocol": 1, "client": <uuid>, "host": <str>, "zone": <name>|null,
    "size": <int>|null, "wait": <seconds>}`.
  - Reply: `{"protocol": 1, "tasks": [<Task.to_json()>…], "shutdown": <bool>}`, or
    `{"protocol": 1, "error": "protocol"|"request", "message": <str>, "server_version": <str>}`.
  - The server clamps `size` to `[1, cap]` and `wait` to `[0, DEFAULT_REMOTE_TIMEOUT]`, bounds the
    request text's length, and answers a malformed request or a protocol mismatch with an error
    reply. It never raises for a bad request. Staged tasks are pre-encoded JSON, so the cut builds
    the reply by joining strings.
- **Blocking (R4, AGENTS § Concurrency).** `request` waits on its zone's condition until its
  ticket is at the head of the zone's FIFO and the pool is non-empty, or until its deadline
  passes. It then cuts `min(size, len(pool))` tasks: a partial bundle if fewer are ready, an empty
  reply at the deadline. The cut and the pop happen under the staging lock, so no task goes to two
  clients. Each cut notifies the scheduler's `need` condition. Each reply is logged at DEBUG:
  `Dispatched N tasks to client <id> (zone <name>)`.
- **Version handshake (R5).**
  - *Old client → new server.* `_get_scheduled`'s provider requires the protocol argument:
    `_get_scheduled(PROTOCOL)` returns the queue, and `_get_scheduled()` raises
    `ClientTooOld("…requires the work-request protocol…")`. Every old line calls
    `_get_scheduled()` first in `connect()`, gets a traceback reply, and exits non-zero at once,
    before any thread or heartbeat. The server logs one WARNING per refused connection,
    `Client too old (no work-request protocol) from <peer>`. The preferred form sets a
    thread-local flag in the provider and logs it in `_InProcessServer.handle_request` after the
    connection closes, outside the server mutex. Other connections are unaffected.
  - *New client → old server.* Creating `_get_dispatch` raises `RemoteError` (`KeyError`).
    `connect()` raises `QueueVersionError(RuntimeError)`: "server at H:P does not support the
    work-request protocol (2.8.x or 2.9.0a2?); run matching versions". This gives one CRITICAL
    line and a non-zero exit, with no fallback. A protocol-mismatch error reply raises the same
    exception, naming both numbers.
  - The new `QueueClient`, `LiveSubmitThread` and every existing queue test use
    `_get_scheduled(PROTOCOL)` and keep the `scheduled` queue unchanged
    ([digest decision 2](research/00-digest.md)).
- **New private-internals pins** (`unsupported_internals()`, `TestPrivateInternals`):
  - `Server.serve_client` takes no mutex.
  - `Server._handle_request` turns an exception into `'#TRACEBACK'`.
  - `Server.create` passes `*args` to the registry callable.
  - `BaseManager.register`'s method passes `*args` to `_create`.
  - `convert_to_error` produces `RemoteError`.
  - `BaseProxy._callmethod` caches a connection per thread (`_tls.connection`).

### 2.2 Staging and the scheduler (`core/dispatch.py`, `server.py`)

- **`Staging`** (owned by the `Dispatcher`):
  - One lock.
  - Per zone id: a `deque` of `(task_id, json_text)`, a ticket FIFO, and a `ready` condition.
  - A `need` condition for the scheduler.
  - `demand: client_id -> (zone_id, size)`.
  - `S_z = max(sizes requested in z)`. For the default zone, `S_z` is at least the server's `-b`.
  - A `finishing` flag.
  - Zone ids are computed from names with `zone_id()` (§2.4), so the callable never touches the
    database.
- **Fill.** `Scheduler` keeps START, LOAD and FINAL. PACK and POST become `STAGE`. For each zone
  with demand (the default zone always counts as demanded), while `len(pool) ≤ queuesize × S_z`,
  LOAD calls `Task.next(limit=S_z, attempts, eager, group=current_group, zone=z)` and STAGE
  appends the rows as JSON text. So a pool holds at most `(queuesize + 1) × S_z` tasks, which is
  today's footprint ([02 §1](research/02-server-staging.md)). Waits are `need.wait(timeout)` with
  today's backoff bounds instead of `time.sleep` or a blocking `put`. The scheduler is woken by
  every cut and by every first registration of a zone. LOAD still polls `check_signal()`.
- **Group gating (R10).** `current_group`, `increment_group`, `count_remaining` and
  `count_retryable` stay global. LOAD calls `increment_group` only when every demanded zone that
  needed a refill loaded nothing. Staged rows are stamped but incomplete, so they hold the group
  exactly as bundles on `scheduled` do today. A group held by a zone with no registered client
  logs one INFO line per (group, zone): `Task group G waiting on zone <name>: N tasks, no client
  serving it`.
- **Cut sizing (R2, R3).** `size = request.size or server_bundlesize`, clamped to
  `cap = max(config.server.bundlemax, server_bundlesize)`. `server.bundlemax` is a new config-only
  key, default 1000.
- **Unstaging by id.**
  1. When HeartMonitor sees a client finish or evicts it, it calls `dispatcher.forget(client_id)`.
     If a non-default zone loses its last client, the dispatcher marks that zone's pool for
     unstaging, and the scheduler thread pops the ids and reverts them with `Task.revert_all(ids)`.
     Only the scheduler thread writes to the database.
  2. Rows still staged after the heart monitor has joined are reverted the same way in
     `ServerThread` shutdown.
  3. A cancelled row that is already staged is delivered as today, and the writeback fence
     refuses its result.
- **Shutdown (AGENTS § Shutdown ordering).** `HeartMonitor.signal_clients` calls
  `dispatcher.finish()` instead of putting one sentinel per client on `scheduled`. Waiting requests
  wake. A reply says `shutdown: true` only once the zone's pool is empty, and in `in_memory` mode
  only once `scheduled` is empty too, so staged work drains first as today. The order stays
  submitter → scheduler → (finish) → sentinels to heartbeat, receiver, confirm. `QueueServer`
  exit also calls `finish()` (idempotent).
- **`in_memory` (invariants §4).** `Scheduler` is `None`. A default-zone request with an empty
  pool drains bundles from the local `scheduled` queue (fed by `LiveSubmitThread`) into the
  default pool and re-cuts them to the requested size. Zoned requests get empty replies until
  `shutdown`. No database write is added.

### 2.3 The client (`client.py`)

- `ClientScheduler.GET_REMOTE` becomes `REQUEST`. It builds the request, calls
  `dispatcher.request`, and goes to UNPACK on tasks, back to REQUEST on an empty reply (after
  `check_signal()` and the `client_timeout` idle check, exactly as `get_remote` does today), or to
  FINAL on `shutdown: true`. An error reply raises `QueueVersionError`. UNPACK decodes
  `Task.from_json` dicts. PUT_CONFIRM, POP and PUT_LOCAL are unchanged. The client is ready to
  request again once the last task of the bundle is handed to the local queue, as today. The
  shutdown order (scheduler → executors → collector → heartbeat) and the executor sentinels are
  unchanged ([03](research/03-client-request.md)). The client log line `Received N tasks` (DEBUG)
  keeps its text; tests and the cluster drive key on it.
- **Advertise (R2).** `ClientApp`'s `-b` default becomes `None`. The effective local value is
  unchanged (`config.submit.bundlesize`); it still sizes the collector and the inbound and
  outbound local queues. The advertised size is the `-b` value when given on the command line,
  or when `config.which('submit', 'bundlesize') != 'default'` (environment or any config file);
  otherwise `None`. In the Python API, `bundlesize=None` advertises nothing. The `-b` help text
  says both meanings.
- **`--zone NAME`** (long only; no config or env key in Z2). Names are stripped. An empty name or
  a name with control characters exits with a usage error, through one validator shared with
  submit and cluster. The name `default` means the default zone. The client sends the name; the
  server computes its id. A client with no `--zone` sends `null`, which means the default zone
  (R11).

### 2.4 Data layer (`data/model.py`)

- `Zone(id UUID PK, name TEXT NOT NULL UNIQUE, created DATETIME NOT NULL)`, modelled on `Source`
  (`NotFound`, `from_id`, `names_for_ids`). `DEFAULT_ZONE_ID` is a fixed literal in the
  reserved-Source style, with `DEFAULT_ZONE_NAME = 'default'` (R7, R11).
- `zone_id(name)`: `DEFAULT_ZONE_ID` for `None`, `''` or `'default'`; otherwise
  `str(uuid5(ZONE_NAMESPACE, name))`. `Zone.ensure(name)` persists the mapping with one
  dialect-dispatched `INSERT … ON CONFLICT DO NOTHING` and a commit. There is no read before the
  insert, so concurrent resolvers converge on one row and none fails (R8). The default row is
  ensured at server start in DB mode. Readers never require it and map NULL to `default`.
- `task.zone`: nullable UUID, no foreign key, declared after `fingerprint` (`tag` stays last).
  New unzoned tasks store NULL, so the default zone is canonically NULL; old rows already are
  (R11, no rewrite). `Task.new(zone=…)` normalises `DEFAULT_ZONE_ID` to `None`.
- `index_tasks_zone (zone, group, schedule_time)`, non-unique. #69's `upgrade_schema` creates the
  table, the column and the index in place, forward-only, with no per-column code (R11;
  [04 §1](research/04-zone-data-model.md)).
- `_filter_zone(query, zone)`: `zone=None` means no filter (every existing caller is unchanged);
  `DEFAULT_ZONE_ID` means `zone IS NULL`; anything else means `zone = :id`. It is applied in
  `select_new` and `query_retryable`, and threaded through `Task.next` and its private helpers.
  The stamping loop in `Task.next` is **not** edited.
- Retry: `__schedule_next_failed_tasks` builds the new row with `zone=task.zone` (R9). Revert and
  cancel never touch `zone`.

### 2.5 CLI, cluster, docs (`submit.py`, `task.py`, `cluster/*`, `docs/`, `share/`)

- `hs submit --zone` (DB mode) resolves the zone once (`Zone.ensure`) and carries `zone_id` on
  `GatedSource`, the way `source_id` already travels, so `Loader` stamps both `Task.new` sites;
  `submit_one` passes it directly. It is a usage error with `-q`. `hs task submit --zone` passes
  it to `Task.new` (R6).
- `hs cluster --zone`:
  - It stamps the tasks it submits through the same `GatedSource` route.
  - It propagates `--zone` to clients in all four modes: local `ClientThread(zone=)`; remote and
    autoscale `client_args`; ssh `client_args` through `shlex.quote`.
  - It is a usage error with `--no-db`.
  - The `-b` argv is unchanged (invariants §11; [05 §3](research/05-cli-cluster-docs.md)).
- R13, each in the same commit as its CLI change:
  - `docs/_include` usage and help snippets for submit, task submit, client and cluster, plus
    `client_desc.rst` and the server `-b` wording.
  - `config_param_ref.rst` (`server.bundlemax`, client `-b` meaning).
  - `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`.
  - Man pages are left to `/hs-release`.

### 2.6 Display (R12, deferrable; `task.py`, `data/model.py`)

- `Zone.names_for_ids`, `resolve_zone` (NULL → `default`, unknown id → raw id), and
  `data['zone']` / `data['zone_id']` in `format_task_fields`.
- A `zone: name (id)` line in the normal view, and a `zone` row in the card's submission region.
- Names in `hs list` and `hs info` json/csv/plain/table and `-x zone`.
- `Task.to_json()` is never changed.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Homogeneous path cuts the same chunk sequence with the same footprint (§2.2); `in_memory` and live submit unchanged in shape; gate vs 2.8.3 with `--min-ratio 0.95`, parity incl. group order (§6) |
| R2 | `request.size` from advertised `-b`; `None` → server `-b`; advertise rule via `config.which` (§2.3) |
| R3 | `cap = max(server.bundlemax, server -b)`; JSON-text request/reply (§2.1, §2.2) |
| R4 | Reply on the requesting connection; per-zone ticket FIFO; cut under one lock; partial / empty-at-deadline (§2.1) |
| R5 | `_get_scheduled(PROTOCOL)` refusal + WARNING; `_get_dispatch` first + `QueueVersionError`; `protocol: 1` (§2.1) |
| R6 | `--zone` on submit, task submit, client, cluster; cluster propagation × 4 and cluster-submit stamping (§2.3, §2.5) |
| R7 | `Zone` table; `task.zone` UUID (§2.4) |
| R8 | `uuid5` id + `INSERT … ON CONFLICT DO NOTHING`; cache unnecessary (§2.4) |
| R9 | Per-zone pools filled by `Task.next(zone=)`; retry row copies `zone` (§2.2, §2.4) |
| R10 | Global group functions; refill-then-increment rule; held-group log line (§2.2) |
| R11 | Default zone = NULL + fixed id; no `--zone` → default only; #69 step creates schema (§2.3, §2.4) |
| R12 | Name display (§2.6), own late phase |
| R13 | Docs/completions in the same commit as each CLI change (§2.5) |
| R14 | Unit tests for dispatcher, client FSM, model; loopback mixed-zone workload; version-skew; existing suite (§6) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design was drafted.

- **§1 Task lifecycle.** Staging stamps through `Task.next`, unchanged. Unstaging reverts through
  `Task.revert_all`, so a staged row is "in-flight" exactly like a bundle on `scheduled` today and
  `revert_interrupted` still covers a crash. The new zone predicate is added beside the existing
  predicates and never replaces one. Transition logic stays in `Task` classmethods.
- **§2 `exit_status`.** No new sentinel. `query_retryable` keeps its `!= 0 AND != CANCEL_STATUS`
  filter; the zone filter is an extra conjunct.
- **§3 Retry model.** Retries are still new rows; `zone` is copied. `attempts` and the global
  `count_retryable` / `increment_group` are unchanged.
- **§4 Server modes.** `in_memory`: no DB write is added (§2.2). `no_confirm`: unchanged, and a
  dead client's cut tasks are not rescheduled, as today.
- **§5 Concurrency.** The new scheduler state has an action and a HALT path. Every blocking wait
  (request long-poll, scheduler `need.wait`) is finite and re-enters. `check_signal()` is polled
  in LOAD (server) and REQUEST (client). `stop()` overrides keep `machine.halt()` first.
- **§6 Shutdown ordering.** Client order is unchanged. Server: submitter → scheduler → `finish()`
  at the point the per-client sentinels were posted → sentinels to heartbeat, receiver and
  confirm (unchanged). Remote payloads are JSON text, and no live object or `None` crosses the
  wire.
- **§7 Resources.** Executors, the inbound queue and `executor_lock` are untouched.
- **§8 Signals.** No new handler and no `reset_signal()`.
- **§9 Transport.** TLS, auth, the key rules, the fingerprint pin and the stock framing are
  unchanged. The new typeid exposes one method and carries JSON text.
- **§10 Configuration.** New key `server.bundlemax`. The client's `-b` CLI default becomes
  `None`, with the effective value unchanged. No `HYPERSHELL_ZONE` is introduced.
- **§11 Cluster.** `--zone` is replicated across local, remote, ssh and autoscale, shell-quoted
  for ssh. JSON-mode template handling is untouched.
- **§12 Conventions.** Docs and completions go in the same commit as each CLI change. Tests are
  `unit` or `integration`. Exit codes come from `exit_status`. The version stays single-sourced.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `_get_scheduled` requires a protocol argument: a deliberate 2.8 wire break | R5 requires old clients to be disconnected cleanly and to exit in bounded time | An always-empty `scheduled` hangs old clients forever. A sentinel-only queue lets them write a `Client` row and exit 0 silently. A new `_get_submitted` typeid moves live submit and rewrites every queue test ([01 §4](research/01-request-callable.md)) |
| Default zone stored as NULL on `task`, not the fixed id | Old rows must be default without a rewrite (R11). The `IS NULL OR = :id` form skips the index on SQLite and doubles every default-zone predicate on the R1 hot path | Storing the fixed id on new rows reads R7 most literally but slows the default-zone drain. See **[NEEDS CLARIFICATION] N2** |
| Zone ids are `uuid5(namespace, name)` rather than random | R8's concurrency safety holds by construction. The request callable, `--no-db` paths and clients need no DB lookup | A random id with `ON CONFLICT (name)` + `SELECT` is also race-free, but every resolver must touch the DB, including on a manager thread. See **N1** |
| `test_server_fsm.py`: five white-box tests that pin `Scheduler` posting on `scheduled` and `HeartMonitor` sentinels are re-pointed at the dispatcher | The behaviour they pin is what Z2 replaces (R2, R4) | Keeping `post_bundle` onto `scheduled` and draining it into pools breaks the per-zone footprint and demand signal. See **N3** |
| `AGENTS.md` (Architecture, Queue transport, shutdown ordering) and `invariants.md` §6 prose updated: "sentinels to clients" becomes "`finish()` on the dispatcher" | Keeps the constitution true. Ordering is preserved and nothing is relaxed | Leaving the prose stale makes `hs-review` grade against a mechanism that no longer exists. To be flagged at sign-off, as writeback's R10 was |

### 3.2 Overlap with `writeback-scoped-conditional`

That cycle's GOAL is on its branch; its PLAN and TECH do not exist yet. It lands first, so Z2's
code is written on top of it. Shared touchpoints, and the rule for each:

| File · function | Writeback (expected) | Z2 | Rule |
|---|---|---|---|
| `model.py` `Task` columns | perhaps an assignment column; a result-column set | adds `zone` after `fingerprint` | `zone` is server-held, never a result column. If the fence is a denylist, add `zone` to it. |
| `model.py` `Entity.update_all` | scoped, conditional write | none | do not touch |
| `model.py` `Task.next` stamping loop | may stamp an assignment token | signature + `zone=` pass-through only | never edit the loop; staging serialises rows **after** `Task.next`, so any stamp rides along |
| `model.py` `revert_all` / `revert_orphaned` / `cancel_all` | end or refuse an assignment | none; Z2 only *calls* `revert_all` | unstaging must end an assignment the way revert does; reusing `revert_all` guarantees that |
| `model.py` retry constructor | likely none | `zone=task.zone` | one-line add |
| `server.py` `Receiver.update_tasks`, `Confirm.update_info` | rewrite or fence | none | do not touch |
| `server.py` `HeartMonitor.check_clients` / `update_client` | eviction ends assignments | one `dispatcher.forget(id)` line each | added after the existing revert/finish statements, as separate lines |

**Rebase plan.**
1. Z1 lands on `develop`.
2. `writeback-scoped-conditional` lands.
3. `git rebase --onto origin/develop aa55e86 feature/zone-dispatch` moves only spec commits, so
   there is nothing to resolve.

`TECH.md` P1's verify checks that #69 and writeback are present. If writeback slips past Z2's
build start, the foreman decides between waiting and building on `develop` + Z1. In the second
case the table above is the conflict map for a later code rebase.

## 4. Rabbit holes (resolved)

- Where does the request run, and does its reply reach only the requester? On the handler thread
  of that client's connection, without the server mutex; checked on 3.12–3.14
  ([01](research/01-request-callable.md)).
- How do old clients exit in bounded time? By refusing their first create, `_get_scheduled()`,
  at once ([01 §4](research/01-request-callable.md), [digest 2](research/00-digest.md)).
- How can the client tell an explicit `-b` from the default? `config.which` reports the layer,
  including when the value equals the default ([03 §3](research/03-client-request.md)).
- Does #69's step take a table, a column and an index with no code? Yes: a prototype on a
  2.9.0a2-shape database added all three, and a second run was a no-op
  ([04 §1](research/04-zone-data-model.md)).
- Can staging keep today's footprint and order? Yes, with chunks of `S_z` refilled at
  `queuesize × S_z` ([02 §1](research/02-server-staging.md)).
- Can 2.8.3 be the gate base? Yes: the tag builds with its lock file, and each side runs its own
  matched server and client ([06 §1](research/06-evidence-harness.md)).

## 5. Risks & open questions

- **R1's 95% bar.**
  - The cut adds a lock and a condition per request; a request replaces today's `get` +
    `task_done` (two round trips) with one.
  - A missed scheduler wake-up turns into a polling delay of up to 2 s. Unit tests assert that a
    cut wakes the scheduler.
  - The new index adds write cost. If the gate shows it costing more than ~2%, drop the index (it
    is not needed for correctness).
  - The gate uses 7 repeats.
- **A client that dies between its cut and its confirm.** Its tasks stay stamped and unconfirmed
  until the next start, the same window as today's `get`-then-crash. Recording cuts per client
  for eviction is a follow-up seed, not Z2 scope.
- **A zone without clients holds the group** (expected, R10). It is logged.
- **Untested here:** PostgreSQL upsert and index; Python 3.11 for the new internals pins (CI
  covers it).
- **Pre-existing behaviour, noted and out of scope:**
  - The task fingerprint ignores zone.
  - `hs update zone=<raw id>` is accepted.
  - `TASK_ZONE` in a task's environment carries the id.
  - `auto_union_sqlite`'s `select *` across partitions with different column sets (a #69 concern
    that applies to any new column).
- **[NEEDS CLARIFICATION]** N1–N4. See `TECH.md` § Open rulings, which repeats them with options
  and a recommendation. Build proceeds on the recommendation unless ruled otherwise.

## 6. Verification strategy

- **Unit** (`@mark.unit`):
  - `tests/test_zone_model.py`: get-or-create under concurrent processes, the upgrade of a
    2.9.0a2-shape database, the zone filter, retry keeps zone, gating stays global.
  - `tests/test_dispatch.py`: sizing, cap, partial and empty replies, FIFO, no double delivery
    under concurrent requesters, scheduler wake on cut, finish and drain, `in_memory` re-cut,
    unstage.
  - `tests/test_client_request.py`: a fake dispatcher; re-enter on timeout, halt on shutdown, the
    advertise rule.
  - `tests/test_dispatch_version.py`: an old-shaped client refused while a new one keeps working;
    the new client against a stock manager.
- **Integration** (`@mark.integration`, `temp_site`):
  - `tests/test_dispatch_workload.py`: hsx default path TLS on and off, `--no-db`,
    `hs server -b 100` with plain clients, `hs submit -q`.
  - `tests/test_zone_workload.py`: two zones, unzoned clients, mixed `-b`, three groups, one
    failing-then-retried task, zone checked via an env marker. Plus the existing suite.
- **Harness:**
  - `bench_queue.py parity --base 2.8.3 --skip-help`, which also checks group order.
  - `skew --old 2.8.3 --old 2.9.0a2`.
  - `gate --base 2.8.3 --repeats 7 --min-ratio 0.95`, with configurations where the client gets
    no `-b`.
- **Multi-node** (foreman-run): see `TECH.md` P6–P7.
