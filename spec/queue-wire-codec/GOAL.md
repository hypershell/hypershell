# GOAL — The queue's RPC envelope carries data, not pickled objects

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** queue-wire-codec
- **kind:** feature
- **appetite:** big  ·  *Taken from the seed (`big`, agreed with Geoffrey on 2026-11-22). One seam, the
  two connection pairs in `core/queue.py`, but it sits under every queue message, on CPython manager
  internals, and breaks compatibility with every earlier release, so `hs-plan` does its research pass.*
- **target:** 2.9.0, as 2.9.0a10  ·  *the lead cycle of 2.9.0a10, so the 2.9 line ships one framing and
  the compatibility break falls inside the alphas.*

**Ruled at the GOAL gate** (2026-11-23). Approved by the foreman, gate judge under the maintainer's
mandate, with the maintainer's own rulings on the forked manager and old peers.

- **Q1 (maintainer):** keep the forked manager; it takes the codec through the shared serializer pair,
  and retiring it is a follow-up seed.
- **Q2 (maintainer):** option (b), one fixed encode-only error reply, then close; (c) if the plan shows
  a 2.9.0a9 client does not exit on it; never (a).
- **Q3:** reuse `QueueVersionError` and `exit_status.runtime_error`; no new status.
- **Q4:** median of 5 runs within 5% of 2.9.0a9 on the same host, or within 2.9.0a9's own run-to-run
  spread if that is larger.

The contract is locked; `hs-plan` designs against it.

## Problem

The queue is a `multiprocessing` `BaseManager`, and the manager's RPC envelope (each request, each
reply, each proxy token) is pickled in both directions. Both of HyperShell's connection types inherit
`send`/`recv` from `multiprocessing.connection`: `SecureConnection` subclasses `_ConnectionBase`
(`core/queue.py:115`), and the cleartext pair is the stock `Listener`/`Client` behind
`_PlainListener`/`_plain_client` (`core/queue.py:378`, `:389`). Both pickle on write and unpickle on
read.

Unpickling can run code chosen by whoever produced the bytes; that is documented Python behaviour, and
the Python documentation warns never to unpickle data from a source you do not trust. So each end of a
queue connection trusts the other with code execution in its own process. The shared key and TLS
decide who may connect; they do not limit what an authenticated peer can make the other end do.
`docs/security.rst` ("Pickle-serialized RPC", known limitation 2) already says so, and `AGENTS.md`
(the wire-format bullet) calls the framing an RCE surface. As long as this holds, a client cannot
distrust its server — which is exactly what the task-signing and client-sandbox seeds need it to do.

None of HyperShell's own data needs pickle. Task bundles, results, confirmations and heartbeats
already cross as JSON bytes (`serialize_tasks`, `data/model.py:386`; `Heartbeat.pack`,
`core/heartbeat.py:113`; `ClientInfo.pack`, `client.py:125`; `make_sentinel`, `core/queue.py:1125`),
and the dispatcher's four methods take and return JSON text or a `bool` (`core/dispatch.py:326`,
`:441`, `:457`, `:468`). Pickle carries only the envelope around them.

## Outcome / vision

Each end of a queue connection decodes a message into a small, closed set of plain values and refuses
anything else. A peer that can connect can ask for the queue operations the surface exposes, and
nothing more. The queues, the `Dispatcher`, the in-process server, every FSM and the shutdown order
stay as they are: only the step that turns a message into bytes and back changes. HyperShell already
owns both serializer registrations (`TLS_SERIALIZER`, registered at `core/queue.py:375`;
`PLAIN_SERIALIZER`, `:404`), and the manager serializes only through its connection's `send`/`recv`,
so the codec goes under every message without touching the manager.

### What this gives the 2.10.0 pair, and what it does not

`issues/untrusted-server-task-signing.md` and `issues/untrusted-server-client-sandbox.md` both depend
on this cycle.

- **Gives:** after this cycle, a server can no longer run code in a client's process (or a client in
  the server's) through the transport itself. What a compromised server can still make a client do is
  bounded by what the client does with the plain data it receives: chiefly, run the commands it is
  handed. That is the remaining gap the two seeds close: signing decides *which* commands a client
  accepts, and the sandbox bounds *what* an accepted command can reach. Without this cycle neither
  holds, because the transport is a way around both.
- **Does not give:** any check on where a task came from (a compromised server or any key holder can
  still author a command, and a client still runs it — that is the signing seed); any confinement of a
  running task (the sandbox seed); per-peer identity beyond the one shared key; authentication bound to
  the TLS channel; protection against a server that drops, delays or reorders work; transport-level
  denial-of-service controls. The docs must not claim that this cycle alone makes a client safe against
  a compromised server.

### The vocabulary the codec carries

