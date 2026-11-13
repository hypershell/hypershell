# Research 01 — Wire compatibility of the topology reply and heartbeat

Scope: R1, R2, R8, R9. Source is `develop` at `fb89670`; `git diff 8403388 fb89670 -- src/` is
empty, so every "8403388" statement below holds for the current tree too.

## 1. Who can read a topology at all

| Build | Sends on heartbeat | Reads topology | Server `topology` method |
|---|---|---|---|
| 2.9.0a7 (latest tag) | `uuid host time state` + `site`, `roster` only (`relay/upstream.py@2.9.0a7:757-759`) | never (no `core/topology.py`, no `fetch`) | absent (`DISPATCH_EXPOSED = ('request', 'takes_submissions')`) |
| 8403388+ (develop, unreleased) | adds `zone bounds clients instance` (`upstream.py:838-848`) | after every running beat (`upstream.py:803-806`) | present (`core/queue.py:986`, `core/dispatch.py:444-453`) |

`instance`, the zone advertisement and `read_topology` all arrived together in 8403388 (#100).
So the only "older relay" that can read a topology is an unreleased develop build
8403388..fb89670; R8's "an older relay's topology read must not fail" concretely means **that
build's `read_topology` must accept the new reply** (and must not just log-and-skip it either,
see §2). A 2.9.0a7 relay is outside the topology entirely: it advertises no zone, so the server
logs it as a non-participant and never records it (`server.py:810-815`), so it can never be a
`Duplicate` and never be retired. In practice every record in `SiteRegistry` carries an instance
key; the non-goal's "identities without an instance key" exist only for a hand-built heartbeat.

The instance key is the relay's first zone identity, `uuid5(INSTANCE, first zone)`
(`relay/__init__.py:533-535`, passed as `instance=self.identity` at `:579`), sent on every beat of
every zone identity.

## 2. `read_topology` tolerance (`core/topology.py:248-275`)

```python
if not isinstance(data, dict) or data.get('protocol') != TOPOLOGY_PROTOCOL:      # :258
    raise ValueError(...)
if 'error' in data: raise ValueError(...)                                          # :260
version = data.get('version'); if version is None: return None                     # :262-264
zones, checks = data.get('zones'), data.get('checks')                              # :265
...
{zone: Entry(zone, _whole(entry['target'], 'target'), _whole(entry['seen'], 'seen'))
 for zone, entry in zones.items()}                                                 # :271-273
```

- **Unknown top-level key:** ignored (only `protocol error version zones checks` are read).
- **Extra key inside a zone entry:** ignored (only `entry['target']`, `entry['seen']`).
- **Extra key in `zones`** (i.e. a fake zone): parsed as a zone; a non-dict value raises
  `ValueError` ("entry is malformed"); a dict value becomes a phantom zone entry.
- **Different `protocol`:** `ValueError`. `version` is opaque text compared only for equality.
- **Not JSON / trailing text:** `ValueError`. JSON is one value, so "extra lines" only matter as
  whitespace (fine) or trailing garbage (fails).
- **Failure mode at the caller** (`upstream.py:875-879`): `ValueError` is caught, logged as a
  WARNING "Topology not read (...) - keeping the last one", and the view is left stale. Not a
  crash, but a behaviour change every beat, so it violates R8.

Checked by feeding modified replies to the current `read_topology` (throwaway script):

| Reply variant | Result |
|---|---|
| top-level `"retired": true` / a list / an object | OK, mark ignored, zones intact |
| per-zone `{"target":0,"seen":2,"retired":true}` | OK, ignored |
| reserved zone key `"__retired__": true` in `zones` | FAIL (malformed entry) |
| `"protocol": 2` | FAIL (not protocol 1) |
| `"version": null, "retired": true` | returns None: mark lost |

`read_request` (`:206-224`) likewise ignores unknown request keys, so a new relay could add a
request field without an 8403388 server refusing it; it must keep `"protocol": 1`.

## 3. Heartbeat tolerance (`core/heartbeat.py`)

`unpack_or_none` (`:130-146`, and `2.9.0a7:107-120`) reads named keys only, with `.get` for the
optional ones: **an unknown field is ignored by both 8403388 and 2.9.0a7** (verified: a beat with
an extra `handback` list unpacks cleanly). But `state` is strict in both:
`ClientState.from_value(unpacked_data['state'])` → `cls(value)` (`:40-43`); a value outside
`RUNNING=0 FINISHED=1 ERROR=2` raises `ValueError` (verified: `3 is not a valid ClientState`).
That raise happens in `HeartMonitor.get_next` (`server.py:762`), which catches only `QueueEmpty`;
`Thread.run` stores the exception (`core/thread.py:32-37`) and the heartbeat thread dies, taking
the server down at its join. **A new `ClientState` (or a new roster entry state,
`RosterEntry.from_dict`, `:63-67`) is unsafe against every older server.** Note also that a
`FINISHED` beat disconnects without reverting tasks (`server.py:796-806`); orphan reversion is
eviction-only (`:913-915`), so any hand-back must happen before the final beat (R6).

