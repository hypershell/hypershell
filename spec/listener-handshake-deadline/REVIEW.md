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

## Review cycle 2 — changes-requested (2026-11-14)

- **Reviewed commit:** 59543ba  ·  **Base:** develop (`origin/develop` = `6da633d`, re-fetched at the end of the review; the merge-base equals it and `git merge-tree` is clean)
- **Mode:** a fresh blind pass over the full diff, `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, plus `git diff origin/develop...HEAD -- 'spec/*/research/*.py'` (the drive scripts the phase `verify:` commands run). The reviewer had the same inputs and exclusions as cycle 1, with the GOAL's gate and review-cycle-1 rulings, and was not shown the cycle 1 findings. The orchestrator graded F1–F3 separately, by executed command.
- **Contract drift:** since cycle 1, one `GOAL.md` commit, `0989ab7`, records the review-cycle-1 rulings the maintainer signed off: values above one day are refused, and accepting goes on when a handler thread does not start. This review grades against it. The foreman also passed the rulings' readings:
  - the key is checked only when the server uses TLS
  - the deadline drives must fail on a build with no deadline
  - the accept-loop change applies to plain servers too
  - the configuration reference's `versionadded` sits inside `.. only:: html`

### Status of the cycle 1 findings

| Finding | Status | Evidence |
|---------|--------|----------|
| F1: deadline above about 24.8 days not honored | **Fixed** | `handshake_seconds` (`core/tls.py:160`) refuses anything above `MAX_HANDSHAKE` (86400 s), naming the key and the accepted forms, with long values cut short. These are refused with `TLSError`: `30d`, `4294968`, `2592000`, `49d17h2m48s`, `1342d4h15m17s`, `1d1s`, `86400.5`, `86401`, `1-00:00:01`, a 401-digit integer (as a string and as an int), `'9'*5000`, `1e300`, `inf`, `nan`, `True` and `-0.0`. These are accepted: `1d`, `24h`, `86400`, `1-00:00:00` → 86400; `23:59:59` → 86399; `0.001` → 1. With the largest accepted deadline (86400 s), `_complete_handshake` against a silent peer was still waiting after 4 s (3.14.6) and ended only when the peer closed. `hs server` with `HYPERSHELL_SERVER_TLS_HANDSHAKE=30d` exits 2, naming the key. |
| F2: a server made plain by `enabled = false` refuses a bad value | **Fixed** | `server.py:1891` and `cluster/__init__.py:432` now check the value only when `server.tls.enabled` is true. That is the switch `from_namespace` reads, and the same check `relay/__init__.py:1201` makes. In a throwaway site with `HYPERSHELL_SERVER_TLS_ENABLED=false`: `hs server` with `abc` or `30d` was still serving after 4 s; `hs cluster` with `abc` or `30d` exited 0 and ran its task. No log line named the key. |
| F3: `hs server` deadline drive passes with no deadline | **Fixed for `server` and `cluster`; still open for `relay` (F4 below)** | No client of `server` or `cluster` now runs before the window has passed, and the `server` drive checks that the server is still up then. Against a no-deadline copy of `src` (`settimeout(None)` in `_complete_handshake`, via `PYTHONPATH`): `server --handshake 2` FAILs, with the held connection not closed in the window, and `cluster --handshake 2` FAILs at 9.15 s. The `--client` mode now drops `PYTHONPATH`. |
| Observation: bare `versionadded` in the man page | **Fixed** | Now inside `.. only:: html`. `sphinx-build -W --keep-going -b man` exits 0, and the man page has no stray "Added in version" line. |

### Verification run

All runs were on macOS (arm64). CLI drives used throwaway sites (`.agents/factory/bin/temp_site.sh`), and probes and mutant copies ran from scratch directories. Every process the review started was stopped by its recorded PID. An unsandboxed `ps` check at the end found only another worktree's processes, which were left alone.

- **Focused suites** (`tests/test_queue_tls.py`, `test_queue_limits.py`, `test_queue_gc.py`, `test_queue.py`):
  - 3.14.6: 176 passed, 3 skipped (version-gated)
  - 3.12.13: 179 passed
  - 3.11.18 (own environment, `hs` on `PATH`): 175 passed, 4 skipped
- **Full suite**, 3.14.6 (`uv run pytest -q -rs`): 2708 passed, 109 skipped (PostgreSQL and TimescaleDB gated).
- **Red on develop.** The branch's `tests/test_queue_tls.py`, run against develop's `src` through an import shim, gave 58 failed and 15 passed. Every behaviour test is red there, including the new handler-start tests; develop logs `Queue server stopped accepting connections`.
- **R1.** 400 pending connections, half of them with a partial ClientHello, with the default deadline: 5 real clients each connected and made their first call in 0.033–0.079 s. The `server`, `cluster` and `relay` drives with the default deadline pass on the branch and fail on develop.
- **R2 and R3.** Deadline 2 s, against five peer types:
  - silent
  - a 20-byte partial hello
  - a full hello, then a stall
  - a hello trickled one byte per 0.2 s
  - the client's Finished trickled one byte per 0.3 s after the server's flight

  Each was closed at 2.0 s on 3.14 and 3.11, with handler threads at 0 and no descriptor left. With `RLIMIT_NOFILE` at 200, 34 connections were refused at once with one WARNING, and none remained after the deadline.
- **Handler start failure** (ruling). A real start failure was forced with an oversized `threading.stack_size`, on TLS and plain servers, on 3.14 and 3.11:
  - the peer saw EOF at once
  - the live set was empty and no descriptor was left
  - the first failure logged a WARNING, later ones DEBUG
  - a later client was served in 0.006–0.055 s
- **Stop races.** 60 rounds of 45–180 mixed pending connections (silent, partial hello, full hello), stopped at random times: no handler thread or descriptor was left over.
- **R4 at the CLI.**
  - The `refuse` drive passed 26 of 26: `hs server`, `hs cluster` and `hs relay` each exit 2 naming the key for `0`, `-5`, `abc`, `30d`, `4294968` and a 401-digit integer.
  - `hs server` and `hs cluster`, made plain by `--no-tls` or `enabled = false`, start with `abc` and `30d`.
  - `86401`, `1d1s`, `86400.5` and `1:00:00:00` exit 2 as well.
- **R5.** 5 closes gave 5 DEBUG lines with the peer address and the deadline, and 1 WARNING naming 2 seconds and `server.tls.handshake`. The marker hostname the peer sent appears in no line.
- **R7.** `research/latency.py compare`, 3 rounds: median connect and first call was 34.69 ms on develop and 34.58 ms on the branch (allowed 39.69 ms), a PASS. The 64-client burst completed 64/64 in all 6 runs.
- **R8.**
  - The 2.9.0a8 client from PyPI finished the tasks (exit 0).
  - The 2.8.7 client connected and authenticated, then was refused as too old (exit 5, `Client too old (no work-request protocol)`) on both develop and the branch.
  - For a fixed ClientHello, the server's TLS flight had the same structure on develop and the branch.
- **Docs.** `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E` with `-b html` and with `-b man` both exited 0.
- **Forked manager path.** `test_secure_manager_subprocess_roundtrip` passes.

### Requirement → evidence matrix

| R-ID / ruling | Implemented by | Verified how | Status |
|---------------|----------------|--------------|--------|
| R1 | `serve()` → `accept_pending`; `handle_pending` runs the handshake on the handler thread | 400-pending probe; drives on branch vs develop; tests red on develop | ✅ |
| R2 | `_complete_handshake` (one `do_handshake`, timeout = deadline − time since accept) | Five peer types closed at 2.0 s (3.11, 3.14) | ✅ |
| R3 | `handle_pending`'s `finally`; `_admits` on the raw socket | Threads 0 and descriptor delta 0 after closes; reserve probe at nofile 200; stop races | ✅ |
| R4 | `handshake_seconds` (`core/tls.py`), `ArgumentError` in `server.py`, `cluster/__init__.py`, `relay/__init__.py`, `QueueServerError` before binding | Value table; boundary values; `refuse` drive 26/26 | ✅ |
| R5 | `_closed_pending`; other failures keep develop's DEBUG line | Logging probe; caplog tests | ✅ |
| R6 | All three commands build `QueueServer` with a `TLSConfig` carrying the key; plain servers skip the check | `server`/`cluster`/`relay` drives at default and 2 s; plain-by-config drives; plain silent connection not closed (test) | ✅ (relay drive evidence fragile, F4) |
| R7 | No existing test edited | Suites; full suite; latency and burst | ✅ |
| R8 | No client, context or wire change | 2.9.0a8 and 2.8.7 drives; TLS flight comparison | ✅ |
| R9 | `docs/security.rst` threat item 5, Limitations item 4, monitoring bullet; `config_param_ref.rst` `.handshake` | Read against develop; html and man builds `-W` clean | ✅ |
| Ruling: refused above one day | `MAX_HANDSHAKE` | As R4 | ✅ |
| Ruling: key checked only when the server uses TLS | `enabled` check in `server.py`, `cluster/__init__.py`, `relay/__init__.py` | Plain-by-config drives and tests | ✅ |
| Ruling: deadline drives fail with no deadline | `research/drive.py` | Mutant runs: `server` and `cluster` fail; `relay` does not reliably | ❌ partly (F4) |
| Ruling: accepting goes on when a handler does not start | `_InProcessServer._unstarted` (`core/queue.py:716`) | Forced start failures, TLS and plain, 3.11 and 3.14; tests red on develop | ✅ |
| Ruling: `versionadded` inside `.. only:: html` | `config_param_ref.rst` | Man build | ✅ |

Unmapped changes (possible scope creep): none. The handler-start change reaches plain servers through the shared accept loop, which the review-cycle-1 ruling covers.

**TLS posture** is unchanged. `build_server_context`, the verify mode, minimum version, ciphers and the client's fingerprint check are untouched. The only `core/tls.py` additions are the `handshake` field and its parser. Authentication minimums are untouched.

### Findings

#### F4 [MEDIUM/CONFIRMED] The `relay` deadline drive does not reliably fail against a build with no deadline
- **Where:** `spec/listener-handshake-deadline/research/drive.py:241` (`scenario_relay`), with `DELAY_LAUNCHER` (`:59`, a fixed 3 s wait) and `check_held` (`:151`).
- **Failure scenario:** the `server` and `cluster` drives hold every client back until the window has passed. The `relay` drive does not: its site client always starts after 3 s, whatever `--handshake` is. When the work is done, the relay stops its local server, which closes the held connection at about 5.5 s whether or not a deadline exists. Nothing checks that the relay's server is still up when the window ends. So the drive passes against a build with no deadline whenever `--handshake` puts 5.5 s inside the window. At the `--handshake 2` its `verify:` uses, it fails such a build by only about 0.5 s, and a faster host could close the gap. This partly misses the review-cycle-1 ruling that the deadline drives fail on a build with no deadline.
- **Evidence:** a copy of `src` with `ssl_sock.settimeout(None)` in `_complete_handshake`, run via `PYTHONPATH`, with `temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py" relay --handshake N`:

  | `N` | Result against the copy with no deadline | Held connection closed after | Expected window |
  |-----|------------------------------------------|------------------------------|-----------------|
  | 3 | exit 0, `PASS` (reviewer twice, orchestrator once) | 5.46 s, 5.45 s, 5.55 s | 3–6 s |
  | 2 | `FAIL` ×4 | 5.46–5.53 s | 2–5 s |

  On the branch itself the relay's local server closed the held connection at 2.0 s, so the shipped behaviour is correct.
