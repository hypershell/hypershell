# 02 — Deadline semantics and the `[server.tls] handshake` key

## A socket timeout bounds the whole handshake (probe, Python 3.11–3.14)

Server side: `raw.settimeout(T)` then `wrap_socket(raw, server_side=True)` (which calls
`do_handshake()`), T = 2 s. Two peers: one that sends nothing, one that sends a real 1516-byte
ClientHello one byte every 0.25 s (so every gap is far below T and the whole hello would take ~6 min).

| Python | OpenSSL | silent peer | slow peer |
|--------|---------|-------------|-----------|
| 3.11.18 | 3.5.9 | `TimeoutError` at 2.0 s | `TimeoutError` at 2.0 s |
| 3.12.13 | 3.5.7 | `TimeoutError` at 2.0 s | `TimeoutError` at 2.0 s |
| 3.13.16 | 3.5.9 | `TimeoutError` at 2.0 s | `TimeoutError` at 2.0 s |
| 3.14.6  | 3.5.7 | `TimeoutError` at 2.0 s | `TimeoutError` at 2.0 s |

CPython's `SSLSocket.do_handshake` computes one deadline from the socket timeout when it starts and
waits against what is left of it on every retry, so data the peer sends does not extend it. That is
R2's "measured from accept, not extended by data". The deadline counts from accept by setting the
timeout to `accepted + handshake - now` on the handler thread (the accept time is taken on the accept
loop, right after the TCP accept); a value at or below zero closes at once. After a successful
handshake `settimeout(None)` restores the blocking socket that `SecureConnection` expects. A timeout
raises `TimeoutError` (`socket.timeout` is its alias), which is how the handler tells a deadline
close (R5's log lines) from any other failure (logged as on `develop`).

No hand-written non-blocking loop is needed; the deadline is one stdlib call on each supported version.

## Configuration

- `HYPERSHELL_SERVER_TLS_HANDSHAKE` reaches `config.server.tls.handshake` today (probe in a throwaway
  site), smart-coerced: `30` → `30` (int), `2.5` → `2.5` (float), `1m` → `'1m'`, `0` → `0`,
  `-5` → `-5`, `abc` → `'abc'`. Unset, the key is absent (`None`); the change adds the default
  `'handshake': 30` to `[server.tls]` in `core/config.py`.
- `core/types.py` `parse_time` takes any of those through `str()`: `30`/`30s`/`1m`/`2.5` (rounded up
  to 3), `1:30`; it raises `UnitError` (message names the value and `TIME_FORMS`) for `abc`, `-5`
  (no form has a sign) and `True`; `0` parses to 0 and is refused by the new check (> 0 required).
  `core/tls.py` imports only `core/logging`; `core/types.py` imports no internal module, so importing
  `parse_time`/`UnitError`/`TIME_FORMS` into `core/tls.py` adds no cycle.
- `TLSConfig` is the carrier every TLS server already receives: `ServerApp.enable_tls` and
  `ClusterApp.enable_tls` build it from `{**config.server.tls, ...}`, the relay from
  `config.server.tls`, all through `from_namespace`, and `resolve_auto_paths` uses
  `dataclasses.replace`, so a new field survives it. Clients (`hs client`, `hs submit`,
  `QueueConfig.load`, the relay's upstream link) build `TLSConfig` from the same section, so
  `from_namespace` must **not** parse or reject the value, or a bad server-only key would stop clients
  too. It copies the raw value (`ns.get('handshake', DEFAULT_HANDSHAKE)`); servers parse it.
- One parser for every server: `core/tls.py` `handshake_seconds(value) -> int` (`parse_time`, then
  `> 0`), raising `TLSError` whose message starts `server.tls.handshake` and names the accepted forms.
  Called by `_InProcessServer.__init__` before it binds (raised as `QueueServerError`, the existing
  start-failure type, so nothing is left listening), and early by the three commands so each refuses
  before doing anything else: `ServerApp.enable_tls`, `ClusterApp.enable_tls`, and
  `RelayApp.check_args` (the relay otherwise starts its site server only after reaching upstream,
  which may never happen). The commands raise `ArgumentError`, as the relay already does for a bad
  `autoscale.period` (`relay/__init__.py` `site_period`). Plain servers never call it (R6).

## Exit paths observed

- `QueueServerError` is a `RuntimeError`; the shared mapping logs it at CRITICAL and exits with
  `exit_status.runtime_error`. `ServerThread.start` re-raises it in the caller, and every cluster mode
  starts its server before any client (`cluster/local.py`, `remote.py`, `ssh.py`).
- `ArgumentError` is cmdkit's usage error (`exit_status.bad_argument`), printed with its message.
