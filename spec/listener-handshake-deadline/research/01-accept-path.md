# 01 — The in-process accept path and what moving the handshake touches

Read on `develop` `6da633d` (the worktree's `src/` is identical). Python 3.14.6, OpenSSL 3.5.

## Where the handshake runs today

- `_InProcessServer.serve` (`core/queue.py`) is the accept loop of every `QueueServer` (`hs server`,
  every `hs cluster` mode, the relay's site-local server via `AutoScalingCluster`). It selects on the
  listening socket with `POLL` (0.25 s), calls `self.listener.accept()`, checks `_admits(conn)` (the
  open-file `RESERVE`), adds the connection to `_live` under `_lock`, and starts one
  `_ServerThread` (`hypershell-queue-handler`) running `handle_request(conn, peer)`.
- For TLS the listener is `SecureListener`, whose `accept()` does the TCP accept, `setblocking(True)`,
  `_no_delay(raw)`, then `wrap_socket(raw, server_side=True)` **inline**: the whole server-side
  handshake runs on the accept-loop thread, on a blocking socket, with no timeout. Until it returns
  no other connection is accepted.
- An accept failure (reset, TLS error, context failure) is logged at DEBUG as
  `Queue server dropped a connection: {error!r}` and the loop continues. That is the "logged as on
  develop" baseline for R5's other handshake failures.
- The plain listener (`_PlainListener.accept`) does no peer I/O; its challenge runs in the handler.

## Contracts the change must keep

- **`SecureListener.accept()` stays as it is.** The stock forked-manager accept loop
  (`SecureManager.start` → `Server.serve_forever` → `accepter`) calls it, and
  `tests/test_queue_address_family.py` drives it directly (`AcceptThread`). The non-goal leaves the
  forked path as is, so the new path is new methods beside `accept()`, which keeps its current body.
- **`TCP_NODELAY` on every accepted TLS socket** (`AGENTS.md` § Queue transport): the new accept
  method calls `_no_delay(raw)` exactly as `accept()` does.
- **Open-file reserve (`_admits`)** reads `conn.fileno()`; a raw socket has one, so the check runs on
  the raw socket right after the TCP accept, before any thread starts. On `develop` it ran after the
  handshake; R3 asks that a pending connection be refused "at once", which this does.
- **`stop()` cuts `_live`** under `_lock` with `_cut(conn)`. `_cut` handles a `SecureConnection`
  (shuts down `_ssl_sock` at the plain-socket level) or a stock connection (through a duplicate
  descriptor). A pending connection is a `socket.socket` (raw, then the unhandshaken `SSLSocket`), so
  `_cut` gains one branch: `isinstance(conn, socket.socket)` → `socket.socket.shutdown(conn, SHUT_RDWR)`.
  `wrap_socket` detaches the raw socket (its `fileno()` becomes -1), so the handler swaps its `_live`
  entry from the raw socket to the `SSLSocket` before the handshake, and to the `SecureConnection`
  after it, each swap under `_lock` and each checking `_halted` (close and return when set).
- **Decref hand-off (`_decref`, `_server_side`, `secure_client`'s `_mark`)**: the handler thread is a
  `_ServerThread`, marked server-side for its whole life, so a proxy finalizer that the cyclic GC runs
  inside the handshake hands off as before. `secure_client` marks the *client* thread for its TLS
  handshake because a server's accept loop waits on it; after the change an in-process server no
  longer waits on it, but a forked manager's accept loop still does, so the mark stays and only the
  docstrings (`secure_client`, `_decref`, the `_server_side` comment, `AGENTS.md` § Queue transport)
  change wording. `tests/test_queue_gc.py` passes against the prototype (below).
- **Thread count per connection is unchanged**: `develop` already starts one handler thread per
  connection, after the handshake; the change starts it before. A pending handshake now holds that
  thread and one descriptor until it completes or the deadline closes it (the GOAL's accepted
  trade-off; a thread or connection cap is a non-goal).

## Shape chosen

`SecureListener` gains `accept_pending()` (TCP accept, blocking mode, `TCP_NODELAY`, records
`last_accepted`, returns `(raw, peer)`, no peer I/O) and `wrap_pending(raw)` (builds or fetches the
server context and returns `wrap_socket(raw, server_side=True, do_handshake_on_connect=False)`,
closing `raw` and re-raising on failure, so the context-failure ERROR-once path is unchanged).
`_InProcessServer.serve` uses them when its listener is a `SecureListener`; a new
`_InProcessServer.handle_pending(raw, peer, accepted)` runs on the handler thread: wrap, swap,
handshake under the deadline (`settimeout(remaining)`, `do_handshake()`, `settimeout(None)`), wrap in
`SecureConnection`, swap, then the existing `handle_request(conn, peer)`. The plain branch of `serve`
is the current code.

## Prototype

A scratch monkeypatch of exactly this shape (never committed) was run against the existing suites:
`tests/test_queue*.py` — 122 passed, 3 skipped (the same as unpatched). With 8 silent connections a
client connected and made its first call in 0.08 s; with a 2 s deadline a silent and a slow peer were
each closed at 2.0 s; handler threads and open descriptors returned to their baseline within 1 s.
