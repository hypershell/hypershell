# 02 — What a 2.9.0a9-or-earlier peer does with the fixed refusal reply

Question (GOAL R5, Clarification Q2): a 2.9.0a10 server answers a pickle-framed request with one fixed,
encode-only reply and closes. Does each old role exit on it, and which reply shape exits cleanest?
All line numbers below are **2.9.0a9** (`git show 2.9.0a9:<path>`) unless marked CPython 3.14.

**Verdict: (b) holds; no fallback to (c) needed.** Every 2.9.0a9 role exits on its first request,
exit status 5 (`exit_status.runtime_error`), in under 1 s, with no redial. The reply shape decides only
how it exits: `('#TRACEBACK', text)` exits with a traceback file; **`('#ERROR', RuntimeError(text))`**
exits with one clean CRITICAL line on every release back to 2.8.x. Recommend the latter.

## The first request, and where the error surfaces

- Every role's first request on the wire is `dummy`: `BaseManager.connect` (CPython `managers.py:530-536`)
  calls `dispatch(conn, None, 'dummy')` before `QueueClient.connect` reaches its `try` around
  `_get_scheduled(PROTOCOL)` (`core/queue.py:1273-1295`). Probe confirms: one 27-byte frame, first byte
  `0x80`, containing `dummy`, never `create`, for client, submit and relay.
- `dispatch` (CPython `managers.py:86-97`) raises `convert_to_error(kind, result)` (`:99-112`): `#ERROR`
  raises the unpickled exception object itself; `#TRACEBACK` raises `RemoteError(text)`, whose `__str__`
  (`:114-116`) wraps the text in two 75-dash rules; an unknown tag raises `ValueError`. The function is
  byte-identical on 3.11, 3.12, 3.13 and 3.14 (hashed locally).
- So the `except RemoteError` → `QueueVersionError` mapping at `core/queue.py:1290` is never reached:
  the error escapes `super().connect()` as whatever the reply carries.
- A fixed `#RETURN` reply cannot work: `dummy` would pass, then `_create` (`managers.py:605-612`) opens a
  new connection, gets the same fixed reply and fails unpacking `id, exposed` (TypeError → traceback).

## Per role (code path)

- **`hs client`** — no reconnect layer at a9 (`client.py` never uses `UpstreamLink`/`as_link`).
  `ClientThread.run_with_exceptions` enters `with self.client:` (`client.py:1392`), the error propagates
  to `ClientApp.exceptions` (`client.py:1726-1733`) and the shared mapping
  (`core/exceptions.py:129-143`): `RuntimeError` → `handle_exception` (one CRITICAL line, status 5);
  any other `Exception` (incl. `RemoteError`, `AttributeError`, `ValueError`) → `write_traceback`
  (CRITICAL line + "Exception traceback written to …/exception-<time>.log", status 5); `EOFError` →
  `handle_disconnect` ("EOFError: server disconnected", status 5).
- **`hs submit -q`** — `LiveSubmitThread` enters `with self.client:` (`submit.py:1117`); same mapping
  (`submit.py:1640-1646` + shared). No reconnect layer either.
- **Relay upstream link** — `UpstreamLink.attempt` (`core/reconnect.py:466-477`) → `dial` → the relay's
  `first_connect` (`relay/upstream.py:211-227`; TLS off first runs `speaks_tls`, a raw TLS handshake that
  the server sees as a failed auth; TLS on re-raises everything but `ssl.SSLError`) → `client.connect()`.
  On error: `is_fatal(e) or not is_transport(e)` → `self.fail(e)` (DEBUG "Cannot use …" only).
  `RemoteError`/`RuntimeError` are neither in `FATAL_ERRORS` (`:97`) nor `TRANSPORT_ERRORS` (`:100`);
  `is_stale_proxy` (`:112-128`) needs the last line to fullmatch `KeyError: '<hex>'`, which our text
  never does (and `RemoteError.__str__` appends dashes to that line anyway). `RelayThread.wait_link`
  (`relay/__init__.py:669-679`) → `wait_up` → `raise_failure` (`reconnect.py:355,387-390`) re-raises;
  `RelayThread` aborts and `RelayApp.exceptions` (`relay/__init__.py:1129-1135` + shared) maps it as
  above. A relay that *re*dials after an upstream restart takes the same `attempt` path (no
  `first_connect`), fails the link, and its puller/heartbeat get the error from `call` → `raise_failure`.
  Only `EOFError`/`OSError` would put it in the backoff loop.
