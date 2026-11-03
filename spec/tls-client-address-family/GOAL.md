# GOAL — TLS client reaches the server on the address family it listens on

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** tls-client-address-family
- **kind:** fix
- **appetite:** small · one client-side change plus a deterministic regression test; lands on
  `develop` and is cherry-picked to `master` for a 2.8.4 patch (see Clarifications)

## Problem

With TLS on (the default), a HyperShell client can connect to a process that is not the HyperShell
server and fail the run. `hs cluster` exits with status 5, and the log shows a
`ConnectionResetError` raised during the TLS handshake. The server never sees the connection. The
same run with `--no-tls` on the same port succeeds.

The trigger is an unrelated local process holding an IPv6-only listener on the same port number as
the server. The server's TLS listener (`SecureListener`, `src/hypershell/core/queue.py:139-154`)
binds IPv4 only. The TLS client (`secure_client`, `queue.py:206-216`) resolves the server's host
name with no address-family restriction, so when `localhost` resolves to `::1` first it can connect
to the other process's IPv6 listener instead. That process resets the TLS ClientHello. On macOS the
order `getaddrinfo('localhost')` returns is not fixed and often puts `::1` first. The cleartext client
(the stock `multiprocessing` client) dials IPv4 only and is unaffected.

Evidence, from a foreman-run diagnosis on a developer machine:

- With `HYPERSHELL_SERVER_PORT` set to a port held that way, `hs cluster` exited 5 with
  `ConnectionResetError` 6 times out of 6 on `develop` (`b31c66e`) and on the zone queue-primitive
  branch (`ec75169`, `7dd85c1`). Control ports passed, and `hs cluster --restart --no-tls` on the
  held port passed 4 out of 4.
- A socket logger recorded the failing connection as AF_INET6 from `::1` to `::1:<port>`.
- In the test suite this shows as a rare flake, about 1 in 1,500 local cluster runs.
  `tests/conftest.py:free_port()` binds `127.0.0.1:0`, so the IPv4 port is free, but it can still
  pick a port number that something else holds on `::1`.

**Pre-existing** since the TLS transport rewrite (`589f6fe`), first released in 2.8.0. The same
listener and dial code is on `master` (2.8.3): `queue.py` lines 139-242 are identical on `master` and
`develop`. Stock CI runners are unlikely to hit it. Developer machines and user hosts with such a
listener can. A remote server host name that has both IPv6 and IPv4 addresses can meet the same
mismatch whenever something other than HyperShell answers on that port over IPv6.

## Outcome / vision

For a given host and port, the TLS client connects to the same endpoint as the cleartext client: the
server's IPv4 listener. An IPv6-only listener on the same port number, owned by any other process,
cannot capture a HyperShell connection. TLS-on and `--no-tls` runs behave alike. Everything else
about the TLS client stays as it is: certificate and host-name verification, fingerprint pinning,
the auth handshake, and what the user sees when no server is listening. The fix is small enough, and
free enough of 2.9-only code, to ship in a 2.8.4 patch.

## Acceptance criteria (the contract)

- **R1** — WHILE another process holds an IPv6-only listener on `::1` at the port number of a
  HyperShell server listening on IPv4 loopback, WHEN `hs cluster` runs with TLS on that port, it SHALL
  complete with exit status 0 and record every task's result, as the same run with `--no-tls` does.
- **R2** — WHEN a TLS client (`hs client`, or the client inside `hs cluster`) is given a server host
  name that resolves to both IPv6 and IPv4 addresses, it SHALL connect to the same endpoint the
  cleartext client reaches for that host and port, and SHALL NOT connect to an IPv6 endpoint. This
  holds for `localhost` and for remote host names.
- **R3** — An in-repo regression test SHALL reproduce the R1 condition deterministically, with no
  special machine setup. The test opens its own IPv6-only listener on `::1` that accepts and resets
  connections, on a port it also binds on IPv4 for a real TLS server, and asserts that the TLS client
  completes its handshake with the real server. It SHALL fail on the pre-fix code and pass after.
  IF the host has no IPv6 loopback, THEN it SHALL skip rather than fail. The same condition SHALL also
  be exercised on the cleartext transport, so the two transports are shown to behave alike.