| Message | Contents |
|---|---|
| Request | `(id, method name, args, kwds)`: `str`/`None`, `str`, a tuple of plain values, a `str`-keyed dict |
| Reply | `('#RETURN', value)`, `('#ERROR', exception)`, `('#TRACEBACK', text)`, `('#PROXY', (exposed, token))`, `('#UNSERIALIZABLE', text)` |
| `Token` | type id, address `(host, port)`, object id |
| Exceptions | `queue.Empty` and `queue.Full` (load-bearing: every timed `get`/`put`), `RemoteError`; any other exception crosses as `RemoteError` text |
| Values | `None`, `bool`, `int`, `float`, `str`, `bytes`, tuple/list, `str`-keyed dict |

Anything outside this table fails to encode on the sender and is refused on the receiver. That also
guards the future: an exposed method that starts returning a richer object fails a test, not a peer.

## Acceptance criteria (the contract)

"Every role" below means each holder of a queue connection: the server of `hs server` and `hs cluster`,
`hs client`, a relay (its upstream link and the server it runs for its site), and `hs submit` into a
live queue; with TLS and without.

- **R1** — On every role, the queue connection SHALL decode each message received from a peer only
  into the vocabulary above, and SHALL never unpickle bytes received from a peer.
- **R2** — IF a message received from a peer falls outside the vocabulary (an unknown tag, a type not
  in the table, a non-`str` dict key, malformed encoding), THEN the receiving end SHALL refuse it and
  close that connection without constructing any object outside the vocabulary, and a server SHALL keep
  serving its other connections. IF a value outside the vocabulary is handed to the encoder, THEN the
  sending end SHALL fail locally and send nothing outside the vocabulary (on a server, the caller
  receives the manager's `#UNSERIALIZABLE` reply as today).
- **R3** — Every message the manager protocol exchanges with HyperShell's server today SHALL round-trip
  through the codec: requests, the five reply kinds and `Token`; the connect handshake's `dummy`, the
  queue providers' `create` (including `_get_scheduled` with the work-request protocol argument),
  `incref`/`decref` (the decref hand-off included) and `accept_connection`; every call in
  `QUEUE_EXPOSED` and `DISPATCH_EXPOSED`; and a timed `get`/`put` that raises `queue.Empty`/
  `queue.Full` at the caller, as it does today. Every payload a role puts on a queue SHALL arrive
  byte-for-byte unchanged.
- **R4** — The authentication challenge, TLS, `TCP_NODELAY` and the 4-byte length-prefixed framing
  (byte-compatible with `multiprocessing.connection`) SHALL be unchanged.
- **R5** — WHEN one end receives a message in the pickle framing (a peer of 2.9.0a9 or earlier; a pickle
  of protocol 2 or later begins with byte `0x80`), it SHALL NOT unpickle it, SHALL close that connection,
  and SHALL name the incompatibility (a peer released before 2.9.0a10; run matching versions): a server
  in one WARNING naming the peer's address, and a client, relay upstream link or `hs submit` as a
  version mismatch that is fatal (no reconnect retries) and ends the program with that message, within
  the existing finite timeouts, never by a hang, a traceback or an unpickle. The exit status and what an
  older peer observes are settled in Clarifications Q2 and Q3.
- **R6** — No option, configuration key or environment variable SHALL re-enable decoding the pickle
  framing.
- **R7** — The pinned surfaces SHALL be unchanged: the nine queue methods (`QUEUE_EXPOSED`), the four
  dispatcher methods (`DISPATCH_EXPOSED`), the shutdown and sentinel ordering, the decref hand-off and
  the relay's upstream link. Their tests SHALL pass unchanged, except tests whose peer is a stock
  pickle-framed `BaseManager` — the `OldClient` refusals in `tests/test_dispatch_version.py` and the
  `Caller` peer in `tests/test_queue.py` (`TestPrivateInternals.test_create_passes_args_and_refusal_is_a_traceback`, `:213`) — which are restated so
  that each still checks what it checked against a codec peer, and the pre-codec case asserts R5; and
  `tests/test_cluster_launch.py`'s 2.9.0a4 contract premise, which is restated (the oldest client that
  can connect is now 2.9.0a10).
- **R8** — IF a received message nests deeper than a fixed depth, or holds a container with more
  elements than a fixed count, THEN the decoder SHALL refuse it as in R2. Both limits SHALL admit every
  message HyperShell's own roles send at the largest bundle size the configuration accepts.
- **R9** — `TestPrivateInternals` (`tests/test_queue.py:100`) SHALL pin the manager's use of
  `conn.send`/`conn.recv` and the reply kinds above on Python 3.11 to 3.14, so that a CPython change to
  the envelope fails a test.
- **R10** — `tests/bench/bench_queue.py` SHALL show no throughput regression against 2.9.0a9 at bundle
  size 1 and at the default bundle size, within the threshold in Clarifications Q4.
