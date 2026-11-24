# 01 — Envelope inventory (CPython 3.11–3.14, HyperShell traffic, sizing, pins)

Read-only research for `hs-plan`, 2026-11-23. CPython paths: `~/.local/share/uv/python/cpython-3.{11.18,12.13,13.16,14.6}-macos-aarch64-none/lib/python3.X/multiprocessing/`. Message shapes below were also **observed live** (3.14.6, plain pair) by wrapping `_ConnectionBase.send/recv` around a `QueueServer`/`QueueClient` round trip of every exposed method.

## 1. CPython envelope — identical on 3.11–3.14

`managers.py` line refs are 3.14.6 (3.11: −1 line up to `dispatch`, same bodies). Diffs 3.11→3.14 touch only `dispatch`/`_callmethod` (`del result` after raise, 3.12+), `BaseProxy` serials (`_decref(token, serial, …)` 3.12+, `(token, authkey, …)` on 3.11) and SyncManager proxy types. **No envelope change in any version.**

| Site | Sends | Receives |
|---|---|---|
| `dispatch` :86 (`c.send` :90, `c.recv` :91) | `(id, methodname, args, kwds)` — `id` is `None` for every `Server.public` call | `(kind, result)`; `#RETURN`→result, else `raise convert_to_error` |
| `BaseManager.connect` :530 | `dispatch(conn, None, 'dummy')` → `(None,'dummy',(),{})` | `('#RETURN', None)`; conn never closed |
| `BaseManager._create` :605 | `(None,'create',(typeid,)+args, kwds)` | `('#RETURN', (ident:str, exposed:tuple))`, unpacked `id, exposed =`; `Token` built **locally** |
| `register.<temp>` :728 | `(None,'decref',(token.id,),{})` (the "decref hand-off") | `('#RETURN', None)` |
| `_debug_info`/`_number_of_objects`/`_finalize_manager` | `(None,'debug_info'|'number_of_objects'|'shutdown',(),{})` | str / int / None (`Server.shutdown` itself sends `('#RETURN',None)`, then `_handle_request` sends it again) |
| `BaseProxy._connect` :810 | `(None,'accept_connection',(name:str,),{})` | `('#RETURN', None)` sent by `Server.accept_connection` :419, then `serve_client` loop |
| `BaseProxy._incref` :858 / `_decref` :879 | `(None,'incref'|'decref',(id:str,),{})` | `('#RETURN', None)` |
| `BaseProxy._callmethod` :819 (`conn.send` :831, `conn.recv` :832) | `(self._id:str, methodname:str, args:tuple, kwds:dict)` | `#RETURN` value; `#PROXY (exposed, token)`; else `convert_to_error` |
| `AutoProxy` :987 | only if `exposed is None`: `(None,'get_methods',(token,),{})` | `tuple(...)` |
| `Server._handle_request` :200 (`c.recv` :205, `c.send` :220/:223) | `('#RETURN', result)` or `('#TRACEBACK', format_exc())` | first request of every connection |
| `Server.serve_client` :242 (`recv = conn.recv`, `send = conn.send`) | `#RETURN`, `('#ERROR', e)`, `('#PROXY', (rexposed, Token))`, `#TRACEBACK`, `('#UNSERIALIZABLE', format_exc())` | `(ident, methodname, args, kwds)` |
| `Server.create` :377 | returns `ident, tuple(exposed)` (`'%x' % id(obj)`); `exposed` = registry value, or `public_methods(obj)`; `list(exposed)+list(method_to_typeid)` only when `method_to_typeid` is set, still returned as a tuple | — |
| `convert_to_error` :99 | `#ERROR`→the exception object; `#TRACEBACK`/`#UNSERIALIZABLE`→`RemoteError(str)` (raises `TypeError` if result not str); other→`ValueError` | — |
| `RebuildProxy` :944 | unpickle hook only; not reached by the codec | — |
| `MakeProxyType` :965 | does `exposed = tuple(exposed)` itself (all versions), so a list arriving as `exposed` is harmless | — |

Hashability: `Token.address` keys `BaseProxy._address_to_local`; `ident` keys `id_to_obj`. Decoding every JSON array as a **tuple** is safe for all manager internals; nothing in the envelope needs a list. `connection.py`: `send` = `_check_closed; _check_writable; _send_bytes(_ForkingPickler.dumps(obj))`; `recv` = `_check_closed; _check_readable; buf = _recv_bytes(); loads(buf.getbuffer())` (3.14 :212/:256; 3.11 :202/:246) — no maxlength. `deliver_challenge`/`answer_challenge` use only `send_bytes`/`recv_bytes` (verified co_names, all four). A pickle body starts `\x80\x04\x95` (3.11–3.13) / `\x80\x05\x95` (3.14).

