---
slug: queue-wire-codec
title: The queue's RPC envelope carries data, not pickled objects
kind: feature
appetite: big
status: done
branch: feature/queue-wire-codec
base: develop
current_phase: done
last_updated: '2026-11-24'
phases:
- id: P1
  name: 'Tests first: wire, refusal and old-peer tests, restated tests, R9 pins as
    tests, drive tooling; red list on 894bccb0 and guards'
  status: done
  satisfies:
  - R7
  - R9
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: RED_LIST=spec/queue-wire-codec/drive/red.txt RED_BASE_REV=894bccb0 python3
    spec/submit-input-checks/drive/bounded.py 2400 sh spec/relay-hardening/drive/red_check.sh
    && sh spec/submit-input-checks/drive/run_list.sh spec/queue-wire-codec/drive/guards.txt
    1200 && python3 spec/submit-input-checks/drive/bounded.py 300 uv run pytest -q
    -p no:cacheprovider --collect-only tests/test_queue_codec.py tests/test_queue.py
    tests/test_dispatch_version.py tests/test_cluster_launch.py
- id: P2
  name: 'core/codec.py: the closed vocabulary over JSON, Latin-1 bytes, R8 limits,
    refusals, the fixed old-peer reply; tests/test_codec.py'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: python3 spec/submit-input-checks/drive/bounded.py 600 uv run pytest -q -p
    no:cacheprovider tests/test_codec.py
- id: P3
  name: 'The codec under both connection pairs (core/queue.py): _EnvelopeCodec, _PlainConnection,
    roles, refusal closes the connection, SecureManager serializer guard, _INTERNALS
    pins'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R6
  - R7
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: downhill
  verify: python3 spec/submit-input-checks/drive/bounded.py 3600 uv run pytest -q
    -p no:cacheprovider -k 'not old_peer' tests/test_codec.py tests/test_queue_codec.py
    tests/test_queue.py tests/test_queue_tls.py tests/test_queue_gc.py tests/test_queue_start.py
    tests/test_queue_limits.py tests/test_queue_address_family.py tests/test_dispatch_version.py
    tests/test_relay_link.py tests/test_relay_fsm.py tests/test_relay_connector.py
    tests/test_relay_db.py tests/test_relay_retire.py tests/test_signal_wake.py tests/test_server_fsm.py
    tests/test_topology_server.py && sh spec/queue-wire-codec/drive/pins.sh && .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/queue-wire-codec/drive/cli_drive.sh"
- id: P4
  name: 'Older peers (R5): the fixed reply and one WARNING on a server, QueueVersionError
    on a client; the restated launch test; bench skew strings; a live 2.9.0a9 drive'
  status: done
  satisfies:
  - R5
  - R6
  - R7
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: downhill
  verify: sh spec/submit-input-checks/drive/run_list.sh spec/queue-wire-codec/drive/red.txt
    2400 && sh spec/submit-input-checks/drive/run_list.sh spec/queue-wire-codec/drive/guards.txt
    1200 && python3 spec/submit-input-checks/drive/bounded.py 3600 uv run pytest -q
    -p no:cacheprovider tests/test_codec.py tests/test_queue_codec.py tests/test_queue.py
    tests/test_dispatch_version.py tests/test_cluster_launch.py tests/test_relay_link.py
    && .agents/factory/bin/temp_site.sh sh "$PWD/spec/queue-wire-codec/drive/old_peer.sh"
- id: P5
  name: 'Real releases (R10, R5, R3): bench_queue.py skew, parity and gate against
    2.9.0a9, read against Q4'
  status: done
  satisfies:
  - R10
  - R5
  - R3
  depends_on:
  - P4
  parallel: false
  hammerable: true
  hill: downhill
  verify: python3 spec/submit-input-checks/drive/bounded.py 3600 uv run python tests/bench/bench_queue.py
    skew --new HEAD --old 2.9.0a9 && python3 spec/submit-input-checks/drive/bounded.py
    3600 uv run python tests/bench/bench_queue.py parity --base 2.9.0a9 --feature
    HEAD --skip-help && python3 spec/queue-wire-codec/drive/q4.py spec/queue-wire-codec/drive/gate.jsonl
