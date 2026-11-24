# 03 — The codec: encoding, `bytes`, hook points, cost

Brief for `hs-plan` (2026-11-23, CPython 3.14.6, `/tmp` venv of this worktree). Settles the GOAL's
plan-time question (how `bytes` crosses) and the shape of the codec. Prototype measured, not kept.

## Where the codec goes

`multiprocessing.connection._ConnectionBase.send` is `self._send_bytes(_ForkingPickler.dumps(obj))`
and `recv` is `_ForkingPickler.loads(self._recv_bytes().getbuffer())` (3.14 `connection.py:212-261`).
The manager reaches its connections only through `send`/`recv` (brief 01). So the codec is two
methods, `send` and `recv`, on HyperShell's own connection classes; `_send_bytes`/`_recv_bytes`
(framing, R4) and `send_bytes`/`recv_bytes` (the HMAC challenge, R4) are untouched.

- **TLS:** `SecureConnection` (`core/queue.py:115`) already subclasses `_ConnectionBase`; it gets the
  codec's `send`/`recv`.
- **Cleartext:** `_PlainListener.accept` (`:381`) and `_plain_client` (`:389`) return a stock
  `Connection` today (`SocketListener.accept` and `SocketClient` build `Connection(s.detach())`,
  3.14 `connection.py:667-690`). They must return a `Connection` subclass carrying the codec. The
  descriptor is handed over, not duplicated: `_plain_client` can dial AF_INET itself, as
  `secure_client` does (`socket.socket(AF_INET)`, `connect`, `detach`), and `_PlainListener.accept`
  can take the accepted socket from the listener's socket, already pinned in `_INTERNALS`
  (`Listener keeps its socket at _listener._socket`, `:474`). `TCP_NODELAY` is then set on the socket
  directly (`_no_delay`), which also retires the descriptor-duplicating `_no_delay_connection` for
  these two paths. The bytes on the wire stay byte-compatible with `multiprocessing.connection`.
- **Role.** A connection knows whether it was accepted (server side) or dialled (client side, with
  the dialled address). The two sides differ only in what they do with a pickle frame (R5).

## Encoding (JSON, per the GOAL's ruling)

JSON cannot tell a tuple from a list, has no `bytes`, and only string keys. The envelope is mostly
tuples, and the client needs them as tuples: `MakeProxyType` caches by `(name, exposed)`, so
`exposed` must arrive hashable (brief 01). The prototype's mapping, chosen so the common case carries
no tag:

| Python | JSON |
|---|---|
| `None`, `bool`, `int`, `float` (finite), `str` | native |
| `tuple` | array |
| `list` | `{"l": [...]}` |
| `dict` with `str` keys | `{"d": {...}}` |
| `bytes` | `{"b": "<one code point per byte>"}` |
| `Token` | `{"k": [typeid, [host, port], id]}` |
| `queue.Empty`, `queue.Full`, `RemoteError` | `{"e": [name, [str args]]}` |

Any other exception handed to the encoder (an `#ERROR` reply) becomes `RemoteError("<Type>: <text>")`,
as the GOAL's table says. Any other value fails to encode, before a byte is written (R2's sender
half: the manager's `serve_client` then sends `#UNSERIALIZABLE`, as it does for a pickling error
today). A JSON object is only ever a one-key tag node, except the map inside `"d"`. Decoding is
`json.loads` of the frame decoded as strict UTF-8, then one walk of the result that maps each node
back and enforces the depth and element limits (R8); a node of any other shape (unknown tag, more
than one key, a tag's value of the wrong type, a `Token` or exception of the wrong shape, an
exception name not in the three) is refused. `json.loads` builds only `dict`/`list`/`str`/`int`/
`float`/`bool`/`None`, so nothing outside the vocabulary is ever constructed (R1, R2).

The decoder turns into refusals: invalid UTF-8, invalid JSON, trailing data, `NaN`/`Infinity`
(`parse_constant` raises; the encoder runs with `allow_nan=False`), an integer past CPython's
digit limit (`ValueError`), nesting past the C parser's recursion limit (`RecursionError`). A
duplicate key in a `"d"` map keeps the last value, as `json` does; the encoder never writes one.

