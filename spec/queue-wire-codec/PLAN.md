# PLAN — The queue's RPC envelope carries data, not pickled objects

> **Status:** Approved at the plan gate · **Last updated:** 2026-11-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (`appetite: big`). Every design element traces to a GOAL R-ID.

## 1. Summary

A new pure module, `core/codec.py`, turns the manager's envelope into JSON and back over a closed
vocabulary, with `bytes` carried as a tagged string of one code point per byte (Latin-1). HyperShell's
own connection classes (`SecureConnection`, and a new cleartext `Connection` subclass that
`_PlainListener` and `_plain_client` return) override only `send` and `recv` with it, so the manager,
the queues, the dispatcher, the in-process server, the framing and the HMAC challenge are untouched
(R1–R4, R7). A message outside the vocabulary is refused and its connection closed. A pickle frame
from an older peer (first byte `0x80`) is never decoded: a server answers it with one fixed reply,
pickled once at import from constants, and logs one WARNING; a client raises `QueueVersionError`,
which is already fatal and exits `runtime_error` (R5, Q2, Q3). Six phases, tests first; the codec
lands as a standalone module before it goes under `core/queue.py`.

## 2. Design

### 2.1 `core/codec.py` (new; R1, R2, R3, R8)

A pure module with no queue imports, so it is testable alone and outside the high-blast-radius file.

- `encode(obj) -> bytes` and `decode(buf) -> obj`, plus `CodecError(ValueError)`.
- **Mapping** ([`research/03-codec-design.md`](research/03-codec-design.md)): `None`, `bool`, `int`,
  finite `float` and `str` are JSON's own; a `tuple` is a JSON array; every other type is a one-key
  JSON object: `{"l": [...]}` a `list`, `{"d": {...}}` a `str`-keyed `dict`, `{"b": "..."}` `bytes`,
  `{"k": [typeid, [host, port], id]}` a `Token`, `{"e": [name, [args]]}` an exception. Types are
  matched exactly (`type(v) is ...`), so `bool` stays apart from `int` (`_versioned_provider` compares
  `args != (PROTOCOL,)`, and `(True,) == (1,)`) and a subclass is not taken for its base.
- **Exceptions:** `queue.Empty`, `queue.Full` and `RemoteError` cross as themselves, matched by class
  identity (`queue.Empty` is `_queue.Empty`); their arguments as `str`. Any other exception handed to
  the encoder (an `#ERROR` reply from a dispatcher method or `task_done`) becomes
  `RemoteError('<Type>: <text>')`, as the GOAL's table says. `#TRACEBACK` and `#UNSERIALIZABLE` text
  crosses verbatim, so `core/reconnect.py`'s stale-proxy match (`KeyError: '<hex>'`) still reads it.
- **Encode** walks the value once into a JSON-ready tree and dumps it with `allow_nan=False`, compact
  separators and ASCII escaping; any value outside the vocabulary (a `set`, an object, a non-`str`
  key, a non-finite float, a `bytes` subclass) raises `CodecError` **before a byte is written**. On a
  server, `serve_client` then sends `#UNSERIALIZABLE`, as it does for a pickling error today (R2's
  sender half).