## 4. How the reply travels, and whether it can differ per instance

`UpstreamHeartbeat.fetch` (`upstream.py:850-894`) sends one request per beat,
`request_text(site, self.identities)` (all of this relay's identities), by
`link.call('heartbeat', ...)` on `client.dispatcher.topology(text)`, a synchronous proxy call on
the relay's own connection (not a queue). Against 2.9.0a7 the proxy has no such method,
`getattr(..., None)` gives None (`:864-865`), and the relay logs "an earlier release" and clears
the view. `Dispatcher.topology` (`dispatch.py:444-453`) answers with
`reply_text(self.published, identities)`: **the reply is already per requester**, built from the
entries of exactly the identities named, so an old and a new instance at one site get different
replies from the same published `Topology`. The old instance does see its own zero entries:
`entries` gives every non-holder `Entry(zone, 0, record.clients)` (`cluster/topology.py:256-257`),
and `reply_text` includes them (`core/topology.py:237-238`).

Supersession is decided in `_arrange` (`cluster/topology.py:133-165`): per site, `first[instance]`
is each instance's earliest-registered record; `newest` is the instance whose first record has the
highest `registered` (first sight on this server, `SiteRegistry.record`, `core/topology.py:162-178`);
`Duplicate(relay=True)` is emitted for each other instance's *first* record only (`:154-155`).
`warn_duplicates` (`:414-424`) logs once per (older, newer) pair. The dispatcher holds only the
published `Topology`, not the registry, so the retired set must be computed in
`TopologyPublisher.check` and carried on `Topology`. Pitfall: `check` republishes only when
`planned != self.published` (`:391-398`), otherwise it re-publishes `replace(self.topology,
checks=...)`; a retired set held on `Topology` must enter that comparison (or be replaced on every
check), or a supersession that leaves the entries unchanged would never reach the old relay.
Retirement must cover every identity of the older instance (all records with
`instance == dup.older.instance`), not just `dup.older`.

## 5. Recommended encodings

**Retired mark (server → relay).** Keep `protocol: 1`. Preferred: a top-level key present only when
true, `{"protocol": 1, "version": ..., "checks": ..., "zones": {...}, "retired": true}`, set by
`reply_text` when the requested identities are in the published topology's retired set (a new
`Topology.retired: FrozenSet[str]`, default empty). Omitting it when false keeps every
non-retired reply byte-identical to today's (R9), and an 8403388 relay reads the reply exactly as
today (zero entries) per `:258-273`. Alternative: a per-zone-entry `"retired": true`, also ignored
by 8403388; it fits `Entry` but makes "retired" per zone when it is per instance. Do not use a
reserved key inside `zones`, a protocol bump, or a mark on a `version: null` reply (lost at
`:263-264`; a server with a retired set always has a published version anyway). The new
`read_topology` should read the key as `data.get('retired') is True` so absence (any older server)
means not retired, and add a `retired: bool = False` to `TopologyReply`. Collision check: no
older server emits a `retired` key (8403388 `reply_text` writes only `protocol version checks
zones`, `error_text` only `protocol error message`; 2.9.0a7 has no reply), so a new relay can
never see a false mark from an old server. The relay should latch the mark: if the newer instance
is later evicted, the older becomes `newest` again and the mark vanishes from the reply.

**Hand-back (relay → server).** Any new signal is sent only after the relay reads the mark, which
only a new server sends, so the gate alone keeps old servers from ever receiving it. Within that,
encodings ranked by tolerance:
1. A new dispatcher method (e.g. `handback(text)` in `DISPATCH_EXPOSED`), called like `topology`
   via `getattr(..., None)`: absent on 8403388 and 2.9.0a7 → None, no error; it returns a
   synchronous JSON ack, so the relay knows which task ids were requeued before marking them
   handed back locally.
2. A new optional heartbeat field (e.g. a list of task ids): ignored by both older servers
   (`unpack_or_none` uses named keys), but fire-and-forget with no ack, and the heartbeat queue
   is consumed on the heartbeat thread.

Never a new `ClientState` value: it kills an older server's heartbeat thread (§3). Never
`Task.revert_orphaned(identity)` for the relay's identity: it would also revert tasks a site client
has started (R5); the hand-back must name task ids.
