# Research digest — zone-dispatch (Z2)

Six read-only briefs, fanned out in parallel on 2026-11-02 because the change touches the
high-blast-radius core (`core/queue.py`, `server.py`, `client.py`, `data/model.py`, `cluster/`):

| Brief | Topic |
|---|---|
| [01](01-request-callable.md) | The request callable, JSON contract, blocking, version handshake |
| [02](02-server-staging.md) | Per-zone staging in the server, fill and cut, unstaging, shutdown, `in_memory` |
| [03](03-client-request.md) | The client's request loop, advertise-or-not, `--zone`, version error |
| [04](04-zone-data-model.md) | `Zone` table, `task.zone`, get-or-create, selection filter, #69 upgrade step |
| [05](05-cli-cluster-docs.md) | CLI flags, cluster propagation, docs and completions, R12 display, the cap |
| [06](06-evidence-harness.md) | R1 gate, parity, version-skew check, loopback test, cluster drive |

Where the briefs disagree, the decision below is the one `PLAN.md` uses.

## Decisions

1. **Request surface (01).** A new registry entry `_get_dispatch` serves one `Dispatcher` per
   `QueueServer`, exposing only `request(text: str) -> str`. Both sides are JSON text, and every
   request carries `"protocol": 1`. The call runs on the handler thread of the requesting client's
   own connection and replies on that connection (R4). The client calls it from its scheduler
   thread, so a client still holds three persistent connections and Z1's C3 sizing holds. The
   proxy is wrapped by `_defer_decref` (C5).
2. **Old clients (01 vs 03, resolved).** 01 proposed moving live submit to a new typeid
   (`_get_submitted`) so `_get_scheduled` could be refused outright. **Rejected** in favour of a
   protocol argument: `_get_scheduled` stays registered, but its provider requires
   `_get_scheduled(PROTOCOL)`. Every old line (2.8.3, `b819de6`, 2.9.0a2) calls
   `_get_scheduled()` with no argument as its first create, so it gets a `ClientTooOld` traceback
   and exits non-zero from `connect()` before it starts a thread or sends a heartbeat. The new
   `QueueClient`, `LiveSubmitThread` (`hs submit --queue`, the `in_memory` self-connection) and
   every queue test keep using `scheduled` unchanged. `BaseManager.register`'s generated method
   and `Server.create` both pass positional arguments through (checked on the local stdlib).
3. **New client to old server (01, 03).** `QueueClient.connect` creates `_get_dispatch` first. A
   `RemoteError` there is raised as `QueueVersionError(RuntimeError)`, which the shared exception
   mapping turns into one CRITICAL line and a non-zero exit.
4. **Blocking (01, 03).** A manager proxy call has no client-side timeout, so the bound lives on
   the server: `request` long-polls for at most `min(wait, DEFAULT_REMOTE_TIMEOUT)` and then
   returns an empty reply. It never raises for "nothing ready". The client re-enters its request
   state and polls signals there.
5. **Staging (02).** A `Staging` structure inside the `Dispatcher` holds one lock, a FIFO pool
   per zone, a FIFO of waiting tickets per zone (strict first-come-first-served, R4), a demand map
   `client_id -> (zone, size)`, a `need` condition for the scheduler, and a `finishing` flag.
   Only the scheduler thread touches ORM rows. It turns them into JSON text before staging, so
   handler threads never touch the session.
6. **Footprint (02).** Today's footprint is `(queuesize + 1) × bundlesize`: the queue's bundles
   plus the one the scheduler holds while it waits to post. The scheduler loads one chunk of
   `S_z` for a zone whenever that zone's pool holds `≤ queuesize × S_z`, so a pool never exceeds
   `(queuesize + 1) × S_z`. `S_z` is the largest size requested in zone `z`, and never less than
   the server's `-b` for the default zone. In the homogeneous case the chunk sequence equals
   today's bundle sequence.
