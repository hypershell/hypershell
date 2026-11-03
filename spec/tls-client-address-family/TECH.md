---
slug: tls-client-address-family
title: TLS client reaches the server on the address family it listens on
kind: fix
appetite: small
status: done
branch: fix/tls-client-address-family
base: develop
current_phase: done
last_updated: '2026-11-03'
phases:
- id: P1
  name: Red-first regression tests, IPv4-only TLS dial, free_port hardening
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: "uv run python -c \"import os, signal, subprocess, sys, threading\ndef run(args,\
    \ limit):\n    p = subprocess.Popen([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider',\
    \ *args], start_new_session=True)\n    timer = threading.Timer(limit, os.killpg,\
    \ (p.pid, signal.SIGKILL))\n    timer.start()\n    try:\n        return p.wait()\n\
    \    finally:\n        timer.cancel()\nsys.exit(run(['-rs', 'tests/test_queue_address_family.py',\
    \ 'tests/test_queue_tls.py', 'tests/test_cluster.py'], 900) or run(['-q', '-n',\
    \ 'auto'], 2400))\""
- id: P2
  name: 'R6 trial: path-limited 3-way apply onto a detached origin/master worktree'
  status: done
  satisfies:
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: "uv run python -c \"import os, signal, subprocess, sys, threading\npaths\
    \ = ['src/hypershell/core/queue.py', 'tests/conftest.py', 'tests/test_queue_address_family.py']\n\
    common = subprocess.check_output(['git', 'rev-parse', '--path-format=absolute',\
    \ '--git-common-dir'], text=True).strip()\ntrial = os.path.join(os.path.dirname(common),\
    \ '.worktrees', 'r6-trial-tls-client-address-family')\nsubprocess.run(['git',\
    \ 'worktree', 'add', '--detach', trial, 'origin/master'], check=True)\ntry:\n\
    \    patch = subprocess.check_output(['git', 'diff', 'origin/develop...HEAD',\
    \ '--', *paths])\n    subprocess.run(['git', '-C', trial, 'apply', '--3way', '--index'],\
    \ input=patch, check=True)\n    env = dict(os.environ, PYTHONPATH=os.path.join(trial,\
    \ 'src'))\n    subprocess.run([sys.executable, '-c', 'import hypershell, sys;\
    \ sys.exit(0 if hypershell.__file__.startswith(sys.argv[1]) else 1)', trial],\
    \ env=env, cwd=trial, check=True)\n    p = subprocess.Popen([sys.executable, '-m',\
    \ 'pytest', '-p', 'no:cacheprovider', '-rs', 'tests/test_queue_address_family.py',\
    \ 'tests/test_queue_tls.py'], env=env, cwd=trial, start_new_session=True)\n  \
    \  timer = threading.Timer(900, os.killpg, (p.pid, signal.SIGKILL))\n    timer.start()\n\
    \    rc = p.wait()\n    timer.cancel()\nfinally:\n    subprocess.run(['git', 'worktree',\
    \ 'remove', '--force', trial], check=True)\n    subprocess.run(['git', 'worktree',\
    \ 'prune'], check=True)\nsys.exit(rc)\""
review:
  last_reviewed_commit: 0afe639dd1cdd23bdb6ed8b8da26c45717e97c9a
  verdict: approved
  blocked_reason: ''
  cycle: 1
---
# TECH.md — TLS client reaches the server on the address family it listens on

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/tls-client-address-family/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one
atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and briefs `01`, `02`.

