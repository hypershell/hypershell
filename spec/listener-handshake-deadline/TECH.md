---
slug: listener-handshake-deadline
title: A TLS queue server keeps serving clients while a connection is slow in its
  handshake
kind: fix
appetite: small
status: in_review
branch: fix/listener-handshake-deadline
base: develop
current_phase: done
last_updated: '2026-11-14'
phases:
- id: P1
  name: Handshake on the handler thread under a deadline from accept
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_queue_tls.py tests/test_queue_limits.py tests/test_queue_gc.py
    tests/test_queue.py tests/test_queue_address_family.py tests/test_queue_start.py
    tests/test_queue_workload.py && git diff --numstat 6da633d -- tests/test_queue_tls.py
    tests/test_queue_limits.py tests/test_queue_gc.py | awk '$2 != 0 {exit 1}' &&
    test -s spec/listener-handshake-deadline/evidence/red-develop.txt
- id: P2
  name: server.tls.handshake refusal at start, and deadline logging
  status: done
  satisfies:
  - R4
  - R5
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_queue_tls.py tests/test_queue_limits.py tests/test_queue_gc.py
    && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    refuse
- id: P3
  name: Docs, CLI drives with current and older clients, latency evidence
  status: done
  satisfies:
  - R6
  - R7
  - R8
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 1
  verify: UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html && .agents/factory/bin/temp_site.sh
    uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py" server
    && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    server --handshake 2 && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    cluster && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    cluster --handshake 2 && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    relay && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    relay --handshake 2 && .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py"
    server --client "$TMPDIR/lhd-clients/2.9.0a8/bin/hs" && .agents/factory/bin/temp_site.sh
    uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py" server
    --client "$TMPDIR/lhd-clients/2.8.7/bin/hs" --expect-too-old && mkdir -p "$TMPDIR/lhd-develop"
    && git archive 6da633d src | tar -x -C "$TMPDIR/lhd-develop" && uv run python
    spec/listener-handshake-deadline/research/latency.py compare "$TMPDIR/lhd-develop/src"
    "$TMPDIR/lhd-tls"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — A TLS queue server keeps serving clients while a connection is slow in its handshake

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/listener-handshake-deadline/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs; harness scripts
  [`research/drive.py`](research/drive.py) and [`research/latency.py`](research/latency.py).

## Frontmatter field reference

