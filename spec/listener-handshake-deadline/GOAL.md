# GOAL — A TLS queue server keeps serving clients while a connection is slow in its handshake, and closes one that misses a deadline

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** listener-handshake-deadline
- **kind:** fix
- **appetite:** small  ·  *One listener path in `core/queue.py`, one new `[server.tls]` key, two docs
  pages and tests. Promoted in part from a pre-shaped seed in the private security lane; the rest of
  that seed is not this cycle (see Non-goals and Clarifications).*

## Problem

A TLS queue server puts no time limit on a connection's TLS handshake. `docs/security.rst` already
says so (threat model item 5, and *Built-in TLS → Limitations* item 4: "The queue sets no
per-connection handshake timeout"), and says that a peer which stalls before authenticating can
consume server resources. Connections like that happen in ordinary operation: a client host that
hangs part-way through connecting, a path that drops mid-handshake, a health check or other tool
that opens the queue port and never completes a TLS handshake. The server holds such a connection for as long as the
peer keeps it open, and nothing guarantees or tests that other clients are served in the meantime.

HyperShell clusters connect and reconnect clients continuously (scale-up, reconnect after a lost
heartbeat, relays), so a queue server has to stay available to them whatever one connection does.

Pre-existing: on `develop` (`6da633d`, 2.9.0a8) and on the 2.8 line. The cleartext (`--no-tls`)
path has no TLS handshake and is not part of this fix. Detail for maintainers is in the private
security lane (`.security/`, outside the repository).

## Outcome / vision

A TLS queue server — `hs server`, the server inside `hs cluster`, and a relay's site-local server —
keeps accepting and serving clients while any number of connections are slow or silent in their TLS
handshake, and closes a connection that has not completed its handshake within a deadline
(`[server.tls] handshake`, default 30 s), releasing everything it held. Clients of every supported
version connect exactly as before, and the security docs say the deadline exists.

## Acceptance criteria (the contract)

- **R1** — WHILE one or more connections to a TLS queue server are open but have not completed their
  TLS handshake, the server SHALL accept, complete the handshake with, authenticate and serve any
  other client. With 8 such connections open and the deadline set far above the test's duration
  (e.g., 300 s), a client that connects alongside them SHALL complete its connect and its first queue
  call within 5 s.
- **R2** — IF a connection to a TLS queue server has not completed its TLS handshake within the
  handshake deadline, THEN the server SHALL close it. The deadline SHALL be measured from when the
  connection was accepted, and data the peer sends before its handshake completes SHALL NOT extend
  it. With a 2 s deadline, a connection that sends nothing, and one whose handshake progresses only
  slowly, a little at intervals shorter than the deadline, SHALL each be closed by the server between
  2 s and 5 s after it connected.
- **R3** — WHEN the server closes a connection under R2, no server thread or open descriptor SHALL
  remain for that connection within 1 s of the close. A connection that has not completed its
  handshake SHALL count toward the existing open-file reserve exactly as on `develop`: one that would
  use a reserved descriptor is refused at once.
- **R4** — The handshake deadline SHALL be read from `[server.tls] handshake`: a duration in any form
  the duration parser accepts (`30`, `30s`, `1m`), rounded up to whole seconds as other
  durations are, default `30s`, and settable as `HYPERSHELL_SERVER_TLS_HANDSHAKE`. IF the value does
  not parse or is not greater than zero, THEN a TLS queue server SHALL refuse to start, with an error
  that names `server.tls.handshake` and the accepted forms.
- **R5** — WHEN the server closes a connection under R2, it SHALL log one DEBUG line naming the peer
  address and the deadline; it SHALL also log one WARNING naming the deadline and the
  `server.tls.handshake` key for the first such close, and at most one more per 60 s after it (the
  interval of the existing open-file refusal warning), the rest at DEBUG. No log line SHALL contain
  any data the peer sent. A handshake that fails for any other reason SHALL be logged as on `develop`.
- **R6** — R1–R5 SHALL hold for every TLS queue server HyperShell starts: `hs server`, the server of
  `hs cluster` (local, remote, SSH and autoscaling), and the site-local server of `hs relay`, each
  reading `server.tls.handshake` from the configuration it runs with. A plain (`--no-tls`) server
  SHALL behave exactly as on `develop` and SHALL ignore the key.
- **R7** — Normal TLS operation SHALL NOT regress: the existing queue, TLS, open-file-limit and
  garbage-collection test suites pass unchanged; 64 clients connecting to one TLS server at once with
  the default deadline all connect and complete a queue call; and on one host the median time for a
  client to connect and complete its first queue call, over at least 50 sequential connections, is
  within 10 % or 5 ms (whichever is larger) of `develop`'s.
- **R8** — A client of 2.9.0a8, or of the latest 2.8 release (2.8.7 at shaping), SHALL connect,
  authenticate and run tasks against the fixed server with no change to its own configuration. The
  fix SHALL NOT change what is sent over the wire.
- **R9** — `docs/security.rst` (threat model item 5, *Built-in TLS → Limitations* item 4) SHALL state
  that a TLS queue server closes a connection that does not complete its handshake within
  `server.tls.handshake`, and SHALL leave every other limitation it states today as it is. The
  configuration reference (`docs/_include/config_param_ref.rst`) SHALL document the key, its forms
  and its default.

**Ruled at the GOAL gate** (2026-11-14): approved by the foreman, gate judge under the maintainer's
mandate, on the maintainer's decision to take this cycle before 2.9.0. All six shaping decisions are
confirmed: configurable `[server.tls] handshake`, default 30 s, no off value; the logging rule; the
thread and descriptor bound through the existing open-file reserve, with a connection or thread cap a
non-goal; the test numbers as floors; appetite small. The contract is locked; `hs-plan` designs
against it.

**Ruled at the plan gate** (2026-11-14, foreman): R8 for a 2.8.7 client reads as "connects,
completes TLS and authenticates, then is refused as too old, exactly as on `develop`". Every 2.9
server refuses a 2.8.x client for its missing work-request protocol, and this cycle does not change
that.

**Ruled at review cycle 1** (2026-11-14; the maintainer signed off in session): R4's accepted range
is bounded above. A `[server.tls] handshake` value greater than one day is refused at start, as a
value of 0 or below is, with an error naming the key. The server does not stop accepting connections
when starting the handler for one connection fails: that connection is closed and logged, and the
server keeps accepting.

## Evidence plan

- **Unit/integration tests** (in the queue TLS or limits suite), each **red on `develop`** and green
  on the branch, with the red run recorded under `spec/listener-handshake-deadline/evidence/`:
  - *Responsiveness* (R1): open 8 TCP connections to an in-process TLS server that send nothing; a
    real `QueueClient` connects alongside them and completes a call within 5 s. On `develop` the
    client does not.
  - *Deadline* (R2, R3): with `handshake = 2`, a connection that sends nothing and one whose handshake
    progresses only slowly are each closed in 2–5 s; the server's handler-thread and
    descriptor counts return to their baseline.
  - *Configuration* (R4): default, each accepted form, and refusal of `0`, a negative and an
    unparseable value; the environment variable reaches the key.
  - *Logging* (R5): caplog shows the DEBUG line with the peer address, one WARNING for several closes
    inside 60 s, and no peer bytes.
  - *Plain unchanged* (R6): a silent connection to a `--no-tls` server is not closed by any deadline.
- **CLI drive** (R6, R8): `hs server` and `hs cluster` with TLS, and an `hs relay` site-local server,
  each keep serving a real client while a silent connection is open; 2.9.0a8 and 2.8.7 clients
  (installed from PyPI into a scratch environment) run tasks against the branch's server.
- **Measurement** (R7): the 64-client burst, and the sequential connect-latency median on `develop`
  and the branch, recorded in `evidence/`.

## Non-goals (no-gos)

The first four remain as `docs/security.rst` *Limitations* item 4 states them, and are tracked
separately.

- A cap on concurrent connections or server threads, or a per-peer connection limit.
- Time limits after the handshake completes — authentication, idle connections, a request that never
  finishes — on TLS or plain servers.
- A cap on the size of an inbound message.
- Rate limiting or backoff on failed authentication.
- A handshake timeout on the client side (a client connecting to a server that is slow).
- The generic forked-manager path (`SecureManager.start`), which no `hs` command serves through. It
  must keep working (its tests pass) but need not meet R1–R5.
- A command-line option for the deadline; the configuration key and its environment variable are
  the interface.
- Any change to the plain listener or to the wire protocol.

## Clarifications

- **Q:** What does this cycle take from the private seed? — **A:** The handshake deadline only. The
  seed's other parts stay in the private lane as their own work. (maintainer's decision via the
  foreman, resolved 2026-11-14)