- **R11** — The docs SHALL describe the new envelope everywhere they describe the old one:
  `docs/security.rst` known limitation 2 ("Pickle-serialized RPC", rewritten, with
  `.. versionchanged:: 2.9.0`) and its "How It Works" paragraph, which says the cleartext wire is "those
  of a stock `BaseManager`"; and `AGENTS.md`'s wire-format bullet and every other line that calls the
  RPC pickle-framed or the stock manager's wire (the pinned-surface paragraph, the "server process
  hosts the manager" bullet, and the `core/queue.py` hotspot line). The change log SHALL state that a
  2.9.0a10 peer cannot connect to 2.9.0a9 or earlier, in either direction. Public text describes the
  property and the fix (a closed vocabulary; no unpickling of peer bytes), never a method of exploiting
  pickle.

## Non-goals (no-gos)

- **Task signing and client sandboxing.** The 2.10.0 pair (`issues/untrusted-server-task-signing.md`,
  `issues/untrusted-server-client-sandbox.md`) builds on this; neither is started here.
- **Peer identity and channel binding.** One shared key, no client certificates (`verify_mode=CERT_NONE`),
  and authentication not bound to the TLS channel all stay as they are.
- **Transport DoS controls.** No inbound frame-size cap, connection limit or thread limit; known
  limitation 4 in `docs/security.rst` stays. R8 bounds the *decoder's* structure, not the frame.
- **Any compatibility path.** No pickle fallback, negotiation or mixed-version mode; a pre-2.9.0a10 peer
  is refused, not served.
- **A new dependency or a new payload format.** No msgpack; payloads stay JSON, and the dispatcher's
  staged rows, the relay's JSON store and `Entity.from_json` are unchanged.
- **Changing the queue machinery.** The queues, the `Dispatcher`, `_InProcessServer`, every FSM, the
  shutdown order and the exposed surfaces stay as they are; only the connections' `send`/`recv` change.
- **The forked manager's local parent-to-child pipe** (`BaseManager.start` hands the child its address
  over a local pipe). It joins a process to its own child, not to a peer; R1 covers the network pair.
  Whether the forked path itself stays is Q1.

## Clarifications

Resolved with Geoffrey during the seed's shaping (2026-11-22):

- **Q:** A new transport, a restricted unpickler, or our own codec under `BaseManager`? — **A:** Our own
  codec under `BaseManager`. A restricted unpickler was the stopgap for a slower route, and this one fits
  in the 2.9 line. (resolved 2026-11-22)
- **Q:** Public or security lane? — **A:** Public. Pickle's limits are public knowledge and
  `docs/security.rst` already discloses the framing; the text describes the property and the fix, not a
  method of exploiting it. (resolved 2026-11-22)
- **Q:** Target and appetite? — **A:** 2.9.0, as 2.9.0a10, appetite big: landing before 2.9.0 final
  means the 2.9 line ships one framing and the compatibility break falls inside the alphas. (resolved
  2026-11-22)
- **Q:** What is the envelope's encoding? — **A:** JSON: no new dependency, one format, nothing to
  negotiate. Encoding does not limit production throughput; the database does (about 7 to 26 ms per task,
  `issues/server-per-task-statement-overhead.md`), and a JSON envelope adds about 2 to 7 µs per task in
  the seed's micro-benchmark (CPython 3.12, 2026-11-22), under 0.1% of that and the same order as the
  payload's own JSON encoding. msgpack was considered and declined: it would carry `bytes` natively but
  add a hard dependency (EPEL/RHEL availability unverified) to save microseconds. Payloads stay JSON.
  (resolved 2026-11-22)
- **Q:** How does a `bytes` value cross the JSON envelope? — **A:** Left to `/hs-plan`, by the same
  ruling: either a tagged UTF-8 string that refuses non-UTF-8 bytes (cheaper), or payload producers and
  consumers switch to `str` so no `bytes` remain in the vocabulary; base64 works but costs the most and
  grows the frame about 33%. The contract is R3's byte-for-byte round-trip, not the mechanism, so this
  is a plan-time choice and does not block planning. (resolved 2026-11-22; confirmed as plan-time at
  shaping, 2026-11-23)

Resolved at the GOAL gate (2026-11-23):

- **Q:** The forked-manager path. `SecureManager.start` (`core/queue.py:1025`) still starts a manager
  in a child process, with `_child_bootstrap` (`:407`) and forked-accept-loop branches in the TLS
  handshake and decref logic (comments at `:336`, `:858`, `:909`). No production caller
  (`QueueServer.start`, `:1210`, overrides it and serves in-process); one test,
  `test_secure_manager_subprocess_roundtrip` (`tests/test_queue_tls.py:542`), whose registered callable
  returns a `dict` inside the vocabulary; on the Python API, `SecureManager` is in `__all__` and named
  in `docs/security.rst` "How It Works". Keep it, or retire it here? — **A:** Keep it. Its network
  connections come from the shared serializer pair, so it gets the codec with no extra code, and its
  test stays as is. Retiring the forked manager is a follow-up seed, outside this cycle. Ruled by the
  maintainer. (resolved 2026-11-23)
