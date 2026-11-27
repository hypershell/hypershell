# 02 — A deterministic regression harness, `free_port()` hardening, and the `master` trial

Scope: how R3 reproduces R1 with no machine setup, how R1 is driven through the real CLI, how R7
changes `tests/conftest.py`, and how R6 is checked without touching `master`.

## The trap: an IPv6 resetter

A test-owned `AF_INET6` socket with `IPV6_V6ONLY=1`, bound to `('::1', P)`, listening; a daemon thread
accepts with a short socket timeout, sets `SO_LINGER` to `(1, 0)` on each accepted socket, closes it
(a TCP reset) and records the peer. It stops on an event and is closed in a `finally`. Binding
`('::1', 0)` failing with `OSError` (or `socket.has_ipv6` false) means no IPv6 loopback → `pytest.skip`.

Port choice: `P = free_port()` (after R7, free on both loopbacks), resetter binds `('::1', P)`, then the
real server binds `127.0.0.1:P`. The two binds cannot conflict (V6ONLY).

## Making resolver order deterministic

The trap only bites when the client's resolver returns `::1` before `127.0.0.1`. That order varies by
OS and `/etc/hosts` (often `::1` first on macOS; `localhost` → `127.0.0.1` only on many Linux CI
images). So the test fixes the order itself: it wraps `socket.getaddrinfo` so that, for host
`localhost` and family `0`/`AF_UNSPEC`/`AF_INET6`, the result list starts with
`(AF_INET6, SOCK_STREAM, IPPROTO_TCP, '', ('::1', P, 0, 0))` (injected when the real resolver lacks it),
followed by the real IPv4 entries. `AF_INET` lookups pass through. This is what "the host resolves to
both IPv6 and IPv4" (R2) means, made machine-independent.

An arming check proves the trap is live before the real assertion: stock
`socket.create_connection(('localhost', P))` under the wrapper must land on the resetter. Without it,
a host where the wrapper did not take effect would let a pre-fix run pass vacuously.

An `AF_INET` socket's `connect` resolves in C and never calls the Python-level `socket.getaddrinfo`,
so the wrapper cannot steer the fixed dial or the cleartext dial; it only exposes a dial that asks for
any family.

## Driving `hs cluster` (R1)

`tests.main()` runs the CLI as a subprocess, so an in-process wrapper does not reach it. The test
writes the same wrapper as a `sitecustomize.py` into `tmp_path` and prepends that directory to
`PYTHONPATH` for the subprocess; console scripts honour `PYTHONPATH`, and it precedes site-packages.
The resetter thread stays in the pytest process. `HYPERSHELL_SERVER_PORT=P`; `LocalCluster` binds the
server to `('localhost', P)` (IPv4) and dials `('localhost', P)` from its client. Parametrize over TLS
on and `--no-tls`; assert exit status 0, every task's result recorded in the database (temp site),
and zero resetter connections. Call `subprocess.run` with `start_new_session=True` and a timeout, not
`tests.main()`, which has no timeout.

Use only helpers present on `master` (`main`, `create_taskfile_echo`, `assert_output`, `temp_site`,
`free_port`). `tests/__init__.py` has develop-only additions that would break R6.

## `free_port()` (R7)

Keep the IPv4 probe (`bind(('localhost', 0))`). Then probe `('::1', port)` with an `AF_INET6`,
`IPV6_V6ONLY` socket: `EADDRINUSE` → draw again (bounded loop, e.g. 32 draws, then raise); any other
`OSError` on the IPv6 side (no IPv6 loopback, `EADDRNOTAVAIL`, `EAFNOSUPPORT`) → accept the port. Split
the IPv4 draw and the IPv6 check into small helpers so a unit test can hold `::1:P`, inject `P` as the
first draw, and assert `free_port()` returns something else. Test-only; `conftest.py` is identical on
`master`.

## Checking R6 on `master` without touching it

A whole-commit `git cherry-pick` of the build commits, or of the eventual squash on `develop`, onto
`origin/master` is not a clean test. Those commits also change `spec/…/TECH.md`, and the squash also
carries `ROADMAP.md` and `issues/`. None of those paths exist on `master`, so a modify/delete conflict
follows. The faithful trial is path-limited:

1. `git worktree add --detach <common-dir parent>/.worktrees/r6-trial-<slug> origin/master`: detached,
   no branch, under the main checkout's ignored `.worktrees/`, not nested in the feature worktree.
2. `git diff origin/develop...HEAD -- src/hypershell/core/queue.py tests/conftest.py
   tests/test_queue_address_family.py | git -C <trial> apply --3way --index`. This is a non-zero exit on
   any conflict.
3. Run the new test file and `tests/test_queue_tls.py` in the trial. Environment: the feature
   worktree's `uv` environment, with `PYTHONPATH=<trial>/src` so `hypershell` imports from the trial
   (assert `hypershell.__file__` is under the trial first). `master` and `develop` differ in
   dependencies only by a docs package and the version string, so no fresh environment is needed, and
   none is fetched from the network.
4. `finally`: `git worktree remove --force <trial>` (`--force` because the trial is dirty by design),
   then `git worktree prune`; confirm it is absent from `git worktree list`.
