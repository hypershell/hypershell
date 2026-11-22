---
status: unshaped
kind: feature
appetite: big
target: 2.9.0
lane: public
---

# The queue's RPC framing carries data, not pickled objects

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.
>
> **Prerequisite of** `issues/untrusted-server-task-signing.md` and
> `issues/untrusted-server-client-sandbox.md`. Neither holds against a compromised server while a
> client still unpickles that server's replies.

## Problem

The queue is a `multiprocessing` `BaseManager`, and the manager's RPC envelope (each request, each
reply, each proxy token) is pickled in both directions. TLS (`SecureConnection`) and cleartext
connections alike inherit `send`/`recv` from `multiprocessing.connection._ConnectionBase`
(`core/queue.py:115`), which pickle on write and unpickle on read.

Unpickling can run code chosen by whoever produced the bytes, which is documented behaviour. The
Python documentation warns never to unpickle data from a source you do not trust. So each end of a
queue connection trusts the other with code execution in its own process. The shared key and TLS
decide who may connect; they do not limit what an authenticated peer can make the other end do.
`docs/security.rst` ("Pickle-serialized RPC") already says so. As long as this holds, a client
cannot distrust its server, which is what the signing and sandbox seeds need it to do.

None of HyperShell's own data needs pickle: task bundles, results, confirmations and heartbeats
already cross as JSON bytes (`serialize_tasks`, `heartbeat.pack`, `ClientInfo.pack`), and the
dispatcher's request and reply are JSON text. Pickle carries only the envelope around them.

## Outcome / vision

Each end decodes a message into a small, closed set of plain values and refuses anything else. A
peer that can connect can ask for the queue operations the surface exposes, and nothing more. The
queues, the `Dispatcher`, the in-process server, every FSM and the shutdown order stay as they are:
only the two methods that turn messages into bytes and back change.

## Why this needs no new transport (research, 2026-11-22, CPython 3.14.6)

- `multiprocessing.managers` serializes only through `conn.send` and `conn.recv`: `dispatch()`
  (`managers.py:90-91`), `Server._handle_request` (`:205-220`), `Server.serve_client` (`:257-314`) and
  `BaseProxy._callmethod` (`:831-832`). The connection object comes from the serializer's
  `(Listener, Client)` pair in `managers.listener_client`, on both ends, decref hand-off included.
- The standard library already registers a serializer that is not pickle: `'xmlrpclib'`, which
  wraps the connection and swaps exactly `send`/`recv` (`connection.py:1012-1043`). It cannot carry
  a `Token` or an exception, so it is not usable as it stands, but it is the precedent.
- HyperShell already owns both pairs it uses: `TLS_SERIALIZER` (`core/queue.py:375`) and
  `PLAIN_SERIALIZER` (`:404`). Giving `SecureConnection` and the cleartext connection their own
  `send`/`recv` puts the codec under every message without touching the manager.
- The authentication challenge uses `send_bytes`/`recv_bytes` and never pickles, so it is unchanged.
- The forked-manager path (`SecureManager.start`, `core/queue.py:1024`) is still present. Its
  parent-to-child pipe is local, but its network connections use the same pair. Shaping decides
  whether that path keeps the codec or is retired, since `QueueServer` serves in-process.

## The vocabulary the codec must carry

| Message | Contents |
|---|---|
| Request | `(id, method name, args, kwds)`: `str`/`None`, `str`, a tuple of plain values, a `str`-keyed dict |
| Reply | `('#RETURN', value)`, `('#ERROR', exception)`, `('#TRACEBACK', text)`, `('#PROXY', (exposed, token))`, `('#UNSERIALIZABLE', text)` |
| `Token` | type id, address `(host, port)`, object id |
| Exceptions | the few the surface raises: `queue.Empty`/`queue.Full` (load-bearing: every timed `get`/`put`), `RemoteError`; anything else as `RemoteError` text |
| Values | `None`, `bool`, `int`, `float`, `str`, `bytes`, tuple/list, `str`-keyed dict |

Anything outside this table fails to encode on the sender and is refused on the receiver. That
also guards the future: an exposed method that starts returning a richer object fails a test, not
a peer.

## Settled with Geoffrey (2026-11-22)

- **Our own codec under `BaseManager`**, not a new transport and not a restricted unpickler. A
  restricted unpickler was the stopgap for a slower route, and this one fits in the 2.9 line.
- **Public lane.** Pickle's limits are public knowledge, and `docs/security.rst` already discloses
  the framing. The seed describes the property and the fix, not a method of exploiting it.
- **Target 2.9.0, as 2.9.0a10, appetite big.** Landing it before 2.9.0 final means the 2.9 line ships
  one framing, and the compatibility break falls inside the alphas.

## Open for shaping: the wire format

There are two layers, and they can make different choices:

- **The envelope** above is private to HyperShell, and both ends always ship together, so its
  format can be whatever serves us best.
