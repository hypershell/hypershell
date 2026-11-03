# 01 — The TLS client's dial vs the cleartext client's dial

Scope: what `secure_client` must change to reach the endpoint the cleartext client reaches (R2), what it
must keep (R4, R5), and whether the change ports to `master` (R6). Codebase-first; no web research.

## Current TLS dial

`src/hypershell/core/queue.py`, `secure_client(address, family=None, authkey=None)`:

1. `raw = socket.create_connection(address)`: resolves `address[0]` with `getaddrinfo(host, port, 0,
   SOCK_STREAM)` (no family restriction) and tries each result in resolver order until one connects.
2. `servername` = `cfg.servername` if set, else `address[0]` when `ctx.check_hostname` is on.
3. `ctx.wrap_socket(raw, server_hostname=servername)`, then `verify_peer_fingerprint`, then the
   `answer_challenge`/`deliver_challenge` auth handshake over `SecureConnection`.

`family` is accepted for signature compatibility and ignored.

## Cleartext dial (the reference)

Stock `multiprocessing.connection.Client` → `SocketClient(address)`: `family = address_type(address)`,
which is `'AF_INET'` for every tuple address, then `socket.socket(AF_INET)`, `setblocking(True)`,
`connect(address)`. CPython resolves the host for an `AF_INET` socket with an `AF_INET`-only lookup and
connects to the first IPv4 address. No IPv6 path exists.

The server side matches: `SecureListener` refuses any family but `AF_INET` and binds IPv4 only.

## Who calls `secure_client`

Every TLS connection: `BaseManager.connect`/`_create`, and every proxy connection (`BaseProxy._connect`,
`_incref`, `_decref`) via `listener_client[TLS_SERIALIZER][1]`. Proxies carry `Token.address`, which is
either the user's `(host, port)` or the server's `getsockname()` (`('127.0.0.1', port)`). One change
at the dial covers all of them.

## Probe (developer machine, scratch script, not committed)

A `::1` IPv6-only listener that accepts and resets (`SO_LINGER` 1/0), on the same port as a real IPv4
`SecureListener`; `socket.getaddrinfo` wrapped to put `('::1', port)` first for `localhost`:

| Dial | Result over 5 trials | Resetter connections |
|------|----------------------|----------------------|
| current `secure_client(('localhost', P))` | 5/5 fail (`BrokenPipeError` here; `ConnectionResetError` in the field) | 5 |
| same, with the dial replaced by an `AF_INET` socket + `connect` | 5/5 handshake OK | 0 |
| stock `Client(('localhost', P))` (cleartext) | OK | 0 |

The pre-fix failure is deterministic once the resolver order is forced; the exception class varies
with the reset race, so a test must assert success, not a particular error.

No-IPv4 host: `Client(('::1', P))` and `socket.socket(AF_INET).connect(('::1', P))` both raise
`socket.gaierror` immediately (0.000 s). Nothing listening: both dials raise `ConnectionRefusedError`
(the current `create_connection` also raises it, as the last address's error).

## Error mapping is by exception type

`src/hypershell/client.py`: `ConnectionRefusedError` → `exit_status.runtime_error`,
`ConnectionResetError` → `handle_disconnect`, `gaierror` → `HostAddressInfo("Could not resolve host
…")`. An `AF_INET` dial raises the same types the cleartext client raises for the same input, so the
CLI's messages and statuses follow the cleartext ones (R4).

## Host-name verification

`servername` is derived from `address[0]` as given, before and after the change, since the dial does
not rewrite `address`. With `cafile`, `check_hostname = bool(cfg.servername)`; with a fingerprint pin
or `insecure`, no host check. The change does not touch `get_client_context`, `verify_peer_fingerprint`
or the auth handshake (R5).

## `master` parity (R6)

`git diff origin/master origin/develop -- src/hypershell/core/queue.py` touches only the import line
and `_tls_bootstrap`→`_child_bootstrap`/`SecureManager.start`; `SecureListener` and `secure_client`
are byte-identical. `tests/conftest.py` and `tests/test_queue_tls.py` are identical. `socket.AF_INET`
socket + `connect` is standard library on every supported Python.