- **Touches:** the R6 evidence for `hs relay` and the review-cycle-1 drive ruling. R2 on the relay's server is covered by the shared `QueueServer` path and its unit tests, which are red on develop.
- **Direction:** as for `cluster`, hold the site client back until the window has passed (for example, a launcher delay of `SEC + 5` when `--handshake` is given), and report that the relay is still running when the window ends.

#### F5 [LOW/CONFIRMED] `AGENTS.md` understates when the handshake value is refused
- **Where:** `AGENTS.md:351` (new hunk): "default 30 s, refused at start unless greater than 0".
- **Failure scenario:** since the review-cycle-1 rulings, a value above one day is refused too, and only a server that uses TLS checks the value. `AGENTS.md` is the ground truth the factory and contributors read, so this line now misstates the rule the code and `docs/_include/config_param_ref.rst` both follow.
- **Evidence:**
  - `HYPERSHELL_SERVER_TLS_HANDSHAKE=86401 uv run hs server t.in` in a throwaway site exits 2: `server.tls.handshake must be greater than 0 and at most one day (86400 seconds)`. So do `1d1s`, `86400.5`, `30d` and `24:00:01`.
  - With `HYPERSHELL_SERVER_TLS_ENABLED=false`, `abc` and `30d` start.
- **Direction:** say "greater than 0 and at most one day; checked only when the server uses TLS".