- **The payloads** (task records, results, heartbeats) are where interoperability matters, and they
  are expensive to change. The dispatcher keeps each staged task as encoded JSON text and joins those
  texts into its reply without encoding them again (`core/dispatch.py:148`, `:307`). The relay stores
  records as JSON, and `Entity.from_json` decodes fields by their declared column type. They stay
  JSON in this cycle.

The options for the envelope:

- **JSON.** No new dependency. Payloads are `bytes`, which JSON cannot hold, so they would need base64
  (a third larger, plus the encoding cost) or a change to `str`. A JSON payload inside a JSON string
  is escaped a second time. Readability is not a real advantage, because the default wire is TLS.
- **msgpack.** Holds `str` and `bytes` natively, so a JSON payload passes through as an opaque blob
  with no second escaping. Tagged values (`Token`, exceptions) fit its extension types, decoded only
  by our own hook. Its decoder takes explicit limits (`max_buffer_size`, `max_str_len`,
  `max_bin_len`, `max_array_len`, `max_map_len`, `strict_map_key`). The costs are below.
- **Both, with msgpack an opt-in extra.** The `uuid7` extra works because that choice is local and
  nothing else has to agree with it. A wire format is a choice two processes must share: an extra on
  one host and not the other needs negotiation and fallback rules, and doubles the test matrix. That
  is the same mixed-fleet problem R4 exists to make clean.

msgpack's costs:
- It becomes a hard dependency: a C extension with a pure-Python fallback. Check its availability in
  EPEL/RHEL (EL8 to EL10) against the low-floors policy. If it is missing there, that settles the
  question.
- Its defaults changed at 1.0 (`raw=False`, `use_bin_type=True`, `strict_map_key=True`), so floor at
  1.0 and pass every option explicitly.
- An integer wider than 64 bits fails to pack, where JSON would carry it. Nothing in the vocabulary
  needs one, but the encoder must refuse one cleanly.
- Tuples decode as lists, which is harmless, because the receiver rebuilds tuples from the closed
  vocabulary anyway.

**Recommendation (agent, 2026-11-22):** one envelope format, no negotiation. Prefer msgpack if it
passes the EPEL check, otherwise JSON. Settle it in the plan phase by benchmarking both envelopes with
`tests/bench/bench_queue.py` at bundle size 1 and at the default size. Payloads in msgpack are a
separate, later performance question. At small bundles the measured ceiling is the server's per-task
SQL (`issues/server-per-task-statement-overhead.md`), not encoding.

## Sketch of the acceptance criteria

- **R1** — Neither end SHALL unpickle bytes received from a peer. Every message SHALL be decoded
  into the closed vocabulary, and one outside it SHALL be refused, closing that connection, without
  constructing any other object.
- **R2** — Every message the manager protocol sends today SHALL round-trip: requests, the five reply
  kinds, `Token`, `create`/`incref`/`decref`/`accept_connection`, and a timed `get`/`put` that
  raises `queue.Empty`/`queue.Full` at the caller, as it does today.
- **R3** — The authentication challenge, TLS, `TCP_NODELAY` and the length-prefixed framing
  (byte-compatible with `multiprocessing.connection`) SHALL be unchanged.
- **R4** — A peer that speaks the pickle framing (a 2.9.0a9 or earlier client, server or relay)
  SHALL be refused on both sides with an error that names the incompatibility, within the existing
  finite timeouts, never by a hang, a traceback or an unpickle. A pickle frame is recognisable from
  its first byte (`0x80`, protocol 2 and later).
- **R5** — No option, configuration key or environment variable SHALL re-enable pickle framing.
- **R6** — The pinned surfaces SHALL be unchanged, and their tests SHALL pass as they stand: the
  nine queue methods (`QUEUE_EXPOSED`), the four dispatcher methods (`DISPATCH_EXPOSED`), the
  shutdown and sentinel ordering, the decref hand-off, and the relay's upstream link.
- **R7** — The decoder SHALL bound the nesting depth and the size of the containers it accepts.
- **R8** — `TestPrivateInternals` SHALL pin the manager's use of `conn.send`/`conn.recv` and the
  reply kinds above on Python 3.11 to 3.14, so that a CPython change to the envelope fails a test.
- **R9** — `bench_queue.py` SHALL show no throughput regression against 2.9.0a9 at bundle size 1 and
  at the default size.
- **R10** — `docs/security.rst` ("Pickle-serialized RPC", marked `.. versionchanged:: 2.9.0`) and
  `AGENTS.md` (the wire-format bullet) SHALL describe the new envelope.

## Notes

- **Mixed versions.** `tests/test_cluster_launch.py` pins launched-client options to what a 2.9.0a4
  client accepts. After this cycle, the oldest client that can actually connect is 2.9.0a10, so that
  test's premise needs restating. A relay is both a client (upstream) and a server (its site), so
  each hop meets R4 on its own.
- **What this does not fix:** peer identity resting on one shared key, and authentication not bound
  to the TLS channel. Those stay separate work.
- **Python versions.** The envelope's shape has been stable for years, but the in-process server
  already leans on private internals across 3.11 to 3.14. R8 extends the same pins.
- Found by: research while reviewing the untrusted-server seed pair, with Geoffrey, 2026-11-22.
