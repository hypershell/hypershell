---
slug: relay-hardening
title: 'Relay hardening for 2.9.0: no aborts on removed rows or a restarted upstream,
  a TLS mismatch that ends, zones that wait for their work, eviction by the server''s
  clock'
kind: fix
appetite: big
status: in_progress
branch: fix/relay-hardening
base: develop
current_phase: P6
last_updated: '2026-11-13'
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
    && sh spec/relay-hardening/drive/red_check.sh && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
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
    && sh spec/relay-hardening/drive/red_check.sh && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
- id: P6
  name: 'End-to-end laptop evidence: restart, TLS, zones and pair drives; earlier
    relay drives and the full suite on the head'
  status: pending
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
    $mode || exit 1; done && for mode in spread kill; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/relay-topology/drive/topology_drive.sh" $mode || exit 1; done &&
    uv run pytest && sh spec/relay-hardening/drive/red_check.sh
- id: P7
  name: 'Tail investigation on the laptop: tooling, diagnostic runs, the fix if the
    logs name one, post-fix runs'
  status: pending
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
  status: pending
  satisfies:
  - R8
  depends_on:
  - P7
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_tail_judge.py && uv run python spec/relay-hardening/drive/tail_judge.py
    --budget 10 --outcome spec/relay-hardening/evidence/laptop spec/relay-hardening/evidence/cluster
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
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
  listed; it is named in the phase's commit body as CI-only red.
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
- **Verify:** see frontmatter — the SQLite model, writeback, recovery, scheduler, group, zone and
  cancel suites; the PostgreSQL and TimescaleDB legs; the red check (sandbox off).
