# PLAN — A TLS queue server keeps serving clients while a connection is slow in its handshake

> **Status:** Draft for review · **Last updated:** 2026-11-14
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

The in-process accept loop stops running the TLS handshake. It accepts the TCP connection, applies
the open-file reserve, and hands the raw socket to the per-connection handler thread it already
starts; that thread runs the handshake under a socket timeout set to what is left of
`accepted + server.tls.handshake`, which CPython enforces as one absolute deadline (probed on
3.11–3.14), then serves the connection exactly as today. The key travels on `TLSConfig`, is parsed
only by servers, and refuses `0`, negatives and unparseable values at start. The forked manager path,
the plain listener and the wire are untouched. A scratch prototype of this shape passed every
existing queue suite, met R1–R3 on the probes and matched develop's connect latency.

## 2. Design

### 2.1 Listener (`src/hypershell/core/queue.py`)

- `SecureListener.accept()` keeps its current body (inline blocking handshake). The forked
  `SecureManager.start` path (stock `Server.accepter`) and `tests/test_queue_address_family.py` use
  it; the GOAL leaves that path as is.
- New `SecureListener.accept_pending() -> (raw, peer)`: TCP accept, `setblocking(True)`,
  `_no_delay(raw)`, records `last_accepted`; no peer I/O.
- New `SecureListener.wrap_pending(raw) -> ssl.SSLSocket`:
  `self._server_context().wrap_socket(raw, server_side=True, do_handshake_on_connect=False)`; on any
  failure closes `raw` and re-raises (the context-failure ERROR-once behaviour is unchanged).
- A module helper `_complete_handshake(ssl_sock, deadline_at)`: `remaining = deadline_at -
  time.monotonic()`; at or below zero raise `TimeoutError`; else `settimeout(remaining)`,
  `do_handshake()`, `settimeout(None)`.

### 2.2 In-process server (`_InProcessServer`, same file)

- `__init__(registry, address, authkey, serializer, tls)`: when `tls` is given, first
  `self._handshake = handshake_seconds(tls.handshake)` (a `TLSError` becomes `QueueServerError`)
  **before** `super().__init__` binds; plain servers set `None` and never read the key (R4, R6).
  New state: `self._handshake_warned_at: Optional[float] = None`.
- `serve()`: when the listener is a `SecureListener`, `raw, peer = self.listener.accept_pending()`,
  `accepted = time.monotonic()`, `_admits(raw)` (refused → `raw.close()`, at once, R3), add `raw` to
  `_live` under `_lock` (halted → close, break), start `_ServerThread(target=self.handle_pending,
  args=(raw, peer, accepted), name='hypershell-queue-handler')`. Accept errors, the out-of-descriptor
  pause and the plain branch are the current code.
- New `handle_pending(raw, peer, accepted)` on the handler thread:
  1. `ssl_sock = self.listener.wrap_pending(raw)`; failure → DEBUG `Queue server dropped a
     connection: {error!r}` (develop's text), drop `raw` from `_live`, return.
  2. Under `_lock`: swap `raw` → `ssl_sock` in `_live`; if `_halted`, close and return.
  3. `_complete_handshake(ssl_sock, accepted + self._handshake)`. `TimeoutError` → `_closed_pending(peer)`
     (R5 logging), close. Any other exception → develop's DEBUG line, close. `finally` drops the
     socket from `_live` and closes it on any failure, so the thread ends and the descriptor is
     released at once (R3).
  4. `conn = SecureConnection(ssl_sock)`; under `_lock` swap `ssl_sock` → `conn` (halted → close,
     return); then `self.handle_request(conn, peer)` — the existing serve-and-close path.
- `_cut(conn)` gains `isinstance(conn, socket.socket)` → `socket.socket.shutdown(conn, SHUT_RDWR)`, so
  `stop()` also wakes handshakes in progress.
- `_closed_pending(peer)` (R5): always DEBUG `Queue server closed a connection from {host}:{port}
  that did not complete its TLS handshake within {N} seconds`; and, under a small lock, when
  `_handshake_warned_at` is unset or `REFUSAL_WARNING_INTERVAL` old, WARNING `Closing queue
  connections that do not complete their TLS handshake within {N} seconds (server.tls.handshake);
  more within {interval} seconds are logged at DEBUG`. Only the peer address and the deadline are
  formatted; nothing read from the socket.
- Docstrings: `secure_client`, `_decref`, the `_server_side` comment and the class docstring say the
  in-process server runs each handshake on its connection's handler thread under the deadline, and
  that a forked manager's accept loop still runs it inline (why `secure_client` keeps its mark).

### 2.3 Configuration (`core/config.py`, `core/tls.py`, apps)

- `core/config.py` `[server.tls]`: `'handshake': 30,  # Seconds a connection has to complete its
  TLS handshake (duration forms)`. Env `HYPERSHELL_SERVER_TLS_HANDSHAKE` already maps to it.
- `core/tls.py`: `HANDSHAKE_KEY = 'server.tls.handshake'`, `DEFAULT_HANDSHAKE = 30`;
  `TLSConfig.handshake: Union[int, float, str] = DEFAULT_HANDSHAKE` (as configured);
  `from_namespace` copies `ns.get('handshake', DEFAULT_HANDSHAKE)` raw — clients never parse it;
  `handshake_seconds(value) -> int`: `parse_time` (rounded up), `UnitError` →
  `TLSError(f'{HANDSHAKE_KEY}: {error}')`, `<= 0` → `TLSError(f'{HANDSHAKE_KEY} must be greater than 0
  (given {value!r}): expected {TIME_FORMS}')`.
- Early refusal, so each command stops before doing work: `ServerApp.enable_tls`
  (`server.py`), `ClusterApp.enable_tls` (`cluster/__init__.py`) and `RelayApp.check_args`
  (`relay/__init__.py`, when the site server uses TLS) call `handshake_seconds` and raise
  `ArgumentError(str(error))`, as the relay does for `autoscale.period`. `_InProcessServer` is the
  backstop for any other `QueueServer`.

### 2.4 Docs

- `docs/_include/config_param_ref.rst` `[server.tls]`: a `.handshake` entry — seconds a connection has
  to complete its TLS handshake, counted from accept and not extended by data, closed when it
  misses; the duration forms (`30`, `"30s"`, `"1m"`), rounded up; default `30`; must be greater than
  0 (the server refuses to start otherwise); `HYPERSHELL_SERVER_TLS_HANDSHAKE`; ignored by `--no-tls`
  servers; `.. versionadded:: 2.9.0` (no PR number yet; `hs-release` adds it).
- `docs/security.rst` threat-model item 5 and *Built-in TLS → Limitations* item 4: replace "no
  handshake timeout" / "no per-connection handshake timeout" with the deadline (`server.tls.handshake`,
  default 30 s, the server keeps accepting others meanwhile), each with `.. versionchanged:: 2.9.0`;
  every other limitation sentence stays word for word (R9).