- **`FixedLink`** (`reconnect.py:561-571`) just calls through; no role reaches it before connecting.

## Live probe (2.9.0a9, CPython 3.14.6, cleartext, 127.0.0.1)

Fake server (`/tmp/claude-501/venv314-codec/probe/fake_server.py`): stock `Listener(authkey=None)`,
`deliver_challenge` + `answer_challenge`, `recv_bytes()` (never unpickled; first byte and length logged),
`send_bytes(FIXED)`, close. Roles run with fresh `HOME`/`HYPERSHELL_SITE`, key via environment, `--no-tls`.
"link" drives `UpstreamLink` + `first_connect` exactly as `RelayThread` does; "relay" is real `hs relay`.
Conns counts accepted TCP connections (the relay's `speaks_tls` probe is 1 of each pair).

| FIXED reply | client | submit -q | link / relay | what the user sees |
|---|---|---|---|---|
| `('#TRACEBACK', T)` | 5, 0.37 s, 1 conn | 5, 0.36 s, 1 | FAILED / 5, 0.70 s, 2 | `CRITICAL RemoteError:  - ----… - T----…` + "Exception traceback written to …" (file written) |
| `('#ERROR', RemoteError(T))` | 5, 0.36 s, 1 | 5, 0.36 s, 1 | FAILED / 5, 0.68 s, 2 | identical to `#TRACEBACK` |
| `('#ERROR', RuntimeError(T))` | 5, 0.37 s, 1 | 5, 0.37 s, 1 | FAILED / 5, 0.69 s, 2 | `CRITICAL [hypershell.client] RuntimeError: T` — one line, no file |
| `('#ERROR', QueueVersionError(T))` (by reference) | 5, 0.36 s, 1 | 5, 0.37 s, 1 | FAILED (fatal) / 5, 0.76 s, 2 | `CRITICAL … QueueVersionError: T` — one line |
| close only (option a) | 5, 0.35 s, 1 (`EOFError: server disconnected`) | 5, 0.36 s, 1 (same) | DOWN after 60 s, 18 conns / **killed at 120 s, 22 conns** | relay: "Cannot reach … (EOFError: ) - trying again", INFO at attempts 2, 4, 8 …, redials forever |

(`T` = "peer released before 2.9.0a10: this server does not speak the pickle framing; run matching
server and client versions".) Every role also logs its usual WARNING "Stopping …" lines before the
CRITICAL. No hang, no retry, in any `#ERROR`/`#TRACEBACK` row; close-only hangs only the relay
(correcting Q2's premise: a9 `hs client` and `hs submit` do not redial on `EOFError`; only the relay's
`UpstreamLink` does).

## Older releases (read from tags)

- Connect shape (`super().connect()` first, so `dummy` first) and the app mapping (`RuntimeError` →
  one line; `Exception` → traceback file; status 5) are the same in 2.8.0–2.8.7 and 2.9.0a1–a9
  (`core/exceptions.py` mapping; `client.py` 2.8.7:1584-1589; `core/queue.py` 2.8.7:506-513).
- `QueueVersionError` exists only from 2.9.0a4; `reconnect.py`/`UpstreamLink` and the relay from 2.9.0a6.
  A by-reference `QueueVersionError` reply would fail to unpickle on 2.8.x–2.9.0a3 (`AttributeError`
  from `c.recv()`) → traceback-file exit, still status 5. `builtins.RuntimeError` resolves everywhere.

## Recommendation for the plan

- `FIXED = pickle.dumps(('#ERROR', RuntimeError(TEXT)), protocol=4)` at import (protocol pinned so the
  constant is the same on 3.11–3.14; protocol 4 = `builtins.RuntimeError`, readable by any 3.8+). Encode
  only; pin the bytes in a test. Name the version and the remedy in `TEXT`; keep it one line (the
  `RemoteError` variants smear dashes into the log).
- Alternative if the class name matters more than ≤2.9.0a3 cleanliness: `QueueVersionError(TEXT)`
  (fatal in a6+ reconnect; same observed outcome as `RuntimeError` on a9).
- Server side: read the whole request frame before replying (as `recv_bytes` does) and close normally
  (no zero linger), so no unread input turns the close into a reset that drops the reply; ~40 probe
  connections showed none. TLS path (read only): `SecureConnection` (`core/queue.py:115`) inherits
  `_ConnectionBase.recv`, so the reply is read the same way after the TLS handshake and auth.
- Change-log note: an old client, `hs submit` or relay meeting a new server exits with status 5 and
  that message; a new peer meeting an old server is refused by R5 on the new side.
