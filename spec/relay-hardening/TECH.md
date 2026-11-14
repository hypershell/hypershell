---
slug: relay-hardening
title: 'Relay hardening for 2.9.0: no aborts on removed rows or a restarted upstream,
  a TLS mismatch that ends, zones that wait for their work, eviction by the server''s
  clock'
kind: fix
appetite: big
status: in_review
branch: fix/relay-hardening
base: develop
current_phase: done
last_updated: '2026-11-14'
phases:
- id: P1
  name: 'Shared conditional task writes: the eviction revert and the dispatch stamp,
    every server'
  status: done
  satisfies:
  - R1
  - R2
  - R9
  - R10
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_return.py tests/test_contended_recovery.py
    tests/test_writeback.py tests/test_writeback_statement.py tests/test_server_fsm.py
    tests/test_relay_roster.py tests/test_topology_server.py tests/test_group_order.py
    tests/test_retry_gate.py tests/test_zone_model.py tests/test_zone_dispatch.py
    tests/test_restart.py tests/test_cancel.py && for ts in community ''; do HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE=$ts uv run pytest -v tests/test_contended_write.py tests/test_writeback_postgres.py
    tests/test_zone_postgres.py || exit 1; done && HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE=community sh spec/relay-hardening/drive/red_check.sh
- id: P2
  name: Eviction aged by the server's monotonic receive time, every client
  status: done
  satisfies:
  - R7
  - R9
  - R10
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_server_fsm.py tests/test_relay_roster.py tests/test_topology_server.py
    tests/test_zone_dispatch.py tests/test_relay_launch.py tests/test_relay_outage.py
    tests/test_server.py && sh spec/relay-hardening/drive/red_check.sh && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
- id: P3
  name: A restarted upstream's stale proxy id counts as a lost link, all four roles
  status: done
  satisfies:
  - R3
  - R9
  - R10
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_link.py tests/test_relay_outage.py tests/test_relay_retire.py
    tests/test_relay_connector.py && sh spec/relay-hardening/drive/red_check.sh
- id: P4
  name: TLS mismatch with the upstream fails fast at first connection, both directions
  status: done
  satisfies:
  - R4
  - R9
  - R10
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_link.py tests/test_relay_workload.py tests/test_queue_tls.py
    && sh spec/relay-hardening/drive/red_check.sh && RED_BASE_REV=ff0973a RED_LIST="$PWD/spec/relay-hardening/drive/red-ff0973a.txt"
    sh spec/relay-hardening/drive/red_check.sh && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
- id: P5
  name: An idle zone asks once for itself after a shutdown reply; relay docs
  status: done
  satisfies:
  - R5
  - R6
  - R9
  - R10
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_relay_connector.py tests/test_relay_workload.py
    tests/test_relay_retire.py tests/test_dispatch.py tests/test_dispatch_staged.py
    && sh spec/relay-hardening/drive/red_check.sh && RED_BASE_REV=ff0973a RED_LIST="$PWD/spec/relay-hardening/drive/red-ff0973a.txt"
    sh spec/relay-hardening/drive/red_check.sh && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
- id: P6
  name: 'End-to-end laptop evidence: restart, TLS, zones and pair drives; earlier
    relay drives and the full suite on the head'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R9
  - R10
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: for mode in restart tls-on tls-off zones zones-nodb pair-relay pair-server;
    do .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-hardening/drive/hardening_drive.sh"
    $mode || exit 1; done && sh spec/relay/drive/relay_smoke.sh && for mode in retire
    idle; do .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-retire/drive/retire_drive.sh"
    $mode || exit 1; done && for mode in spread fill kill; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/relay-topology/drive/topology_drive.sh" $mode || exit 1; done &&
    uv run pytest && sh spec/relay-hardening/drive/red_check.sh
- id: P7
  name: 'Tail investigation on the laptop: tooling, diagnostic runs, the fix if the
    logs name one, post-fix runs'
  status: done
  satisfies:
  - R8
  depends_on:
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_tail_judge.py && uv run python spec/relay-hardening/drive/tail_judge.py
    --budget 10 spec/relay-hardening/evidence/laptop && uv run pytest -v tests/test_relay_connector.py
    tests/test_relay_return.py tests/test_server_fsm.py tests/test_relay_workload.py
- id: P8
  name: Conditional cluster tail job (foreman submits) and the recorded R8 outcome
  status: done
  satisfies:
  - R8
  depends_on:
  - P7
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_tail_judge.py && uv run python spec/relay-hardening/drive/tail_judge.py
    --budget 10 --outcome spec/relay-hardening/evidence/laptop
review:
  last_reviewed_commit: 4baaada36a22532b7d69a04b7b75583590a37e45
  verdict: approved
  blocked_reason: ''
  cycle: 3
---
# TECH.md — Relay hardening for 2.9.0

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay-hardening/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`07`.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work. The coupled core
  (`data/model.py`, `server.py`, `client.py`, `core/*`) is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` → `crest` → `downhill`. A phase stuck `uphill` across builds is a raised hand.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from [`AGENTS.md`](../../AGENTS.md);
  consult [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §3, §4, §5, §6,
  §9, §12 for this change.
- One phase per `hs-build` invocation; one atomic commit with the code and this file's state change.
  Subjects `[fix] Build relay-hardening P<n>: …`. **No `Co-Authored-By` trailer.**
- Docs in the same commit as each user-visible change: a `.. versionchanged:: 2.9.0` line (no
  `:pr:`; `hs-release` adds it) where released behaviour changes; relay pages are new in 2.9.0, as
  `relay.rst:92` shows the precedent for a change line there. No CLI change in this cycle, so no
  `docs/_include` help snippet or `share/` completion changes.
- **Environment.** Keep the worktree's `.venv` intact: never `uv run --python …` without
  `UV_PROJECT_ENVIRONMENT` pointing elsewhere (the docs command in each verify does). The PostgreSQL /
  TimescaleDB legs, the docs build (`sphinx_sitemap`) and the laptop drives run with the sandbox off;
  29 upgrade-template failures under `HS_TEST_POSTGRES` in the **full** suite are expected and
  unrelated (the per-phase verifies do not include those files).
- **R9 red check.** Each phase adds the node ids of its new regression tests to
  `spec/relay-hardening/drive/red.txt` (one per line; prefix `pg:` for a test that needs
  `HS_TEST_POSTGRES`). `drive/red_check.sh` (P1) exports `2ffb5cc`'s `src` with `git archive` into a
  fresh `$TMPDIR` directory, runs each listed test with `PYTHONPATH=<export>/src` (ahead of the
  editable install; `hs` subprocesses inherit it), and exits non-zero unless **every** listed test
  fails there; `pg:` lines run only when `HS_TEST_POSTGRES` is set and are skipped with a note
  otherwise. It prints one line per test (`RED`/`NOT RED`) and never edits the tree. A test that
  fails on `2ffb5cc` only on TimescaleDB 2.29–2.31 (not reproducible on the local 2.32) is **not**
  listed; it is named in the phase's commit body as CI-only red. *Since review cycle 1:* a test is
  `RED` only when its first failure is in the test's call and is an assertion (`assert`,
  `pytest.fail`) or the exception its line names (` raises=NAME`), as recorded by
  `drive/red_outcome.py`, a pytest plugin the check loads; a failure in collection, setup or
  teardown, or on an import, attribute, type or other error, is `NOT RED`. A test that could fail on
  `2ffb5cc` only on a missing API, or through a harness that cannot see that build's write, is not
  listed; P1's remediation names where the red for its defect lives.
- Line numbers below are `develop` at `2ffb5cc`.

---

## Phase P1 — Shared conditional task writes
**Satisfies:** R1, R2, R9, R10 · **Depends on:** —
**Goal:** the eviction revert and the dispatch stamp are per-row conditional Core writes through the
one §1 loop, on every server; a gone, completed or no-longer-qualifying row is skipped, never raised
on; a contended row that still qualifies lands. Design: PLAN §2 R1, R2; research 01, 02.

- [x] Write `spec/relay-hardening/drive/red_check.sh` and `drive/red.txt` (see Conventions); a
      `sh -n` check and one dry run with an empty list (exits 0, prints nothing to check).
- [x] Generalise `Task.__row_statements` (`model.py:916`) / `__write_rows` (`:937`): a condition
      argument (`pending` as today; `held_by=<client id>` → `client_id = :r_held_by AND schedule_time
      IS NOT NULL AND completion_time IS NULL`; `unscheduled=True` → `schedule_time IS NULL`), a bind
      prefix (default `r_`; every existing statement byte-for-byte as today), the re-read carrying the
      same binds, and a 0-row outcome that **skips** a gone / no-longer-qualifying row at DEBUG
      (non-strict) instead of raising `StaleDataError`. Skip messages: "already complete" (revert),
      "already scheduled" (stamp). The re-run at most `CONTENDED_RERUNS`, DEBUG per extra attempt,
      WARNING at the bound, unchanged.
- [x] `Task.revert_orphaned` (`:1224`) writes through `__write_rows(…, 'revert', 'task left in
      flight', held_by=client_id)`; `select_orphaned` unchanged; `revert_all` unchanged (start-up
      recovery). Docstrings say the revert never undoes a landed result or touches a removed row.