- **R4** — IF no server is listening at the given host and port, THEN `hs client` and `hs cluster`
  SHALL fail with the same exit status and the same kind of message as today. IF the host name has no
  IPv4 address, THEN the TLS client SHALL fail promptly, as the cleartext client does, and SHALL NOT
  hang.
- **R5** — Certificate and host-name verification, `servername` handling, fingerprint pinning
  (`verify_peer_fingerprint`) and the auth challenge SHALL behave as today. Where the client checks a
  host name, it SHALL check the name the user gave, not a resolved address. The existing
  `tests/test_queue_tls.py` suite SHALL pass unchanged.
- **R6** — The client change SHALL use only the standard library and nothing introduced after 2.8.3,
  so that it cherry-picks onto `master` without conflict and the R3 test passes there. `TECH.md` SHALL
  state whether that was checked by a trial cherry-pick or by diff inspection.
- **R7** — `tests/conftest.py:free_port()` SHALL NOT return a port number that is bound on `::1`
  when the host has IPv6 loopback, so the rest of the suite never draws a port something else holds
  there. Test-only; it SHALL cherry-pick onto `master` with the client change.

## Non-goals (no-gos)

- **IPv6 server support (a dual-stack or IPv6 listener).** The server stays IPv4-only, and the client
  is brought in line with it. Deferred as [`issues/ipv6-server-listener.md`](../../issues/ipv6-server-listener.md).
- **Changing the cleartext transport.** It already dials IPv4 and is the reference behaviour here.
- **Making the rc-5 catch-all error more specific** (for example, naming the endpoint a handshake was
  reset by). Worth doing, but it is not this fix.
- **Detecting or warning about other listeners on the server's port.**
- **Carrying the fix into the zone branches.** `feature/zone-queue-primitive` and
  `feature/zone-dispatch` move `secure_client` but keep the same dial (`create_connection` at
  `queue.py:262`/`:265`). They pick the fix up when they next merge `develop`. Their cycles own that
  merge.

## Clarifications

- **Q:** Appetite? — **A:** `small`, the default for `kind: fix` and the foreman's call. The defect is
  one dial site and the evidence is already gathered (foreman, as judge under the maintainer's
  delegation, 2026-11-03).
- **Q:** Branch base and release path? — **A:** Branch from `origin/develop` (`b31c66e`), land on
  `develop`, then cherry-pick to `master` for a 2.8.4 patch. Hence R6 (foreman, 2026-11-03).
- **Q:** Is this a security-lane item? — **A:** No. Its observed effect is a failed connection, and
  the server side is unaffected. The fix is an ordinary public change (shaping, 2026-11-03; flagged
  for the judge to confirm).
- **Q:** Should the test-side hardening ride along? Two candidates, both small: (a)
  `tests/conftest.py:free_port()` rejects a port it cannot also bind on `::1`; (b)
  `src/hypershell/cluster/local.py` (~239) has its client dial the server's bound address rather than
  `'localhost'`. — **A (2026-11-03, maintainer's delegate at the sign-off gate):** (a) only, as **R7**; (b) is
  left out. **Shaper's recommendation, adopted:** include (a) and leave out (b). (a) is test-only, a few lines, and cherry-picks
  cleanly; it keeps the rest of the suite from drawing a port number something else holds on `::1`,
  while R3 still creates that condition on purpose, so the regression stays covered. (b) changes
  product code on both release lines and is redundant once R2 holds, since the client then dials
  IPv4 for `localhost` regardless. If (a) is taken it becomes **R7** — "`free_port()` SHALL NOT return
  a port number that is bound on `::1` when the host has IPv6 loopback."

## Related materials

- Source: `src/hypershell/core/queue.py` (`SecureListener`, `secure_client`, `listener_client`),
  `src/hypershell/client.py` (exception mapping: `ConnectionRefusedError`, `ConnectionResetError`),
  `src/hypershell/cluster/local.py` (server and client both on `('localhost', port)`),
  `tests/conftest.py` (`free_port`), `tests/test_queue_tls.py`
- Origin: the TLS transport rewrite `589f6fe` (2.8.0)
- Deferred: [`issues/ipv6-server-listener.md`](../../issues/ipv6-server-listener.md)
- Downstream: `feature/zone-queue-primitive`, `feature/zone-dispatch` (same dial code, moved)