**Failure paths (decide how a refusal closes):**
- `_handle_request` (first message on every connection — `dummy`, `create`, `incref`, `decref`, `accept_connection`): **any** exception from `recv` → `msg = ('#TRACEBACK', format_exc())` → `c.send(msg)`; if that raises, one retry of a `#TRACEBACK`, swallowed, `util.info`; return → HyperShell `handle_request` closes (`core/queue.py:807-821`). So an old peer's first pickled request always lands here. Encode failure here yields `#TRACEBACK`, not `#UNSERIALIZABLE`.
- `serve_client`: `EOFError` → `sys.exit(0)` (absorbed by `handle_request`, conn closed). `AttributeError` → `#TRACEBACK`, **loop continues**. **Any other exception from `recv` → `#TRACEBACK` reply and the loop continues on the same connection.** So to "refuse and close" (R2) the codec's `recv` must raise `EOFError` (or a subclass), or close the connection before raising so both `send`s fail → `conn.close(); sys.exit(1)`. `send` raising → `send(('#UNSERIALIZABLE', format_exc()))`; if that raises too → close + `sys.exit(1)`. The codec must encode fully before writing any byte.
- Client: `dispatch` propagates `recv` errors (`connect`, `_incref`, `_decref`, temp leave the conn to GC; `_create`/`_debug_info`/`get_methods` close in `finally`). `_callmethod` propagates but **keeps the cached `_tls.connection`** — a refusing `recv` must close itself so the next call raises `OSError` (in `TRANSPORT_ERRORS`, `core/reconnect.py:100`), unless it is the R5 case, which must raise `QueueVersionError` (`FATAL_ERRORS`, :97). Raising `EOFError` client-side would be retried as a transport outage.

## 2. HyperShell's traffic (observed shapes, 3.14 live trace)

Requests: `dummy (None,'dummy',(),{})`; `create (None,'create',('_get_scheduled', 1),{})` (`PROTOCOL: int = 1`, `core/dispatch.py:51`; `core/queue.py:1289`), other four creates `(…,('_get_x',),{})`; `incref`/`decref`/`accept_connection` one `str`; queue calls `(ident,'put',(bytes,),{'timeout': 2})` (`DEFAULT_REMOTE_TIMEOUT: int = 2`, `core/queue.py:1116`); `get`/`get_nowait`/`qsize`/`empty`/`full` `()` args; dispatcher `request|topology|handback` `(str,)`, `takes_submissions` `()`.
Replies: `#RETURN` with `None`, `bool` (empty/full/takes_submissions), `int` (qsize), `str` (request/topology/handback), `bytes` (remote `get`, tests only), `(str, (9×str))` for queue `create` (`QUEUE_EXPOSED`, :1167), `(str, (4×str))` for `_get_dispatch` (`DISPATCH_EXPOSED`, :1172).