- **Q:** Fixed or configurable deadline, and what default? — **A:** Configurable, `[server.tls]
  handshake`, default 30 s. The key sits under `[server.tls]` because only TLS servers have a
  handshake, and `hs relay`'s site-local server already reads `[server.tls]`. It is one word because
  an environment variable cannot reach a key whose own name has an underscore
  (`issues/env-config-key-mapping.md`). The default is generous on purpose: R1 keeps the server
  responsive whatever the deadline, so the deadline only bounds how long a stalled connection holds
  its resources, and a short one could drop legitimate clients when hundreds connect at once to a
  loaded host. (shaping decision, 2026-11-14; signed off by the foreman at the GOAL gate)
- **Q:** Can the deadline be turned off with `0`? — **A:** Proposed: no. `0`, negative or
  unparseable values refuse to start (R4), because an unbounded handshake is the behaviour being
  removed. (shaping decision, 2026-11-14; signed off by the foreman)
- **Q:** At what level is a closed handshake logged? — **A:** Proposed: DEBUG for each, with the
  peer address; a rate-limited WARNING, at the open-file refusal warning's interval, so an operator
  sees it without repeated closes filling the log; never peer data (R5). (shaping decision, 2026-11-14;
  signed off by the foreman)
- **Q:** Does serving handshakes concurrently trade one problem for another? — **A:** If the plan
  serves pending handshakes concurrently, each holds one server thread and one descriptor until it
  completes or the deadline closes it. R3 bounds how long, and the existing open-file reserve bounds
  how many descriptors connections can take. A cap on concurrent connections or threads is a
  Non-goal. (shaping decision, 2026-11-14; signed off by the foreman)
