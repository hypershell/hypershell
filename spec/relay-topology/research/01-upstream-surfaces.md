# Research 01: upstream wire surfaces (advertisement, fleet counts, topology fetch, compatibility)

Read at `d0c8fd7` (`feature/relay-topology`). The wire files (`core/heartbeat.py`, `core/dispatch.py`,
`core/queue.py`, `relay/upstream.py`, `client.py`) are byte-identical to the previous release,
`2.9.0a7` (`git diff --stat 2.9.0a7 HEAD`; only `server.py`'s scheduler and `data/model.py` moved, for Z3).
So "old server" and "old relay" below both mean 2.9.0a7, which already has the relay.

## Findings

**Heartbeat: one-way, schema-tolerant.**
- `Heartbeat` packs `uuid/host/time/state`, plus `site` and `roster` only when set (`core/heartbeat.py:95-105`).
  `unpack_or_none` reads named keys with `.get` for the optional ones (`:108-120`), and `RosterEntry.from_dict`
  reads only `uuid/host/state` (`:61-65`). `deserialize` is plain `json.loads` (`core/types.py:83-85`). **An old
  server silently ignores any new top-level key (`zone`, `bounds`, `clients`) and any new per-entry key
  (`zone`).** A new server that reads new keys with `.get` handles a beat without them the same way.
- The relay beats once per zone identity, each carrying `site`; only the first identity, the *holder*,
  carries the roster, which is the whole relay's (every zone) (`relay/upstream.py:691-768`, `submit` `:742-768`).
  The roster comes from the relay-local `client` table: id and host only, no zone (`relay/store.py:276-287`).
  Identities are `uuid5(INSTANCE, zone)` (`relay/upstream.py:127-134`). A relay *restart* makes new
  identities, and a *link reconnect* keeps them.
- Heartbeats go over `client.heartbeat.put(...)` on a managed `Queue` (`relay/upstream.py:752-755`). **There is no
  reply or acknowledgement to a heartbeat.**
- Server side (`server.py:686-897`). `get_next` unpacks (`:746-759`). In `update_client` (`:761-793`), a new uuid
  registers: `Client.from_heartbeat` sets `site` and `relay=uuid` on a relay's own row (`data/model.py:1334-1338`).
  A row that already exists takes `Client.reconnect(id, site=, relay=)`, which **overwrites `site`** and clears
  `disconnected_at/evicted` (`server.py:772-779`, `model.py:1364-1366`). `beats[uuid] = hb` keeps the latest
  beat. A roster updates the holder's entries (`:807-840`): new entries are inserted or reconnected with the
  holder's `site/relay`, an entry owned by another relay is ignored, and dropped entries are
  `disconnect_all`. A FINISHED beat pops the identity, ends its roster and calls `dispatcher.forget`
  (`:785-793`).
- Eviction (`check_clients`, `:865-882`) runs every `evict/10` s (`:722-724`; default `evict` is 600,
  `core/config.py:143`). An identity whose last beat's **`hb.time` (relay clock)** is older than `evict` is
  marked `evicted`. Its tasks are reverted with `Task.revert_orphaned(uuid)`, which selects scheduled,
  incomplete tasks whose `client_id` is the identity (`model.py:1141-1159`); that is skipped in-memory or
  with no-confirm. The roster is disconnected (`disconnect_roster`, `model.py:1378-1383`) and the identity
  forgotten in the dispatcher. Detection latency is between `evict` and `1.1·evict`.
- Client columns: `site`, `relay` (`model.py:1297-1302`). There are no zone, bounds or registration-time
  columns. **`connected_at` is set only on first insert (`from_heartbeat`) and is not refreshed by `reconnect`**,
  and `beats` holds the latest beat time, not the registration time. So "most recently registered" has no
  record today. The HeartMonitor must stamp it (server clock) when an identity enters `beats` (`server.py:767`).

**Request/reply ("Z2").**
- `request_text` sends `{protocol, client, host, zone, size, wait}` (`relay/upstream.py:169-172`). `Request.decode`
  reads named keys only and **ignores extra fields** (`core/dispatch.py:115-135`; 4096-char cap `:52`). The
  reply is the hand-built `{"protocol", "tasks", "shutdown"}` (`:138-141`), and errors are `{protocol, error,
  message, server_version}` (`:144-147`). Relay `read_reply` raises only on `error` or a protocol mismatch, and
  reads `tasks`/`shutdown` (`upstream.py:175-182`, `:386-399`). The client reads `reply['tasks']`
  (`client.py:227-233`). **An extra reply field is ignored by old relays and clients.**
- But a request is not a probe. It registers demand (`_register`, `dispatch.py:259-269`), and `size` is
  clamped to at least 1 (`:320`), so any request may cut tasks. The puller never asks at `bound == 0`
  (`upstream.py:354`), and requests run at gate cadence, not heartbeat cadence.
- Remote surface: queues expose `QUEUE_EXPOSED` (`core/queue.py:981-982`) and the dispatcher exposes only
  `('request', 'takes_submissions')` (`:986`, registered at `:1041`). Nothing else can fetch state.
- Unknown method on an old server: the dispatcher's `AutoProxy` class is built from the **server's** exposed
  list (stdlib `AutoProxy`/`MakeProxyType`), so `client.dispatcher.topology` raises **`AttributeError` locally,
  with no round trip**, and `hasattr` detects it. A raw `_callmethod` gets a server-side AttributeError, which
  arrives as `RemoteError`. An unregistered typeid (`_get_topology`) also comes back as `RemoteError`, as
  `connect()` relies on for `_get_scheduled(PROTOCOL)` (`queue.py:1102-1108`). The link re-raises anything not
  fatal (`QueueVersionError`/auth/TLS) and not transport (`EOFError/OSError`) unchanged, and the **link stays
  up** (`core/reconnect.py:89-103`, `:285-291`). A new `QueueClient` is built per connection
  (`relay/__init__.py:505`), so a capability check per epoch is natural.

**Liveness per site and zone.** A live relay identity is a key of `HeartMonitor.beats` (in memory, the
authority), or a `client` row with `id == relay` and `disconnected_at IS NULL` (DB). Zone is implicit in
the identity but **not recorded upstream**: the server learns identity→zone only from `dispatcher.demand`
after a work request, which a zero-client relay never sends. Site comes from `hb.site`. `beats` is mutated by
the heartmonitor thread with no lock. The autoscaler runs in the same process (`cluster/remote.py:859`,
`:896`), so a publisher needs a locked registry, not raw `beats`.

**Counters.**
- `Dispatcher.clients(zone)` (`dispatch.py:412-415`) counts `demand` entries: distinct ids that have
  **requested** in that zone and not yet been forgotten. Upstream, that is one per relay zone identity (not site
  clients) plus ordinary clients, and heartbeat-only identities are excluded. Callers: `report_held`
  (`server.py:377-378`, log only), `AutoScalingCluster` `registered` (`cluster/remote.py:896`, `:535-541`),
  and relay `zone_clients` (`relay/__init__.py:546-548`, which counts **site clients** on the relay-local
  dispatcher).
- `Client.count_connected` (`model.py:1354-1361`) counts site clients and ordinary clients, excluding relay rows,
  with no per-site or per-zone breakdown.

## Options compared (topology fetch)

| | (a) field in work-request reply | (b) new dispatcher method `topology` | (c) heartbeat ack |
|---|---|---|---|
| Exists today | reply yes | no (adds to `DISPATCH_EXPOSED`) | **no reply exists** |
| Zero-client relay learns it | **no**: never requests, and a probe request can cut tasks | yes, at heartbeat cadence | n/a |
| New relay → old server | no field → fallback | `AttributeError` locally, link stays up → fallback | n/a |
| Old relay → new server | ignores field | never calls | n/a |
| Tests touched | `test_reply_shape` exact-equality if always sent | `test_dispatcher_remote_surface_is_pinned` (by design) | — |
| Cost | lowest code, wrong cadence and gate | one method plus decode, own link role | would need a new channel, which is (b) |

A new registry typeid (`_get_topology`) works like (b) but adds a proxy object and decref traffic
for nothing.

## Recommendation

- **Advertise on the heartbeat. Fetch through a new dispatcher method.**
  - **Advertisement:** each zone identity's beat carries its own `zone` (name) and `bounds` `{min,max,init}`, so
    a site-zone's liveness and advertisement are the same identity's. Optionally it also carries
    `clients`, the relay's live site clients in that zone from relay-local `dispatcher.clients`, for the
    per-site, per-zone count `fleet_demand` needs. That is better than putting `zone` on roster entries,
    whose zone is unknown in the relay `client` table. Send it on **every** beat. That satisfies "on connect
    and every reconnect", is idempotent, and survives a server restart. The new HeartMonitor overwrites a
    per-identity record in a locked registry (site, zone, bounds, count, registered-at stamped at
    `server.py:767`), and clears it at eviction or finish next to `end_roster`/`forget`.
  - **Fetch:** `Dispatcher.topology(text) -> text`, added to `DISPATCH_EXPOSED`. The request is
    `{protocol, site, identity}`. The reply is `{protocol, version, entries: {zone: count}}` for that site, or
    `{protocol, version: null}` when the server is not autoscaling with sites. The relay calls it on the
    heartbeat cadence under its own link role, gated on `hasattr(client.dispatcher, 'topology')`. On a
    miss, an `AttributeError`/`RemoteError`, or `version: null`, it uses the local fleet (R6). It applies a
    reply only when `version` changes. The `PublishActuator` sets the topology under the dispatcher lock, in process.
  - Reuse no work-request field. It cannot reach a zero-client relay, and a probe would take tasks.
- **Legacy beats:** a site beat without `zone`/`bounds` (a 2.9.0a7 relay) is a non-participating relay. It gets
  no topology entries, while its pulls, roster and failover work as now.

## Compatibility matrix

| | Old server (2.9.0a7) | New server, not sites | New server, sites |
|---|---|---|---|
| **Old relay** | as shipped | as shipped | relay is excluded from the topology (no advertisement); runs its local fleet; work and failover unchanged |
| **New relay** | extra beat keys ignored; `topology` missing → local fleet (R6) | `version: null` → local fleet (R6) | full topology |
| Ordinary client | unchanged | unchanged | unchanged (no `site` → never a site) |

2.8.x and earlier are already refused at connect (`QueueVersionError`, `queue.py:1102-1108`).

## Tests to keep green (or amend deliberately)

- `test_relay_roster.py`: `test_beat_without_roster_fields_decodes` (a legacy beat decodes, and a plain beat
  packs exactly 4 keys). **Exact key sets** in `test_beat_with_site_and_roster_round_trips` (`{uuid,host,time,
  state,site,roster}`, entries `{uuid,host,state}`) and `test_relay_beat_carries_site_and_roster` need amending
  if beats gain keys. Also: leaf registration and attribution, disconnects, `beat_without_roster_keeps_the_roster`,
  `roster_never_takes_another_relays_leaf`, eviction and finish disconnect the roster, `leaf_never_evicted_on_its_own`,
  `count_connected_counts_leaves_not_relays`, `reregistration_refreshes_server`, `beat_without_roster_fields_is_accepted`,
  `relay_without_failover_logs_one_error`, `pre_column_database_upgrades_in_place`, `every_identity_beats_and_the_holder_carries_the_roster`,
  and `failover_of_a_two_zone_relay_reverts_and_unstages_both_zones`.
- `test_relay_connector.py`: `request_is_an_ordinary_work_request` pins request fields. Also
  `no_request_without_site_clients`, `a_relay_with_no_site_client_in_any_zone_never_asks`, the gate and size tests,
  `error_reply_raises_the_client_version_error`, and `real_dispatcher_serves_the_relay_as_a_client`.
- `test_dispatch_version.py`: `test_dispatcher_remote_surface_is_pinned` (exact `DISPATCH_EXPOSED` and the proxy
  `_exposed_`; must change with (b)), old client refused, new client vs old server, other-protocol reply, CLI
  vs old server.
- `test_dispatch.py`: `test_reply_shape`, `test_empty_reply_within_wait` and `test_finish_wakes_waiters_and_drains_first`
  assert exact reply dicts. Keep the work-request reply unchanged.
- `test_queue.py::test_exposed_methods` (queue surface), `tests/__init__.py:106-108` (`ORIGINAL_CLIENT_COLUMNS`,
  for the upgrade path if columns are added), `test_server_fsm.py` (HeartMonitor in memory),
  `test_relay_fsm.py`/`test_relay_link.py`/`test_relay_outage.py` (reconnect, evict hint, re-registration), and
  `test_relay_judge.py` (failover evictions and reverts).