- **Callers over the wire:** client `confirmed.put` (`client.py:288`), `completed.put` (:489), `heartbeat.put` (:1139), `dispatcher.request` (:254); submit `takes_submissions` (`submit.py:1130`), `scheduled.put` (:985); relay upstream `dispatcher.request` (`relay/upstream.py:550`), `confirmed.put` (:596), `completed.put` (:875), `heartbeat.put` (:1050), `topology` (:1124), `handback` (:665, `relay/__init__.py:829`). All `server.py` `get`/`put`/`task_done` and `cluster/watch.py:120` are on the local `Queue` — no RPC. **No production role makes a remote `get`**; tests do (`tests/test_queue.py:270`, `:308`) and put ints (`:235`, `:248`).
- **Dispatcher** (`core/dispatch.py`): `request(text: str) -> str` (:326), `takes_submissions() -> bool` (:441), `topology(text: str) -> str` (:457), `handback(text: str) -> str` (:468). Bad text gets an error reply, **but `request` can still raise**: `size` of `Infinity`/`1e400` → `OverflowError`, `NaN` → `ValueError` (`Request.decode` :139-141, verified, crosses as `#ERROR`); `_add_inbound` (:301) raises `AttributeError`/`UnicodeDecodeError`/`JSONDecodeError`/`KeyError`/`TypeError` on a malformed submitted bundle, inside the requesting client's call.
- **`#ERROR` exceptions seen:** `queue.Full()` (module `queue`), `queue.Empty()` (**class is `_queue.Empty`**, map by identity, not module name), both `args == ()`; plus any of the above; `Queue.task_done` → `ValueError`; bad call arity → `TypeError`. `ClientTooOld` (`core/queue.py:1180`) is raised inside `create` → `_handle_request` → `#TRACEBACK` text, never `#ERROR`. `reconnect.py:103` parses `#TRACEBACK` text (`KeyError: '<hex>'`) for stale proxies, so that text must stay verbatim.
- **`#PROXY`/`Token`:** never occur. Every registry entry has `method_to_typeid=None` (`core/queue.py:1224-1227`; client `register('_get_x')`, `AutoProxy` has no `_method_to_typeid_`), so `gettypeid` is `None`; `create` always returns `exposed`, so `get_methods` is never sent. `Token` (`__slots__ = ('typeid','address','id')`: `str`, `(str host, int port)`, `str`) crosses only in those two unreachable paths.
- **Containers:** no `list` and no non-`str` dict key in HyperShell traffic; the only dicts are `kwds` (`{}` or `{'timeout': int}`; `block: bool` possible from the Python API) and the forked-manager test's `#GETVALUE` reply `{'status': 'ok', 'value': 42}` (`tests/test_queue_tls.py:535`; method name `'#GETVALUE'` via `fallback_mapping`, so method names are not just identifiers). **Floats:** none in production envelopes (timeouts are int 2; `wait` floats live inside the request JSON text); tests use float timeouts. Codec must keep `bool` ≠ `int`: `_versioned_provider` compares `args != (PROTOCOL,)` and `(True,) == (1,)`.

## 3. Payload bytes

`serialize` = `json.dumps(data).encode('utf-8')` (`core/types.py:82`); `serialize_tasks` (`data/model.py:386`), `Heartbeat.pack` (`core/heartbeat.py:113`, roster list inside), `ClientInfo.pack` (`client.py:125`), `make_sentinel` = `serialize(None)` = `b'null'` (`core/queue.py:1125`) all go through it → `bytes`, `ensure_ascii=True` → **always ASCII** (lone surrogates from argv are `\udcxx`-escaped). Relay returner bundle: `'[' + ', '.join(records) + ']'` `.encode('utf-8')` (`relay/upstream.py:872`) of records made by `json.dumps` (`relay/store.py:444`) → ASCII. Dispatcher reply text joins records from `json.dumps(task.to_json())` (`server.py:468`, `core/dispatch.py:307`) → ASCII `str`. The only `ensure_ascii=False` (`data/model.py:707`) is a local md5 fingerprint. No test puts non-UTF-8 bytes. Consumers need **real `bytes`**: `deserialize` calls `data.decode` (`core/types.py:89`), as does `_add_inbound` (`core/dispatch.py:303`); a codec that hands them `str` breaks the server. Third-party users of `QueueClient` could have put any picklable object before; after, only the vocabulary.

## 4. R8 sizing

Every bundle, reply text, heartbeat, client info and topology crosses as **one `bytes`/`str` leaf**, so task count never enters depth or element count; it only lengthens the leaf (and the frame, a non-goal).

| Message | Depth (containers, outer tuple = 1) | Largest container |
|---|---|---|
| `request` reply at cap = `max(bundlemax, bundlesize)` (`core/dispatch.py:247`; default 1000): `('#RETURN', str)` | 1 | 2 |
| `completed`/`scheduled`/`heartbeat`/`confirmed` put: `(id, 'put', (bytes,), {'timeout': 2})` | 2 | 4 |
| `create` reply `('#RETURN', (ident, QUEUE_EXPOSED))` | 3 | **9** |
| `#ERROR` with exception (if encoded as tag + args) | 2–3 | 2 |
| forked `#GETVALUE` dict / `public_methods(dict)` exposed | 2–3 | 11 (`public_methods(Queue())` = 10) |
| `#PROXY` / `get_methods` with `Token` (unreachable) | 4 | 9 |