### Observations (not graded)
- The forked manager path (`SecureListener.accept`, used by `SecureManager.start`) still runs the handshake inline with no deadline. This is a stated non-goal, and its test passes.

## Human-gate triggers (cycle 2)

- **Not triggered by this cycle's findings.** F4 is in the evidence harness (`spec/…/research/drive.py`) and F5 in `AGENTS.md`. Neither is a high-blast-radius file or touches a security or DB-lifecycle invariant.
- This cycle's changes to the high-blast-radius core were re-reviewed by executed command and no finding was confirmed against them: `core/queue.py` `_unstarted` and `_complete_handshake`, `core/tls.py` `handshake_seconds`, and the `server.py` check. TLS posture is unchanged. The maintainer signed off the cycle 1 gate, including the accept-loop change for plain servers.
- **Loop bound:** this is cycle 2 of at most 3. Both remaining items are small and outside shipped code paths. If cycle 3 does not converge, escalate to the maintainer.

## Review cycle 3 — approved (2026-11-14)

- **Reviewed commit:** 338aa2c  ·  **Base:** develop (`origin/develop` = `6da633d`, re-fetched at the end of the review; it is an ancestor of the branch and `git merge-tree` is clean, so no merged-tree run was needed)
- **Mode:** a fresh blind pass over the full diff, `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, plus `git diff origin/develop...HEAD -- 'spec/*/research/*.py'` (the drive scripts the phase `verify:` commands run). The reviewer had the same inputs and exclusions as cycles 1 and 2, with the GOAL's gate and review-cycle-1 rulings and the foreman's readings of them, and was not shown earlier findings. The orchestrator graded F1–F5 separately, by executed command.
- **Contract drift:** none since cycle 2. The `GOAL.md` commits after shaping are `e77edc5` (plan-gate rulings) and `0989ab7` (review-cycle-1 rulings), both graded in earlier cycles.
- **Since cycle 2:** `src/`, `tests/` and `docs/` are unchanged. The build commit `338aa2c` touches `AGENTS.md` (F5) and `research/drive.py` (F4), plus spec artifacts.

### Status of earlier findings

| Finding | Status | Evidence |
|---------|--------|----------|
| F1: deadline above about 24.8 days not honored | **Fixed (holds)** | The `refuse` drive passes 26 of 26. `hs server`, `hs cluster` and `hs relay` each exit 2, naming the key, for `30d`, `4294968` and a 401-digit integer. In a throwaway site, `hs server` with `HYPERSHELL_SERVER_TLS_HANDSHAKE=86400` was still serving after 4 s, while `86401` and `24:00:01` exited 2 with `server.tls.handshake must be greater than 0 and at most one day (86400 seconds)`. |
| F2: a server made plain by `enabled = false` refuses a bad value | **Fixed (holds)** | With `HYPERSHELL_SERVER_TLS_ENABLED=false`, `hs server` with `86401` or `abc` was still serving after 4 s. The `refuse` drive's plain-start checks pass. |
| F3: `hs server` deadline drive passes with no deadline | **Fixed (holds)** | Against a copy of `src` with `ssl_sock.settimeout(None)` in `_complete_handshake` (run via `PYTHONPATH`), `server --handshake 2` fails (the held connection was never closed) and `cluster --handshake 2` fails (closed at 9.11 s, expected 2–5 s). Both pass on the branch. |
| F4: `relay` deadline drive does not reliably fail with no deadline | **Fixed** | `relay --handshake N` now delays the site client's launcher by `N + 5` s (`drive.py:248`). It checks the deadline close inside the window, and then that the relay and upstream are still running when the window ends (`:280`). Against the same no-deadline copy, `--handshake` 1, 2, 3 and 6 each exit 1: the held connection was not closed in the window, and the relay was still running when it ended. So the drive passes only if the deadline closes the connection. On the branch, `--handshake 2` closed it at 2.0 s and `--handshake 3` at 3.0 s, both exit 0, and the default-deadline `relay` drive passes. The blind reviewer reproduced the mutant failure for `server`, `cluster` and `relay` independently. |
| F5: `AGENTS.md` understates when the value is refused | **Fixed** | `AGENTS.md:351–352` now reads "refused at start unless greater than 0 and at most one day, and checked only by a server that uses TLS". That matches `handshake_seconds` (`core/tls.py:178`), the `enabled` checks in `server.py`, `cluster/__init__.py` and `relay/__init__.py`, the configuration reference, and the CLI runs in the F1 and F2 rows. |

### Verification run

All runs were on macOS (arm64). CLI drives used throwaway sites (`.agents/factory/bin/temp_site.sh`), and probes and mutant copies ran from scratch directories. Every process the review started was stopped by its recorded PID. Unsandboxed `ps` checks found no process of this review's left. The only other `hs`-related processes belonged to another worktree's review and were left alone.

- **Focused suites** (`tests/test_queue_tls.py`, `test_queue_limits.py`, `test_queue_gc.py`, `test_queue.py`):
  - 3.14.6: 176 passed, 3 skipped (platform skips)
  - 3.11.18, 3.12.13 and 3.13.16 (scratch environments): pass, with `hs` on `PATH` for `test_server_keeps_its_files_at_the_open_file_limit`, which needs it
- **Full suite**, 3.14.6: 2708 passed, 109 skipped.
- **Red runs.**
  - The new tests against develop's `src`: every behaviour test fails.
  - Against the no-deadline copy: the deadline (silent and slow) and logging tests fail.
- **R1.** 200 silent peers with the default deadline: a client's first call took 0.043 s. The `server`, `cluster` and `relay` drives with the default deadline pass. Develop's `server --handshake 2` drive fails with its client held for 25 s.
- **R2 and R3.**
  - 200 peers at a 2 s deadline: all closed at 2.00 s, with handler threads and descriptors back at their baseline.
  - A peer that stalls after a full ClientHello: closed at 2.0 s.
  - With a real open-file limit of 64, 34 connections were refused at once, 26 were closed at a 3 s deadline, and a client was then served in 0.078 s.
  - Stopping the server with 50 handshakes pending left no handler or socket.
- **R4.** The unit tests for forms, default, refusals and the environment variable pass, and so does the `refuse` drive (26/26). The CLI messages name the key and the accepted forms.
- **R5.** The logging test (3 DEBUG lines, 1 WARNING, no peer bytes) and the rate test pass. A handshake that fails for another reason is logged as on develop.
- **R6.** `server`, `cluster` (local) and `relay` drives pass with the default deadline and `--handshake 2`. The plain-server and forked-manager tests pass (`test_secure_manager_subprocess_roundtrip`).
- **R7.** No existing test was edited (the tests diff removes no line). `research/latency.py compare` gave a median connect and first call of 35.77 ms on develop and 35.48 ms on the branch (allowed 40.77 ms), a PASS. The 64-client burst completed 64/64 in all six runs.
- **R8.** The 2.9.0a8 client finished the tasks (exit 0). The 2.8.7 client was refused as too old (exit 5) on the branch, and the same way on develop.
- **Docs.** `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html` exited 0.

### Requirement → evidence matrix

| R-ID / ruling | Implemented by | Verified how | Status |
|---------------|----------------|--------------|--------|
| R1 | `serve()` → `accept_pending`; `handle_pending` runs the handshake on the handler thread | 200-peer probe; drives on branch vs develop; tests red on develop | ✅ |
| R2 | `_complete_handshake` (one `do_handshake`, timeout = deadline − time since accept) | 200 peers and a post-ClientHello stall closed at 2.0 s; drives at `--handshake 2` | ✅ |
| R3 | `handle_pending`'s `finally` and live-set hand-over; `_admits` on the raw socket | Baseline counts; reserve probe at a limit of 64; stop with 50 pending | ✅ |
| R4 | `handshake_seconds`, `MAX_HANDSHAKE`, `ArgumentError` in the three commands, `QueueServerError` before binding | Unit tests; `refuse` drive 26/26; CLI boundary 86400/86401 | ✅ |
| R5 | `_closed_pending` | Logging and rate tests; logging probe | ✅ |
| R6 | All three commands build `QueueServer` with a `TLSConfig` carrying the key; plain servers skip the check | Drives at default and 2 s; plain-by-config runs; plain tests | ✅ |
| R7 | No existing test edited | Suites; full suite; latency and burst | ✅ |
| R8 | No client, context or wire change | 2.9.0a8 and 2.8.7 drives, branch and develop | ✅ |
| R9 | `docs/security.rst` threat item 5, Limitations item 4, monitoring bullet; `config_param_ref.rst` `.handshake` | Read against develop; `-W` build clean | ✅ (see Observations) |
| Ruling: refused above one day | `MAX_HANDSHAKE` | As R4 | ✅ |
| Ruling: key checked only when the server uses TLS | `enabled` checks in `server.py`, `cluster/__init__.py`, `relay/__init__.py` | Plain-by-config runs; tests | ✅ |
| Ruling: deadline drives fail with no deadline | `research/drive.py` | No-deadline copy: `server`, `cluster` and `relay` fail (relay at 1, 2, 3 and 6 s) | ✅ |
| Ruling: accepting goes on when a handler does not start | `_InProcessServer._unstarted` | TLS and plain tests pass, red on develop | ✅ |
| Ruling: `versionadded` inside `.. only:: html` | `config_param_ref.rst` | Read; `-W` build | ✅ |

Unmapped changes (possible scope creep): none.

**TLS posture** is unchanged. The reviewer compared develop and the branch with docstrings stripped, and these are identical:
- the server and client context builders, the protocol floor and its mapping, the fingerprint check, and `TLSConfig.is_active`
- `secure_client`, `SecureConnection`, `SecureListener.accept` and the request handler
- the authentication minimums and `resolve_auth`

The only server-side change is `wrap_socket(..., do_handshake_on_connect=False)` followed by an explicit timed `do_handshake()`, on the same context.

### Findings

None CONFIRMED.

### Observations (not graded)
- **`docs/security.rst:404–406`, Limitations item 4.** The item still says failed authentication and handshake attempts are "handled by the underlying multiprocessing layer without surfacing in *HyperShell*'s logs". A connection closed at the deadline is logged by *HyperShell*: a WARNING, then DEBUG lines (the logging test asserts it).
  - The blind reviewer reported this as LOW. The orchestrator did not grade it as a finding, for three reasons:
    - the sentence describes attempts the multiprocessing layer handles, and a deadline close is not one of them;
    - the same page's monitoring bullet (`:994–997`) states the exception;
    - R9 asks that the item's other statements stay as they are.
  - A clause such as "except a connection closed at the handshake deadline" would remove the ambiguity, if the maintainer wants it at publish.

## Human-gate triggers (cycle 3)

- **No new trigger.** No finding was confirmed this cycle. The code under review (`core/queue.py`, `core/tls.py`, `server.py`) is unchanged since cycle 2, where executed review confirmed no finding against it. TLS posture is unchanged.
- **The cycle 1 gate still applies to publication.** F1 (`core/queue.py`, `core/tls.py`) and F2 (`server.py`) were CONFIRMED in high-blast-radius core. Their fixes are in core files. The maintainer signed off the cycle 1 gate and the rulings that set the remediation, but the remediated core has not had a human read. Explicit maintainer sign-off is required before `/hs-publish`.
- **Loop bound:** this is cycle 3 of at most 3, and it converged with no CONFIRMED finding.