- `AGENTS.md` § Queue transport & security: the decref paragraph's "the accept loop runs it inline"
  becomes the handler-thread wording (§2.2).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | §2.2 `serve` only accepts TCP; `handle_pending` runs the handshake on the handler thread |
| R2 | §2.1 `_complete_handshake`: socket timeout = remainder of `accepted + handshake`, one `do_handshake()` (absolute on 3.11–3.14, research/02) |
| R3 | §2.2 `finally` close + thread exit; `_admits(raw)` before the thread; `_live` swaps so `stop()` cuts pending sockets |
| R4 | §2.3 default `30`, `handshake_seconds`, env mapping, refusal in `_InProcessServer.__init__` and the three commands |
| R5 | §2.2 `_closed_pending`: DEBUG per close, rate-limited WARNING naming the key; develop's text for other failures |
| R6 | One code path (`_InProcessServer`) for `hs server`, every `hs cluster` mode, the relay site server; each reads the key through `TLSConfig` from `config.server.tls`; the plain branch of `serve` unchanged and never reads the key |
| R7 | No new per-connection thread, no extra round trip; latency and burst judged by `research/latency.py compare` |
| R8 | No byte on the wire changes (same context, same `wrap_socket`, same framing); 2.8.7 behaviour per §5 |
| R9 | §2.4 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
and again after this design. Touched: §9, §10, §12, and the high-blast-radius files `core/queue.py`,
`core/tls.py`.

- **§9 Queue transport & security** — TLS stays on by default; the server context, `CERT_NONE` server
  side, protocol floor, ciphers and the client's post-handshake fingerprint check are untouched; the
  handshake is the same `wrap_socket` on the same context, only on another thread and with a
  deadline. Auth is untouched (the challenge still runs in `handle_request`). `--no-tls` stays
  `tls=None` and never reads the key.
- **§10 Configuration** — the key is read once from the import-time `config` through `TLSConfig`;
  `0` is refused rather than meaning "unlimited" (a deliberate, documented exception to the `X or
  None` idiom, settled at the GOAL gate). The key name has no underscore, so its env variable works.
- **§12 Conventions** — no CLI option, so no `docs/_include` help snippet or `share/` completion
  changes; the prose pages carry `versionadded`/`versionchanged`; new tests are tagged `@mark.unit`
  or `@mark.integration`; Python 3.11–3.14 behaviour probed; no dependency change.
- **§5/§6 (adjacent)** — no FSM state or shutdown ordering changes; `stop()` keeps its
  never-blocks contract (it only shuts sockets down).
- **AGENTS.md § Queue transport, decref hand-off** — handler threads are `_ServerThread`s (marked);
  `secure_client` keeps its mark; `tests/test_queue_gc.py` passes on the prototype.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Two new `SecureListener` methods beside `accept()` | The in-process loop must not do peer I/O, and the forked path and its tests still call `accept()` | Changing `accept()` itself would alter the forked path (a non-goal) and `test_queue_address_family`'s direct use |