- **Decode** refuses an empty frame and a frame whose first byte is `0x80` (`PickleFrame`, a
  `CodecError` subclass, so the connection can tell R5 from R2), decodes the rest as strict UTF-8,
  `json.loads` it with `parse_constant` refusing `NaN`/`Infinity`, and walks the result once, mapping
  each node back and refusing any other shape: an unknown tag, a tag object with more than one key, a
  tag's value of the wrong type, a `"b"` string holding a code point above U+00FF, a malformed
  `Token` or exception, an exception name outside the three. `ValueError` (bad JSON, trailing data,
  an integer past CPython's digit limit, bad UTF-8, bad Latin-1) and `RecursionError` become
  `CodecError`. `json.loads` builds only `dict`/`list`/`str`/`int`/`float`/`bool`/`None`, so no
  object outside the vocabulary is constructed on the way (R1, R2).
- **Limits (R8):** `MAX_DEPTH = 8` containers (the outer tuple counts as 1) and `MAX_ITEMS = 64`
  elements in any one tuple, list or dict, enforced by the walk on both encode and decode. Every
  bundle, reply, heartbeat and topology crosses as **one** `bytes` or `str` leaf, so the limits do not
  depend on bundle size, and the configuration's bundle keys (`server.bundlemax`, `*.bundlesize`;
  none has an upper bound) cannot reach them. The largest messages HyperShell's roles send are depth 3
  with 9 elements (the `create` reply with `QUEUE_EXPOSED`); the unreachable `Token` paths and the
  forked manager's `#GETVALUE` test reach depth 4 with 11 ([`research/01-envelope-inventory.md`](research/01-envelope-inventory.md)
  §4). The limits leave twice the depth and five times the width. A test pins that each real message
  shape passes and that depth 9 and 65 elements are refused. `json.loads`'s own recursion bound
  differs across Pythons, so the codec never relies on it for R8; it only turns its `RecursionError`
  into a refusal.
