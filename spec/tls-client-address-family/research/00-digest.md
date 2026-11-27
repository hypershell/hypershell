# 00 — Research digest

`core/queue.py` is on the high-blast-radius list, so `hs-plan` Step 3 calls for a fan-out regardless
of the small appetite. The root cause was already diagnosed (GOAL), so the fan-out is two briefs,
written in sequence by the planner, that pin the touched contracts before design:

- [`01-dial-contract.md`](01-dial-contract.md): the TLS dial vs the cleartext dial, error mapping,
  host-name verification, `master` parity.
- [`02-regression-harness.md`](02-regression-harness.md): the IPv6 resetter, a forced resolver order,
  the CLI drive, `free_port()`, and the `master` trial.

## Decisions

1. **Dial like the cleartext client.** `secure_client` opens `socket.socket(AF_INET, SOCK_STREAM)`,
   `setblocking(True)`, `connect(address)`, closing the socket on any failure. This is exactly
   `multiprocessing.connection.SocketClient`'s dial. It is chosen over a `getaddrinfo(…, AF_INET)` loop,
   which would also try a second and later A record. That would keep `create_connection`'s multi-address
   fallback, but could then reach an endpoint the cleartext client does not, and R2 says "the same
   endpoint". `address` is passed through untouched, so `servername` stays the user's name.
2. **Assert success, not an error class.** The pre-fix failure surfaces as `ConnectionResetError` or
   `BrokenPipeError` depending on the reset race. The red test asserts the handshake (and a round-trip)
   completes and that the resetter saw zero connections.
3. **Force the resolver order in the test,** and prove it took effect with an arming check
   (`create_connection` lands on the resetter). This is what makes R3 deterministic on any host. In
   process for the unit test; via a test-written `sitecustomize.py` on `PYTHONPATH` for the CLI drive.
4. **One new test file,** `tests/test_queue_address_family.py`, so `tests/test_queue_tls.py` stays
   unchanged (R5). It uses only test helpers present on `master` (R6).
5. **`free_port()`** draws again on `EADDRINUSE` at `('::1', port)`, and treats any other IPv6 error as
   "no IPv6 loopback" (R7). Split into helpers for a deterministic unit test.
6. **R6 by trial, path-limited:** a 3-way apply of the three code paths onto a detached `origin/master`
   worktree, the new tests and `test_queue_tls.py` run there, and the worktree removed in `finally`. A
   whole-commit cherry-pick conflicts on `spec/`, `ROADMAP.md` and `issues/`, which `master` does not
   have, so it says nothing about the code.

No cross-brief contradictions.