See [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md). Every phase here
touches `core/*` or its tests, so none is `parallel`, and every phase is `hammerable: false`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); the footgun checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §9, §10, §12.
  `core/queue.py` and `core/tls.py` are high-blast-radius files.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build listener-handshake-deadline P<n>: …`. **No `Co-Authored-By` trailer.**
- No CLI option is added, so no `docs/_include` help snippet or `share/` completion changes. Prose
  pages carry `.. versionadded::` / `.. versionchanged:: 2.9.0` (no PR number yet).
- Tests and drives are responsiveness and deadline tests. Existing tests are never edited: new tests
  are appended to `tests/test_queue_tls.py` in their own section, every one tagged `@mark.unit` or
  `@mark.integration`. Every test that opens sockets or servers closes them in `finally`, so a red
  run on the base commit fails within its bounds instead of hanging.
- A test sets the deadline by assigning `cfg.handshake = 2` on a `TLSConfig` instance (not a
  constructor argument), so on the base commit it fails on behaviour.
- Environment: `uv sync --python 3.14` with `UV_CACHE_DIR` under `$TMPDIR`; the docs build and any
  command that needs PyPI run with network allowed; Chromium is not needed.
- **Red runs on the base commit** use the base `src/`, exported once:
  `mkdir -p "$TMPDIR/lhd-develop" && git archive 6da633d src | tar -x -C "$TMPDIR/lhd-develop"`, then
  `PYTHONPATH="$TMPDIR/lhd-develop/src" uv run pytest …` (it shadows the editable install; checked).

---

## Phase P1 — Handshake on the handler thread under a deadline from accept
**Satisfies:** R1, R2, R3, R6 (plain unchanged), R7 (suites unchanged) · **Depends on:** —
**Goal:** the in-process TLS accept loop does only the TCP accept; each connection's handler thread
completes the handshake within `handshake` seconds of accept or closes it; nothing is left behind.

- [x] `core/tls.py`: `HANDSHAKE_KEY`, `DEFAULT_HANDSHAKE = 30`, `TLSConfig.handshake` (raw configured
      value, default 30), `from_namespace` copies `ns.get('handshake', DEFAULT_HANDSHAKE)` unparsed,
      `handshake_seconds(value) -> int` (PLAN §2.3). `core/config.py`: `'handshake': 30` in
      `[server.tls]`.
- [x] `core/queue.py`: `SecureListener.accept_pending()`, `wrap_pending()`, `_complete_handshake()`;
      `accept()` unchanged (PLAN §2.1).
- [x] `_InProcessServer`: parse the deadline in `__init__` before binding (`QueueServerError` on a bad
      value — the refusal tests are P2's); TLS branch of `serve`; `handle_pending` with the `_live`
      swaps and `_halted` checks; `_cut` handles a plain `socket.socket` (PLAN §2.2). For now a
      deadline close logs the DEBUG line only (the WARNING is P2's).
- [x] Docstrings: `secure_client`, `_decref`, the `_server_side` comment, `_InProcessServer`.
- [x] Tests (new section in `tests/test_queue_tls.py`): responsiveness (8 silent connections, deadline
      300 s, client connect + first call ≤ 5 s); deadline 2 s for a silent peer and for a peer sending
      a real ClientHello (built with `ssl.MemoryBIO`) one byte every 0.25 s, each closed 2–5 s after
      it connected; after the closes, `hypershell-queue-handler` threads and open descriptors
      (`/dev/fd`) back to baseline within 1 s; `stop()` with a handshake pending releases it within
      1 s; open-file reserve still refuses a pending connection at once (reuse the
      `_open_file_limit` monkeypatch pattern of `test_queue_limits.py`); plain server: a silent
      connection is still open after 3 s with `handshake = 2` on a `TLSConfig` it never uses;
      64 clients at once all connect and complete a call.
- [x] Red evidence: run the new tests against the base `src/` (Conventions), save the output to
      `spec/listener-handshake-deadline/evidence/red-develop.txt` (the plain-server and burst tests
      are expected to pass there; say so at the top of the file).
- **Verify:** the P1 `verify:` — every queue suite, the existing three suites' diff against the base
  adds lines only, and the red evidence exists.
- **Touches:** `src/hypershell/core/queue.py`, `src/hypershell/core/tls.py`,
  `src/hypershell/core/config.py`, `tests/test_queue_tls.py`, `spec/…/evidence/`.

## Phase P2 — `server.tls.handshake` refusal at start, and deadline logging
**Satisfies:** R4, R5, R6 (each command reads the key) · **Depends on:** P1
**Goal:** every TLS server command refuses a bad deadline before doing anything else, naming the key;
deadline closes are logged per R5.

- [x] Early refusal: `ServerApp.enable_tls` (`server.py`), `ClusterApp.enable_tls`
      (`cluster/__init__.py`), `RelayApp.check_args` (`relay/__init__.py`, when the site server's
      TLS is enabled) call `handshake_seconds` and raise `ArgumentError(str(error))`.
- [x] `_InProcessServer._closed_pending(peer)`: DEBUG per close (peer, deadline); WARNING naming the
      deadline and `server.tls.handshake` for the first close and at most once per
      `REFUSAL_WARNING_INTERVAL`, from `_handshake_warned_at` under a lock; nothing read from the peer
      is formatted (PLAN §2.2).
- [x] Tests: `handshake_seconds` accepts `30`, `'30s'`, `'1m'`, `2.5` (→ 3), default 30; refuses `0`,
      `-5`, `'abc'`, `True` with a message naming `server.tls.handshake` and the forms; a TLS
      `QueueServer` with each refused value raises `QueueServerError` and leaves nothing bound; a
      plain one starts; `HYPERSHELL_SERVER_TLS_HANDSHAKE=1m` reaches `config.server.tls.handshake` and
      `from_namespace` (subprocess, since `config` is built at import); `ServerApp`/`ClusterApp`
      `enable_tls` carry the key onto `TLSConfig`; caplog: with `REFUSAL_WARNING_INTERVAL`
      monkeypatched long, three deadline closes give three DEBUG lines with the peer and one WARNING
      naming the key, and no record contains the bytes the slow peer sent; a handshake that fails
      otherwise (peer sends non-TLS bytes and closes) logs develop's `Queue server dropped a
      connection` at DEBUG and no WARNING.
- **Verify:** the P2 `verify:` — the suites, and the `refuse` drive (`hs server`, `hs cluster`,
  `hs relay` each exit non-zero naming `server.tls.handshake` for `0`, `-5`, `abc`).
- **Touches:** `src/hypershell/core/queue.py`, `src/hypershell/server.py`,
  `src/hypershell/cluster/__init__.py`, `src/hypershell/relay/__init__.py`, `tests/test_queue_tls.py`.

## Phase P3 — Docs, CLI drives with current and older clients, latency evidence
**Satisfies:** R6, R7, R8, R9 · **Depends on:** P2
**Goal:** the docs say the deadline exists; every TLS server command is shown to keep serving and to
apply the configured deadline; older clients behave as on develop; latency is within R7.

- [x] `docs/_include/config_param_ref.rst`: `[server.tls] .handshake` entry with `versionadded`
      (PLAN §2.4). `docs/security.rst`: threat-model item 5 and *Limitations* item 4 state the
      deadline, `versionchanged`; every other limitation sentence word for word. Apply the
      "Monitor connection patterns" clause only if the foreman signs it off (PLAN §5).
- [x] `AGENTS.md` § Queue transport & security: the decref paragraph's handshake wording (PLAN §2.4).
- [x] Older clients (network to PyPI): `uv venv -q --python 3.14 "$TMPDIR/lhd-clients/2.9.0a8" &&
      VIRTUAL_ENV="$TMPDIR/lhd-clients/2.9.0a8" uv pip install -q hypershell==2.9.0a8`, and the same
      for `2.8.7`.
- [x] Run the P3 `verify:`; save its drive and latency output to
      `spec/listener-handshake-deadline/evidence/drives.txt` and `evidence/latency.txt`. Before the
      verify, dry-run one drive by hand and confirm it stops every process it started (it prints
      `stopped pid …` for each).
- [x] *Amended in build:* `research/drive.py` judges the held connection at the end of the work
      (the client's exit for `server`, the process exit for `cluster`, the relay's "Upstream server
      is shutting down" line for `relay`): a server that stops once the work is done closes it, and
      the planning dry runs (on develop) had never reached that passing branch. The clients
      installed during planning were linked into `$TMPDIR/lhd-clients/`.
- **Verify:** the P3 `verify:` — `-W` docs build (sandbox off); `server`, `cluster`, `relay` drives
  with the default deadline (served while a connection is held, which stays open) and with
  `--handshake 2` (closed in 2–5 s); a 2.9.0a8 client runs the tasks; a 2.8.7 client connects,
  authenticates and is refused as too old, as on develop (subject to the foreman's R8 ruling); R7 by
  `latency.py compare` (three interleaved rounds against the base `src/`).
- **Touches:** `docs/_include/config_param_ref.rst`, `docs/security.rst`, `AGENTS.md`,
  `spec/…/evidence/`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