**Base:** the branch was cut from `origin/develop` at `b31c66e`. Diff and compare against
`origin/develop`, not a local `develop`, which may lag.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`
- `appetite`: `small | big`. It caps the phase count and the build-iteration budget.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: the GOAL R-IDs this phase delivers.
- `depends_on`: phase ids that must be `done` first.
- `parallel`: `false` for both phases; P1 touches `core/queue.py` (coupled core), and P2 needs P1.
- `hammerable`: `false` for both phases. Both are correctness gates.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter (`set_phase.py --phase P<n> --record-attempt`).
- `verify`: the exact command that proves the phase. Both run their pytest in a **new process group
  under a kill timer** (`start_new_session=True` + `threading.Timer` → `os.killpg`), so a hung socket
  test cannot outlive the gate. Run them from the feature worktree root.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; [`invariants.md`](../../.agents/factory/invariants.md)
  §9 and §12 are the sections in play. `core/queue.py` is high-blast-radius.
- One atomic commit per phase, code and `TECH.md` state together:
  `[fix] Build tls-client-address-family P<n>: …`. **No `Co-Authored-By` trailer.** Stage by
  explicit path.
- No CLI change, so no `docs/_include` or `share/` update.
- Standard library only in `src/`. Tests use only helpers that also exist on `master` (`tests.main`,
  `create_taskfile_echo`, `assert_output`, the `temp_site` fixture, `free_port`). `tests/__init__.py`
  has develop-only additions, so do not reach for them, or R6 breaks.
- Every blocking call in the new tests is bounded: socket timeouts, thread joins with a timeout, and
  `subprocess.run(..., timeout=…, start_new_session=True)` for the CLI drive.

---

## Phase P1 — Red-first regression tests, IPv4-only TLS dial, free_port hardening
**Satisfies:** R1, R2, R3, R4, R5, R7 · **Depends on:** —
**Goal:** the TLS client dials IPv4 exactly as the cleartext client does. A new deterministic test
file proves it at the transport and `hs cluster` levels, and `free_port()` never returns a port number
held on `::1`.

- [x] Create `tests/test_queue_address_family.py` (PLAN §2 "Regression tests"):
  - [x] `ipv6_resetter(port)`: `AF_INET6` + `IPV6_V6ONLY` on `('::1', port)`; a daemon accept thread
        that resets (`SO_LINGER` `(1, 0)`) and counts connections; event stop, bounded join, close in
        `finally`; `pytest.skip` when `::1` cannot be bound.
  - [x] `ipv6_first_getaddrinfo`: wraps the real `socket.getaddrinfo`; for `localhost` with family
        `0`/`AF_UNSPEC`/`AF_INET6`, puts `('::1', port, 0, 0)` first, injected if absent; `AF_INET`
        passes through. Installed with `monkeypatch` in process. Its source is also written to
        `tmp_path/sitecustomize.py` and prepended to `PYTHONPATH` for the CLI drive.
  - [x] Arming check helper: under the wrapper, `socket.create_connection(('localhost', P))` reaches
        the resetter (count ≥ 1). Every trap test calls it before its real assertion.
  - [x] TLS transport under the trap: real `SecureListener(('127.0.0.1', P))` and
        `secure_client(('localhost', P), authkey=…)`; handshake + auth + payload round-trip; resetter
        count unchanged after arming.
  - [x] Cleartext twin: stock `multiprocessing.connection.Listener`/`Client` with an authkey, same
        setup and assertions.
  - [x] `hs cluster` under the trap, parametrized TLS / `--no-tls`: `HYPERSHELL_SERVER_PORT=P`,
        `create_taskfile_echo` in `temp_site`, DB mode. Exit 0, every task recorded complete with
        `exit_status == 0`, resetter count unchanged after arming.
  - [x] Nothing listening: both transports raise `ConnectionRefusedError` for `('localhost', P)`.
  - [x] No IPv4 address: both transports raise `socket.gaierror` for `('::1', P)` well under 5 s.
  - [x] Host-name check uses the given name: materials for `localhost` only; patch
        `get_client_context` to a context with `check_hostname=True` and that cert as CA, with
        `servername` unset; `secure_client(('localhost', P))` succeeds.
  - [x] `free_port()` skips a `::1`-held port: hold `::1:P`, inject `P` as the first IPv4 draw, assert
        the result `!= P` and that it binds on `::1`.
  - [x] Tag each test `unit` or `integration`.
- [x] **Red run on the unchanged source.** Run the new file (in a process group with a timeout).
      Expect: the TLS trap, TLS `hs cluster`, no-IPv4 and `free_port` tests fail; the cleartext,
      `--no-tls`, refused and host-name tests pass; nothing hangs. Record the pass/fail list in the
      commit body. If a TLS trap test passes here, the arming check is broken: stop and fix the test
      before touching `src/`.
- [x] `src/hypershell/core/queue.py`, `secure_client`: replace `socket.create_connection(address)`
      with `socket.socket(AF_INET, SOCK_STREAM)`, `setblocking(True)`, `connect(address)`; widen the
      existing close-on-failure `try` to cover the connect; add a comment on why IPv4. Leave
      `servername`, `wrap_socket`, `verify_peer_fingerprint` and the challenge/response untouched.
- [x] `tests/conftest.py`, `free_port()`: an IPv4 draw helper, plus a `::1` check (V6ONLY bind). On
      `EADDRINUSE` draw again, up to a bound, then raise. Any other IPv6 `OSError` accepts the port.
      Update the docstring.
- [x] Green: the new file passes, and `tests/test_queue_tls.py` passes **unchanged**.
- **Verify:** the frontmatter `verify:` runs the new file, `tests/test_queue_tls.py` and
  `tests/test_cluster.py` with `-rs` (timeout 900 s), then the full suite `-n auto` (timeout 2400 s),
  each in its own process group. Confirm in the `-rs` output that the trap tests **ran** (not skipped)
  on the build host.
- **Touches:** `src/hypershell/core/queue.py`, `tests/conftest.py`, `tests/test_queue_address_family.py`.

### P1 build record (2026-11-03)

**Red/green, test by test** (`tests/test_queue_address_family.py`; red = the new tests on the unchanged
`queue.py` and `conftest.py`; same result on 3.12 and 3.14; nothing hung, the red file ran in ~5 s):

| Test | Red (pre-fix) | Green |
|------|---------------|-------|
| `test_tls_client_skips_ipv6_listener` | FAIL: `BrokenPipeError` in the TLS handshake (the dial hit the `::1` resetter) | pass |
| `test_cleartext_client_skips_ipv6_listener` | pass (reference) | pass |
| `test_cluster_skips_ipv6_listener[tls]` | FAIL: `hs cluster` non-zero, `CRITICAL [hypershell.cluster] BrokenPipeError` | pass |
| `test_cluster_skips_ipv6_listener[no-tls]` | pass (reference) | pass |
| `test_refused_when_nothing_listening` | pass (R4 guard) | pass |
| `test_no_ipv4_address_fails_promptly` | FAIL: TLS dial raised `ConnectionRefusedError` from `::1`, not `gaierror` | pass |
| `test_hostname_check_uses_given_name` | pass (R5 guard) | pass |
| `test_free_port_skips_port_held_on_ipv6_loopback` | FAIL: `AttributeError`, the unchanged `conftest` has no draw seam | pass |

The `free_port` test's red is the missing seam, not the behaviour, so it was also checked by mutation:
with the `::1` check in `free_port()` disabled (draw seam present), it fails; restored, it passes.

**Verify** (the frontmatter command, run with the worktree's own frozen environment per interpreter,
built offline with `uv sync --frozen`; no `.venv` in the worktree, so `uv run` was replaced by that
environment's interpreter on `PATH`): 3.12.16 and 3.14.10, each `105 passed` for the `-rs` stage
(new file + `test_queue_tls.py` + `test_cluster.py`, trap tests ran, none skipped) and
`639 passed, 64 skipped` for the full suite with `-n auto`.
Also run, full suite only with `-n auto -rs`: 3.11.18 and 3.13.18, `639 passed, 64 skipped` each. Every
skip, on all four interpreters, is an environment-gated PostgreSQL/TimescaleDB test or the 2.8.3
fixture regeneration; none is in the new file. Neither known pre-existing failure appeared: no
`hs cluster` rc 5 and no `tests/test_groups.py` hang.

**Where the trap tests skip.** `test_tls_client_skips_ipv6_listener`,
`test_cleartext_client_skips_ipv6_listener`, both `test_cluster_skips_ipv6_listener` cases and
`test_free_port_skips_port_held_on_ipv6_loopback` skip on a host without IPv6 loopback (no `::1` to
bind), as R3 allows: expect that on CI images without `::1`. The refused, no-IPv4 and host-name tests
never skip. On the build host (macOS, `::1` present) none skipped.

**Departures from the plan (minor, no R-ID affected).** The resetter context manager draws its own
port number (free on IPv4, bindable on `::1`, redrawing on `EADDRINUSE`) instead of taking one, so the
trap is robust even on the unchanged `free_port()`. The `free_port()` helpers are public module
functions, `draw_port()` and `held_on_ipv6_loopback()`, so the unit test can inject a draw. The
`hs cluster` test also arms the trap inside a fresh interpreter (asserting the test's
`sitecustomize` is the one imported) before driving the CLI, and reads task rows from the temp-site
SQLite file directly.

## Phase P2 — R6 trial: path-limited 3-way apply onto a detached origin/master worktree
**Satisfies:** R6 · **Depends on:** P1
**Goal:** show that the fix's code paths apply to `master` (2.8.3) without conflict, and that the new
tests pass there, without touching the `master` branch.

- [x] `git fetch origin` so `origin/master` and `origin/develop` are current (read-only).
- [x] Run the frontmatter `verify:`. It:
  - adds a **detached** worktree of `origin/master` at `<main checkout>/.worktrees/r6-trial-tls-client-address-family`
    (derived from `git rev-parse --git-common-dir`; no branch is created);
  - pipes `git diff origin/develop...HEAD -- src/hypershell/core/queue.py tests/conftest.py tests/test_queue_address_family.py`
    into `git -C <trial> apply --3way --index`, which exits non-zero on any conflict;
  - asserts `hypershell` imports from the trial's `src/` (`PYTHONPATH` overlay on the feature
    worktree's environment; `master` differs only by a docs dependency and the version);
  - runs the new file and `tests/test_queue_tls.py` there with `-rs`, in a process group with a 900 s
    kill timer;
  - in `finally`, removes the trial worktree (`git worktree remove --force`) and prunes.
- [x] Confirm `git worktree list` no longer shows the trial.
- [x] Record the outcome below under **R6 check** (method, `origin/master` sha, pass/skip counts), then
      mark P2 done.
- **Why path-limited, not a whole-commit cherry-pick:** branch commits (and the eventual squash on
  `develop`) also change `spec/tls-client-address-family/`, `ROADMAP.md` and `issues/`. None exist on
  `master`, so a whole-commit pick conflicts as modify/delete for reasons unrelated to the fix. The
  2.8.4 port takes the same three paths.
- **Touches:** `spec/tls-client-address-family/TECH.md` only.

### R6 check

**Checked by trial apply, not by diff inspection** (2026-11-03). `origin/master` at `4d5771b`
(2.8.3), `origin/develop` at `b31c66e`, after `git fetch origin`.

- **Method:** the frontmatter `verify:`, run from the feature worktree at P1's commit. It added a
  detached worktree of `origin/master` under the main checkout's `.worktrees/` (no branch), piped
  `git diff origin/develop...HEAD -- src/hypershell/core/queue.py tests/conftest.py
  tests/test_queue_address_family.py` into `git -C <trial> apply --3way --index`, ran the tests there,
  and removed the trial in `finally`, then pruned. `queue.py` and `conftest.py` applied cleanly by
  3-way merge; the new test file, having no base blob, applied directly. No conflict.
- **Environment:** an overlay. The feature worktree's frozen environment per interpreter, with
  `PYTHONPATH=<trial>/src`. `hypershell` imported from `<trial>/src/hypershell/__init__.py`, both in
  the test interpreter and in the interpreter the `hs` console script runs (which the `hs cluster`
  test drives), so the CLI under test was `master`'s code plus the fix.
- **Result:** `tests/test_queue_address_family.py` and `tests/test_queue_tls.py` on the trial:
  **47 passed, 0 skipped** on 3.12.16 (the `verify:` as written) and again on 3.14.10 (a repeat of
  the same steps that also printed the import paths). The trap tests ran, none skipped.
- **Clean-up:** `git worktree list` showed no trial afterwards, after both runs.

**For the 2.8.4 release (path-limited port).** The squash commit of this fix on `develop` will not
cherry-pick onto `master` whole: it also carries `spec/tls-client-address-family/`, and possibly
`ROADMAP.md` and `issues/`, none of which exist on `master` (modify/delete conflicts unrelated to the
fix). Port exactly three paths, as this trial did: `src/hypershell/core/queue.py`,
`tests/conftest.py` and `tests/test_queue_address_family.py`, e.g. `git diff <base>..<squash> --
<those paths> | git apply --3way --index` on the release branch.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `fix/tls-client-address-family`, `origin/develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command. Never advance on a checkbox alone.
5. Amend this file if reality diverges (`set_phase.py`; note it in the commit body). STOP only on a
   `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.

---

## CI follow-up (2026-11-03)

**Failure.** On the PR's CI run, the Linux `ubuntu-latest` job on Python 3.12.16 (serial pytest)
errored at setup of `test_tls_client_skips_ipv6_listener`: `assert_trap_armed` raised
`ConnectionRefusedError: [Errno 111]` from `socket.create_connection(('localhost', port))`. The same
file passed on 3.11, 3.13 and 3.14 in that run. The product change was not involved: the error is in
the fixture's arming dial, before any `hypershell` code runs.

**Cause (CONFIRMED by local reproduction): the resetter's reset raced the arming dial's own
`connect()`.** `IPv6Resetter._serve` reset each connection the moment `accept()` returned. With a
timeout set, `socket.connect` is a non-blocking connect, then a poll, then a read of `SO_ERROR`. The
resetter thread can accept and send its RST in that gap. `SO_ERROR` then holds `ECONNRESET`, so the
dial to `::1` fails, although it landed on the resetter. `create_connection` moves on to
`127.0.0.1`, where nothing listens yet, and raises only the last error, `ConnectionRefusedError`.
"Refused" therefore did not mean that nothing accepted on `::1`. Reproduced on macOS by dialling the
unchanged resetter 3,000 times back to back under the shim, with `all_errors=True` to see every
address's error. 61 dials failed on 3.12.16 and 73 on 3.14.10, every one as `ConnectionResetError`
on `::1` then `ConnectionRefusedError` on IPv4. Every dial, failed or not, was counted by the
resetter. The race is timing-dependent, not Linux- or 3.12-specific; one arming dial per trap setup
makes it rare in a single run, which is why it had not shown locally. The other candidates were
ruled out by reading the code. The resolver patch is applied before arming. The listener is closed
only in `close()` and the bind error path; an exiting serve thread would leave it open and still
completing handshakes. `ipv6_resetter()` binds `::1` and redraws on `EADDRINUSE`, and binding and
listening finish before the dial.

**Fix (`tests/test_queue_address_family.py` only; `git diff 0afe639 -- src/` is empty).**
- `IPv6Resetter` counts a connection as soon as it is accepted, then waits for the peer to send or
  close before the `SO_LINGER (1, 0)` reset. The wait is bounded: a 0.1 s receive poll that also
  checks the stop event, up to `TIMEOUT`. A peer that has sent or closed has already returned from
  `connect()`, so the reset can no longer fail the dial. The trap's effect on a client is unchanged:
  the pre-fix TLS client sends its ClientHello and is reset.
- The arming dials (`assert_trap_armed` and the `hs cluster` subprocess check) pass
  `all_errors=True` (Python ≥ 3.11, the floor on `develop` and `master`). A failed arm now reports
  every address's error, not only the IPv4 refusal.

**Gates** (frozen offline environments from `/tmp/claude-501/tlsaf-review/venv`, overlaid on trees
exported to `/tmp/claude-501/tlsaf-ci/src/`; every pytest in its own process group under a kill
timer):
- Stress, the same 3,000-dial loop on the fixed resetter: 0 failures on 3.12.16 and on 3.14.10, with
  all 3,000 counted (before: 61 and 73).
- The file 200 times in a row on 3.12.16: 200 of 200 runs `8 passed`. And 50 times on 3.14.10: 50 of
  50 runs `8 passed`.
- Full suite once on each, serial as CI runs it: 3.12.16 and 3.14.10, each `639 passed, 64 skipped`.
  The skips are the PostgreSQL, TimescaleDB and fixture-regeneration gates; none is in this file.
- Red/green, re-proved on 3.12.16 and 3.14.10. The `prefix` tree has `core/queue.py` from
  `origin/develop` `b31c66e`, so the client is the pre-fix one. It gives `3 failed, 5 passed`:
  `test_tls_client_skips_ipv6_listener` (`ConnectionResetError` in the TLS handshake, in the test
  body, not at setup), `test_cluster_skips_ipv6_listener[tls]` (`hs cluster` exit 5,
  `ConnectionResetError`) and `test_no_ipv4_address_fails_promptly`. The `head` tree gives
  `8 passed`. The red errors are now `ConnectionResetError` where the build saw `BrokenPipeError`,
  because the reset follows the ClientHello instead of preceding it.