- id: P6
  name: 'Docs (R11): security.rst limitation 2 and How It Works, alternatives.rst,
    AGENTS.md, invariants.md §9/§11; the change-log note for the squash body'
  status: done
  satisfies:
  - R11
  depends_on:
  - P4
  parallel: true
  hammerable: true
  hill: downhill
  verify: python3 spec/submit-input-checks/drive/bounded.py 900 /tmp/claude-501/docs-venv-rel/bin/sphinx-build
    -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-codec && sh spec/queue-wire-codec/drive/docs_check.sh
review:
  last_reviewed_commit: 040ccdf0
  verdict: approved
  blocked_reason: 'Cycle 4 (scoped): F5/F6 fixed; F7 SecureManager.get_server() builds
    the stock server; escalated to maintainer, who authorized cycle 5 for F7'
  cycle: 5
---
# TECH.md — The queue's RPC envelope carries data, not pickled objects

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/queue-wire-codec/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–04.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (leaf `task.py` apps, docs,
  tests). The coupled core (`data/model.py`, `server.py`, `client.py`, `core/*`) is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in `.agents/factory/bin/temp_site.sh sh -c "…"` so they hit a throwaway site,
  never the developer's real database).
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md) — it is the constitution. Consult
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §5, §6, §9, §11, §12.
- One phase per `hs-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. Branch commit subjects follow the house style `[feature] Build
  queue-wire-codec P<n>: …` (no `WIP:` prefix) — squashed into the single PR-title commit at
  `hs-publish`.
- **No `Co-Authored-By` trailer** (repo convention; overrides the AGENTS.md default).
- No CLI surface changes, so no `docs/_include/*.rst` help snippets or `share/` completions. The
  user-visible change (the incompatibility) is marked `.. versionchanged:: 2.9.0` on
  `docs/security.rst`; its user-facing note goes in the PR's squash commit body (PLAN § 2.6).
- **Build host:** run pytest serially (`pytest -n auto` is broken there); bind only 127.0.0.1 in any
  probe or drive; Python 3.14 venvs under `/tmp/claude-501/venv314-codec` with
  `UV_CACHE_DIR=/tmp/claude-501/uv-cache-codec` (the shared uv cache is corrupt); `uv` needs
  `pypi.org` and `files.pythonhosted.org`. Never kill a process the build did not start.
- **Naming:** every test that needs the R5 behaviour (P4) has `old_peer` in its name, so P3's verify
  can leave them out with `-k 'not old_peer'`.
- **Public text** (docs, docstrings, log messages, test names and comments) describes the property
  and the fix, never a method of exploiting pickle: hostile-frame tests use benign content and assert
  that nothing was decoded. Examples use top-level commands and the key in `HYPERSHELL_SERVER_AUTH`
  (never `-k`).

---

## Phase P1 — Tests first
**Satisfies:** R7, R9 · **Depends on:** —
**Goal:** every new and restated test exists and collects; each behaviour test fails on the base
(`894bccb0`, whose `src/` is develop's) for what it checks (the red list), and the pins and restated
tests that hold on the base pass there (the guards). No `src/` change.

- [x] `tests/test_queue_codec.py` (new; `@mark.integration`, TLS and cleartext parametrized): a fixture
      that patches `pickle.loads`, `pickle.Unpickler`'s `load` and `multiprocessing.reduction.ForkingPickler.loads`
      to `pytest.fail('unpickled peer bytes')` for the session's duration.
      - R1/R3: a full `QueueServer`/`QueueClient` session drives `connect` (`dummy`), `create` with
        `PROTOCOL`, every `QUEUE_EXPOSED` method (timed `get` raising `queue.Empty` and `put` on a full
        size-2 queue raising `queue.Full` at the caller), every `DISPATCH_EXPOSED` method, `incref`/
        `decref` and the decref hand-off (a proxy finalized on a server-side thread), `accept_connection`;
        payload `bytes` (all 256 byte values, an empty payload, the sentinel) arrive identical. On the
        base these fail by the patch (red).
      - R2: on a raw authenticated connection (`_plain_client`/`secure_client` to the server, then
        `send_bytes` of a crafted frame, one test per kind: unknown tag, two-key tag object, non-`str`
        key, `"b"` above U+00FF, depth 9, 65 elements, `NaN`, bad UTF-8, bad JSON, trailing data, empty
        frame), the server closes that connection (`recv_bytes` → `EOFError`) and logs one WARNING
        naming the peer, while a concurrent `QueueClient` keeps working. Benign content only.
      - R2, client side (the plan gate's ruling): a stand-in server that completes the handshake and
        answers the first request with a frame outside the vocabulary makes `QueueClient.connect`
        raise `QueueVersionError` naming the server within 5 s, and an `UpstreamLink` dialling it fails
        at once (state `FAILED`, one connection, no redial). Benign content only.
      - R6: static: `SecureConnection.recv` and the cleartext connection's `recv` are not
        `_ConnectionBase.recv`; no `loads`/`_ForkingPickler` in their code names; `SecureManager(...,
        serializer='pickle')` raises `ValueError`; with every `HYPERSHELL_*` the test can set, a pickle
        frame is still refused (P4).
      - R5 (`old_peer` in each name): a stock `BaseManager` (default `'pickle'` serializer) connecting
        to `QueueServer` gets a `RuntimeError` whose text names the version mismatch, within 5 s, the
        server logs one WARNING naming its address, and a new client is served meanwhile; a new
        `QueueClient` against a stock pickle-framed server (`multiprocessing.managers.Server` with
        `'pickle'`, served on a thread) raises `QueueVersionError` naming the older framing within 5 s,
        with nothing unpickled; an `UpstreamLink` dialling that server fails at once (state `FAILED`,
        one connection, no redial).
- [x] Restated tests (PLAN § 2.5): `tests/test_dispatch_version.py` `OldClient` subclasses
      `SecureManager` (still no protocol argument), so `test_old_client_is_refused_while_new_client_works`
      checks the same `ClientTooOld` refusal; `tests/test_queue.py`
      `test_create_passes_args_and_refusal_is_a_traceback` with `Caller(SecureManager)`;
      `test_plain_listener_under_default_timeout` dials with `_plain_client` and
      `test_plain_client_under_default_timeout` listens with `_PlainListener`;
      `tests/test_cluster_launch.py`: `CONTRACT_TAG` docstring and module comment restated (the option
      floor stays 2.9.0a4's; the oldest client that can connect is 2.9.0a10), and
      `test_old_client_runs_under_new_launcher` becomes `test_old_peer_client_refused_under_new_launcher`
      (launcher and ssh: the tag's client runs no task and exits non-zero with the fixed reply's text,
      the server log has the R5 WARNING, the cluster exits 5 through the launch watch within the bound,
      the key off every command line).
- [x] R9 pins as tests in `TestPrivateInternals` (`tests/test_queue.py`): research 01 § 5's
      `co_names`/`co_consts` checks (string constants flattened through nested tuples), `Token.__slots__`,
      the challenge on the byte methods, `_ConnectionBase.send`/`recv` through `_send_bytes`/
      `_recv_bytes`; a live check that a non-`EOFError` from `recv` inside `serve_client` keeps the loop
      running. They pass on the base (guards); P3 moves the static ones into `_INTERNALS` too.
- [x] Drive tooling in `spec/queue-wire-codec/drive/`: `red.txt` (the red list, `raises=` where the
      defect's own error is meant), `guards.txt`, `pins.sh` (runs `TestPrivateInternals` on Python 3.11,
      3.12, 3.13 and 3.14 with `uv run --isolated --python 3.X`, bounded), `cli_drive.sh` (P3),
      `old_peer.sh` (P4), `q4.py` (reads `gate --out` JSON lines and applies Q4 per config: the feature's
      median ≥ the base's median − max(5% of it, the base's min-max spread); prints the table; exit 1 on
      a miss), `docs_check.sh` (P6). Each script states its use at the top, as the sibling cycles' do.
- **Verify:** the frontmatter `verify:`: the red list fails on `894bccb0` for what it checks, the
  guards pass, the touched files collect.
- **Touches:** `tests/test_queue_codec.py` (new), `tests/test_queue.py`, `tests/test_dispatch_version.py`,
  `tests/test_cluster_launch.py`, `spec/queue-wire-codec/drive/*`.

## Phase P2 — `core/codec.py`
**Satisfies:** R1, R2, R3, R8 (at the unit level) · **Depends on:** P1
**Goal:** the codec exists as a pure module with its unit tests, written first in this phase; nothing
uses it yet.

- [x] `tests/test_codec.py` (`@mark.unit`) per PLAN § 6 "Unit": round trips of every vocabulary type
      (`bytes` over all 256 values; `bool` apart from `int`; tuple apart from list; `Token`; `Empty`,
      `Full`, `RemoteError`; another exception → `RemoteError('<Type>: <text>')`); every decode refusal
      raises `CodecError` (`PickleFrame` for `0x80`); every encode refusal writes nothing; each real
      message shape from research 01 § 2 passes the limits, depth 9 and 65 elements are refused, on
      encode and decode; `OLD_PEER_REPLY` equals pinned bytes and decodes (in the test, from our own
      constant) to `('#ERROR', RuntimeError(OLD_PEER_TEXT))`; `codec.py` names only `pickle.dumps`.
- [x] `src/hypershell/core/codec.py` per PLAN § 2.1: `encode`, `decode`, `CodecError`, `PickleFrame`,
      `MAX_DEPTH = 8`, `MAX_ITEMS = 64`, `OLD_PEER_TEXT`, `OLD_PEER_REPLY` (protocol 4), `__all__`.
      Exact type dispatch; one walk each way; `json` only.
- **Verify:** `tests/test_codec.py`.
- **Touches:** `src/hypershell/core/codec.py` (new), `tests/test_codec.py` (new).

## Phase P3 — The codec under both connection pairs
**Satisfies:** R1, R2, R3, R4, R6, R7, R9 · **Depends on:** P2
**Goal:** every queue connection, TLS and cleartext, server and client side, encodes and decodes with
the codec and refuses what it cannot decode by closing that connection; the pins guard it.

- [x] `core/queue.py`: `_EnvelopeCodec` (`send`, `recv` only); `SecureConnection(_EnvelopeCodec,
      _ConnectionBase)`; `_PlainConnection(_EnvelopeCodec, Connection)`; `_PlainListener.accept` and
      `_plain_client` build it from the socket (blocking, `TCP_NODELAY`), never patching the stock class;
      connection role (server side; client side with the dialled address) set by `SecureListener.accept`,
      `handle_pending`, `_PlainListener.accept`, `secure_client`, `_plain_client`.
- [x] Refusal (PLAN § 2.2): close first, record the reason, raise `_Refused(EOFError)` on the server
      side, `QueueVersionError` on the client side (fatal: no redial, `exit_status.runtime_error`, as
      ruled at the plan gate); `_InProcessServer.handle_request` logs one WARNING naming the peer and
      the reason, outside the lock. A `0x80` frame is refused the same way for now
      (P4 adds the reply and the old-peer wording).
- [x] `SecureManager.__init__` refuses a `serializer` other than `TLS_SERIALIZER`/`PLAIN_SERIALIZER`
      (`ValueError`).
- [x] `_INTERNALS`: the static R9 pins from P1 join the list (one entry per fact, each with its
      check), so `unsupported_internals()` refuses a Python that reshapes the envelope.
- [x] Update the module and class docstrings that say `send`/`recv` "transparently pickle" or that the
      cleartext wire is "the stock one, byte for byte" (`PLAIN_SERIALIZER`'s docstring, `SecureConnection`,
      `_InProcessServer`, `SecureManager`).
- [x] `drive/cli_drive.sh`: in the throwaway site, `hs cluster` with local clients completes a run
      with TLS on and with `--no-tls`; `hs server --no-db` with `hs client` and `hs submit` into the live
      queue completes (bounded; key in `HYPERSHELL_SERVER_AUTH`; 127.0.0.1); every task's exit status
      is 0 and nothing logs an error.
- [x] **F1 remediation (review cycle 1):** a client builds its proxies from its own method lists.
      `QueueClient.connect` registers each typeid with a `_ClientProxy` over `QUEUE_EXPOSED` or
      `DISPATCH_EXPOSED` (proxy classes built from those names, the full ones at import; never
      `AutoProxy`). The names a `create` reply offers are only compared with the client's own, as a
      set: anything but a tuple of names, or a name outside the client's list, is `QueueVersionError`
      before any proxy exists; a server offering fewer gets a proxy with fewer (R7: the
      earlier-surface dispatcher test passes unchanged). A `#PROXY` reply on a client's connection is
      refused in `_EnvelopeCodec.recv` as outside the vocabulary. Tests first, red on b24d3a28:
      `test_client_refuses_unexpected_method_names` and `test_client_refuses_proxy_it_did_not_ask_for`
      (`tests/test_queue_codec.py`, a codec-speaking stand-in server). The server side is unchanged.
- [x] **F2 remediation (review cycle 2):** on a client's connection `_EnvelopeCodec.recv` refuses a
      `#PROXY` reply as a tuple or as a list (`QueueVersionError`). A stricter rule (refuse any reply
      that is not a tuple pair) was tried and dropped: the transport tests send other values over a
      dialled connection. A dict or text reply cannot yield a proxy: its unpacked result has no token.
      Every other client-side shape check was read: `_ClientProxy` already refuses anything but a
      tuple of names (a list is refused); a `create` result is unpacked by the stock manager and its
      names reach `_ClientProxy`; the client never calls `get_methods` (no `AutoProxy`); reply kinds
      are compared by value, after the `#PROXY` check. Tests first, red on 328c501c: `test_client_refuses_proxy_it_did_not_ask_for` now covers
      both shapes at connect, at a queue call and at a dispatcher call, TLS and plain, and asserts the
      server saw no incref or decref for the unrequested id (6 of 12 red on 328c501c, the list form).
- [x] **F3 remediation (review cycle 2):** `NOT_AN_IDENTIFIER` in `tests/test_queue_codec.py` is the
      inert `'not a method'`; the module-level hook it needed is gone. The test still asserts the
      server saw no incref for the refused object, so the client refuses before any proxy exists.
- [x] **F5 remediation (review cycle 3):** `_ManagerServer` (the server class of every queue manager:
      `_InProcessServer`'s base and `SecureManager._Server`) arms each connection to take its first
      request through `_EnvelopeCodec._recv_request`, which refuses as outside the vocabulary anything
      but a tuple `(ident, name, args, kwds)` naming one of `PUBLIC_REQUESTS` (`Server.public`). It is
      an explicit check, so it holds under `python -O`; the refusal closes that connection, logs one
      WARNING naming the peer and serves the rest. The stock request paths were read on 3.11–3.14 for
      other `assert` statements guarding a peer's input: `_handle_request`'s was the only one
      (`decref` raises `AssertionError` explicitly; the `connection` asserts are Windows-only or check
      constants; `_create` and `register` are client-side). Pins: `_INTERNALS` gains the stock
      `_handle_request` taking the request from `recv` after the challenge, and `TestPrivateInternals`
      the forked manager building `cls._Server` and serving through `_handle_request`. Later requests
      on a connection take the unchanged `recv`, so R10's per-call path is untouched (about 110 ns once
      per connection); the bench was not rerun. Tests first, red on d50f3780:
      `test_server_refuses_non_public_requests` (5 cases, TLS and plain),
      `test_manager_server_refuses_non_public_requests`, and
      `test_server_refuses_non_public_requests_under_optimisation` (a `python -O` server subprocess,
      TLS and plain).
- [x] **F6 remediation (review cycle 3):** the codec refuses a `RemoteError` without text on decoding,
      and sends one raised without text as `RemoteError('RemoteError: ')`. Tests first, red on d50f3780:
      `test_decode_refuses[remote-error-without-text]`, `test_remote_error_always_carries_text`,
      `test_client_refuses_remote_error_without_text`, `test_link_fails_cleanly_on_remote_error_without_text`
      (the link fails with `QueueVersionError` and its thread ends) and
      `test_client_command_exits_with_version_error_on_remote_error_without_text` (`hs client`, exit 5).
- [x] **F7 remediation (review cycle 4):** `SecureManager.get_server` builds `self._Server` (so
      `_ManagerServer`) with the stock method's state checks and arguments, which are the same on
      3.11–3.14; the stock one builds `Server` itself. The only other stdlib construction sites are
      `BaseManager._run_server` (`cls._Server`, already covered) and `SharedMemoryManager.get_server`
      (its own class, not reachable from a `SecureManager`). Pin: `TestPrivateInternals` checks that the
      stock `get_server` makes the same state checks as ours, then builds the stock `Server` from
      `_registry`, `_address`, `_authkey` and `_serializer` positionally. It is not in `_INTERNALS`, since
      the in-process server never calls `get_server`. Tests first, red on 968f032c:
      `test_get_server_builds_the_manager_server` and
      `test_get_server_refuses_non_public_requests_under_optimisation` (a `python -O` subprocess, TLS and
      plain: every non-public first request is closed unanswered, the listener is unchanged, nothing
      listens at an address named by a request, and nothing is unpickled).
- **Verify:** the frontmatter `verify:` (the queue, dispatch, relay and launch-adjacent suites minus
  `old_peer`, `pins.sh` on four Pythons, the CLI drive).
- **Touches:** `src/hypershell/core/queue.py`, `src/hypershell/core/codec.py`.

## Phase P4 — Older peers
**Satisfies:** R5, R6, R7 · **Depends on:** P3
**Goal:** a 2.9.0a9-or-earlier peer is refused on both sides as R5 says, never by a hang, a traceback
or an unpickle; every red test is now green.

- [x] Server side: on `PickleFrame`, `send_bytes(OLD_PEER_REPLY)`, close, `_Refused` marked old-peer;
      the WARNING reads `Refused a queue connection from <host:port>: the peer speaks the queue framing
      of a release before 2.9.0a10; run matching server and client versions` (wording final here).
- [x] Client side: on `PickleFrame`, close and raise `QueueVersionError('Server at <host:port> speaks the
      queue framing of a release before 2.9.0a10; run matching server and client versions')`. Confirm by
      test that `hs client`, `hs submit` and a relay's upstream link each end with one CRITICAL line and
      `exit_status.runtime_error` through the existing mapping, no new handler.
- [x] `tests/bench/bench_queue.py` `skew`: the pass strings become the R5 WARNING (old client → new
      server) and the R5 CRITICAL (new client → old server); the module docstring's `skew` line says so.
      The `Client too old` count stays only as a codec-peer case if kept at all.
- [x] `drive/old_peer.sh`: a throwaway venv with `hypershell==2.9.0a9` (`uv pip install`, cached under
      `/tmp/claude-501/venv314-codec`); for TLS on and off: an a9 `hs client` and an a9 `hs submit` into a
      live queue against this tree's `hs server --no-db` (each exits 5 within 10 s, one connection, the
      server logs one WARNING per connection); this tree's `hs client`, `hs submit` and `hs relay` against
      an a9 `hs server` (each exits 5 within 10 s with one CRITICAL naming the older framing, no redial).
- **Verify:** the frontmatter `verify:` (the whole red list and the guards green, the touched suites,
  the live 2.9.0a9 drive).
- **Touches:** `src/hypershell/core/queue.py`, `tests/bench/bench_queue.py`, `spec/queue-wire-codec/drive/old_peer.sh`.

## Phase P5 — Real releases
**Satisfies:** R10, R5, R3 · **Depends on:** P4
**Goal:** the benchmark shows no regression against 2.9.0a9 within Q4, and the release-to-release
skew and parity checks pass against the committed tip.

- [x] Run with P4 committed (`HEAD` is the committed tip: the bench builds refs with `git archive`):
      `uv run python tests/bench/bench_queue.py gate --base 2.9.0a9 --feature HEAD --repeats 5 --out
      spec/queue-wire-codec/drive/gate.jsonl` with `--only` for every `b=1` row and every
      `client-default` row (bundle size 1 is also the configured default). Record the printed table in
      `spec/queue-wire-codec/drive/gate.md` with the host, load average and Python.
- [x] `q4.py` passes on every recorded config. If a config misses, do not loosen Q4: amend this file
      with a remediation phase (PLAN § 5 risk 1: carry large `str`/`bytes` leaves out of band in the
      same frame; then a fast path for the hot shapes) and re-run.
- [x] `skew --new HEAD --old 2.9.0a9` and `parity --base 2.9.0a9 --feature HEAD --skip-help` pass
      (the `--help` text is unchanged, but parity's help check is not this cycle's contract).
- **Verify:** the frontmatter `verify:` (skew, parity, and `q4.py` over the recorded gate run; the gate
  itself runs once, by the step above, since it takes hours).
- **Touches:** `spec/queue-wire-codec/drive/gate.jsonl`, `spec/queue-wire-codec/drive/gate.md`.

## Phase P6 — Docs and the change-log note
**Satisfies:** R11 · **Depends on:** P4
**Goal:** every page and agent file that describes the old envelope describes the new one, and the
change-log line is ready for `hs-publish`.

- [x] `docs/security.rst`: known limitation 2 rewritten (the closed vocabulary; neither end unpickles a
      peer's bytes; what an authenticated peer can still do; what this does not give: per-peer identity,
      channel binding, transport DoS controls, any guarantee against a compromised server's commands),
      with `.. versionchanged:: 2.9.0` naming the incompatibility; "How It Works" (`:135-151`): framing
      and handshake are the stock manager's, the envelope is HyperShell's own on both pairs.
- [x] `docs/alternatives.rst:826-828` rewritten to match.
- [x] `AGENTS.md`: the wire-format bullet, the pinned-surface paragraph, "The server process hosts the
      manager", the `core/queue.py` hotspot line, the 2.9.0a4 contract sentence; and
      `.agents/factory/invariants.md` §9 and §11 in lockstep.
- [x] `spec/queue-wire-codec/drive/changelog.md`: the squash-body note (PLAN § 2.6) for `hs-publish`.
- [x] `drive/docs_check.sh`: fails if `docs/`, `AGENTS.md` or `invariants.md` still says the RPC is
      pickle-framed or that the wire is the stock manager's, or if `docs/` shows `-k`/`--auth` or an
      `hs task` command in a changed line.
- [x] **F1 remediation (review cycle 1):** `docs/security.rst` limitation 2 says a client builds its
      proxies from its own method lists and stops at a server offering any other method, and its
      `versionchanged:: 2.9.0` says so; `AGENTS.md` (wire format) and `invariants.md` §9 describe
      `_ClientProxy`; `drive/changelog.md` notes the outcome.
- [x] **F4 remediation (review cycle 2):** `docs/security.rst` (limitation 2's `versionchanged`), the
      `SecureManager` docstring and `drive/changelog.md` say the client-side refusals belong to
      HyperShell's own queue client, and that code using `SecureManager` directly as a client should
      register each type with an explicit proxy type and list of methods. `AGENTS.md` and
      `invariants.md` §9 say the `#PROXY` refusal holds for either shape.
- [x] **F5 docs (review cycle 3):** `docs/security.rst` limitation 2, the `SecureManager` docstring and
      `drive/changelog.md` say a server refuses a connection whose first request is not one of the
      manager's public requests, under any interpreter flags; `AGENTS.md` (wire format) and
      `invariants.md` §9 name `_ManagerServer` and the no-`assert` rule, and the `RemoteError` text rule.
- **Verify:** the frontmatter `verify:` (docs build with `-W`, then `docs_check.sh`).
- **Touches:** `docs/security.rst`, `docs/alternatives.rst`, `AGENTS.md`, `.agents/factory/invariants.md`,
  `spec/queue-wire-codec/drive/changelog.md`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