- **Q:** What does a 2.9.0a9-or-earlier peer observe? R5 binds the new side; closing alone is not
  enough, because a 2.9.0a9 client treats `EOFError` as a transport outage (`core/reconnect.py`,
  `TRANSPORT_ERRORS`) and redials with backoff indefinitely, which looks like a hang. — **A:** Option
  (b): a new server answers a pickle-framed request with one fixed error reply, built once from
  constants and encode-only (nothing from the peer is decoded, so R1 and R6 hold), naming the version
  mismatch, and then closes. `/hs-plan` verifies what a 2.9.0a9 client, relay and `hs submit` do with
  it (connect's `dummy` call raises `RemoteError` outside `QueueClient.connect`'s version check). If
  the plan shows a 2.9.0a9 client does not exit on that reply, fall back to (c): document the old
  side's behaviour in the change-log note. Never (a), closing alone. A new client meeting an old
  server needs no such step: the old server's reply is itself pickle-framed and R5 refuses it. Ruled
  by the maintainer. (resolved 2026-11-23)
- **Q:** Exit status of the R5 refusal. — **A:** Reuse `QueueVersionError` (`core/queue.py:426`): fatal
  in reconnect (`core/reconnect.py`, `FATAL_ERRORS`), exiting with `exit_status.runtime_error` through
  the shared `RuntimeError` mapping. No new status: it is the same kind of failure (run matching
  versions), the message already distinguishes it, and a new status would be a public contract for
  launchers and scripts to learn. Ruled by the foreman under the maintainer's mandate. (resolved
  2026-11-23)
- **Q:** R10's threshold. — **A:** At bundle size 1 and at the default bundle size, the median of 5 runs
  is within 5% of 2.9.0a9 on the same host, or within 2.9.0a9's own run-to-run spread if that is
  larger. Ruled by the foreman under the maintainer's mandate. (resolved 2026-11-23)

## Notes

- **The work-request protocol refusal (`ClientTooOld`, `_versioned_provider`, `core/queue.py:1180`)** was
  reached by releases before the protocol, all of which pickle. After this cycle the framing refuses
  them first (R5), so a real release never reaches that path; whether it stays as a guard for a codec
  peer that omits the protocol argument is the plan's call. `PROTOCOL` (`core/dispatch.py:51`) need not
  change for this cycle.
- **Mixed versions.** A relay is both a client (upstream) and a server (its site), so each hop meets R5
  on its own.
- **Python versions.** The envelope's shape has been stable for years; the in-process server already
  leans on private internals across 3.11 to 3.14, and R9 extends the same pins. The CPython references
  in the seed (`managers.py` `dispatch`, `Server._handle_request`, `Server.serve_client`,
  `BaseProxy._callmethod`; `connection.py`'s XML-RPC serializer) were checked against 3.14.3 at shaping.
- **Size.** Eleven criteria is at the circuit-breaker's edge, but they describe one deliverable (the
  codec under one seam); R9 to R11 are its pins, benchmark and docs. No pilot split proposed.

## Related materials

- Seed: `issues/queue-wire-codec.md` (promoted; retired by `/hs-roadmap` when the branch lands)
- Dependents: `issues/untrusted-server-task-signing.md`, `issues/untrusted-server-client-sandbox.md`
  (2.10.0)
- `ROADMAP.md`, "The queue's RPC envelope is pickled in both directions"
- `docs/security.rst`, "How It Works" and known limitation 2 ("Pickle-serialized RPC")
- `AGENTS.md`, *Queue transport & security* (wire-format bullet)
- Source: `src/hypershell/core/queue.py` (`SecureConnection` `:115`, `_PlainListener` `:378`,
  `_plain_client` `:389`, serializer registrations `:375`/`:404`, `SecureManager` `:1002`,
  `QueueServer` `:1199`, `QueueClient.connect` `:1273`), `src/hypershell/core/reconnect.py`,
  `src/hypershell/core/dispatch.py`
- Tests: `tests/test_queue.py` (`TestPrivateInternals`), `tests/test_dispatch_version.py`,
  `tests/test_queue_tls.py`, `tests/test_cluster_launch.py`, `tests/bench/bench_queue.py`
- `issues/server-per-task-statement-overhead.md` (the per-task database cost behind the JSON ruling)
- Python docs, `pickle` module security warning; CPython `multiprocessing.managers`,
  `multiprocessing.connection`
