# REVIEW — A TLS queue server keeps serving clients while a connection is slow in its handshake

> Adversarial QA by `hs-review`, run in an isolated context. The correctness pass was delegated to
> a fresh reviewer that saw [`GOAL.md`](GOAL.md) with its gate rulings,
> `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, the AGENTS.md invariants, the review
> rubric and the runnable repo. It did not see `PLAN.md`, `TECH.md`, `META.md`, `research/*.md` or
> `evidence/`. **Deviation (foreman's instruction):** it was also given
> `git diff origin/develop...HEAD -- 'spec/*/research/*.py'`, the drive scripts the phase `verify:`
> commands run, so the evidence harness was graded too (finding F3). It could also read the
> maintainer's private note on the original problem, which sits outside the repository, to judge
> whether the fix closes it. Every finding below cites an executed command.

- **Reviewed commit:** f75d5e6  ·  **Base:** develop (`origin/develop` = `6da633d`, 2.9.0a8; merge-base equals it, so the branch merges cleanly)  ·  **Date:** 2026-11-14
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 (escalate to the human on non-convergence)
- **Contract drift:** one post-shape `GOAL.md` commit, `e77edc5`, which records the plan-gate ruling
  on R8 for a 2.8.7 client. It is legitimate, and this review grades against it.

## Verification run

All runs were on macOS (arm64). CLI drives used throwaway sites (`.agents/factory/bin/temp_site.sh`),
probes ran from the scratch directory, and every process started was stopped by its recorded PID. An
unsandboxed `ps` check confirmed none remain.

- **Target suites** (`uv run pytest -q tests/test_queue_tls.py tests/test_queue_limits.py
  tests/test_queue_gc.py tests/test_queue.py`):
  - 3.14: 146 passed, 3 skipped.
  - 3.12 and 3.11 (throwaway environments via `UV_PROJECT_ENVIRONMENT=$TMPDIR/…`): 149 passed and
    145 passed / 4 skipped. `test_queue_limits.py` needs `hs` on `PATH` there; with it set, 5 passed
    each.
  - 3.13: no environment could be built offline. The deadline behaviour was checked on 3.13.16
    directly (see the R2 drive below).
- **Full suite**, 3.14 (`uv run pytest -q -rs`): 2678 passed, 109 skipped.
- **Red on develop:** develop's `src` (`git archive 6da633d src`) with the branch's
  `tests/test_queue_tls.py`, run via `PYTHONPATH`, fails the responsiveness, deadline (silent and
  slow), stop, reserve-at-once and logging tests. It passes the plain, burst and
  other-failure tests, which guard against regressions.
- **R1 drive.** 8 silent and 8 slowly progressing connections, deadline 300 s. A real client
  connected and made its first call in 0.080 s (3.11), 0.081 s (3.12) and 0.077 s (3.14). Develop
  gave no result within 8 s.
- **R2 drive.** Deadline 2 s, against these peer types:
  - silent
  - a ClientHello trickled one byte per 0.25 s
  - the same at one byte per 1.5 s
  - half a ClientHello
  - a full ClientHello then silence

  Each was closed at 2.0 s on 3.11, 3.12 and 3.14, and handler threads and descriptors were back at
  their baseline within 0.00 s. The branch's `_complete_handshake`, run against the same peer types
  on 3.11.18, 3.12.13, 3.13.16 and 3.14.6, raised `TimeoutError` (errno None) at 2.0 s every time.
  Develop closed none of them.
- **Stop races.** Stopping the server before the wrap, after the wrap and after the handshake: the
  peer saw the close each time, with handler threads at 0 and descriptors back at their baseline.
- **Logging.** With a 1 s deadline and three peers, the log had:
  - three DEBUG lines with `127.0.0.1:<port>` and the deadline
  - one WARNING naming `server.tls.handshake`
  - no line containing a marker the peer sent as its server name

  A non-TLS peer was logged as `Queue server dropped a connection: SSLError(…)`, the same as on
  develop.
- **Configuration values.**
  - Refused with `TLSError` naming the key and the accepted forms: `inf`, `nan`, `True`, `False`,
    `None`, `-0.0`, `'-5'`, `1e300`, `''` and `'<none>'`.
  - Accepted and rounded up: `2.5` → 3, `'0.001'` → 1, `' 30 '` → 30, `'1:30'` → 90.
  - `HYPERSHELL_SERVER_TLS_HANDSHAKE` reaches the running server: the drive with a 2 s deadline
    closed the held connection at 2.0 s.
- **CLI drives** (`temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py" …`).
  All of these passed:
  - `server`, `cluster` and `relay`, each with the default deadline and with `--handshake 2`
  - `refuse`: exit 2 in `hs server`, `hs cluster` and `hs relay` for each of `0`, `-5` and `abc`
  - `server --client` with the 2.9.0a8 client
  - `server --client` with the 2.8.7 client and `--expect-too-old`. That client exits 5, and the
    server logs `Client too old (no work-request protocol)`, on both develop and the branch.
- **R7 latency and burst** (`spec/listener-handshake-deadline/research/latency.py compare <develop src> …`):
  - Sequential connect and first call, median over the runs: develop 34.26 ms, branch 34.17 ms
    (allowed 39.26 ms). PASS.
  - 64-client burst: 64/64 in all six runs (branch 0.53 s, develop 1.73 s).
- **Forked manager path.** `test_secure_manager_subprocess_roundtrip` passes.
- **Docs.** `UV_PROJECT_ENVIRONMENT=$TMPDIR/… uv run --python 3.12 --group docs sphinx-build -W
  --keep-going -q -E -b html docs <scratch>` exited 0. A man build (`-b man`) to scratch was
  inspected for the note under "Observations".
- **Merge.** `git fetch origin develop`: `origin/develop` is still `6da633d`, the merge-base, so the
  branch merges cleanly.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/queue.py`: `SecureListener.accept_pending`, the pending branch of `_InProcessServer.serve`, `handle_pending` | R1 drive on 3.11/3.12/3.14 (~0.08 s); new test green on branch, red on develop | ✅ |
| R2 | `core/queue.py`: `_complete_handshake` (one `do_handshake`, timeout = what is left of the deadline from accept) | R2 drive (5 peer types, 2.0 s, 3.11–3.14); new tests red on develop | ❌ for deadlines above about 24.8 days (F1) |
| R3 | `core/queue.py`: `handle_pending`'s `finally`, `_admits` on the raw socket, `_cut` on raw and wrapped sockets | Thread and descriptor counts back to baseline at once; stop races; reserve test red on develop, green on branch | ✅ |
| R4 | `core/tls.py` `handshake_seconds`, `core/config.py` default, checks in `server.py`, `cluster/__init__.py`, `relay/__init__.py`, `QueueServerError` in `_InProcessServer` | Value table above; `refuse` drive (exit 2 ×3 commands); env var drive | ❌ partly: F1 (large values), F2 (refusal in a server made plain by configuration) |
| R5 | `core/queue.py` `_closed_pending` | Logging drive; `test_deadline_closes_are_logged_without_peer_data`; other failures logged as on develop | ✅ |
| R6 | All three commands build `QueueServer` with a `TLSConfig` carrying the key | `server`/`cluster`/`relay` drives, default and 2 s; `--no-tls` with `abc` starts; plain silent connection not closed | ✅ for TLS servers; ❌ plain-by-configuration (F2); `hs server` deadline drive does not discriminate (F3) |
| R7 | No existing test edited (additions only in `tests/test_queue_tls.py`) | Suites above; full suite; latency and burst comparison | ✅ |
| R8 | No client or wire-format change | 2.9.0a8 and 2.8.7 client drives; parity with develop for 2.8.7 | ✅ |
| R9 | `docs/security.rst` threat item 5, Limitations item 4, the monitoring bullet; `docs/_include/config_param_ref.rst` `.handshake` | Read against develop: other limitations unchanged, one neutral clause in the monitoring bullet, `versionchanged`/`versionadded` present; `-W` build clean | ✅ |

Unmapped changes (possible scope creep): none. The AGENTS.md paragraph, the raw-socket branch in
`_cut`, the listener's context lock and the docstring edits all serve R1–R3 or keep the invariants
accurate.

**TLS posture** is unchanged: `build_server_context` is untouched, and the same context wraps the
socket, now with `do_handshake_on_connect=False` and an explicit handshake. Verify mode,
certificate, key, CA, fingerprint, minimum version and ciphers are all as before. **Plain servers**
take the unchanged code path, except for the start-time check in F2.

## Findings

### F1 [MEDIUM/CONFIRMED] A deadline above about 24.8 days is not honored: never enforced, or enforced far earlier than configured
- **Where:** `src/hypershell/core/queue.py:581` (`HANDSHAKE_TIMEOUT_CEILING = 1e9`), `:597`
  (`settimeout(min(remaining, HANDSHAKE_TIMEOUT_CEILING))`), `:730` (`accepted + self._handshake`);
  `src/hypershell/core/tls.py:159` (`handshake_seconds`, no upper bound).
- **Failure scenario:** CPython's `ssl` module waits for the handshake in milliseconds held in a C
  `int`, which wraps above 2147483647 ms, about 24.8 days. The ceiling of 1e9 s is far above that.
  The result depends on the configured value:
  - `30d` wraps to a negative wait, so the connection is never closed: no deadline at all.
  - `4294968` s (about 49.7 days) wraps to under a second, so legitimate clients are closed almost
    at once.
  - A value too large for a float (an integer of a few hundred digits; TOML accepts one) passes the
    start-time check. Every connection is then dropped with
    `OverflowError('int too large to convert to float')`.
- **Evidence:**
  - Silent peer against the branch's `_complete_handshake` (3.11 and 3.14):
    - 4294968 s: `TimeoutError` after 0.707 s
    - 4294969 s: `TimeoutError` after 1.707 s
    - 2592000 s (30 d): still waiting after 4 s

    Re-run independently on 3.14: 2147483 s and 2147484 s still waiting after 3 s; 4294968 s:
    `TimeoutError('…The handshake operation timed out')` at 0.71 s; 2592000 s still waiting after
    3 s.
  - A real in-process `QueueServer` and a client that starts its TLS late, then completes it:
    - `handshake = '49d17h2m48s'`, client starts after 1.0 s: dropped with `SSLEOFError(8, …)`
    - `'1342d4h15m17s'`, client starts after 0.05 s: dropped with `SSLEOFError(8, …)`
    - default `30`: completed (TLSv1.3) in both cases
  - `handshake_seconds('1' + '0'*400)` is accepted. The server starts, then logs
    `Queue server dropped a connection: OverflowError('int too large to convert to float')` for
    every client, and the client sees `ConnectionResetError(54, …)`.
- **Touches:** R2 (the deadline is not what was configured), R4 (a value that cannot be served is
  accepted at start).
- **Direction (builder's choice):** keep each wait below the platform's limit and wait again until
  the deadline from accept has passed, or bound the accepted value at start. Bounding the value is a
  `GOAL.md` change, since R4 accepts any duration greater than zero. Either way, a value too large
  to use should be refused at start, not on every connection.

### F2 [LOW/CONFIRMED] A server made plain by `server.tls.enabled = false` refuses a bad `handshake` value; develop starts
- **Where:** `src/hypershell/server.py:1888` and `src/hypershell/cluster/__init__.py:429`.
- **Failure scenario:** both commands check the value whenever the command-line TLS flag is on, that
  is, unless `--no-tls` is given. But `tls_from_namespace` returns `None`, which means a plain
  server, when the section's `enabled` is false. So a server that runs plain because of
  configuration still refuses to start on a bad `handshake`. R6 says a plain server ignores the key.
  `hs relay` (`relay/__init__.py:1199`) checks the same `enabled` switch that decides its local
  server's TLS, and behaves correctly. The new docs mention only `--no-tls`.
- **Evidence:** in a throwaway site with `HYPERSHELL_SERVER_TLS_ENABLED=false
  HYPERSHELL_SERVER_TLS_HANDSHAKE=abc`:
  - develop: `hs server` still running after 4 s; `hs cluster` exit 0
  - branch: `hs server` exit 2 and `hs cluster` exit 2, both logging
    `CRITICAL [hypershell] server.tls.handshake: Duration 'abc' is not valid…`
  - `--no-tls` starts on both
- **Touches:** R6 (plain servers ignore the key, as on develop).
- **Direction:** check the value only when the server will actually use TLS, the same condition
  `tls_from_namespace` applies. Say in the configuration reference that a plain server, through
  `--no-tls` or `enabled = false`, ignores it.

### F3 [LOW/CONFIRMED] The `hs server` deadline drive passes against a server with no deadline
- **Where:** `spec/listener-handshake-deadline/research/drive.py:140` (`check_held`). It accepts any
  close in `[SEC, SEC + 3]`.
- **Failure scenario:** `hs server` finishes its four tasks and exits about 2.5–3 s in. The exit
  closes the held socket inside the 2–5 s window, so the `server --handshake 2` drive passes whether
  or not the server enforces a deadline. The `cluster` drive passes the same mutant's bound by only
  0.09 s.
- **Evidence:** a copy of the branch's `src` with `settimeout(None)` in `_complete_handshake`, run via
  `PYTHONPATH`:
  - `server --handshake 2`: `PASS: hs server: server closed the held connection 2.9 s after it
    connected (expected 2 to 5 s)`
  - `cluster --handshake 2`: FAIL at 5.09 s
  - `relay --handshake 2`: FAIL at 5.5 s
- **Touches:** the R6 evidence for `hs server`. R2 itself is covered by the unit tests, which are
  red on develop.
- **Direction:** keep the server running well past `SEC + 3` (for example, more or longer tasks)
  and assert the close came from the deadline, not the exit.
- **Also:** the drive's `--client` mode passes `PYTHONPATH` on to the older client, which would
  silently run the branch's code if `PYTHONPATH` were set. It was not set in the recorded runs.

### Observations (not graded)
- `docs/_include/config_param_ref.rst:361` has a bare `.. versionadded:: 2.9.0`. Every other marker
  in that file sits inside `.. only:: html`. The file is included in `docs/manual.rst`, so the man
  page prints "Added in version 2.9.0." under `.handshake` alone. This is cosmetic; worth folding
  into the fix.

## Human-gate triggers

- **Triggered.** F1 is a CONFIRMED finding in `core/queue.py`, which is high-blast-radius core.
  F2 is CONFIRMED in `server.py`, also high-blast-radius core. Explicit human sign-off is required
  before any further step, including the `/hs-build` remediation loop.
- TLS posture (a security invariant) was checked and is unchanged; no finding touches it.

## Optional completeness sub-pass

Not run (not requested).