- [x] `Task.next` (`:793-810`): ids via `sqlalchemy.inspect(task).identity[0]`; stamp
      `server_id`/`server_host`/`schedule_time` through `__write_rows(…, 'dispatch', 'task left
      unscheduled', unscheduled=True, prefix='d_')`; commit; return the landed rows in order; set no
      ORM attribute (objects reload in `pack_bundle`, so the wire `schedule_time` is the stored
      value). The retry branch untouched (Non-goals).
- [x] `AGENTS.md` and `.agents/factory/invariants.md` §1 "Pattern" name the eviction revert
      (`Task.revert_orphaned`) and the dispatch stamp (`Task.next`), in lockstep.
- [x] R9 tests (each in `red.txt` unless CI-only):
  - `tests/test_relay_return.py` (`before_cursor_execute` on the first `UPDATE task` after the read,
    no threads): **revert, relay-local** — the hook applies one result and runs `collect_results`;
    `revert_orphaned` returns, the other row is reverted, the collected row stays collected;
    optionally through `HeartMonitor.check_clients`, which returns its next state. **revert,
    primary** — the hook commits the Receiver's result on its own connection; the result is kept,
    `Task.next` does not return it, the other row is reverted. **stamp, gone** — three unscheduled
    rows, the hook deletes row 2; `Task.next(limit=3)` returns rows 1 and 3, both stamped.
    **stamp, cancelled** — the hook cancels row 2; it is skipped and stays cancelled.
  - `tests/test_contended_recovery.py` (SQLite fault injection; extend `Contention` for new tests only
    to key on the stamp's `d_id` too): revert `lose(id, 1, meanwhile=…)` with completion / confirm
    to another client / delete → kept / left with that client / skipped, no WARNING; stamp lose 1 →
    lands with one DEBUG re-run; lose always → `['UPDATE','SELECT'] × attempts`, one WARNING naming
    the task and "dispatch", row unscheduled and taken by the next `Task.next`; lose + cancelled /
    deleted meanwhile → skipped; uncontended → one UPDATE per row.
  - `tests/test_contended_write.py` (`pg:`): `contend()` holders that write a result, `DELETE` the
    row, or cancel it, for the revert and for the stamp → kept / skipped / skipped. Holder `UPDATE
    client_host` for the stamp → lands (CI-only red: TimescaleDB 2.31).
- [x] **Review-cycle-1 remediation (RV2, RV3).**
  - RV2: the §1 sentence added to `.agents/factory/invariants.md` is scoped to the conditional
    writes (`revert_staged`, the eviction revert, the dispatch stamp): they skip a row gone or no
    longer qualifying; `confirm_all` (strict, the default) and `revert_all` still raise
    `StaleDataError` on a gone row, by design. `AGENTS.md` carries the same sentence.
  - RV3, the check: `red_check.sh` requires each failure to be an assertion or the defect's own
    error, named on the line (see Conventions). Shown on `2ffb5cc` with a control list: the two
    TLS link tests read `NOT RED (AttributeError, not an assertion)`, the returner test `NOT RED
    (TypeError, not an assertion)`, an R2 test whose `StaleDataError` is not named `NOT RED`, a
    passing test `NOT RED (pytest exit 0)` and a missing file `NOT RED (pytest exit 4)`. **Limit:**
    an assertion that fails because the harness cannot see the base build's write still reads
    `RED` (the contended dispatch tests below do), so each entry's failure on `2ffb5cc` was read:
    the check prints its kind.
  - RV3, the list: 24 entries, every one red on `2ffb5cc` for its defect. ` raises=StaleDataError`
    on the three whose defect is that error (the revert of a row collected meanwhile, the stamp of
    a row deleted meanwhile, the contended revert of a deleted row); ` raises=RemoteError` on R3's
    four restart tests. Dropped, kept in the suite as tests of the head: the five
    `test_contended_recovery.py` dispatch tests (the injection, keyed on the stamp's `d_id` bind,
    never meets `2ffb5cc`'s ORM flush, which runs on the connection and not through the session's
    `execute` the harness wraps; the uncontended cost test pins the fix's cost, no defect), the two
    `test_relay_link.py` TLS tests (no `first_connect` on `2ffb5cc`) and P7's returner test (no
    `idle` on `2ffb5cc`).
  - Where the red for those defects lives: the stamp of a row deleted or cancelled meanwhile,
    `test_relay_return.py` (SQLite) and `test_contended_write.py::test_contended_dispatch_skips_*`
    (PostgreSQL, TimescaleDB 2.32); the contended stamp of a row that still qualifies, which lands
    on a re-run, CI's TimescaleDB 2.31 jobs only (`test_contended_write.py::test_contended_dispatch_lands`),
    per plan-gate ruling 7, with no red on any local backend; R4, the two `test_relay_workload.py`
    CLI tests (by behaviour: the relay still runs at 12 s); P7's fix (i), no unit red on `2ffb5cc`,
    its effect the post-fix runs' s2 (0.07–0.12 s against 2.9–3.8 s). Keying the SQLite injection
    on the statement was not taken: reaching an ORM flush needs a second, cursor-level harness, for
    a case CI's 2.31 jobs already show.
- **Verify:** see frontmatter — the SQLite model, writeback, recovery, scheduler, group, zone and
  cancel suites; the PostgreSQL and TimescaleDB legs; the red check (sandbox off).
- **Touches:** `src/hypershell/data/model.py`, `tests/test_relay_return.py`,
  `tests/test_contended_recovery.py`, `tests/test_contended_write.py`, `AGENTS.md`,
  `.agents/factory/invariants.md`, `spec/relay-hardening/drive/red_check.sh`, `drive/red.txt`.
  *Review cycle 1:* also `drive/red_outcome.py`.

## Phase P2 — Eviction by the server's receive time
**Satisfies:** R7, R9, R10 · **Depends on:** P1
**Goal:** no client that keeps beating is evicted, whatever its clock; a silent one is evicted
`server.evict` after its last beat arrived. Design: PLAN §2 R7; research 06.

- [x] `HeartMonitor` (`server.py:698`): `received: Dict[str, float]` beside `beats` (init `:737`);
      `time.monotonic()` taken right after the `get` in `get_next` (`:770`), stored with the beat at
      `:801`, popped wherever `beats` pops (`:810`, `:921`). `check_clients` (`:913`): one monotonic
      `now` per check, `age = timedelta(seconds=now - received[uuid])`, strict `> evict_after`; a
      missing stamp never evicts. `hb.time`, `connected_at`, `last_check` and the wire untouched.
- [x] Adapt the four tests that make a client stale through its sender time
      (`test_relay_roster.py:223`, `:500`; `test_topology_server.py:104`, `:435`) to back-date
      `machine.received[uuid]` instead; **assertions unchanged** (PLAN §3 deviation row 1).
- [x] R9 tests (`tests/test_server_fsm.py`, `TestHeartMonitor` style, `in_memory=True`, no sleeps):
      a beat stamped `now − 3×evict` survives `check_clients` (red); a beat stamped `now + 1 h` whose
      receive stamp is back-dated past evict is evicted (red); exactly evict kept, past it evicted.
      One DB-backed case (`test_relay_roster.py` style): a relay beat skewed 3×evict is not evicted,
      its roster stays, its tasks are not reverted (red). *As built:* the two clock-dependent cases
      move a stand-in for the `time` module that `hypershell.server` sees (`FakeClock`) instead of
      editing `received`, so on `2ffb5cc` they fail on their assertions rather than on a missing
      attribute.
- [x] Docs: remove the clock-skew bullet `docs/cli/relay.rst:116-118`; restate `server.evict` in
      `docs/_include/config_param_ref.rst:269-274` (by the server's clock, from the last heartbeat
      received) with `.. versionchanged:: 2.9.0` inside its `.. only:: html` as at `:222-225`.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/server.py`, `tests/test_server_fsm.py`, `tests/test_relay_roster.py`,
  `tests/test_topology_server.py`, `docs/cli/relay.rst`, `docs/_include/config_param_ref.rst`,
  `drive/red.txt`.

## Phase P3 — Stale proxy id is a lost link
**Satisfies:** R3, R9, R10 · **Depends on:** P2
**Goal:** after an upstream restart at the same address, each role's first call either succeeds or
reconnects and carries on; never exit 5, never a refused hand-back on that account. Design: PLAN §2
R3; research 03.

- [x] `core/reconnect.py`: `is_stale_proxy(error)` (a `RemoteError` whose text's last non-blank line
      matches `KeyError: '[0-9a-f]+'`), OR-ed into `is_transport` (`:101`); `call` logs a short
      reason ("upstream server restarted (unknown proxy id …)"), not the remote traceback; the module
      docstring's error classes updated. No change to `core/queue.py`.
- [x] R9 tests in `tests/test_relay_link.py` (`@mark.integration`): parametrised over
      `puller`/`heartbeat`/`returner`/`handback` with each role's real call shape; `QueueServer` on
      port 0, link with deadline 5 and backoff 0.01–0.02, warm up through another role, shut the old
      server down **keeping it referenced**, start a new one on the same port; the first call raises
      `LinkDown` (never `RemoteError`), then `wait_up`, the same call succeeds and `link.epoch`
      advanced (red, ×4). A unit test of the predicate: true for the real text; false for another
      `RemoteError` text, a bare `KeyError`, `RemoteError('Unserializable message: …')`. Optional
      role-level check (`RelayThread.hand_back` keeps `handback_due`, logs no "Could not hand back").
      *As built:* the short reason comes from `describe` beside `is_stale_proxy` (`stale_proxy_id`
      gives the id); the restart test also asserts that the one "Lost connection" WARNING names the
      restart and carries no traceback. The optional role-level check is not taken: each role's
      `LinkDown` handling is unchanged and already covered, and P6's `restart` drive runs the roles
      end to end.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/core/reconnect.py`, `tests/test_relay_link.py`, `drive/red.txt`.

## Phase P4 — TLS mismatch fails fast
**Satisfies:** R4, R9, R10 · **Depends on:** P3
**Goal:** a relay whose TLS setting differs from its upstream's exits 5 within one deadline of its
first attempt, in both directions, with no site client launched and an error naming its own setting;
refused/unroutable at start and losses after a first connection retry as today. Design: PLAN §2 R4;
research 04.

- [x] `core/reconnect.py`: an optional first-connection hook on `UpstreamLink`, called inside the
      `dial` connect thread only while `not self.connected`, with the remaining deadline; a mismatch
      error class subclassing `TLSError` (fatal through `FATAL_ERRORS` unchanged).
- [x] `relay/upstream.py`: `speaks_tls(address, timeout)` (IPv4, throwaway `CERT_NONE` context, no
      data, no key; true only on a completed handshake) and `speaks_plain(address, timeout)` (4-byte
      big-endian length then `#CHALLENGE#`; optionally capped at 5 s); the hook: `--no-tls` → check
      TLS before the dial; TLS → on an `SSLError` other than `SSLCertVerificationError`, check plain,
      raise the mismatch from the original or re-raise it. Messages as research 04 §Wording, naming
      `--no-tls` / `[relay.upstream.tls] enabled` and "must agree on TLS".
- [x] `relay/__init__.py`: `RelayApp.exceptions` (`:1094-1100`) maps the mismatch to
      `handle_exception` with `exit_status.runtime_error` (one CRITICAL line, no traceback file).
- [x] R9 tests: `tests/test_relay_link.py` — both directions against an in-process `QueueServer`
      with/without TLS, `wait_up` raises the mismatch in < 1 s with the setting and "must agree" in
      the text (red ×2; bound every wait, since on `2ffb5cc` the plain → TLS case never returns on
      its own); connected-once-then-mismatched → DOWN and retrying, not FAILED;
      refused at start → retrying, then connects when a matching server comes up.
      `tests/test_relay_workload.py` (CLI, as the wrong-key test at `:746`): `hs server --forever`
      with and without `--no-tls`, key in `HYPERSHELL_SERVER_AUTH`, relay opposite with
      `HYPERSHELL_RELAY_UPSTREAM_DEADLINE=5` and a recording launcher: exit 5 within 5 s, the text,
      no launch, no "Relay-local server ready" (red ×2).
- [x] Docs: one sentence in `docs/cli/relay.rst` (TLS on the upstream hop) that a TLS setting that
      differs from the upstream server's ends the relay at once with an error, with
      `.. versionchanged:: 2.9.0`.
      *As built:* the hook is `UpstreamLink(first_connect=…)`, called with the new client and the
      seconds left of the attempt in place of `client.connect()`; the relay passes
      `relay.upstream.first_connect(upstream)`. The error is `reconnect.TLSMismatchError`. A TLS
      relay checks for a cleartext challenge only on a plain `ssl.SSLError`: a certificate failure,
      `SSLEOFError` and `SSLZeroReturnError` are re-raised as they are. `speaks_plain` is capped at
      `PLAIN_CHECK_WAIT` (1 s since review cycle 1, below). The docs sentence is in `docs/_include/relay_desc.rst` (beside the
      relay that waits for an unreachable upstream, so the man page carries it too), with the
      `versionchanged` after its `.. include::` in `docs/cli/relay.rst`. The cleartext → TLS link
      test serves its TLS upstream from an `hs server` process, killed with the test, so every wait
      is bounded. The two link tests are red on `2ffb5cc` because the relay's hook does not exist
      there; the two CLI tests are red by behaviour (the relay is still running at the 12 s bound).
- **Verify:** see frontmatter.
- [x] **Review-cycle-1 remediation (foreman's note).** `PLAIN_CHECK_WAIT` is 1 s, not 5 s: a
      cleartext upstream sends its challenge as soon as it accepts (about 1 ms measured), so a 1 s
      wait loses nothing. The R4 tests and the `tls-on` / `tls-off` drives re-run on the head (P6).
      RV3 took the two TLS link tests out of `red.txt` (P1).
- [x] **Review-cycle-2 remediation (RV6).** A relay with TLS on recognises a cleartext upstream from
      its own failed handshake, which is simpler and independent of timing: no second connection and
      no wait. `first_connect` raises `TLSMismatchError` only on an `ssl.SSLError` whose `reason` is
      `WRONG_VERSION_NUMBER` (`upstream.CLEARTEXT_REASON`), the error the TLS layer reports when the
      peer's first record does not begin with a TLS version (major byte 3); a cleartext queue server
      opens with the 4-byte length of its challenge (first byte 0). Every TLS record a TLS server
      sends begins with a content type and a 3.x version, so a TLS upstream cannot produce it,
      however slowly it answers. A certificate failure (`CERTIFICATE_VERIFY_FAILED`), `SSLEOFError`,
      `SSLZeroReturnError`, an alert from a TLS server and a refused connection are re-raised as they
      are, so the link treats each as before. `speaks_plain`, `PLAIN_CHECK_WAIT` and `CHALLENGE` are
      gone (nothing else used them); `speaks_tls` stays for the TLS-off direction. Measured: Python
      3.14.6 with OpenSSL 3.5.7 reports `[SSL: WRONG_VERSION_NUMBER]` against a cleartext
      `QueueServer`, directly and through the review's 1.1 s-per-chunk proxy (after 1.11 s).
  - Tests (`tests/test_relay_link.py`): a cleartext `QueueServer` behind `SlowPath` (a loopback proxy
    holding each chunk 1.1 s): `TLSError` naming the setting within 1.1 s + 2, no retry attempt
    counted, **red on `ff0973a` by assertion** (`DID NOT RAISE`: the check gave up after 1 s and the
    `SSLError` was retried), listed in the new `drive/red-ff0973a.txt`; a TLS `QueueServer` behind
    the same path completes a handshake of more than a second and connects, never classed as
    cleartext (a `Handshake` client stand-in makes the TLS handshake with the queue client's context
    and nothing else: a whole queue-client connection over that path takes minutes, one round trip
    per proxy); the classification itself, parametrised (certificate, EOF, zero-return, alert,
    refused → re-raised as is; `WRONG_VERSION_NUMBER` → the mismatch from that error). The R4 tests
    already there are unchanged and green; the `tls-on` / `tls-off` drives re-run on the head, PASS
    (`evidence/review-2/`).
- **Touches:** `src/hypershell/core/reconnect.py`, `src/hypershell/relay/upstream.py`,
  `src/hypershell/relay/__init__.py`, `tests/test_relay_link.py`, `tests/test_relay_workload.py`,
  `docs/_include/relay_desc.rst`, `docs/cli/relay.rst`, `drive/red.txt`. *Review cycle 2:* also
  `drive/red-ff0973a.txt`.

## Phase P5 — Idle zones and the relay docs
**Satisfies:** R5, R6, R9, R10 · **Depends on:** P4
**Goal:** a zone ends only when upstream has nothing staged for it; staged rows run at the site once
the zone has a client, with or without an upstream database; the docs say what a relay with no
client anywhere does. Design: PLAN §2 R5, R6; research 05.

- [x] `relay/upstream.py`: in `UpstreamPuller.gate` (`:365-398`), once `self.finished` is non-empty a
      zone with bound 0, nothing held and not finished is gated in with size 1; remove `finish_idle`
      (`:400-411`) and its call (`:378`); an INFO line when an idle zone takes tasks at a finish and
      waits for a client. Restate the comment at `:247-249` (research 05 §Corrected texts).
- [x] `tests/test_relay_connector.py`: restate the docstring at `:557-561` (and rename the test to
      `…_asks_once_and_ends_on_its_own_shutdown`); the request lists at `:566` / `:607` become
      `['cpu', 'gpu']`; every other assertion unchanged (PLAN §3 deviation row 2).
- [x] R9 tests (`tests/test_relay_connector.py`, `@mark.unit`, no threads, the real `Dispatcher`,
      `zoned_puller` / `drive` / `complete_all`), parametrised over an upstream with and without a
      database: rows staged for the idle zone after `finish()` → the puller does not reach FINAL, the
      zone holds 1 and the rest stay staged; a client arrives → the zone takes the rest; then FINAL
      with every staged id run (red ×2). Nothing staged → FINAL at once, both zones finished.
- [x] Docs: `docs/_include/relay_desc.rst:56-59` per research 05 §R6 (R5's rule; R6: no client in
      any zone → never asks, never hears an upstream shutdown, and a non-forever upstream server
      waits for it too until it is drained or stopped); `.. versionchanged:: 2.9.0` after the
      `.. include::` in `docs/cli/relay.rst` (one clause: an idle zone asks once more at an upstream
      shutdown and takes what is still staged for it).
      *As built:* the gate marks such a request `idle` (size 1); its INFO line, when the reply carries
      tasks, names the zone and the count held until a site client runs them. The restated comment
      is the class docstring of `UpstreamPuller` (the comment the plan cites moved there in P4's line
      shift); it also says an empty reply has the zone ask again. The R9 test is
      `test_a_zone_without_site_clients_takes_what_is_staged_for_it_at_a_shutdown[database|no-database]`
      (red ×2 by behaviour: on `2ffb5cc` the puller reaches FINAL with rows staged); the nothing-staged
      case is `test_a_zone_without_site_clients_ends_at_once_with_nothing_staged_for_it`, which also
      asserts that both identities asked. No `share/` change (no CLI change); the man page picks up
      the text at the next `hs-release`.
- **Verify:** see frontmatter.
- [x] **Review-cycle-1 remediation (RV1).** The one-task request is made only by a zone whose fleet
      would launch a client for it. `UpstreamPuller` takes `view`, the relay's `TopologyView` (the one
      its fleets follow; `RelayThread` passes it), and `launches(zone)` is true without a published
      topology (the fallback fleet keeps at least one client) and otherwise only while the zone's
      entry at this relay has a target above 0. At the gate, once some zone has heard the shutdown, a
      zone with no site client, nothing held and no launch to come ends at once, as `finish_idle` ended
      it before, with an INFO line ("no site client for zone Z and none planned here"); what is staged
      for it stays upstream for the relay that serves it. Docs: `relay_desc.rst` adds the rule under
      an upstream server autoscaling with relay sites; the `versionchanged` clause in `relay.rst` says
      so too.
  - Test `test_a_zone_the_topology_gives_no_client_here_ends_at_a_shutdown_and_leaves_its_tasks_upstream`
    (×8: FIXED and DYNAMIC, SPREAD and FILL, B's gpu client running or exited): the review's shape,
    planned by the real `plan_sites` (B first, serving gpu; A serving cpu and gpu; min 0, init 0,
    max 2; one gpu task left), A's entries read into a `TopologyView`, the real finishing
    `Dispatcher` with three gpu rows staged. Asserted: the plan gives A cpu 1, gpu 0 and B gpu 1; the
    puller reaches FINAL with both zones finished, gpu never asked, all three rows still staged, the
    relay database empty. **Red on `a741340`, 8 of 8, by assertion** (the puller holds one gpu task
    and stays at GATE: "holding 1 task for zone gpu, which has no site client"); green on the head.
    It passes on `2ffb5cc` (which ended every such zone), so it is listed in `drive/red-a741340.txt`
    (run with `RED_BASE_REV=a741340 RED_LIST=…`), not in `red.txt`. The view is set on the machine
    after construction, so on `a741340` the test fails on what the puller does, not on its
    signature. Its complement, `test_a_zone_its_fleet_would_launch_for_takes_what_is_staged_for_it_at_a_shutdown`
    (gpu target 1; no topology published), keeps R5's ask.
  - **No two-relay drive mode.** The `relay-topology` fill harness runs a server that ends only when
    the drive stops it, after every task completes and every client idles out; RV1 needs rows
    still staged for a zone at an upstream shutdown, which a server with a database reaches only by
    a drain while they are staged (P6 `zones`), with a second relay holding that zone's plan. That is
    a new drive flow, not a mode of the fill harness, and its timing (a drain landing while the
    other relay's client still has rows staged) would be the drive's own uncertainty. The unit test
    covers the decision itself with the planner's real output and the real `Dispatcher`; the
    remaining link, the relay's one `TopologyView` read after every beat and shared with the fleets,
    is the object `SiteScaler` already follows in the topology drives, and the puller is given that
    same object.
- [x] **Review-cycle-2 remediation (RV5).** A zone holding only rows it took through the one-task
      request, with no site client, asks `launches()` again at every gate pass; once no launch is
      coming it hands those rows back upstream with the `handback` call (relay-retire's) and ends.
  - **Puller.** The rows an idle request inserted are remembered per identity (`asked`). At the
    gate, a zone with bound 0, a backlog and no launch to come, whose backlog is all `asked` rows,
    takes them back (`take_back`) and goes to a new state, `HAND_BACK`: chunks of at most
    `MAX_HANDBACK_TASKS`, on the puller's own link role; each acknowledged chunk's stamps are dropped;
    a chunk the link loses is sent again after `WAIT_LINK` (the gate goes to `HAND_BACK` while one is
    pending). Then the gate ends the zone as before ("no site client … and none planned here").
  - **Never a row a site client has received.** `store.take_back` deletes each row, by one
    conditional DELETE, only while it is incomplete and either unscheduled at the relay or just taken
    out of the relay-local staging pool, then returns it with its stamp as received. The relay passes
    `unstage` (`RelayThread.unstage` → the relay-local `Dispatcher.withdraw(zone, ids)`, a new method
    that removes named rows still in a zone's pool under the dispatcher's lock); a row already cut for a
    site client is in no pool and keeps its local schedule time, so it stays held, and runs. The
    default zone is the case this matters for: the relay-local scheduler stages default-zone rows with
    no client, other zones only while a client serves them.
  - **Fallback.** The stamps of rows taken back stay until the hand-back is acknowledged, so
    `count_held` still counts them and `finish_upstream` sends no final heartbeat. An upstream server
    without the call (reply `None`, or a remote error), one that refuses it (no database), or rows with
    no stamp (from a server without a database: nothing to name them by) end the zone with one
    WARNING ("Could not hand back N tasks for zone Z … - they are left to the upstream server's
    eviction of this relay"), as retire falls back. Taking the rows out of the relay-local server is
    what lets the relay finish at all: its local server runs forever, and a row it held would keep
    the site waiting.
  - **Upstream side (deviation, server.py).** An upstream server answers a zone with a shutdown only
    once its scheduler has ended (a drain, in practice, with rows still staged), so the scheduler's
    `revert_handed_back` pass never runs for such a hand-back: the dispatcher queued it and the rows
    stayed assigned to the relay after its clean final heartbeat. `ServerThread.revert_handed_back`
    now requeues what is still queued as the server stops, after the staged revert, each only while
    still that relay's (the same conditional `Task.revert_handed_back`). The same gap closes for a
    relay retired during a drain. `AGENTS.md` and `invariants.md` §6 list the step in the shutdown
    order.
  - Tests (`tests/test_relay_connector.py`, the review's shape: real finishing `Dispatcher`, target 1
    → ask → target 0): `…_hands_it_back_once_no_client_is_planned_here` (handed back under the zone
    identity with the stamp as sent, FINAL, nothing held, the INFO line); `…_cannot_hand_back_…`
    `[no-call|no-database]` (one WARNING, FINAL, the stamp still held); `…_hands_back_only_what_no_site_client_has_received`
    `[staged|received]` (a row in the relay-local pool goes back; a row cut for a client stays held
    until it completes, and nothing is handed back); `…_keeps_it_while_a_client_is_still_planned_here`.
    `tests/test_dispatch.py`: `withdraw`. `tests/test_relay_retire.py`: `take_back` / `drop_stamps`,
    and the stop-time requeue on a real queue server. **Red on `ff0973a` by assertion** (the puller
    stays at `GATE`): the first, both fallback cases and `[staged]`, in `drive/red-ff0973a.txt` (on
    `2ffb5cc` the zone took nothing, so they belong to no `red.txt` line). The reviewer's probe
    reaches `FINAL` with nothing held on the head.
  - Docs: `relay_desc.rst` (the zone hands back what it took if the upstream server stops planning a
    client for it here before one starts; an upstream server that cannot take it back leaves it to
    eviction) and the `versionchanged` clause in `relay.rst`.
  - Re-run on the head: the `zones`, `zones-nodb`, `restart`, `pair-relay` and `pair-server` drives,
    all PASS (`evidence/review-2/`); `red_check.sh` 24 of 24 red with the PostgreSQL lines, and
    `drive/red-ff0973a.txt` 5 of 5 red against `ff0973a`; the full suite, 2132 passed, 101 skipped.
- **Touches:** `src/hypershell/relay/upstream.py`, `tests/test_relay_connector.py`,
  `docs/_include/relay_desc.rst`, `docs/cli/relay.rst`, `drive/red.txt`. *Review cycle 1:* also
  `src/hypershell/relay/__init__.py`, `drive/red-a741340.txt`. *Review cycle 2:* also
  `src/hypershell/relay/store.py`, `src/hypershell/core/dispatch.py`, `src/hypershell/server.py`,
  `AGENTS.md`, `.agents/factory/invariants.md`, `tests/test_dispatch.py`, `tests/test_relay_retire.py`,
  `drive/red-ff0973a.txt`.

## Phase P6 — End-to-end laptop evidence
**Satisfies:** R1, R2, R3, R4, R5, R9, R10 · **Depends on:** P5
**Goal:** the GOAL's laptop drives on the head, recorded; the earlier relay drives and the full suite
unchanged. Design: PLAN §6.

- [x] `spec/relay-hardening/drive/hardening_drive.sh MODE` (built on `retire_drive.sh`'s machinery:
      roles as loopback processes, a recording shim, system-style UTC logs, `DRIVE_DEADLINE`,
      `DRIVE_EVIDENCE`, `DRIVE_BASE_REV` / `DRIVE_BASE_SRC`, PID cleanup on every exit, deletes
      nothing) plus a judge (`hardening_judge.py`). Modes:
  - `restart` — `hs server --restart` with a database and a backlog, one relay with site clients;
    the upstream is stopped and started again on its port mid-run (twice, once while a hand-back or
    a return is likely in flight). Pass: the relay never exits 5 and logs no traceback, every task
    completes, the relay exits 0 at the finish.
  - `tls-on` / `tls-off` — the relay's TLS opposite the server's: exit 5 within
    `relay.upstream.deadline` of its first attempt, the error names the relay's setting and says the
    two ends must agree, no launch recorded.
  - `zones` / `zones-nodb` — a two-zone relay (`cpu`, `gpu`) against a non-forever upstream with a
    database / with `--no-db`; work in both zones; the `gpu` shim delays its client until after the
    `cpu` zone heard the shutdown. Pass: every task runs at the site once, the relay and the upstream
    exit 0.
  - `pair-relay` / `pair-server` — `DRIVE_BASE_REV=2ffb5cc` for the relay / for the server: a plain
    run completes as today (R10).

  *As built (amended):* the shim is `hardening_shim.sh` (`LOG LABEL WORD HOLD`: the first HOLD launches
  of a zone run at once, later ones wait for `release-WORD`); each zone launches through its own
  `[relay.zone.NAME]` launcher, and the relay's fleet check runs every second
  (`HYPERSHELL_AUTOSCALE_PERIOD=1`, as the retire drive). `DRIVE_BASE_ROLES` runs any mode with the
  roles named on the earlier build. `restart` runs 60 tasks of 1 s on 3 site clients and restarts the
  upstream (SIGTERM, `hs server` does not catch it) at 15 complete, at once, and at 35, down 3 s, with
  the relay's bundlewait at 1 s so that results are usually held for return. Two scenarios changed
  from the plan, which assumed what the code does not do:
  - **`zones`: a drain, not a non-forever finish.** A non-forever upstream with a database stops
    scheduling only once every task is *complete* (`Task.count_remaining` counts rows without a
    `completion_time`), so it never finishes while rows sit staged for a zone, and a zone never staged
    for has no rows staged at all (a zone is refilled only while a client of it is registered). The
    case R5 names arises from a drain: group 0 (12 cpu tasks of 2 s, 2 gpu tasks of 1 s) gives the gpu
    zone a site client that runs its tasks and exits idle (timeout 2 s), its relaunch is held, so group
    1's 3 gpu tasks are staged for the relay's gpu identity (it still beats with no client); once every
    task is staged the upstream is drained (SIGUSR1). The gpu client is released when the relay holds a
    gpu task taken at the shutdown. Shown red on `2ffb5cc` (`DRIVE_BASE_ROLES='relay server'`): the
    gpu zone ends with the cpu zone's shutdown and the 3 staged tasks never run.
  - **`zones-nodb`: the other half of R5.** Without a database a task has no zone (`hs submit --zone`
    needs one), and a default-zone row sits in a pool, outside the inbound queue, only after a request
    drained a submitted bundle larger than itself; a zone whose client left at that moment cannot be
    timed by a laptop drive. So this mode drives the relay against `hs server --no-db` serving the
    default zone and gpu, every gpu launch held: the zone with nothing staged and no site client ends
    with the default zone's shutdown, and the relay and the `--no-db` upstream exit 0. The staged
    no-database case is R9's unit test against the real `Dispatcher`
    (`test_a_zone_without_site_clients_takes_what_is_staged_for_it_at_a_shutdown[no-database]`).
- [x] Re-run on the head with `DRIVE_EVIDENCE`: every mode of `spec/relay-retire/drive/retire_drive.sh`
      and `spec/relay-topology/drive/topology_drive.sh`, and `spec/relay/drive/relay_smoke.sh`;
      record each in `spec/relay-hardening/evidence/laptop/README.md` (mode, result, one line) with
      the scrubbed `MODE.txt` files of the new drive.
      *As built:* every mode passes but topology `lower`, which fails the three checks relay-retire
      recorded as changed by its own fix (A, superseded, now retires; `spec/relay-retire/TECH.md` P3).
      Their scrubbed files are not kept here: the names collide with this drive's, and their cycles'
      evidence holds the record of each mode.
- [x] The full suite (`uv run pytest`) on SQLite; the PostgreSQL/TimescaleDB legs of P1 once more.
- [x] **Review-cycle-1 remediation (RV4, re-runs).** `hardening_drive.sh` records the earlier
      build's source it used as the fact `base_src` (the export, or `DRIVE_BASE_SRC`), and
      `hardening_judge.py` judges each pair role against it (`on_base`, by real path), not against a
      fixed `/base/src/` segment. `pair-relay` with `DRIVE_BASE_SRC` set to an export outside the site:
      PASS, both source checks included (`evidence/review-1/base-src/`). The verify now runs the
      topology `fill` mode too (RV1's harness), and was re-run whole on the remediation build with the
      evidence in `evidence/review-1/` (`README.md` there): every hardening mode, the relay smoke, the
      retire `retire` and `idle` modes and the topology `spread`, `fill` and `kill` modes PASS; the full
      SQLite suite 2116 passed, 101 skipped; the red check 19 of 19 red, 5 `pg:` skipped (24 of 24
      with `HS_TEST_POSTGRES`, P1).
- **Verify:** see frontmatter (the seven new modes, the relay smoke, two retire and three topology
  modes as the R10 guard, the full suite, the red check). Expect ~40 min.
- **Touches:** `spec/relay-hardening/drive/hardening_drive.sh`, `drive/hardening_shim.sh`,
  `drive/hardening_judge.py`, `spec/relay-hardening/evidence/laptop/`. *Review cycle 1:* also
  `spec/relay-hardening/evidence/review-1/`.

## Phase P7 — Tail investigation on the laptop
**Satisfies:** R8 · **Depends on:** P6
**Goal:** the tail measured and attributed on the head within the budget; fixed if the logs name a
cause whose fix fits. Design: PLAN §2 R8, §5 item 2–4; research 07.

- [x] Tooling in `spec/relay-hardening/drive/`: `tail_run.py` (loads
      `spec/relay/drive/relay_throughput.py` by path, unedited; TRACE, system style, `TZ=UTC`;
      records the exit time; one JSON record per run with commit, sizes, walls, tail, direct-run
      tail, segments s1–s8, last task id, WARNING/ERROR counts, kept site paths); `tail_laptop.sh N`
      (refuses N > 10 and refuses to exceed 10 records already in the evidence directory);
      `tail_cluster.sh` (the `retire_cluster.sh` / `topology_cluster.sh` environment contract: `HS`
      required; `HSD_WORK`, `HSD_RESULTS`, `HSD_NODE_TMP`, `HSD_LABEL`, `HSD_RUN_ID`, `HSD_RUNS` ≤ 10;
      unset caller `HYPERSHELL_*`; one node, ≥ 36 cores; node-local scratch copied to `HSD_WORK` after
      each run; PID cleanup; no `#SBATCH`, no site names); `tail_judge.py` (per run: tail, direct
      tail, segments, `TAIL>5`, `OVER` per segment past its bound; `--budget N` fails on more than N
      records per directory or on an incomplete run; `--require-none-over S`; `--outcome` prints and
      checks the recorded outcome, P8). `tests/test_tail_judge.py` over synthetic records.
- [x] Rehearse `tail_cluster.sh` on the laptop at a reduced size (`HSD_RUNS=1`, ≤ 200 × 0.05 s via a
      size override that the budget runs never set). Not a budget run; its record is kept under
      `evidence/rehearsal/`, never `laptop/`.
- [x] **Diagnostic runs**, at most 5, at the R8 size (2000 × 0.2 s, 32 executors) on the head; every
      started run counts; records and scrubbed log windows (`T_last − 10 s` to exit) in
      `evidence/laptop/`.
- [x] Decide from the segments (record the decision here):
  - **H0** (every segment within its design bound; the tail tracks s1 + s4 + s6): apply fix (i) —
    the returner sends at once when the relay's store holds no unfinished task (`relay/upstream.py`),
    with a unit test; and fix (ii) — the heart monitor's shutdown signal does not wait out its 2 s
    `get` (`server.py`), **only if the foreman pre-authorised it at the plan gate** (PLAN §5 item 3).
    Then the remaining laptop runs (≤ 10 in all) on the fixed head.
  - **An anomaly** (a segment `OVER` its bound): name the cause from the logs; fix it if it fits the
    appetite, else record it for outcome (b). No cluster job.
- [x] Record each run's tail and segments in a table here and in `evidence/laptop/README.md`. If the
      post-fix runs (or, with no fix, all runs) show none over 5 s, P8 asks the foreman for the
      cluster job; otherwise P8 records the outcome directly.

  *As built (amended):*
  - **Nine segments, not eight.** The relay launches its site clients with `relay.bundlewait` as their
    own result bundle wait and a bundle of `max(relay.bundlesize, relay.num_threads)` (8 here), so the
    last result can sit up to 5 s in a site client before the relay-local server has it; research 07's
    s1 ran from `T_last` to the relay's next return and folded that wait in, and in `laptop-02` it read
    as an anomaly in the return (s2 4.99 s, the next return after one that did not carry the last
    result). `tail_run.py` takes a stamp at the relay-local server's `Completed task (<last>)`: s1 site
    client hold, s2 relay hold, s3 land, s4 scheduler, s5 signal, s6 heard, s7 site stop, s8 final
    beat, s9 exit; `tail_judge.py` bounds them 5.1, 5.1, 2, 2, 2, 2.1, 3, 2 and 2.5 s (+0.25 s slack).
    Runs 01–05 were measured again from their kept scratch logs (`tail_run.py --remeasure`, marked
    `remeasured`; no run added). The signal stamps also take the `Finishing dispatch` line fix (ii) adds.
  - Log windows leave out the per-task lines of every task but the last (the first line counts them),
    so a window is about 0.3 MB, not several; the full logs stay in the scratch sites. Records carry
    `src_changed` (product source differs from the recorded commit: the post-fix runs, made before the
    P7 commit). `tail_cluster.sh` takes `HSD_MIN_CORES` (36) and a rehearsal-only size.
  - Rehearsal: `tail_cluster.sh` with `HSD_RUNS=1`, 200 tasks of 0.05 s on 2 clients of 4 threads,
    `HSD_MIN_CORES=1`: complete, judged (`SIZE TAIL>5`, tail 5.11 s, no segment over), scratch copied;
    record and windows in `evidence/rehearsal/`.

  **Decision (2026-11-14).** The five diagnostic runs (tails 6.71, 13.38, 6.64, 12.51, 6.74 s) name
  the designed chain (H0): every segment within its bound but one, and the tail the sum of the waits
  the chain was designed with: s2 relay hold (2.9–3.8 s), s5 and s7 heart-monitor reads (to 1.9 and
  1.6 s), s8 about 1 s, and in `laptop-02` s1 site client hold (5.0 s). One segment exceeded its bound:
  `laptop-04` s4 6.03 s, the upstream scheduler's back-off (H1): it saw the last 8 results not yet
  landed 4 ms before they did, backed off 4 s, and is woken only by a cut or new demand, never by a
  landed result, so it looked again only after 6 s. Under plan-gate ruling 3 both apply: the chain is
  fixed with the pre-authorised fixes and the remaining runs run on the fixed head; the anomaly is
  chased from the logs (its cause named above), and **no cluster job runs**.
  - **Fix (i)** (`relay/upstream.py`, `relay/store.py`, `relay/__init__.py`): `UpstreamReturner` takes
    `idle`, a check that the site holds no unfinished task; the relay passes `store.site_idle` (no row
    of its database without a completion), and pending results then go at once instead of at the
    bundle wait. The default (never idle) keeps the returner's own tests as they were.
    `test_returner_sends_at_once_when_the_site_holds_no_unfinished_task` (red on `2ffb5cc`).
  - **Fix (ii)** (`server.py`, every server): `HeartMonitorThread.signal_clients` finishes dispatch
    itself (logging `Finishing dispatch`) before it sets the heart monitor's flag, so a request hears
    the shutdown without waiting out the heart monitor's 2 s heartbeat read; the heart monitor still
    logs the signal on its next turn. Order kept (scheduler, then dispatch finish, then heart monitor
    join, staged revert, sentinels). `TestHeartMonitor::test_signal_from_the_server_finishes_dispatch_at_once`
    (red on `2ffb5cc`). `relay.bundlewait` in `docs/_include/config_param_ref.rst` says that a relay
    with nothing left to run returns at once.
  - The five post-fix runs: tails 1.72, 1.67, 7.00, 6.86, 6.88 s. s2 0.07–0.12 s, s5 0.00, s7
    0.07–0.50 s; the direct runs' tail fell from about 2.7 s to 0.8–1.8 s (fix ii). Three runs are over
    5 s, each by s1 alone (5.0 s: the site client's result bundle wait, which neither pre-authorised fix
    touches). H1 did not recur in these five (s4 0.61–0.84 s) and is not fixed.
  - The runs (seconds; `evidence/laptop/README.md`):

  | Run | Label | Tail | Direct tail | s1 | s2 | s3 | s4 | s5 | s6 | s7 | s8 | s9 | Relay / direct wall |
  |-----|-------|------|-------------|----|----|----|----|----|----|----|----|----|---------------------|
  | `laptop-01` | diagnostic | 6.71 | 2.67 | 0.01 | 3.00 | 0.03 | 0.77 | 0.30 | 0.00 | 1.55 | 1.04 | 0.01 | 25.8 / 21.7 |
  | `laptop-02` | diagnostic | 13.38 | 2.73 | 5.03 | 3.56 | 0.00 | 1.15 | 1.94 | 0.00 | 1.64 | 0.05 | 0.01 | 35.9 / 21.3 |
  | `laptop-03` | diagnostic | 6.64 | 2.73 | 0.00 | 2.91 | 0.03 | 0.92 | 0.12 | 0.00 | 1.64 | 1.01 | 0.00 | 24.8 / 21.2 |
  | `laptop-04` | diagnostic | 12.51 | 2.75 | 0.01 | 3.75 | 0.02 | **6.03** | 0.04 | 0.00 | 1.62 | 1.02 | 0.01 | 34.5 / 20.8 |
  | `laptop-05` | diagnostic | 6.74 | 2.65 | 0.01 | 3.00 | 0.03 | 0.78 | 0.31 | 0.00 | 1.59 | 1.02 | 0.01 | 24.8 / 21.3 |
  | `laptop-06` | post-fix | 1.72 | 0.81 | 0.01 | 0.12 | 0.03 | 0.61 | 0.00 | 0.00 | 0.48 | 0.46 | 0.01 | 20.7 / 19.2 |
  | `laptop-07` | post-fix | 1.67 | 1.75 | 0.01 | 0.12 | 0.03 | 0.67 | 0.00 | 0.00 | 0.40 | 0.43 | 0.01 | 19.8 / 20.2 |
  | `laptop-08` | post-fix | 7.00 | 1.76 | 5.04 | 0.08 | 0.00 | 0.65 | 0.00 | 0.00 | 0.27 | 0.95 | 0.01 | 29.4 / 20.3 |
  | `laptop-09` | post-fix | 6.86 | 1.66 | 5.03 | 0.07 | 0.00 | 0.84 | 0.00 | 0.00 | 0.50 | 0.41 | 0.01 | 29.4 / 20.3 |
  | `laptop-10` | post-fix | 6.88 | 1.65 | 5.04 | 0.07 | 0.00 | 0.77 | 0.00 | 0.00 | 0.07 | 0.92 | 0.01 | 29.4 / 20.3 |

  - So R8's outcome (a) is not reached on the laptop and the budget (10 of 10) is spent: no cluster
    job (ruling 3, twice over: a segment exceeded its bound, and post-fix runs exceed 5 s). P8 records
    outcome (b), with a narrowed seed for the two named causes left: the site client's result bundle
    wait at the end of a run (`relay.bundlewait` doubling as the site clients' bundle wait; a client
    flushing when it runs nothing, or the relay giving its site clients a shorter wait), and the
    upstream scheduler not woken by a landed result (its back-off doubling to `server.poll`).
- **Verify:** see frontmatter (judge tests, the budget check over the laptop records, the suites a
  fix would touch).
- **Touches:** `spec/relay-hardening/drive/tail_*.{py,sh}`, `tests/test_tail_judge.py`,
  `spec/relay-hardening/evidence/`, and only if a fix is taken, `src/hypershell/relay/upstream.py`
  (i) / `src/hypershell/server.py` (ii) with their tests. *As built:* also `relay/store.py`,
  `relay/__init__.py`, `tests/test_relay_fsm.py`, `tests/test_server_fsm.py`,
  `docs/_include/config_param_ref.rst`, `drive/red.txt`.

## Phase P8 — Conditional cluster job and the R8 outcome
**Satisfies:** R8 · **Depends on:** P7
**Goal:** R8 closed with outcome (a) or (b), recorded. Split gate: the builder prepares, the foreman
submits the one cluster job (if triggered), the gate judges.

- [x] If P7 triggers it: **not triggered.** The foreman ruled (2026-11-14) that the cluster job does
      not run: plan-gate ruling 3 rules it out because a segment exceeded its design bound (`laptop-04`
      s4, the upstream scheduler's back-off) and the post-fix runs still exceed 5 s (`laptop-08` to
      `laptop-10`). `tail_cluster.sh` stays rehearsed on the laptop only. `evidence/cluster/` was not
      produced.
- [x] Record the outcome in `spec/relay-hardening/evidence/OUTCOME.md` and a table here: **outcome
      (b)**, below; narrowed seed `issues/relay-shutdown-tail.md` (`status: unshaped`), with its
      ROADMAP entry in Part I.

  **Outcome (b), recorded 2026-11-14.** Ten laptop runs (the whole budget), at the R8 size, scratch
  kept, scrubbed log windows in `evidence/laptop/`. Tails (seconds), runs `laptop-01` to `laptop-05`
  diagnostic on `475334c`: 6.71, 13.38, 6.64, 12.51, 6.74; runs `laptop-06` to `laptop-10` post-fix
  (P7's fixes (i) and (ii)): 1.72, 1.67, 7.00, 6.86, 6.88. The per-segment table is under P7 above and
  in `evidence/laptop/README.md`.

  - **Named causes.** (1) The designed chain: the relay's result hold (s2, 2.9 to 3.8 s), the upstream
    heart monitor's and the relay-local server's 2 s heartbeat reads (s5 up to 1.9 s, s7 up to 1.6 s)
    and the relay's final beat (s8, about 1 s). (2) The site client's result bundle wait (s1, 5.0 s in
    `laptop-02`, `laptop-08`, `laptop-09`, `laptop-10`): the relay starts site clients with
    `relay.bundlewait` as their own bundle wait. (3) The upstream scheduler's back-off (`laptop-04`
    s4, 6.03 s over a 2 s bound): a landed result does not wake it, and the back-off doubles toward
    `server.poll`.
  - **Landed in P7.** Fix (i), the returner sends at once when the relay's store holds no unfinished
    task (removes cause 1's s2); fix (ii), the heart monitor's shutdown signal does not wait out its
    2 s read (removes s5 and s7; the direct runs' tail falls from about 2.7 s to 0.8 to 1.8 s). After
    them the tails are 1.7 s when nothing waits (s2 0.07 to 0.12 s, s5 0.00 s, s7 0.07 to 0.50 s).
  - **Left.** Cause 2 (three of five post-fix runs, 7 s: s1 alone is 5.0 s) and cause 3 (not seen in
    the post-fix runs, s4 0.61 to 0.84 s; rare, unbounded by the back-off). Both are in the seed
    `issues/relay-shutdown-tail.md` with the options: a site client flushes when it holds nothing to
    run, or the relay gives site clients a shorter wait; the scheduler woken by a landed result.
  - **Why no cluster job.** The cluster job is for a tail seen only on Linux with none over 5 s on the
    laptop. A segment exceeded its design bound and the post-fix runs exceed 5 s on the laptop
    itself, so ruling 3 excludes it, and the laptop budget (10 of 10) is spent. `evidence/cluster/` is
    not produced; R8 closes under (b) and the release does not wait on the remaining work.
  - **Verify amended.** The frontmatter verify named `evidence/cluster`, which exists only if the job
    runs; it now names `evidence/laptop` alone. `--outcome` takes `evidence/` as the root of its one
    evidence directory and reads `evidence/OUTCOME.md` (`outcome: b`, `seed: issues/relay-shutdown-tail.md`,
    which must exist and be `status: unshaped`).
- **Verify:** see frontmatter (`--outcome` checks the outcome file against the records: (a) needs the
  post-fix records none over 5 s; (b) needs the seed path to exist; budget ≤ 10 per directory).
- **Touches:** `spec/relay-hardening/evidence/OUTCOME.md`, `issues/relay-shutdown-tail.md`,
  `ROADMAP.md`.

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