| `TLSConfig.handshake` holds the raw configured value, parsed by servers | A bad server-only key must not stop clients that build `TLSConfig` from the same section | Parsing in `from_namespace` would make `hs client`/`hs submit` refuse a key they never use |
| Refusal checked in three commands as well as in `_InProcessServer` | The relay starts its site server only after reaching upstream, so the backstop alone could report late or never | Backstop only: the relay would sit retrying upstream with a bad key; commands only: a library `QueueServer` would accept a bad value |

## 4. Rabbit holes (resolved)

- Does a socket timeout bound a handshake that keeps receiving bytes? → Yes on 3.11–3.14: one deadline
  per `do_handshake()` ([`research/02-deadline-and-config.md`](research/02-deadline-and-config.md)).
- Does moving the handshake break the decref hand-off or the stop/cut contract? → No, with the `_live`
  swaps and the `_cut` branch; all queue suites pass on a prototype
  ([`research/01-accept-path.md`](research/01-accept-path.md)).
- Will clients pay for it? → No: prototype medians match develop, and a 64-client burst completes 3×
  faster ([`research/03-measurements-and-drives.md`](research/03-measurements-and-drives.md)).
- Can the relay's site-local server be driven? → Yes, with a launcher that delays and points the site
  client at loopback (research/03).

## 5. Risks & open questions

- **Needs the foreman (GOAL wording): R8 and 2.8.7.** A 2.8.x client cannot run tasks against any 2.9
  server, `develop` included: it is refused as too old by the work-request protocol (research/03, run
  on develop). Proposed reading for review and the evidence: a 2.9.0a8 client runs tasks; a 2.8.7
  client connects, completes TLS, authenticates and is refused as too old exactly as on develop. A
  one-line GOAL clarification would make that the contract; otherwise R8 cannot be met as written.
- **Decision for sign-off: early refusal raises `ArgumentError`** (usage-error exit status) in the
  three commands, following the relay's `autoscale.period` precedent; a library `QueueServer` raises
  `QueueServerError`. Alternative: `ConfigurationError` (bad-config exit status).
- **Decision for sign-off: `docs/security.rst` "Monitor connection patterns" bullet.** It says
  HyperShell does not log failed TLS handshakes. After the change a deadline close does log (a
  rate-limited WARNING). Proposed: add one clause naming that warning; R9 only names the two items,
  so this is outside its letter but keeps the page true.
- **Decision for sign-off: R6 drives cover `hs cluster` local mode only.** Remote, SSH and
  autoscaling modes start the same `ServerThread`/`QueueServer` from the same `ClusterApp.enable_tls`
  `TLSConfig`; a unit test pins that `enable_tls` carries the key. Driving SSH needs an SSH server
  the build host lacks.
- **Decision for sign-off: R5 WARNING wording.** It names the deadline and the key, and also the peer
  of the close that triggered it; R5 requires neither more nor less of the WARNING.
- **Risk: thread and descriptor per pending handshake** for up to the deadline — accepted at the GOAL
  gate; the open-file reserve still bounds descriptors.
- **Risk: timing tests on CI.** The deadline tests use a 2 s deadline and assert closure in 2–5 s (the
  GOAL's floor); the responsiveness test's 5 s bound is generous against a ~0.1 s observed time.

## 6. Verification strategy

- **Tests**, appended to `tests/test_queue_tls.py` (existing tests unmodified; the verify checks that
  the diff of the existing suites against the base adds lines only), each red on the base commit and
  green on the branch, the red run saved to `evidence/`:
  responsiveness (8 silent connections, client connect + first call ≤ 5 s, deadline 300 s);
  deadline (2 s: silent peer and a peer sending a real ClientHello a byte at a time, each closed in
  2–5 s; handler-thread count and open descriptors back to baseline within 1 s); stop during a
  pending handshake releases it; configuration (default 30, `30`/`30s`/`1m`/`2.5`, refusal of `0`,
  `-5`, `abc` as `QueueServerError` naming the key and forms, env variable in a subprocess,
  `from_namespace` copies the raw value, the commands' early refusal); logging (caplog: DEBUG with the
  peer and deadline, one WARNING for several closes inside the interval, no peer bytes); plain
  server (a silent connection outlives a 2 s deadline); a 64-client burst. Tests set the deadline by
  assigning `cfg.handshake` on a `TLSConfig` instance, so on the base commit they fail on behaviour,
  not on a constructor argument. The red run uses the base `src/` exported with `git archive` and put
  first on `PYTHONPATH`.
- **CLI drives** (`research/drive.py` in `temp_site.sh`): `server`, `cluster`, `relay` with the
  default deadline (served while a connection is held) and with `--handshake 2` (closed in 2–5 s);
  `refuse`; 2.9.0a8 and 2.8.7 clients from PyPI in scratch environments.
- **Measurement**: `research/latency.py compare` against the base `src/`, three interleaved rounds,
  results saved to `evidence/`.
- **Docs**: the `-W` Sphinx build.