7. **Cap (02, 05).** A config-only key `server.bundlemax` (default 1000), with effective cap
   `max(bundlemax, server -b)`, so no bundle any configuration delivers today shrinks (R3).
   Config-only means no `hs server` / `hs cluster` flag; it is documented in
   `config_param_ref.rst`.
8. **Advertise or not (03, 05).** The client's `-b` CLI default becomes `None`. The client
   advertises when `-b` was given on the command line or when `config.which('submit',
   'bundlesize')` names a layer other than `default`. Launched clients keep today's argv (they
   always get the cluster's `-b`, which equals the embedded server's `-b`, so behaviour is
   identical). The `submit.` versus `client.` key question goes to the judge.
9. **Zone ids (04).** `zone_id(name) = uuid5(ZONE_NAMESPACE, name)`, persisted with
   `INSERT … ON CONFLICT DO NOTHING`, so get-or-create has no check-then-act window (R8) and needs
   no cache. The default zone has a Source-style fixed id and the reserved name `default`. On
   `task`, the default zone is stored as **NULL** (pre-existing rows already are, and the
   `IS NULL OR = :id` form skips the index on SQLite). Both readings go to the judge.
10. **Data layer (04).** Declare `Zone`, `task.zone` (nullable, no FK) and a non-unique index
    `(zone, group, schedule_time)`. #69's step then creates all three in place with no
    per-column code. The filter is applied in `select_new` and `query_retryable` only; group
    gating stays global (R10). The retry constructor gets `zone=task.zone` (R9).
11. **Shutdown (01, 02).** `Dispatcher.finish()` replaces the per-client sentinels on
    `scheduled`, at the same point as `HeartMonitor.signal_clients`. A reply says
    `shutdown: true` only once the zone's pool (and, in `in_memory` mode, `scheduled`) is empty.
    The sentinels to the heartbeat, receiver and confirm threads are unchanged. Staged rows left
    over after the heart monitor joins are reverted by id with `Task.revert_all`.
12. **`in_memory` (02).** The scheduler is `None`, and `scheduled` stays the inbound channel for
    live submit. A default-zone request drains it lazily into the default pool and re-cuts to the
    client's size. Zoned requests get empty replies until `shutdown`. `--zone` is rejected with
    `hs submit -q` and `hs cluster --no-db` (05).
13. **CLI (05).** Long-only `--zone NAME` on `hs submit`, `hs task submit`, `hs client` and
    `hs cluster`. No config or env keys in Z2, because `HYPERSHELL_ZONE` belongs to Z3. `hs task
    run` is not named in R6 and is left out. Cluster propagation goes to all four modes,
    shell-quoted under SSH. `hs cluster --zone X FILE` also stamps X on the tasks it submits.
14. **Display (05).** R12 is deferrable: `Zone.names_for_ids` and a `zone` field in
    `format_task_fields`, and names in the human and `json`/`csv` outputs of `hs list` /
    `hs info`. `Task.to_json()` stays the wire format and is never changed for display.
15. **Evidence (06).**
    - R1's gate: `bench_queue.py gate --base 2.8.3 --feature HEAD`, plus a new `--min-ratio 0.95`
      and configurations where the client gets no `-b`.
    - `parity` gains `--skip-help` and a group-order check.
    - `compat` is replaced by `skew` (R5 in both directions).
    - A loopback mixed-zone integration test.
    - The cluster drive gains zone knobs, and `compare.py` gains `--zoned` and `--min-ratio`.

## Residual risks carried to PLAN

- Strict 95% throughput bar on a noisy single host (use 7 repeats, don't loosen R1).
- A missed scheduler wake-up turns into a polling delay of up to 2 s and fails R1. The scheduler
  must be woken on every cut and on every first registration of a zone.
- A client that dies between its cut and its confirm leaves stamped, unconfirmed rows. Today's
  `get`-then-crash window does the same, and those rows revert on the next start.
- PostgreSQL paths (the upsert, the index) are untested in research.
- The task fingerprint ignores zone: `--update` of the same file under another `--zone` reports
  every task as already present (pre-existing shape, out of scope, noted).