- **Q:** Are the test numbers (8 stalled connections, 5 s, 64-client burst, 10 % / 5 ms latency
  tolerance, 2–5 s close window) right? — **A:** Proposed as written; `/hs-plan` may tighten them
  but not loosen them without a GOAL edit. (shaping decision, 2026-11-14; signed off by the foreman)
- **Q:** Do plain servers change? — **A:** No (R6). They have no TLS handshake. (resolved
  2026-11-14)
- **Q:** Appetite? — **A:** Small: one listener path, one key, two docs pages, tests. (shaping
  decision, 2026-11-14; signed off by the foreman)

## Related materials

- Seed: private, `.security/issues/listener-timeouts-and-caps.md` (gitignored; this cycle takes one
  part of it).
- Code (`develop` `6da633d`): `src/hypershell/core/queue.py` — `SecureListener.accept`,
  `_InProcessServer.serve`, `_InProcessServer._admits` (open-file reserve), `REFUSAL_WARNING_INTERVAL`;
  `secure_client` and `_decref`, whose docstrings reason about where the server runs the handshake
  for the in-process decref hand-off (`tests/test_queue_gc.py` guards it).
- `src/hypershell/core/config.py` (`[server.tls]` defaults), `src/hypershell/core/tls.py`
  (`TLSConfig`, `from_namespace`), `src/hypershell/core/types.py` (`parse_time`).
- Relay site-local server: `src/hypershell/relay/site.py` (built through `AutoScalingCluster`, so
  `QueueServer`), `src/hypershell/relay/__init__.py` (reads `config.server.tls`).
- Tests: `tests/test_queue_tls.py`, `tests/test_queue_limits.py`, `tests/test_queue_gc.py`.
- Docs: `docs/security.rst`, `docs/_include/config_param_ref.rst`.
- `issues/env-config-key-mapping.md` (why the key has no underscore).
