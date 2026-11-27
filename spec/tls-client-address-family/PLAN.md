# PLAN — TLS client reaches the server on the address family it listens on

> **Status:** Draft for review · **Last updated:** 2026-11-03
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`secure_client` dials with `socket.create_connection`, which tries every address the resolver returns
in order, IPv6 included. The server listens on IPv4 only, and the cleartext client dials IPv4 only.
The fix makes the TLS dial the cleartext dial: an `AF_INET` socket and `connect(address)`, the same as
`multiprocessing.connection.SocketClient`. Everything after the TCP connect is unchanged. A new test
file holds a test-owned `::1` resetter on the server's port number and forces the resolver to offer
`::1` first, so the pre-fix failure reproduces on any host with IPv6 loopback. It covers the bare
transport and `hs cluster`, TLS and cleartext. `free_port()` stops returning port numbers held on
`::1`. This is one function, one test helper and one test file, all of which cherry-pick to `master`.

## 2. Design

### The dial (`src/hypershell/core/queue.py`, `secure_client`)

Replace `raw = socket.create_connection(address)` with an IPv4 stream socket that is set blocking and
connected to `address`. Close it on any exception from `connect` through `wrap_socket`: the existing
`try/except: raw.close(); raise` widens to cover the connect. Add a short comment saying why: the
listener is `AF_INET`-only, and the cleartext client dials `AF_INET`, so the TLS client must not
follow the resolver to IPv6. `address` is not rewritten, so `servername` still defaults to
`address[0]`, the name the user gave. `get_client_context`, `verify_peer_fingerprint`, `SecureConnection`
and the challenge/response stay as they are. `family` stays accepted and ignored, as today. Standard
library only.

Consequences, all of them the cleartext client's behaviour, which R2 and R4 name as the reference:

- `localhost`, or any name with A and AAAA records → the first IPv4 address. Never IPv6 (R1, R2).
- Nothing listening → `ConnectionRefusedError` → same `client.py` mapping and exit status as today (R4).
- A name with no IPv4 address (or a literal `::1`) → `socket.gaierror` at once → `client.py`'s
  `HostAddressInfo` path, as the cleartext client does. No hang (R4).
- A name with several A records → only the first is tried; `create_connection` used to try the rest.
  This matches the cleartext client (decision 1 in the [digest](research/00-digest.md)).

### Regression tests (`tests/test_queue_address_family.py`, new)

Shared pieces, defined in the file:

- **`ipv6_resetter(port)`** (context manager / fixture). It binds an `AF_INET6` + `IPV6_V6ONLY`
  socket to `('::1', port)`, and a daemon thread accepts and resets (`SO_LINGER` 1/0), counting
  connections. It stops via an event with a bounded join, and closes in `finally`. It skips the test
  when `::1` cannot be bound.
- **`ipv6_first_getaddrinfo`**: a wrapper around the real `socket.getaddrinfo` that, for `localhost`
  and a family of `0`/`AF_UNSPEC`/`AF_INET6`, puts `('::1', port)` first, injecting it when it is
  absent. Installed with `monkeypatch` in process. For the CLI drive, its source goes into
  `tmp_path/sitecustomize.py`, prepended to `PYTHONPATH`.
- **Arming check**: under the wrapper, stock `socket.create_connection(('localhost', P))` reaches the
  resetter. This proves the trap is live on this host, so a pass is never vacuous.

Tests, each tagged `unit` or `integration`, with every blocking call bounded (socket timeouts, thread
joins, `subprocess.run(..., timeout=…, start_new_session=True)`):

| Test | Asserts | Red on pre-fix? |
|------|---------|-----------------|
| TLS transport under the trap (`integration`) | `secure_client(('localhost', P))` completes the handshake and auth with a real `SecureListener` on `127.0.0.1:P`, a payload round-trips, resetter count 0 | **yes** (R3) |
| Cleartext transport under the trap (`integration`) | stock `Client`/`Listener` with an authkey, same port setup, round-trip, resetter count 0 | no; it is the reference (R3) |
| `hs cluster` under the trap, parametrized TLS / `--no-tls` (`integration`) | exit 0, every task recorded complete with status 0 in the temp-site database, resetter count 0 | **TLS case yes** (R1) |
| Nothing listening (`integration`) | both transports raise `ConnectionRefusedError` for `('localhost', P)` | no (R4 guard) |
| No IPv4 address (`unit`) | both transports raise `socket.gaierror` for `('::1', P)` within a few seconds | **yes**: pre-fix TLS dials IPv6 (R4) |
| Host-name check uses the given name (`integration`) | a cert whose names are `localhost` only (no `127.0.0.1`); a client context with `check_hostname` on and that cert as CA, `servername` unset (patch `get_client_context` for the test); dialing `('localhost', P)` succeeds. A check against a resolved address would fail | guard (R5) |
| `free_port()` skips a `::1`-held port (`unit`) | hold `::1:P`, inject `P` as the first draw, `free_port() != P`, and the result binds on `::1` | **yes** (R7) |

TLS materials follow `tests/test_queue_tls.py`'s fixture pattern (`ensure_default_materials` for
`localhost`/`127.0.0.1`, `install_process_context`/`clear_process_context`). Use only test helpers
present on `master` (see [research/02](research/02-regression-harness.md)).

### `free_port()` (`tests/conftest.py`)