- **`bytes` (the GOAL's plan-time question):** a tagged string, one code point per byte. It is total
  (any `bytes` round-trips, no refusal case, no producer has to promise UTF-8), and it costs what UTF-8
  would for HyperShell's payloads, which are all `json.dumps` output and so pure ASCII. Switching
  producers to `str` was rejected: it touches every role's payload path and its pinned tests for no
  measurable gain; base64 was rejected by the GOAL's own numbers.
- **`OLD_PEER_REPLY`:** the fixed reply to a pickle-framed peer (2.3), built here once at import with
  `pickle.dumps(('#ERROR', RuntimeError(OLD_PEER_TEXT)), protocol=4)`, where `OLD_PEER_TEXT` is one
  constant line: `This queue server runs HyperShell 2.9.0a10 or later, whose queue framing a release
  before 2.9.0a10 cannot read; run matching server and client versions` (wording final at build). The
  protocol is pinned so the constant is the same bytes on every Python, and a test pins those bytes.
  It is the module's only use of `pickle`, and only `dumps`. `#ERROR` with a builtin `RuntimeError`
  is chosen over `#TRACEBACK` because every older release then prints one clean CRITICAL line and
  exits 5, while a `RemoteError` makes it print the text inside dashed rules and write a traceback
  file; a `QueueVersionError` would not unpickle on releases before 2.9.0a4
  ([`research/02-old-peer.md`](research/02-old-peer.md)).

### 2.2 The connections (R1, R2, R4, R6) — `core/queue.py`

- **One mixin, `_EnvelopeCodec`,** defines `send` and `recv` and nothing else. `send(obj)` checks the
  connection is open and writable, encodes (a `CodecError` propagates with the connection intact, so
  `serve_client` can answer `#UNSERIALIZABLE`), then `_send_bytes`. `recv()` checks it is open and
  readable, `_recv_bytes()`, then decodes. `_send_bytes`/`_recv_bytes` (the 4-byte length-prefixed
  framing) and `send_bytes`/`recv_bytes` (the HMAC challenge) are inherited unchanged (R4).
- **TLS:** `SecureConnection(_EnvelopeCodec, _ConnectionBase)`.
- **Cleartext:** a new `_PlainConnection(_EnvelopeCodec, Connection)`. `_PlainListener.accept` takes
  the accepted socket from the stock listener's socket (`_listener._socket`, already pinned in
  `_INTERNALS`), sets it blocking and `TCP_NODELAY`, and returns `_PlainConnection(sock.detach())`, as
  `SocketListener.accept` does with the stock class. `_plain_client` dials AF_INET itself, as
  `secure_client` already does, and returns a `_PlainConnection`, then runs the challenge. The stock
  `Connection` class is never patched: the forked manager's parent-child `Pipe` and process spawning
  use it (the GOAL's non-goal). The bytes on the wire stay byte-compatible with
  `multiprocessing.connection`. Whether `_no_delay_connection` stays (it is now unused by these two
  paths) is a build-time tidy; its tests stay or go with it.
- **Role.** Each connection knows its side: accepted (server side) or dialled (client side, holding
  the address it dialled, for messages). `SecureListener.accept`, `handle_pending` and
  `_PlainListener.accept` build server-side connections; `secure_client` and `_plain_client`
  client-side ones.
- **Refusal (R2).** When `recv` cannot decode a frame, the connection closes itself first (so neither
  of the manager's send attempts can reach the peer, and a client's cached per-thread connection in
  `BaseProxy._tls` is not reused), records why on itself, and raises:
  - **server side:** `_Refused`, an `EOFError` subclass. In `serve_client` that ends the handler
    quietly (`sys.exit(0)`, absorbed by `handle_request`); in `_handle_request` (every connection's
    first message) the manager's one `#TRACEBACK` attempt fails on the closed connection. Either way the
    server keeps serving its other connections. `_InProcessServer.handle_request` then logs one WARNING
    naming the peer and the reason (outside any lock, as the too-old refusal is logged today).
  - **client side:** `QueueVersionError` naming the server's address and saying it sent a message
    outside this version's queue vocabulary (run matching versions). It is fatal in reconnect, so a
    client, a relay's upstream link or `hs submit` does not redial a server it cannot understand, and
    the program ends with one CRITICAL line and `exit_status.runtime_error` (5) (§5, risk 2, ruled at
    the plan gate).
- **R6.** Nothing reads an option, key or variable to pick the decoder: `recv` has one path. A test
  pins that `recv` on both connection classes is the codec's (not `_ConnectionBase.recv`), that their
  code names hold no `loads`/`_ForkingPickler`, and that `pickle.loads`/`ForkingPickler.loads` are never
  reached during a full queue session with a pickle-framed peer. `SecureManager.__init__` refuses a
  `serializer` other than its two (`TLS_SERIALIZER`, `PLAIN_SERIALIZER`) with `ValueError`; today its
  `**kwargs` would let a Python caller select the stock pickle pair. No caller in `src/` or `tests/`
  passes one.

### 2.3 Older peers (R5; Q2, Q3)

**New server, old client.** The old client's first request on every connection is pickle-framed
(`BaseManager.connect`'s `dummy`). The server-side `recv` sees `0x80`, does not decode the frame,
sends `OLD_PEER_REPLY` with `send_bytes`, closes, and raises `_Refused` marked as an old peer; the
handler logs one WARNING: `Refused a queue connection from <host:port>: the peer speaks the queue
framing of a release before 2.9.0a10; run matching server and client versions`. Nothing from the peer
is decoded to build or choose the reply, and the reply is the same bytes for every peer (R1, R6).

What an older peer does with the reply was probed against a real 2.9.0a9 install (a stand-in server
that runs the stock challenge, reads the request frame without decoding it, sends the reply and
closes; [`research/02-old-peer.md`](research/02-old-peer.md)). Every role's first request is
`dummy`, from `BaseManager.connect`, so the reply surfaces there, before `QueueClient.connect`'s
version check. `convert_to_error('#ERROR', e)` returns the `RuntimeError`, which every role maps to
`exit_status.runtime_error`:

| 2.9.0a9 role | Outcome with the fixed reply |
|---|---|
| `hs client` | exit 5 in 0.37 s, one connection, `CRITICAL [hypershell.client] RuntimeError: <text>` |
| `hs submit` into a live queue | exit 5 in 0.37 s, one connection, the same one line |
| `hs relay` (upstream link) | the link fails at once (the error is neither fatal nor transport there, so `UpstreamLink.attempt` fails the link), exit 5 in 0.7 s, no redial |

So the maintainer's option (b) holds and the fallback (c) is not needed. The same mapping is in every
release from 2.8.x on (read from the tags). For contrast, closing alone (option a): `hs client` and
`hs submit` still exit 5 (`EOFError: server disconnected`), but the relay redials forever (22
connections in 120 s). That corrects the GOAL's premise in one detail: in 2.9.0a9 only the relay's
upstream link redials on `EOFError`; `hs client` and `hs submit` have no reconnect layer. The server
reads the whole frame (`recv_bytes` does) before it replies, and closes normally, so the reply is not
lost to a reset.

**New client, old server.** The new client's `dummy` is JSON, whose first byte `[` is not a pickle
opcode, so a 2.9.0a9-or-earlier server fails to unpickle it and answers with a pickled `#TRACEBACK`.
The client-side `recv` sees `0x80`, closes, and raises `QueueVersionError`: `Server at <host:port>
speaks the queue framing of a release before 2.9.0a10; run matching server and client versions`. It
is raised inside `QueueClient.connect` (from `BaseManager.connect`'s `dummy`), before the work-request
protocol check; `core/reconnect.py` has it in `FATAL_ERRORS`, so a client, a relay's upstream link and
`hs submit` stop at once, and the shared `RuntimeError` mapping (`core/exceptions.py:137`) logs it as
one CRITICAL line and exits `exit_status.runtime_error` (Q3: no new status). No new code is needed
past the raise.

**The work-request protocol refusal** (`ClientTooOld`, `_versioned_provider`) stays as a guard for a
codec peer that omits the protocol argument (the GOAL's note): it is two lines, has its own test, and
removing it would drop that guard for no gain. `PROTOCOL` does not change.

### 2.4 Pins (R9) — `core/queue.py` `_INTERNALS`, `tests/test_queue.py` `TestPrivateInternals`

The pins in [`research/01-envelope-inventory.md`](research/01-envelope-inventory.md) §5, verified
true on 3.11.18, 3.12.13, 3.13.16 and 3.14.6, join `_INTERNALS` (so a queue server refuses to start
on a Python that reshapes them, as it already does for the serve-loop privates) and are exercised by
`test_shared_checks_pass` on each CI Python:

- `dispatch`, `Server._handle_request`, `Server.serve_client` and `BaseProxy._callmethod` name `send`
  and `recv` and none of `send_bytes`/`recv_bytes`/`dumps`/`loads`/`_ForkingPickler`; every other
  manager call (`connect`, `_create`, `_connect`, `_incref`, `_decref`, `AutoProxy`, `register`'s
  method) goes through `dispatch`.
- The reply kinds, read from string constants flattened through nested tuples: `serve_client` holds
  all five, `_handle_request` `#RETURN`/`#TRACEBACK`, `convert_to_error` `#ERROR`/`#TRACEBACK`/
  `#UNSERIALIZABLE`, `_callmethod` `#RETURN`/`#PROXY`.
- `deliver_challenge`/`answer_challenge` use `send_bytes`/`recv_bytes`, not `send`/`recv` (R4).
- `_ConnectionBase.send`/`recv` go through `_send_bytes`/`_recv_bytes` (what the override replaces).
- `Token.__slots__ == ('typeid', 'address', 'id')`.

Plus two live checks in `TestPrivateInternals`: a real queue session through the codec carries every
reply kind HyperShell meets, and a non-`EOFError` raised by `recv` inside `serve_client` keeps the loop
running (the fact that makes the refusal close the connection itself).

### 2.5 Restated tests (R7)

[`research/04-tests-docs-bench.md`](research/04-tests-docs-bench.md) lists them. The three the GOAL
names, plus two outside the pinned surfaces that a sweep found:

- `test_dispatch_version.py`: `OldClient` becomes a codec peer (`SecureManager` subclass) that still
  omits the protocol argument, so `test_old_client_is_refused_while_new_client_works` checks the same
  `ClientTooOld` refusal; a new test meets the server with a stock pickle-framed `BaseManager` and
  asserts R5 (the WARNING naming the peer, the old client's `RuntimeError` carrying the fixed text, the
  server still serving a new client).
- `test_queue.py::TestPrivateInternals::test_create_passes_args_and_refusal_is_a_traceback`: `Caller`
  subclasses `SecureManager`; its two pins are unchanged.
- `test_queue.py::test_plain_listener_under_default_timeout` and
  `test_plain_client_under_default_timeout`: the stock end becomes HyperShell's other end
  (`_plain_client`, `_PlainListener`); what they check (blocking mode under a process default timeout,
  the handshake and a first exchange) is unchanged. Not pinned surfaces; listed so review sees them.
- `test_cluster_launch.py`: `CONTRACT_CLIENT_OPTIONS` stays 2.9.0a4's set as the option floor of a
  launch (every option in it that a launch emits is one this tree's client accepts; `-k`/`--auth` are in
  the set but never emitted); `CONTRACT_TAG`'s docstring and the module comment say that the oldest
  client that can connect is now 2.9.0a10. `test_old_client_runs_under_new_launcher` is restated as a
  refusal: the tag's client, under the new launcher and over ssh, runs no task and exits non-zero at
  its first request with the fixed reply's text; the server logs the R5 WARNING; and the cluster ends
  through the launch watch (`cluster/watch.py`: every client exited, at least one non-zero, work
  remains → exit 5, `ClusterLaunchError`) within the test's bound instead of hanging. The key stays off
  every command line, as the test checks today. A 2.9.0a4 client maps a `RuntimeError` the same way
  2.9.0a9 does (brief 02, older releases).

Every other test passes unchanged, including the forked-manager round trip
(`test_queue_tls.py:520-560`, Q1: it keeps the codec through the shared pair).

### 2.6 Docs and the change log (R11)

- `docs/security.rst` known limitation 2 becomes "Closed RPC vocabulary" (title at build): the
  envelope is JSON over a fixed set of plain values; neither end unpickles a peer's bytes; what an
  authenticated peer can still do (ask for the exposed queue and dispatcher operations, and hand a
  client commands to run, which the 2.10.0 signing and sandbox work addresses) and what the change does
  not give (no per-peer identity, no channel binding, no transport DoS controls). Marked
  `.. versionchanged:: 2.9.0` with the incompatibility. "How It Works" (`:135-151`): the framing and
  handshake are the stock manager's; the envelope is HyperShell's own codec on both pairs. The docs do
  not say a client is safe against a compromised server (GOAL, "Does not give").
- `docs/alternatives.rst:826-828`: the pickle-framing aside is rewritten to match (an "everywhere"
  line R11 did not name).
- `AGENTS.md`: the wire-format bullet, the pinned-surface paragraph, "The server process hosts the
  manager" bullet, the `core/queue.py` hotspot line, and the 2.9.0a4 contract sentence; and
  `.agents/factory/invariants.md` §9 and §11 in lockstep.
- **Change log:** the PR's squash commit body (`AGENTS.md` § Docs) states that a 2.9.0a10 peer cannot
  connect to 2.9.0a9 or earlier, in either direction, and what each side reports: a new client or
  relay ends with one CRITICAL line naming the older framing; a new server logs one WARNING naming the
  peer, and the older client, relay or `hs submit` ends at once with the server's one-line message
  (exit status 5 on both sides). It also names `SecureManager`'s narrowed `serializer` argument.
  `hs-publish` drafts it from this section; nothing in the tree is a change log.
- Public text describes the property and the fix (a closed vocabulary; no unpickling of peer bytes),
  never a method of exploiting pickle; examples use top-level commands and the key in
  `HYPERSHELL_SERVER_AUTH`.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `core/codec.py` decode (closed walk over `json.loads`'s plain types); `_EnvelopeCodec.recv` on `SecureConnection` and `_PlainConnection`; `0x80` never decoded (2.1, 2.2) |
| R2 | decode refusals → connection closes itself, `_Refused` (server, handler ends, others served, WARNING) or `QueueVersionError` (client); encode refusal before any write → `#UNSERIALIZABLE` (2.1, 2.2) |
| R3 | the mapping covers requests, the five reply kinds, `Token`, the three exceptions, `bytes` byte for byte (Latin-1 tag); tests drive `dummy`, `create` incl. `_get_scheduled(PROTOCOL)`, `incref`/`decref` incl. the hand-off, `accept_connection`, all of `QUEUE_EXPOSED`/`DISPATCH_EXPOSED`, timed `get`/`put` raising `Empty`/`Full`, TLS and cleartext (2.1, 2.5) |
| R4 | only `send`/`recv` overridden; `_send_bytes`/`_recv_bytes`, `send_bytes`/`recv_bytes`, TLS and `TCP_NODELAY` unchanged; R9 pins the challenge's use of the byte methods (2.2, 2.4) |
| R5 | `0x80` → server: `OLD_PEER_REPLY`, close, one WARNING naming the peer; client: `QueueVersionError`, fatal, `runtime_error` (2.3) |
| R6 | one decode path; static and live tests; `SecureManager` refuses other serializers (2.2) |
| R7 | no change to the queues, dispatcher, in-process server, FSMs or shutdown order; the restated tests (2.5) |
| R8 | `MAX_DEPTH = 8`, `MAX_ITEMS = 64`, sized from the real messages (2.1) |
| R9 | new `_INTERNALS` entries and two live checks in `TestPrivateInternals` (2.4) |
| R10 | `tests/bench/bench_queue.py gate --base 2.9.0a9 --feature HEAD`, read against Q4 (§6); the out-of-band leaf is the held-back lever (§5) |
| R11 | `docs/security.rst`, `docs/alternatives.rst`, `AGENTS.md`, `invariants.md`, the squash-body change-log line (2.6) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design was drafted.

- **§9 Queue transport & security.** TLS stays on by default; auth is always required, the HMAC
  challenge is unchanged (`send_bytes`/`recv_bytes`); no mTLS and the post-handshake fingerprint
  check stay (`secure_client` is unchanged up to the `SecureConnection` it builds). The bullet
  "Multiprocessing RPC framing is pickle (an RCE surface); payloads are JSON" is the fact this cycle
  changes: it is rewritten in `AGENTS.md` and `invariants.md` in the same commit (R11), keeping its
  point that the envelope and the payload are separate layers.
- **§6 Shutdown / sentinel ordering, remote-queue payloads.** Untouched. Payloads still go through
  `serialize_tasks`/`deserialize_tasks`/`heartbeat.pack`, never a live object or a literal `None` on a
  remote queue; the codec carries them as opaque `bytes` leaves. The sentinel (`make_sentinel()`) is
  `bytes` and crosses byte for byte.
- **§5 Concurrency.** Every blocking queue op keeps its finite timeout; a refusal closes one
  connection and never blocks. A refused handler thread ends as a disconnected one does today. No FSM
  changes.
- **§11 Cluster orchestration.** The launch builder is untouched; the 2.9.0a4 option-floor sentence
  is restated (the floor stays; the oldest connectable client is 2.9.0a10), sanctioned by R7.
- **§12 Conventions.** Version single-sourced (the reply text names the boundary release 2.9.0a10, a
  historical fact, not the running version); no new dependency (stdlib `json`); Python 3.11–3.14; tests
  tagged `@mark.unit`/`@mark.integration`; return codes from `exit_status`; `.. versionchanged:: 2.9.0`
  on the prose page; docs build with `-W`. No CLI change, so no help snippets or completions.
- **High-blast-radius file `core/queue.py`.** Changed only at the two connection pairs, the refusal
  log in `_InProcessServer.handle_request`, `_INTERNALS`, and `SecureManager.__init__`; the codec
  itself lives in a new file. Any CONFIRMED review finding here gets the mandatory human gate.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `invariants.md` §9 and §11 text rewritten | The facts they state (pickle framing; 2.9.0a4 as the oldest client) stop being true, and R7/R11 require it | Leaving them would make the invariant gate assert a false fact; the file is kept in lockstep with `AGENTS.md` by its own rule |
| A new module `core/codec.py` | Keeps the vocabulary, limits and refusals pure and testable outside the high-blast-radius file | Writing the codec inside `core/queue.py` grows the riskiest file and couples codec tests to sockets |
| `_PlainConnection` replaces the stock `Connection` on the cleartext pair | The stock class pickles in `send`/`recv` and is shared with the forked manager's pipe, so it cannot be patched | Patching `Connection.send`/`recv` would change the local pipe (a non-goal) and every other `multiprocessing` user in the process |

## 4. Rabbit holes (resolved)

- **Does the manager reach its connections only through `send`/`recv`, the same on 3.11–3.14?** Yes:
  `dispatch`, `_handle_request`, `serve_client`, `_callmethod`, and everything else through
  `dispatch`; the challenge uses the byte methods; identical on all four
  ([`research/01-envelope-inventory.md`](research/01-envelope-inventory.md) §1, §5).
- **Which values cross?** No `list`, `#PROXY` or `Token` in HyperShell traffic; `dict` only as
  `kwds`; no float in production envelopes; every payload is one ASCII `bytes`/`str` leaf; `Empty`,
  `Full` and other errors as `#ERROR` (01 §2–3). The vocabulary keeps the GOAL's full table anyway, so
  the stock paths (`get_methods`, `#PROXY`, `#GETVALUE`) stay covered.
- **How does a refused message close its connection?** `serve_client` keeps looping after a
  non-`EOFError` from `recv`, and `_callmethod` keeps its cached connection, so the codec closes the
  connection itself before raising, and raises an `EOFError` subclass on the server side (01 §1).
- **How does `bytes` cross?** Tagged Latin-1 string (2.1; [`research/03-codec-design.md`](research/03-codec-design.md)).
- **R8 sizes.** Structure is independent of bundle size; 8 and 64 against a worst case of 4 and 11
  (01 §4).
- **Does a 2.9.0a9 client exit on the fixed reply?** Yes: `hs client`, `hs submit` and `hs relay`
  each exit 5 within a second on `('#ERROR', RuntimeError(text))`, with one CRITICAL line and no
  redial; the fallback (c) is not needed ([`research/02-old-peer.md`](research/02-old-peer.md)).
- **R10.** The envelope costs ≈1–4 µs per small message and ≈2 µs per task in a large reply (03),
  against 7–26 ms per task of database work in the gate's workload.

## 5. Risks & open questions

1. **R10 at bundle size 1.** Each task makes a handful of small messages, ≈10–20 µs added per task by
   the micro-benchmark. The gate's workload is database-bound, so this should sit inside 5%, but it
   has not been measured end to end. If the gate fails, the held-back lever is to carry large
   `str`/`bytes` leaves out of band in the same frame (03, last section), which removes the JSON
   re-escaping; a second lever is a fast path for the few hot message shapes. Either would be a phase
   amendment, not a GOAL change.
2. **A client refuses an out-of-vocabulary message as fatal.** The GOAL fixes the server's behaviour
   (close, keep serving) but not what a client does next. This plan raises `QueueVersionError` there
   too (fatal, `runtime_error`), on the reasoning that a server sending messages this version cannot
   read is a mismatched or misbehaving server, which redialling cannot cure. The alternative is to
   treat it as a transport error and redial with backoff. **Ruled by the maintainer at the plan gate
   (2026-11-23): fatal, as planned** (see *Plan gate* below).
3. **The fixed reply is pickled by the new side.** The server only ever *encodes* it, once, from
   constants, and never decodes peer bytes, so R1 and R6 hold; but the module does import `pickle`.
   A test pins that `codec.py` calls only `pickle.dumps`, and the reply's exact bytes. An older peer
   decodes it with its own `pickle`, which is that peer's existing behaviour, not a new surface.
4. **The 2.9.0a4 launch test** runs the tag's own client tree. Brief 02 read from the tags that 2.9.0a4
   maps the reply's `RuntimeError` to exit 5 as 2.9.0a9 does, but the restated test is its first live
   run; if the tree stops importing under a future interpreter, the test skips, as it does today.
5. **Python API.** `SecureManager` refusing a third serializer is a small public-API narrowing (it is
   in `__all__`, with no `docs/api` page). Named in the change-log note.

## 6. Verification strategy

- **Unit (`tests/test_codec.py`, new):** every vocabulary type round-trips, `bytes` byte for byte over
  all 256 values; each refusal (unknown tag, two-key tag object, non-`str` key, `"b"` above U+00FF, bad
  `Token`/exception shapes, unknown exception name, `NaN`/`Infinity`, bad UTF-8, bad JSON, trailing
  data, huge integer, depth 9, 65 elements, deep nesting past the C parser's bound, empty frame, `0x80`
  frame) raises `CodecError` and builds nothing else; encode refusals (`set`, object, non-`str` key,
  non-finite float) write nothing; every real message shape from 01 §2 passes the limits;
  `OLD_PEER_REPLY` is a fixed protocol-2 pickle of the expected tuple (the test decodes our own
  constant, nothing from a peer).
- **Wire (`tests/test_queue_codec.py`, new; TLS and cleartext):** a full `QueueServer`/`QueueClient`
  session drives every message of R3 (each of `QUEUE_EXPOSED` and `DISPATCH_EXPOSED`, `dummy`, `create`
  with the protocol argument, `incref`/`decref`, the decref hand-off, `accept_connection`, a timed
  `get` raising `Empty` and `put` raising `Full` at the caller) with `pickle.loads` and
  `ForkingPickler.loads` patched to fail the test if reached; payload bytes arrive identical. Hostile
  frames on a raw authenticated connection (benign content, one per refusal kind) close that
  connection with one WARNING while a concurrent client is served (R2). R5 both ways with stock
  pickle-framed peers (the server's WARNING and the old client's `RuntimeError`; the new client's
  `QueueVersionError` within the bound), and `UpstreamLink` failing at once with no redial.
- **Pins (R9):** `TestPrivateInternals` with the new `_INTERNALS` entries, on every CI Python
  (3.11–3.14; locally on the four installed interpreters).
- **CLI drives (a throwaway site, `.agents/factory/bin/temp_site.sh`):** `hs cluster` with local
  clients, TLS on and off, completes a run; `hs server` + `hs client` + `hs submit` into a live queue
  (`--no-db` server) complete. Against a 2.9.0a9 install (`uv pip install hypershell==2.9.0a9` in a
  throwaway venv): an a9 `hs client` against a new `hs server`, and a new `hs client`, `hs submit` and
  `hs relay` upstream against an a9 `hs server`: each refused as R5 says, in bounded time.
- **Real releases (R5, R3, R10):** `tests/bench/bench_queue.py skew --new HEAD --old 2.9.0a9` with its
  pass strings restated to the R5 messages; `parity --base 2.9.0a9 --feature HEAD` (identical task
  tables); `gate --base 2.9.0a9 --feature HEAD --repeats 5`, read against Q4: for the `b=1` rows
  (bundle size 1, which is also the configured default) and the `client-default` rows (the client
  takes the server's size), the feature's median is at least 2.9.0a9's median less the larger of 5% of
  it and 2.9.0a9's own min-max spread. Results recorded under `spec/queue-wire-codec/drive/`.
- **Regression:** the queue, dispatch, relay, TLS, GC and launch suites, serially (`pytest -n auto` is
  broken on the build host), each bounded.

## Plan gate (2026-11-23)

**Approved** by the foreman, gate judge under the maintainer's mandate.

- **Maintainer's ruling on the client's refusal** (§5, risk 2, "A client refuses an out-of-vocabulary
  message as fatal"): a client, a relay's upstream link or `hs submit` that receives a message outside
  the vocabulary from its server raises `QueueVersionError`. That is fatal: the program exits with
  `exit_status.runtime_error` (5), and nothing redials. §2.2 and §5 say so; TECH P1 tests it and P3
  builds it.
- **Accepted as planned:**
  - `bytes` cross as Latin-1 tagged strings (§2.1);
  - the R8 limits, depth 8 and 64 elements (§2.1);
  - restating `test_plain_listener_under_default_timeout` and `test_plain_client_under_default_timeout`
    alongside R7's three (§2.5);
  - the 2.9.0a4 launch test becomes a refusal test (§2.5);
  - `SecureManager` refuses other serializers, noted for the change log (§2.2, §2.6, §5 risk 5);
  - the `docs/alternatives.rst` rewrite (§2.6).
- **Q4 is not loosened.** If R10 misses, the build uses the plan's lever (§5, risk 1: large
  `str`/`bytes` values carried outside the JSON part of the frame) and reports before changing
  anything else.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
