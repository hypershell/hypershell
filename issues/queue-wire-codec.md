---
status: adopted:queue-wire-codec
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

## The envelope is JSON (settled with Geoffrey, 2026-11-22)

Encoding does not limit throughput in production. With a database, the database does: the server
runs its own statements for every task, and `nf-hypershell` measured about 150 tasks/s on a laptop,
77/s with SQLite on home, and 38/s on Lustre (`issues/server-per-task-statement-overhead.md`). That is
roughly 7 to 26 ms per task.

A micro-benchmark of the envelope alone (CPython 3.12, records shaped like `Task.to_json`, a
round trip, 2026-11-22):

| Bundle | pickle | JSON, payload as text | JSON, payload as base64 |
|---|---|---|---|
| 1 task (733 B) | 0.4 µs | 4.4 µs | 6.6 µs |
| 100 tasks (73 KB) | 0.06 µs/task | 2.2 µs/task | 4.1 µs/task |
| 1000 tasks (735 KB) | 0.02 µs/task | 2.2 µs/task | 4.0 µs/task |

A JSON envelope adds 2 to 7 µs per task, under 0.1% of the per-task database cost. It's the same
order as the payload's own JSON encoding, which every run already pays (about 6 µs per task). Even
without a database, the envelope alone would allow over 200,000 tasks/s, far above the rate clients
can start subprocesses. The frame grows about 10% when the payload goes as text, or 33% as base64.

So the envelope is JSON: no new dependency, one format, nothing to negotiate. msgpack was
considered. It would carry `bytes` natively and avoid escaping a JSON payload a second time, but it
would add a hard dependency (whose EPEL/RHEL availability is unverified) to save microseconds.
Payloads stay JSON as well. That keeps the dispatcher's encoded staged rows, the relay's JSON store
and `Entity.from_json` as they are.

**Left for the plan:** how `bytes` crosses the envelope. Every payload is UTF-8 JSON text, so either
the codec carries a `bytes` value as a tagged UTF-8 string and refuses bytes that are not UTF-8
(cheaper, as in the table), or payload producers and consumers switch to `str`, leaving no `bytes`
in the vocabulary. Base64 works too, but costs the most and makes the frame largest.

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