`free_port()` keeps the IPv4 draw, then checks `('::1', port)` with a V6ONLY bind. On `EADDRINUSE` it
draws again, up to a bound, then raises. Any other IPv6 `OSError` means no IPv6 loopback, and the
port is accepted. The draw and the check are small module-level helpers so the R7 unit test can
inject a draw. The docstring states the IPv6 rule.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | IPv4-only dial in `secure_client`; `hs cluster` test under the trap, TLS and `--no-tls` |
| R2   | IPv4-only dial (never IPv6, the first IPv4 address like `SocketClient`); resetter count 0 in every trap test |
| R3   | `tests/test_queue_address_family.py`: resetter + forced resolver order + arming check; skip without `::1`; cleartext twin; red on the pre-fix code |
| R4   | Unchanged exception types (`ConnectionRefusedError`, `gaierror`) → unchanged `client.py` mapping; refused and no-IPv4 tests |
| R5   | Nothing after the TCP connect changes; `servername` still from `address[0]`; host-name test; `tests/test_queue_tls.py` untouched and passing |
| R6   | Standard library only; P2's path-limited trial apply onto a detached `origin/master` worktree, tests run there; result recorded in `TECH.md` |
| R7   | `free_port()` IPv6 check and redraw; unit test |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(§9, §12; high-blast-radius file `core/queue.py`) and again against this design.

- **§9 TLS on by default:** untouched. `tls=None` still selects the stock client, and nothing about
  `--no-tls` changes.
- **§9 Auth always required:** `answer_challenge`/`deliver_challenge` run unchanged after the
  handshake. No key-policy code is touched.
- **§9 Pickle framing / JSON payloads:** `SecureConnection` framing is untouched (byte-compatible).
- **§9 No mTLS; keep the post-handshake fingerprint check:** `verify_peer_fingerprint` still runs on
  every TLS connection, in the same place.
- **§9 `<auto>` materials / nothing forwarded to clients:** untouched.
- **High-risk `core/queue.py`:** the change sits before `wrap_socket`, so per-process context install
  and framing are not involved. Any review finding here goes to the human gate.
- **§12 Same-commit rules:** no CLI change, so no `docs/_include` or `share/` update. No dependency or
  version change. Standard library only, Python 3.11–3.14. New tests are tagged `unit`/`integration`.
- **§11 Cluster orchestration:** `cluster/local.py` is deliberately not touched (GOAL clarification
  (b)), so client argv and `HOSTNAME` handling are unchanged.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

- *Is the pre-fix failure deterministic enough for a red test?* Yes, once the test owns the resolver
  order. 5/5 failures with the current dial and 0/5 with an `AF_INET` dial, the resetter untouched in
  the fixed case ([research/01](research/01-dial-contract.md)).
- *Which error class does the red case raise?* It varies (`ConnectionResetError` /
  `BrokenPipeError`), so tests assert success instead ([digest](research/00-digest.md) decision 2).
- *Can an in-process resolver patch reach `hs cluster`?* No, because the CLI is a subprocess. A
  test-written `sitecustomize.py` on `PYTHONPATH` does ([research/02](research/02-regression-harness.md)).
- *Does the fix change `hs client` errors?* Only for a name with no IPv4 address, where TLS now fails
  the way cleartext already does. R4 requires exactly that ([research/01](research/01-dial-contract.md)).
- *Can R6 be shown by a plain cherry-pick?* No. Whole commits carry `spec/`, `ROADMAP.md` and
  `issues/` changes that conflict as modify/delete on `master`. The trial is path-limited
  ([research/02](research/02-regression-harness.md)).

## 5. Risks & open questions

- **Multi-A-record narrowing (for the plan-gate judge).** TLS clients stop falling back to a second
  IPv4 address when the first refuses. The cleartext client never did, and R2 asks for parity, so this
  plan accepts it. The alternative is a `getaddrinfo(AF_INET)` loop. It keeps the fallback but can
  reach a different endpoint than cleartext.
- **The 2.8.4 port is path-limited.** The squash commit on `develop` will not cherry-pick cleanly to
  `master` as a whole, because of `spec/`, `ROADMAP.md` and `issues/`. Whoever cuts 2.8.4 picks the
  three code paths: `queue.py`, `conftest.py` and the new test. P2 proves that set applies and passes.
- **`sitecustomize.py` shadowing.** If an environment already relies on a `sitecustomize`, the test's
  copy on `PYTHONPATH` shadows it for that one subprocess only. Acceptable for a test.
- **Hosts without IPv6 loopback** skip the trap tests (R3 allows this), so stock Linux CI without `::1`
  covers only the R4/R5 guards. The red test is meant for developer machines and CI images with `::1`.
- **`free_port()` redraw bound.** A host with nearly every ephemeral port held on `::1` would exhaust
  the bound and raise. That is unrealistic, and it is loud rather than flaky.

## 6. Verification strategy

- **Red first (P1):** write `tests/test_queue_address_family.py` and the `free_port()` test, run them
  on the unchanged dial, and record in the commit body that the R1/R3/R4-no-IPv4/R7 cases fail and
  the cleartext and refused cases pass. Then make the two source changes and re-run.
- **Phase gate (P1):** the new file, `tests/test_queue_tls.py` and `tests/test_cluster.py` with `-rs`
  (skips visible), then the full suite with `-n auto`, since `free_port()` feeds every test. Run in a
  new process group with a kill timer. The `hs cluster` drive lives inside the new tests against a
  temp site, with a resetter on the port, which a bare `temp_site.sh` drive cannot arrange.
- **R6 gate (P2):** the path-limited trial on a detached `origin/master` worktree. The new file and
  `test_queue_tls.py` must pass there. The worktree is removed in `finally`, and the method and result
  are recorded in `TECH.md`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