- **Touches:** `src/hypershell/data/model.py`, `tests/test_relay_return.py`,
  `tests/test_contended_recovery.py`, `tests/test_contended_write.py`, `AGENTS.md`,
  `.agents/factory/invariants.md`, `spec/relay-hardening/drive/red_check.sh`, `drive/red.txt`.

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
      `PLAIN_CHECK_WAIT` (5 s). The docs sentence is in `docs/_include/relay_desc.rst` (beside the
      relay that waits for an unreachable upstream, so the man page carries it too), with the
      `versionchanged` after its `.. include::` in `docs/cli/relay.rst`. The cleartext → TLS link
      test serves its TLS upstream from an `hs server` process, killed with the test, so every wait
      is bounded. The two link tests are red on `2ffb5cc` because the relay's hook does not exist
      there; the two CLI tests are red by behaviour (the relay is still running at the 12 s bound).
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/core/reconnect.py`, `src/hypershell/relay/upstream.py`,
  `src/hypershell/relay/__init__.py`, `tests/test_relay_link.py`, `tests/test_relay_workload.py`,
  `docs/_include/relay_desc.rst`, `docs/cli/relay.rst`, `drive/red.txt`.

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
- **Touches:** `src/hypershell/relay/upstream.py`, `tests/test_relay_connector.py`,
  `docs/_include/relay_desc.rst`, `docs/cli/relay.rst`, `drive/red.txt`.

## Phase P6 — End-to-end laptop evidence
**Satisfies:** R1, R2, R3, R4, R5, R9, R10 · **Depends on:** P5
**Goal:** the GOAL's laptop drives on the head, recorded; the earlier relay drives and the full suite
unchanged. Design: PLAN §6.

- [ ] `spec/relay-hardening/drive/hardening_drive.sh MODE` (built on `retire_drive.sh`'s machinery:
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
- [ ] Re-run on the head with `DRIVE_EVIDENCE`: every mode of `spec/relay-retire/drive/retire_drive.sh`
      and `spec/relay-topology/drive/topology_drive.sh`, and `spec/relay/drive/relay_smoke.sh`;
      record each in `spec/relay-hardening/evidence/laptop/README.md` (mode, result, one line) with
      the scrubbed `MODE.txt` files of the new drive.
- [ ] The full suite (`uv run pytest`) on SQLite; the PostgreSQL/TimescaleDB legs of P1 once more.
- **Verify:** see frontmatter (the seven new modes, the relay smoke, two retire and two topology
  modes as the R10 guard, the full suite, the red check). Expect ~30 min.
- **Touches:** `spec/relay-hardening/drive/hardening_drive.sh`, `drive/hardening_judge.py`,
  `spec/relay-hardening/evidence/laptop/`.

## Phase P7 — Tail investigation on the laptop
**Satisfies:** R8 · **Depends on:** P6
**Goal:** the tail measured and attributed on the head within the budget; fixed if the logs name a
cause whose fix fits. Design: PLAN §2 R8, §5 item 2–4; research 07.

- [ ] Tooling in `spec/relay-hardening/drive/`: `tail_run.py` (loads
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
- [ ] Rehearse `tail_cluster.sh` on the laptop at a reduced size (`HSD_RUNS=1`, ≤ 200 × 0.05 s via a
      size override that the budget runs never set). Not a budget run; its record is kept under
      `evidence/rehearsal/`, never `laptop/`.
- [ ] **Diagnostic runs**, at most 5, at the R8 size (2000 × 0.2 s, 32 executors) on the head; every
      started run counts; records and scrubbed log windows (`T_last − 10 s` to exit) in
      `evidence/laptop/`.
- [ ] Decide from the segments (record the decision here):
  - **H0** (every segment within its design bound; the tail tracks s1 + s4 + s6): apply fix (i) —
    the returner sends at once when the relay's store holds no unfinished task (`relay/upstream.py`),
    with a unit test; and fix (ii) — the heart monitor's shutdown signal does not wait out its 2 s
    `get` (`server.py`), **only if the foreman pre-authorised it at the plan gate** (PLAN §5 item 3).
    Then the remaining laptop runs (≤ 10 in all) on the fixed head.
  - **An anomaly** (a segment `OVER` its bound): name the cause from the logs; fix it if it fits the
    appetite, else record it for outcome (b). No cluster job.
- [ ] Record each run's tail and segments in a table here and in `evidence/laptop/README.md`. If the
      post-fix runs (or, with no fix, all runs) show none over 5 s, P8 asks the foreman for the
      cluster job; otherwise P8 records the outcome directly.
- **Verify:** see frontmatter (judge tests, the budget check over the laptop records, the suites a
  fix would touch).
- **Touches:** `spec/relay-hardening/drive/tail_*.{py,sh}`, `tests/test_tail_judge.py`,
  `spec/relay-hardening/evidence/`, and only if a fix is taken, `src/hypershell/relay/upstream.py`
  (i) / `src/hypershell/server.py` (ii) with their tests.

## Phase P8 — Conditional cluster job and the R8 outcome
**Satisfies:** R8 · **Depends on:** P7
**Goal:** R8 closed with outcome (a) or (b), recorded. Split gate: the builder prepares, the foreman
submits the one cluster job (if triggered), the gate judges.

- [ ] If P7 triggers it: hand the foreman `tail_cluster.sh`, the commit to run, the env contract and
      the expected wall (~45 s per run, ≤ 10 runs; ask 30 min). The foreman submits with its own
      batch options and copies the records unchanged into `evidence/cluster/` with a README row
      (record, commit, wall, judge). The builder never submits.
- [ ] Record the outcome in `spec/relay-hardening/evidence/OUTCOME.md` and a table here:
      **(a)** the cause named from logs, the fix, and post-fix runs with no tail over 5 s; or **(b)**
      every run, its tail and segments, the logs kept, the cause or the hypotheses ruled out, and a
      narrowed seed in `issues/` (`status: unshaped`) for the remaining work.
- **Verify:** see frontmatter (`--outcome` checks the outcome file against the records: (a) needs the
  post-fix records none over 5 s; (b) needs the seed path to exist; budget ≤ 10 per directory).
- **Touches:** `spec/relay-hardening/evidence/`, possibly `issues/<narrowed seed>.md`.

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