The frame of every codec message starts with `[` (the envelope is a tuple). `[` and `{` are not
pickle opcodes, so a 2.9.0a9-or-earlier server fails at once on a codec request
(`UnpicklingError: invalid load key, '['`) and answers with a pickled `#TRACEBACK`, which the new
client recognises by its first byte (`0x80`, protocol 2 and later) and refuses (R5).

## How `bytes` crosses: a tagged string, one code point per byte (Latin-1)

The GOAL left two options: a tagged UTF-8 string that refuses non-UTF-8 bytes, or switching every
payload producer and consumer to `str`. A third variant of the first is strictly better:
**decode the bytes as Latin-1** (each byte value 0-255 is the code point of the same number).

- It is total: every `bytes` value round-trips byte for byte, so R3's contract has no refusal case
  and no producer has to promise UTF-8. The decoder refuses a `"b"` string holding a code point above
  U+00FF (`str.encode('latin-1')` raises).
- It costs the same as UTF-8 for HyperShell's payloads. Every payload is `json.dumps(...)`
  (`core/types.py:84`, `ensure_ascii` on), so it is pure ASCII: Latin-1 and UTF-8 decode it with the
  same fast path (≈16 µs per 286 KB, under 2% of the envelope's own cost below), and no byte is
  escaped beyond JSON's own `"` and `\`.
- Switching producers to `str` touches the client, server, submit, relay, heartbeat and dispatcher
  staging, their in-process queues, and the pinned tests that assert payload types (R7) — for no
  measurable gain. Rejected. Base64 is rejected by the GOAL's own numbers (+33% frame, slowest).

## Cost (prototype; `min` of 5 repeats; round trip = encode + decode; Python 3.14.6, Apple M-series)

| Message | pickle | codec | delta | frame pickle / codec |
|---|---|---|---|---|
| `get` request `(id, 'get', (True, 2), {})` | 1.76 µs | 3.03 µs | +1.3 µs | 43 / 40 B |
| `get` reply, one task (`#RETURN`, 573 B bytes) | 1.80 µs | 4.41 µs | +2.6 µs | 614 / 662 B |
| `put` request, one task | 1.99 µs | 5.90 µs | +3.9 µs | 633 / 691 B |
| `put` reply `('#RETURN', None)` | 1.64 µs | 1.66 µs | 0 | 25 / 16 B |
| `#ERROR` `queue.Empty()` | 2.42 µs | 2.60 µs | +0.2 µs | 45 / 29 B |
| dispatcher reply, 1,000 tasks as one `str` | 70 µs | 2,180 µs | +2.1 ms | 644 / 762 KB |

The large-reply cost is JSON escaping a payload that is itself JSON (`json.dumps` ≈450 µs and
`json.loads` ≈520 µs per 286 KB; the UTF-8 steps are ≈20 µs). That is ≈2 µs per task, as the seed
measured, against 7-26 ms per task of database work (`issues/server-per-task-statement-overhead.md`).
At bundle size 1 a task's traffic is a handful of small messages, ≈10-20 µs added per task.

**If R10 fails**, the lever is to carry a large `str`/`bytes` leaf out of band: the JSON envelope
holds a reference and the leaf's bytes follow it raw in the same frame. That removes the escaping
(the dominant cost) without a new format or dependency. It is not needed by the numbers above, and
adds a second parsing rule, so it is held back unless the gate shows the regression.

## Fixed reply to a pickle-framed peer (R5, Q2)

*Superseded in part by brief 02 and the digest (decision 6): the reply is `('#ERROR',
RuntimeError(TEXT))` at protocol 4. The rest of this section stands.*

Built once at import, encode-only, from constants: the pickle of `('#TRACEBACK', TEXT)` at protocol 2
(readable by every Python an old release runs on), sent with `send_bytes` and never decoded. Nothing
from the old peer is decoded to build it. Whether `#TRACEBACK` or `#ERROR` reads better on the old
side is brief 02's question. The module that builds it is the only place that imports `pickle`, and
only `dumps` is called; a test pins that `pickle.loads` and `ForkingPickler.loads` are never reached
from a queue connection (R1, R6).