Leaf sizes: one task record ≈ 780 chars (short command); a 1000-task reply ≈ 786 kB `str`. **Configuration is unbounded above**: `server.bundlemax`, `server.bundlesize`, `client.bundlesize`, `submit.bundlesize`, `relay.bundlesize`, `relay.upstream.bundlesize` are all `Kind.INT` (`core/config_schema.py:79,85,88,99,105`) with only `max(1, int(x))` floors (`core/dispatch.py:208,210`; `relay/upstream.py:423,791`); topology/hand-back text is bounded only on input (`MAX_TOPOLOGY_REQUEST` 16384, `MAX_HANDBACK_REQUEST` 256 KiB, `core/topology.py:46,55`). **Recommendation: max depth 8, max container 64 elements** (observed 3/9 in production, 4/11 worst incl. Token and the forked test); both independent of bundle size, so "the largest bundle the configuration accepts" is admitted for any value. Enforce depth in the codec itself: `json.loads` raises `RecursionError` at nesting 5000 on 3.11 but parses 5000 on 3.14 (version-dependent); ints over 4300 digits already raise `ValueError`; `NaN`/`Infinity` parse by default (`allow_nan`).

**R10 cost note (measured, 3.14):** JSON-quoting a 786 kB JSON leaf escapes every `"`: frame +10.2%, encode 1.22 ms + decode 1.33 ms vs pickle 0.014 + 0.043 ms (~2.5 µs/task, matches the GOAL's 2–7 µs). If R10 is tight, carry `str`/`bytes` leaves out of band (JSON skeleton + length-prefixed raw segments in the same frame) to avoid the escaping.

## 5. R9 pins

Already pinned (`core/queue.py:461-508`, run by `TestPrivateInternals.test_shared_checks_pass` `tests/test_queue.py:108` and at server start): `Server.__init__`/`_handle_request(c)`/`serve_client(conn)`/`shutdown(c)` signatures; `serve_client` uses `stop_event`, `sys.exit`, no `mutex`; `'#TRACEBACK'` in `_handle_request` co_consts + `format_exc` (:501); `listener_client` pairs (:476); `create` `*args`; `register` passes args; `convert_to_error('#TRACEBACK','')` → `RemoteError` (:505); `_callmethod` `_tls.connection` (:506). Not pinned: anything about `send`/`recv`, the other reply kinds, `Token`. `test_create_passes_args_and_refusal_is_a_traceback` (:213) uses a stock pickle `BaseManager` peer (R7 restate).

New pins, cheap and verified true on 3.11.18, 3.12.13, 3.13.16, 3.14.6 (co_names; string co_consts **flattened through nested tuples and code objects** — `Server.shutdown`'s `('#RETURN', None)` is a folded tuple constant, and its position differs on 3.14):
- `{'send','recv'} <= co_names` and no `send_bytes`/`recv_bytes`/`dumps`/`loads`/`_ForkingPickler` in: `dispatch`, `Server._handle_request`, `Server.serve_client`, `BaseProxy._callmethod`; `send` in `Server.accept_connection`, `Server.shutdown`.
- `'dispatch' in co_names` (and no `send`/`recv`) for `BaseManager.connect/_create/_debug_info/_number_of_objects/_finalize_manager`, `BaseProxy._connect/_incref/_decref`, `AutoProxy`, and `register`'s inner function.
- Reply tags: `serve_client` ⊇ {`#RETURN`,`#ERROR`,`#PROXY`,`#TRACEBACK`,`#UNSERIALIZABLE`}; `_handle_request` ⊇ {`#RETURN`,`#TRACEBACK`}; `convert_to_error` ⊇ {`#ERROR`,`#TRACEBACK`,`#UNSERIALIZABLE`}; `_callmethod` ⊇ {`#RETURN`,`#PROXY`}; `'EOFError' in serve_client` co_names.
- `deliver_challenge`/`answer_challenge` use `send_bytes`/`recv_bytes` and not `send`/`recv` (R4).
- `_ConnectionBase.send` co_names ⊇ {`_check_closed`,`_check_writable`,`_send_bytes`}; `recv` ⊇ {`_check_closed`,`_check_readable`,`_recv_bytes`} (what the override must replicate).
- `Token.__slots__ == ('typeid','address','id')`, `Token.__init__` params `[self, typeid, address, id]`; `'tuple' in co_names` of `Server.create`/`get_methods` and `MakeProxyType`; `Server.public` set (9 names); `fallback_mapping` keys {`__str__`,`__repr__`,`#GETVALUE`}.
- Behavioural (live, one each): a request decoded by the codec round-trips every tag; a non-EOF error from `recv` in `serve_client` leaves the loop running (so the codec's refusal must not rely on it).

Plain-pair caveat for the plan: `_PlainListener.accept` and `_plain_client` return the stock `multiprocessing.connection.Connection`, which the forked manager's `Pipe` and process spawning also use; the override needs a subclass (or class swap per connection), never a patch of the stock class.
