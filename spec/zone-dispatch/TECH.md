---
slug: zone-dispatch
title: Zone-aware, demand-driven dispatch (zone, cycle 2)
kind: feature
appetite: big
status: in_progress
branch: feature/zone-dispatch
base: develop
current_phase: P2
last_updated: '2026-11-04'
phases:
- id: P1
  name: Zone schema and zone-filtered selection on the upgrade step
  status: done
  satisfies:
  - R7
  - R8
  - R9
  - R10
  - R11
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -f src/hypershell/data/upgrade.py && { test -f spec/writeback-scoped-conditional/TECH.md
    || git cat-file -e origin/fix/writeback-scoped-conditional:spec/writeback-scoped-conditional/TECH.md;
    } && uv run pytest -v tests/test_zone_model.py tests/test_zone_postgres.py tests/test_groups.py
    tests/test_retry_gate.py tests/test_initdb.py tests/test_source.py tests/test_cancel.py
    tests/test_restart.py
- id: P2
  name: Request callable, staging and the client request loop (default zone, homogeneous
    path)
  status: in_progress
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R13
  - R14
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v && uv run python tests/bench/bench_queue.py parity --base
    2.8.3 --feature HEAD --skip-help
- id: P3
  name: 'Version handshake: refuse old clients, fail cleanly against old servers'
  status: done
  satisfies:
  - R5
  - R14
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_dispatch_version.py tests/test_queue.py tests/test_queue_tls.py
    tests/test_queue_start.py tests/test_queue_gc.py tests/test_queue_limits.py &&
    uv run python tests/bench/bench_queue.py skew --new HEAD --old 2.8.3 --old 2.9.0a2
    --bound 30
- id: P4
  name: 'Zones end to end: --zone on submit, task submit, client and cluster; per-zone
    pools'
  status: done
  satisfies:
  - R6
  - R9
  - R10
  - R11
  - R13
  - R14
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_zone_dispatch.py tests/test_zone_workload.py
    tests/test_completions.py tests/test_main.py tests/test_submit.py tests/test_cluster.py
    tests/test_client.py && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs
    && uv run sphinx-build -q docs docs/_build
- id: P5
  name: 'R1 gate: throughput and footprint against 2.8.3, full suite'
  status: done
  satisfies:
  - R1
  - R14
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v && uv run python tests/bench/bench_queue.py parity --base
    2.8.3 --feature HEAD --skip-help && uv run python tests/bench/bench_queue.py gate
    --base 2.8.3 --feature HEAD --repeats 7 --min-ratio 0.95
- id: P6
  name: 'Multi-node drive, run A: zone knobs in the drive, local smoke, hand back
    (external wait follows)'
  status: done
  satisfies:
  - R1
  - R4
  - R14
  depends_on:
  - P4
  parallel: true
  hammerable: false
  hill: uphill
  verify: sh tests/cluster/smoke.sh
- id: P7
  name: 'Multi-node drive, run B: judge the external evidence'
  status: done
  satisfies:
  - R1
  - R4
  - R14
  depends_on:
  - P5
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python tests/cluster/compare.py spec/zone-dispatch/evidence/cluster
    --baseline 2.8.3 --candidate feature --control 2.9.0a2 --min-ratio 0.95 --zoned
    feature
- id: P8
  name: (Deferrable) Show zone by name in hs list, hs info and the card
  status: done
  satisfies:
  - R12
  depends_on:
  - P4
  parallel: false
  hammerable: true
  hill: uphill
  verify: uv run pytest -v tests/test_zone_display.py tests/test_list.py tests/test_card_info.py
    tests/test_card_render.py tests/test_card_integration.py tests/test_main.py
review:
  last_reviewed_commit: b946b14820b4109d483a5311b8f24692e2d9c307
  verdict: changes-requested
  blocked_reason: ''
  cycle: 1
---
# TECH.md — Zone-aware, demand-driven dispatch (zone, cycle 2)

The YAML frontmatter above is the resume ground-truth. Read it with
`uv run python .agents/factory/bin/next_phase.py spec/zone-dispatch/TECH.md`. `hs-build` executes
the next actionable phase, runs its `verify:`, updates state with `set_phase.py`, and makes one
atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). The R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). Section references below (§) point there.
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and briefs 01–06.

## Frontmatter field reference

See [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md). This cycle
uses Z1's **external wait** convention for P7:

- When `spec/zone-dispatch/evidence/cluster/` is absent, `hs-build` marks P7 `blocked` with
  `blocked_reason: "awaiting multi-node evidence (run B)"` and stops. That does not count against
  `attempts`.
- The foreman may run `hs-review` on P1–P6 while P7 is blocked.
- P8 is the only deferrable phase (`hammerable: true`). It can be cut to Z3 whole: no other phase
  depends on it.

## Build precondition (before P1)

**Integration base (foreman ruling, 2026-11-02).** The build runs on `integ/zone-dispatch-base` =
`30993b8` (Z1 head `43d2d85` with `origin/develop` `d996579` merged, signed); this branch's spec
commits were rebased onto it with `git rebase --onto integ/zone-dispatch-base aa55e86`. Until
`writeback-scoped-conditional` is merged into the base, P1's check reads that cycle's TECH from
`origin/fix/writeback-scoped-conditional` (`git cat-file -e`). When Z1 or writeback moves, the
foreman has the base re-merged; this branch never edits their code.

- This branch carries only spec commits over Z1's `aa55e86`. The foreman lands Z1, then
  `writeback-scoped-conditional`, on `develop`, and rebases with
  `git rebase --onto origin/develop aa55e86 feature/zone-dispatch`. There is nothing to resolve.
- P1's verify fails fast if `data/upgrade.py` (#69) or `spec/writeback-scoped-conditional/TECH.md`
  is missing. That is not a red verify: `hs-build` marks P1 `blocked` with
  `blocked_reason: "awaiting rebase onto develop with #69 and writeback"` and stops.
- **Overlap rules with writeback** (PLAN §3.2), binding in every phase:
  - Never edit `Entity.update_all`, the `Task.next` stamping loop, `revert_all` /
    `revert_orphaned` / `cancel_all`, `Receiver.update_tasks` or `Confirm.update_info`.
  - `zone` is server-held: never a result column; if the fence is a denylist, add `zone` to it.
  - Unstaging calls `Task.revert_all` and nothing else.

## Plan gate rulings (foreman, as judge under the maintainer's delegation, 2026-11-02)

The plan gate is **passed** with these rulings and conditions.

- **N1 — accepted.** Zone ids are `uuid5(name)` ("minted by HyperShell"), inserted with `ON CONFLICT DO NOTHING`.
- **N2 — accepted.** The default zone is stored as NULL on `task.zone`; presentation (R12) and docs name it as the reserved default zone.
- **N3 — accepted, bounded.** Exactly the five white-box tests in `test_server_fsm.py` that pin the replaced `scheduled` posting and sentinels may be re-pointed at the dispatcher. Each keeps asserting the same observable ordering, and TECH lists each one with a one-line rationale. No other pre-existing test is modified (R14).
- **N4 — accepted.** "Explicit `-b`" is `config.which('submit', 'bundlesize')`, the key the client reads today. The `client.`/`submit.` key mismatch becomes an `unshaped` seed with a `ROADMAP.md` entry.
- **Invariant prose (`AGENTS.md`, `invariants.md` §6).** Accepted, as wording that names the new mechanism (`dispatcher.finish()` in place of "sentinels to clients"). The shutdown order is unchanged and nothing is relaxed. Make the edit in one commit with no code in it. The foreman reports it to the maintainer.
- **PostgreSQL path — condition.** The `Zone` get-or-create upsert, and zone-filtered selection on PostgreSQL, get a test that runs in the existing PostgreSQL CI job (skipped when no PostgreSQL URL is configured). P1's gate gains "passes in CI". The foreman reads CI on the PR.
- **Cluster drive — condition.** The test-cluster job class allows at most 4 nodes and 1 h. Node 0 runs the server and nodes 1–3 run clients. Run 2's mixed zones put several client processes per node, each with its own `--zone` and advertised size. Mixed zones are labels, not nodes, so ≥ 4 client *processes* across 3 nodes meets the plan's intent. Run 1 and Run 2 are separate jobs, each guarded to finish inside 54 minutes.
- **Base — condition.** The build runs on an integration base (Z1's final head with `origin/develop` merged in), and `writeback-scoped-conditional` is merged in when it lands. P1's "writeback TECH present" check reads it from that base. Once Z1 and writeback have both landed on `develop`, the foreman rebases this branch onto `develop` before review.

## Build status (2026-11-03)

- **Re-merge after writeback landed.** `integ/zone-dispatch-base` = `ebd2534` (signed merge of
  `origin/develop` `6f216d0`). Rebasing this branch onto it stops at P1 with textual conflicts in
  `src/hypershell/data/model.py` (the `from enum import Enum` / `from uuid import ...` import lines;
  the `__all__` line where writeback adds `'ResultOutcome'` and Z2 adds the zone names) and in
  `.github/workflows/tests.yml` (both cycles append a PostgreSQL test file to the same pytest line).
  Per the foreman's rule the rebase was aborted unresolved; the branch still sits on `30993b8`.
- **[NEEDS CLARIFICATION] after that rebase:** writeback's `tests/test_writeback.py::
  test_result_columns_are_fixed` pins the literal set of 20 non-result columns; Z2 adds `zone`
  (server-held, never a result column), so that existing test fails until `zone` joins its set.
  The ruling allows no other existing test to change.
- **P6:** code committed, phase `in_progress`. The local smoke (`sh tests/cluster/smoke.sh`) was not
  run here: it and `build_archive.sh` delete their scratch with `rm -rf`, which this build may not
  run. The foreman runs it, with `HSD_SMOKE_KEEP=1` to keep results.

- **Foreman rulings (judge), 2026-11-03.**
  1. Rebase onto `ebd2534`, resolving the three textual hunks only: keep both import lines in
     `data/model.py`; `__all__` is the union (`'ResultOutcome'` plus the zone names); the workflow's
     PostgreSQL line runs `tests/test_upgrade_postgres.py tests/test_writeback_postgres.py
     tests/test_zone_postgres.py`, in that order. Any other conflicting hunk stops the build.
  2. `tests/test_writeback.py::test_result_columns_are_fixed` is the one writeback-test edit, beside
     the N3 five: `zone` joins its pinned set of non-result columns, and it asserts
     `'zone' not in Task.result_columns`. `Task.result_columns` stays writeback's 13.
  3. P6's local smoke is skipped; the foreman runs the cluster drive on the second test cluster.
     The P1 PostgreSQL CI check is read on the PR.
  4. Measure the parity wall-time difference (+3 s on 10 tasks) and record where it goes.
  5. No Z1 re-merge until the foreman says Z1's new head has landed.

- **Rebase attempt under ruling 1, 2026-11-03.** The three ruled hunks resolved as ruled (P1). The
  rebase then stopped at P4 (`1e2b937`) on two more textual hunks, outside the ruling:
  `data/model.py` `__all__` (writeback's `'ResultOutcome'` vs P4 adding `'zone_name'` to the same
  line) and `server.py`'s model import line (writeback adds `ResultOutcome`; P4 adds `Zone`,
  `DEFAULT_ZONE_ID`, `DEFAULT_ZONE_NAME`). Aborted per the ruling; the branch is unchanged on
  `30993b8`, so ruling 2's test edit and the post-rebase suites wait for the rebase.
- **Parity wall time (ruling 4), measured.** Not the 2 s request wait. Five interleaved repeats at the
  default log level: time to first schedule 0.36 s vs 0.68 s (2.8.3), the shutdown tail is the same
  (4.16 vs 4.07 s), but the scheduling span is 6.47 s vs 3.47 s. Every held-group step (the retry,
  and each group advance) takes ~2.0 s instead of ~1.0 s: a cut during the scheduler's back-off
  wakes it early, it finds nothing, and the doubled poll puts it into a 2 s wait in which the
  awaited result lands; results never wake the scheduler. Reported, not fixed.

- **Foreman rulings (judge), 2026-11-03, second set.**
  1. The union ruling covers every rebase hunk that is only import lines or `__all__` entries where
     both sides add names (including P4's `data/model.py` `__all__` and `server.py` model import):
     resolve as the union, nothing else in the hunk. A hunk touching logic, a signature or a test
     body stops the rebase. Then rebase onto `ebd2534`, make the `test_result_columns_are_fixed`
     edit as ruled, and run 3.14, 3.11 and P5 parity.
  2. The scheduling-span regression (~2.0 s instead of ~1.0 s per retry or group advance) is in
     scope: fix it minimally inside Z2's code, either by not counting an early wake that finds
     nothing as an empty poll, or by waking the scheduler on a result. Never touch writeback's
     `Receiver`, `Confirm` or `update_all`. Red-first: a deterministic latency test on a small
     workload, bounded against 2.8.3's behaviour; P5 parity wall within ~0.5 s of 2.8.3.
  3. Commit signed at the next clean boundary; FSM stays at P6.

- **Back-off fix (second ruling 2), 2026-11-03.** `Dispatcher.wait_need` now says whether a cut or
  new demand ended the wait; the scheduler grows its back-off only after a wait that ran its full
  length, so a wake that finds nothing restarts the current step instead of moving to the next.
  `Receiver`, `Confirm` and `update_all` are untouched. Red first:
  `tests/test_dispatch_workload.py::test_retry_and_group_advance_latency` (retry 2.00 s at the
  previous head; bound 1.5 s, against 2.8.3's ~1.0 s), green three runs in a row.

- **After the rebase onto `ebd2534` (2026-11-03).** Hunks resolved: the three ruled at P1, then the
  P4 `data/model.py` `__all__` and `server.py` model-import hunks as unions (second ruling 1); no
  other hunk conflicted. Full suite 3.14 and 3.11: 736 passed, 47 skipped, 1 failed each, both
  `tests/test_writeback.py::test_non_result_columns_are_never_written`: [NEEDS CLARIFICATION] its
  body mutates every non-result column it lists by hand, then fails its own precondition because
  `zone` (now in that computed set) was not mutated. Passing needs a test-body edit
  (`copy.zone = uuid()`), which no ruling allows. P5 parity: identical tables and group order,
  TLS on and off. Five-repeat parity timing (load elevated): wall median 9.12 s vs 2.8.3's 11.86 s;
  scheduling span 3.83 vs 4.43 s; shutdown tail 3.69 vs 4.06 s.

- **Foreman ruling (judge), 2026-11-03, third.** The second and last bounded writeback-test
  exception, beside `test_result_columns_are_fixed`: `test_non_result_columns_are_never_written`
  gains `copy.zone = uuid()` among its hand-changed non-result columns (`task.zone` has no foreign
  key). Same rationale: `zone` is server-held, never a result column; the test's intent is unchanged.
  After it: full suite 737 passed, 47 skipped on both 3.14 and 3.11.

- **Merge of Z1 final + develop `b31c66e` (2026-11-03).** `git merge --no-ff ec75169` (Z1 final
  `7dd85c1` with develop `b31c66e`), signed, as merge commit `a040e1d`. Three files conflicted;
  every other file merged without a conflict.
  - `src/hypershell/core/queue.py` (foreman ruling 1: Z1's thread rule is authoritative):
    - `_INTERNALS` is the union: Z1's three thread checks, then this cycle's six work-request pins.
    - The accept loop starts each handler as
      `_ServerThread(target=self.handle_request, args=(conn, peer), name='hypershell-queue-handler')`,
      so the handler that logs `Client too old` is on the server side from its first statement.
    - `QueueClient.connect` keeps `_dispatcher_lock`, the `_get_scheduled(PROTOCOL)` /
      `QueueVersionError` handshake and the protocol sentence of its docstring. The docstring's
      hand-off sentence takes Z1's thread-rule wording.
    - Ported to the thread rule: `connect`'s `_note_dialed(self._address)` call is dropped (Z1
      removed address matching), as is the docstring's address wording. This cycle starts no other
      thread in a queue server: the dispatcher's `request` runs on the handler thread (a
      `_ServerThread`), and the lazy client-side dispatcher proxy is wrapped by `_defer_decref`
      like the four queues.
  - `.github/workflows/tests.yml` (ruling 2): the PostgreSQL job runs
    `tests/test_upgrade_postgres.py tests/test_writeback_postgres.py tests/test_timescale.py
    tests/test_zone_postgres.py`. Every matrix entry and job of both sides is kept. The three
    TimescaleDB entries run the same line. `tests/test_zone_postgres.py` fits there: it builds plain
    tables through `upgrade_schema`, as the upgrade and writeback modules do, and the `timescale`
    provider's hypertable pass is not on that path.
  - `ROADMAP.md` (ruling 3): every entry is kept. Z1's two queue entries come first (forked child
    keeps the listener; server-owned proxies without reference counting), then this cycle's
    (`hs client -b` reads the submit section's key).
- **Gates on `a040e1d`** (macOS; frozen dev venvs from `uv sync --frozen --offline` against
  standalone 3.11.18, 3.12.16, 3.13.18 and 3.14.10, from a `git archive` extract; each pytest run in
  an isolated site, in its own process group under a timeout). `unsupported_internals() == []` on
  all four.
  - **Full suite**, once each, the four side by side: 3.11.18 **817 passed, 71 skipped**; 3.12.16
    **821 passed, 67 skipped**; 3.13.18 and 3.14.10 **818 passed, 70 skipped** each. On each
    Python, 66 of the skips are database tests with no PostgreSQL server configured (test_upgrade_postgres
    29, test_timescale 20, test_writeback_postgres 14, test_zone_postgres 3), and 1 is the 2.8.3
    fixture regeneration. The rest are Z1's queue probes: on 3.11, the first-instruction (no
    `sys.monitoring`) and scheduled-elsewhere probes, 2 each; on 3.13 and 3.14, the
    scheduled-elsewhere probe (2) and the stale-entry test (1).
  - **`tests/test_queue_gc.py` + `tests/test_queue.py`, 3.12.16:** **10/10** consecutive runs pass,
    54 passed each, none skipped.
  - **Zone and dispatch modules, 3.12.16:** 98 passed, 3 skipped (test_zone_postgres, no
    server). test_zone_model 11, test_zone_dispatch 16, test_zone_workload 1, test_zone_display 9,
    test_dispatch 28, test_dispatch_version 4, test_dispatch_workload 7, test_client_request 8,
    test_cluster_compare 14.
  - **Local smoke** (`sh tests/cluster/smoke.sh`, `HSD_SMOKE_KEEP=1`; feature archive on Python
    3.13.18): homogeneous TLS on and off, 2000 tasks, identical results, PASS. Zoned rehearsal
    (`default:40,cpu:30,gpu:30`, 3 groups, one retry per zone, 4 clients, TLS) judged with
    `compare.py --zoned feature`: zones default 122, cpu 91, gpu 91, 304 rows, 4 retried, PASS.
    `smoke: PASS`, with no host or user name in the results.
  - **`parity` vs `b819de6`** (`--skip-help`, since this cycle changes client help): identical
    task tables and groups in order, TLS on and off, rc 0/[0, 0] on both builds.
  - **Compat vs `b819de6`**, as `skew` (which replaced `compat` in P3), TLS on and off. A `b819de6`
    client against this server exits rc 5 in 0.4 s and is logged `Client too old` once, while a new
    client finishes 200/200. A new client against a `b819de6` server exits rc 5 in 0.3 s with one
    CRITICAL `QueueVersionError`. PASS.
  - No follow-up commit was needed. `tests/test_groups.py::test_group_starts_based_on_previous_scheduled_tasks`
    passed in every full suite.

## Open rulings ([NEEDS CLARIFICATION])

The build proceeds on each **recommendation** unless the judge rules otherwise. Record any ruling
in GOAL.md Clarifications.

- **N1 — "Minted UUID" (R7) vs name-derived ids.** Options: (a) `uuid5(ZONE_NAMESPACE, name)`
  with `ON CONFLICT DO NOTHING`; (b) a random uuid7 with `ON CONFLICT (name) DO NOTHING` followed
  by `SELECT`. **Recommend (a):** it is race-free by construction (R8), needs no cache, and keeps
  the database off the manager thread and out of `--no-db` paths. The id is still a UUID minted
  by HyperShell and mapped to the name in `Zone`.
- **N2 — The default zone on `task` stored as NULL** (R7 says "each task SHALL reference its zone
  by UUID"). Options: (a) NULL is the default zone's reference for every row, old and new; (b)
  new rows store `DEFAULT_ZONE_ID` and the filter is `IS NULL OR = :id`. **Recommend (a):** R11
  already makes NULL mean "default" for old rows, and (b) loses the index on SQLite on R1's hot
  path ([04 §3](research/04-zone-data-model.md)).
- **N3 — Amending pinned white-box tests** (R14: "the existing test suite SHALL pass").
  `tests/test_server_fsm.py` pins `Scheduler` posting on `scheduled` and `HeartMonitor` sentinels:
  `TestScheduler.test_posts_bundle`, `test_reenters_on_timeout`,
  `TestHeartMonitor.test_signals_clients`, and `TestThroughQueueServer.test_heartmonitor` and
  `test_scheduler`. Z2 replaces that mechanism. Options: (a) re-point exactly these five at the
  dispatcher (same behaviour: a bundle is offered, the wait is finite and re-enters, clients are
  told to stop) and leave every other existing test unmodified, checked with `git diff --stat`;
  (b) keep `scheduled` as the scheduler's output and drain it into pools. **Recommend (a).** (b)
  breaks the per-zone footprint and the demand signal.
- **N4 — Which config key counts as an explicit client `-b`** (R2). `hs client -b` reads its
  default from `config.submit.bundlesize`, while the docs describe `[client].bundlesize`.
  Options: (a) advertise when `-b` is on the command line or
  `config.which('submit','bundlesize') != 'default'`, the key the client actually reads today;
  (b) switch the client to `client.bundlesize`. **Recommend (a)** for Z2: it reads R2 literally
  and doesn't change the collector's value. File the key mismatch as a seed.

## Conventions (apply to every phase)

- **Rules.**
  - [`AGENTS.md`](../../AGENTS.md) is the constitution. Use
    [`invariants.md`](../../.agents/factory/invariants.md) (§1–§6, §9–§12 apply here).
  - High blast radius: `core/queue.py`, new `core/dispatch.py`, `server.py`, `client.py`,
    `data/model.py`, `cluster/remote.py`, `cluster/ssh.py`. Each phase touches only its listed
    files; anything else needs a TECH amendment noted in the commit body.
  - Existing tests are not modified, except the five named in N3 (P2) and the `OPTION_PAIR`
    tables, which gain `--zone` (P4). Check with `git diff --stat <rebase-base> -- tests/`.
- **Commits and comments.**
  - One phase per `hs-build`; subject `[feature] Build zone-dispatch P<n>: …`.
  - No `Co-Authored-By` trailer.
  - Comments are declarative; no R-IDs or P-IDs in source.
- **Red first.** Write a phase's tests first, run them on the unchanged code, and record which are
  red in the commit body.
- **Hermetic drives.** Never run against a real site. CLI drives live in `@mark.integration`
  tests on the `temp_site` fixture, or isolate with `HYPERSHELL_SITE`,
  `HYPERSHELL_DATABASE_FILE` and `HYPERSHELL_CONFIG_FILE=''`. Every process runs in its own group,
  with a timeout, and the group is killed when done.
- **Logs tests key on** (keep their text):
  - client DEBUG `Received N tasks`
  - client INFO `Running task (<id>)`
  - server DEBUG `Dispatched N tasks to client <id> (zone <name>)`
  - server WARNING `Client too old`
  - server INFO `Task group G waiting on zone <name>`
  - Tests assert a non-zero match count so a rename can't pass silently.
- **Public repo.** No host, user, account, partition or site path in code, tests, evidence or
  docs.
- **Docs and completions** go in the same commit as each CLI change (§12): `docs/_include/*.rst`,
  `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`. Man pages are left to
  `/hs-release`.
- **Tooling.** Venv builds for `2.8.3` / `2.9.0a2` need the package index (`pypi.org`,
  `files.pythonhosted.org`) unless the uv cache is warm.

---

## Phase P1 — Zone schema and zone-filtered selection on the upgrade step
**Satisfies:** R7, R8, R9, R10, R11 · **Depends on:** — (build precondition)
**Goal:** the database knows zones. A 2.8 or 2.9.0a2 database gains the table, column and index in
place. `Task.next(zone=…)` selects only that zone, including retries, which keep their zone, and
group gating stays global. No dispatch change yet: every existing caller passes no zone and
behaves as today.

- [x] **Red first:** `tests/test_zone_model.py` (`@mark.unit`):
  - `zone_id` is deterministic, and `None`, `''` and `'default'` give `DEFAULT_ZONE_ID`.
  - `Zone.ensure` is idempotent (one row).
  - 6 processes × 20 rounds ensure the same new name, and the default, concurrently on one SQLite
    file: no failure, one row each.
  - A 2.9.0a2-shape database (built from `git show 2.9.0a2:src/hypershell/data/model.py` or a
    checked-in fixture DDL) gains `zone`, `task.zone` and `index_tasks_zone` through `checkdb`; a
    second run is a no-op; old rows read `zone IS NULL`.
  - `Task.next(zone=Z)` returns only Z; `zone=DEFAULT_ZONE_ID` returns NULL-zone rows; `zone=None`
    returns all (today).
  - A failed zoned task's retry row has the same zone.
  - `current_group` / `increment_group` advance only when every zone's tasks in the group are
    done.
- [x] `data/model.py`:
  - `Zone` entity (PLAN §2.4), `DEFAULT_ZONE_ID`, `DEFAULT_ZONE_NAME`, `ZONE_NAMESPACE`,
    `zone_id()`, `Zone.ensure()` (dialect-dispatched `insert().on_conflict_do_nothing()`),
    `Zone.names_for_ids()`.
  - `Task.zone` after `fingerprint`, and in `Task.columns`.
  - `index_tasks_zone`.
  - `_filter_zone` in `select_new` and `query_retryable`, with `zone=` threaded through `next`,
    its two private helpers and `__schedule_next_failed_tasks`. Do **not** edit the stamping loop.
  - `Task.new(zone=)` normalises the default to `None`.
  - The retry constructor adds `zone=task.zone`.
- [x] Record N1 and N2 as the readings used in the commit body.
- [x] PostgreSQL condition (plan gate): `tests/test_zone_postgres.py` (upsert under 8 concurrent
      sessions, zone-filtered selection and retry, in-place upgrade), added to the existing
      PostgreSQL CI job's pytest line; skipped without `HS_TEST_POSTGRES`. Gate: passes in CI.
- **Touches:** `src/hypershell/data/model.py`, `tests/test_zone_model.py`,
  `tests/test_zone_postgres.py`, `.github/workflows/tests.yml` (amended: plan gate condition).

## Phase P2 — Request callable, staging and the client request loop (default zone)
**Satisfies:** R1, R2, R3, R4, R13, R14 · **Depends on:** P1
**Goal:** clients request work and the server answers on their connection, from a staging pool
filled by the scheduler. Only the default zone is served: `--zone` arrives in P4. A plain `hsx`
run behaves as today. This is the core, novel slice.

- [x] **Red first:**
  - `tests/test_dispatch.py` (`@mark.unit`, no DB; fake rows as JSON text):
    - Cut size equals the advertised size; `None` gives the server's `-b`; clamped to `cap`.
    - Partial reply when fewer are ready.
    - An empty reply within `wait + 0.5 s`.
    - FIFO across 3 waiters.
    - 8 concurrent requesters on 1,000 staged ids: every id delivered exactly once.
    - A cut notifies `need`.
    - `finish()` wakes waiters; `shutdown: true` only once the pool is empty.
    - A malformed or protocol-mismatched request gives an error reply, never an exception.
    - `in_memory` drain from a local `scheduled` with re-cut.
  - `tests/test_client_request.py` (`@mark.unit`, fake dispatcher, monkeypatched
    `DEFAULT_REMOTE_TIMEOUT` / `check_signal`):
    - REQUEST re-enters on empty.
    - FINAL on `shutdown`.
    - Halts on SIGUSR1.
    - Advertises only an explicit `-b` (CLI, env `HYPERSHELL_SUBMIT_BUNDLESIZE`, config file), not
      the default.
  - `tests/test_dispatch_workload.py` (`@mark.integration`, `temp_site`, process groups, 120 s
    bounds):
    - `hsx` 200 tasks, TLS on and `--no-tls`.
    - `hsx --no-db`.
    - `hs server -b 100` plus two plain `hs client`s: each `Received` ≤ 100, and the sum equals
      the rows.
    - `hs server --no-db` + `hs submit -q` + a client.
    - Every task completes once, with group order preserved.
- [x] `core/dispatch.py` (new): `Staging` / `Dispatcher` (PLAN §2.1–2.2), `PROTOCOL = 1`,
      `request`, `stage`, `finish`, `forget`, `take_unstaged`, `cap`.
- [x] `core/queue.py`:
  - The `_get_dispatch` registry entry.
  - `QueueClient` registers it and creates it first, wrapped by `_defer_decref`.
  - `_get_scheduled` is created with `PROTOCOL` by the new client; the server side still accepts
    the no-argument form in this phase (the refusal is P3).
- [x] `server.py`:
  - `Scheduler` PACK/POST → STAGE (fill rule, `need.wait`, wake on new zone).
  - `HeartMonitor.signal_clients` → `dispatcher.finish()`.
  - `dispatcher.forget` on client finish and eviction (separate lines).
  - Leftover unstage after the heart monitor joins.
  - `in_memory` wiring.
  - The `-b` help text.
- [x] `core/config.py`: `server.bundlemax = 1000`.
- [x] `client.py`:
  - `GET_REMOTE` → `REQUEST`.
  - UNPACK from JSON dicts.
  - The advertise rule (N4).
  - `-b` default `None` and help text.
  - `ClientThread`/`run_client` take `zone=None`, unused until P4.
- [x] `tests/test_server_fsm.py`: re-point the five N3 tests at the dispatcher; nothing else.
- [x] `tests/bench/bench_queue.py`: `parity` gains `--skip-help` and the group-order check from
      `test_queue_workload.py`.
- [x] Docs (R13, same commit): `client_help.rst`, `client_desc.rst`, `server_help.rst`,
      `server_desc.rst`, `config_param_ref.rst` (`server.bundlemax`, client `-b`); the zsh `_hs`
      client `-b` description.
- [x] `AGENTS.md` (Architecture, Queue transport, Shutdown ordering) and `invariants.md` §6
      prose: dispatcher and `finish()` (PLAN §3 deviation row 5). Flag it in the commit body.
- **Build notes (amendments, 2026-11-02):**
  - The client's dispatcher proxy is created on first use (`QueueClient.dispatcher`), not first
    in `connect()`. `tests/test_queue_gc.py` (not one of the five N3 tests) pins four server
    objects per connected client; a client that never requests work (live submit, queue tests)
    now holds no dispatcher reference, and the requesting client still keeps three persistent
    connections. `connect()` creates `_get_scheduled(PROTOCOL)` first instead, which is where P3
    detects a server without the protocol (old providers take no argument).
  - `QueueVersionError` is defined here because the client's error-reply path raises it; P3
    adds the connect-time mapping and its tests.
  - `Dispatcher.stage` skips an id already staged: eager selection can list a new retry row
    twice (`issues/eager-retry-double-bundle.md`), and a pool must never hold one row twice.
  - The invariant prose is its own commit (plan gate ruling), before this one.
  - In memory, `QueueServer.start` attaches `scheduled` to the dispatcher as its inbound queue.
  - The scheduler's states are START, LOAD, PACK, FINAL (POST is gone; PACK now encodes the
    loaded chunks and stages them, and LOAD leaves the loaded rows in `Scheduler.tasks`), not a
    new STAGE state: `tests/test_retry_gate.py` (#68, not an N3 test) drives `load_bundle` on a
    queue-less scheduler and expects PACK. A scheduler built without a queue server stages into
    a dispatcher of its own, so those replays run unmodified.
- [x] **Review-1 remediation** (triage appended to `REVIEW.md`, 2026-11-04). P2 is reopened for
      all four findings: the staged-revert, staging and inbound-queue seams are P2's, and the
      zone-pool revert (`Scheduler.revert_unstaged`, P4's per-zone pools) goes through the same
      new classmethod, so P4 is not reopened. Red first, on `b946b14`'s `src`, all in the new
      `tests/test_dispatch_staged.py`:
  - **F1/F2.** `Task.revert_staged(ids)`: one conditional `UPDATE … WHERE id IN (:ids) AND
    completion_time IS NULL` that clears `Task.revert_columns`, with no rowcount assertion; it
    returns the rows reverted. Both staged reverts call it (`Scheduler.revert_unstaged` and
    `ServerThread.revert_staged`); their log lines keep their text and add how many staged rows were
    left as completed, cancelled or deleted.
    `revert_all` takes its columns from the same `revert_columns` and is otherwise unchanged.
    The ids are bound in chunks of `REVERT_CHUNK` (500), one conditional statement each, in one
    transaction, so a large pool stays within SQLite's and PostgreSQL's bound-parameter limits.
    Tests: `test_revert_staged_leaves_terminal_and_missing_rows` (unit, 1,203 ids),
    `test_staged_rows_changed_before_shutdown[cancel|delete]` and
    `test_staged_rows_changed_before_zone_empties[cancel|delete]` (integration).
  - **F3.** `Dispatcher.stage` replaces the record of an id already staged in place (its queue
    position kept), so a row reverted by hand and loaded again carries its current stamp; the
    duplicate guard (eager retries) still never holds a row twice. Tests:
    `test_stage_replaces_a_staged_record` (unit) and
    `test_reverted_staged_row_runs_with_its_new_stamp` (integration: every row staged before any
    client, one reverted, staged again, then delivered once and completed).
  - **F4.** `Dispatcher.takes_submissions()` (exposed to remote clients beside `request`) says
    whether the inbound queue is served, which only an in-memory server does.
    `LiveSubmitThread.check_server` asks once connected, before loading a task, and raises
    `QueueSubmitRefused` (a `RuntimeError`, so rc 5 with one CRITICAL line) naming the database
    and `--no-db`. `submit_help.rst` and `submit_desc.rst` say the server must run with
    `--no-db`. The deferred capability is seeded as `issues/queue-submit-database-server.md`
    (unshaped) with a `ROADMAP.md` entry. Test: `test_queue_submit_refused_by_database_server`;
    the in-memory path stays covered by `test_queue_submit_to_in_memory_server`.
  - **§6 invariant.** `AGENTS.md` and `invariants.md` name the staged revert between the heartbeat
    join and the receiver/confirm sentinels, and state that a staged revert never touches a
    completed or cancelled row (its own prose-only commit, before the code).
- **Touches:** `core/dispatch.py` (new), `core/queue.py`, `core/config.py`, `server.py`,
  `client.py`, `tests/test_dispatch.py`, `tests/test_client_request.py`,
  `tests/test_dispatch_workload.py`, `tests/test_server_fsm.py`, `tests/bench/bench_queue.py`,
  `docs/_include/{client_help,client_desc,server_help,server_desc,config_param_ref}.rst`,
  `share/zsh/site-functions/_hs`, `AGENTS.md`, `.agents/factory/invariants.md`. Review-1
  remediation (amendment): also `data/model.py`, `submit.py`, `tests/test_dispatch_staged.py`,
  `docs/_include/{submit_help,submit_desc}.rst`, `issues/queue-submit-database-server.md`,
  `ROADMAP.md`.

## Phase P3 — Version handshake
**Satisfies:** R5, R14 · **Depends on:** P2
**Goal:** a 2.8.x / 2.9.0a2 client is refused at its first create, exits at once, and is logged
as too old while other clients carry on. A new client against an old server exits non-zero with
one version error.

- [x] **Red first:**
  - `tests/test_dispatch_version.py` (`@mark.unit`): a stock-manager client registering only the
    four old typeids gets `RemoteError` containing "too old" from `_get_scheduled()`; the server
    logs one WARNING `Client too old`; a concurrent new `QueueClient` round-trips a request.
  - A new `QueueClient` against a stock `BaseManager` serving only the four queues raises
    `QueueVersionError` naming the mismatch, within 5 s.
  - An error reply with `protocol: 2` raises `QueueVersionError` with both numbers.
  - `hs client` against that stock server exits non-zero with exactly one CRITICAL line.
- [x] `core/queue.py`:
  - The versioned `_get_scheduled` provider and `ClientTooOld`.
  - The thread-local flag and the WARNING in `handle_request`.
  - `QueueVersionError`.
  - Pins added to `unsupported_internals()` and `TestPrivateInternals` (PLAN §2.1).
- [x] `tests/bench/bench_queue.py`: replace `compat` with
      `skew --new REF --old REF… --bound S`, run TLS on and off. Old client against new server:
      the client runs without `-T`, exits within the bound, the server log says "too old", and a
      concurrent new client finishes all tasks. New client against old server: non-zero within
      the bound, one version error.
- **Build notes (amendments, 2026-11-02):**
  - A new client finds an old server at its first create, `_get_scheduled(PROTOCOL)`, not at
    `_get_dispatch` (created lazily since P2): every older server's provider takes no argument,
    so the create fails with `RemoteError`, which `connect()` raises as `QueueVersionError`.
  - The refusal sets a thread-local flag in the provider; `_InProcessServer.handle_request`
    logs `Client too old (no work-request protocol) from <peer>` once the connection closes,
    outside the server mutex. The peer address is captured at accept.
  - A reply whose `protocol` differs from the client's also raises `QueueVersionError`, naming
    both protocol numbers.
- **Touches:** `core/queue.py`, `client.py` (error path only, if needed),
  `tests/test_dispatch_version.py`, `tests/test_queue.py` (new pin test methods appended, existing
  ones unmodified), `tests/bench/bench_queue.py`.

## Phase P4 — Zones end to end
**Satisfies:** R6, R9, R10, R11, R13, R14 · **Depends on:** P3
**Goal:** `--zone` on submit, task submit, client and cluster; per-zone pools with demand
registration and zone-loss unstaging; the held-group log line. A loopback mixed-zone run
completes with every task run only by a client of its zone.

- [x] **Red first:**
  - `tests/test_zone_dispatch.py` (`@mark.unit`):
    - Requests register demand.
    - The scheduler fills only demanded zones, and the default zone always.
    - A zoned request never receives another zone's task.
    - A client with no `--zone` gets only default-zone tasks.
    - Forgetting a zone's last client unstages its pool by id and the rows read unscheduled.
    - The held-group INFO line appears once per (group, zone).
  - `tests/test_zone_workload.py` (`@mark.integration`, `temp_site`, process groups, 180 s
    bound):
    - `hs server` with clients in zones `cpu` and `gpu` and unzoned clients, mixed `-b`
      (advertised and not).
    - Three groups mixing zones.
    - One task per zone fails on attempt 1 only (`$TASK_ATTEMPT`).
    - Each client sets a `ZONE_MARK` env var its tasks echo, which is compared with the task's
      zone.
    - Asserts: completion, zero cross-zone runs, retries keep zone, global group order, each task
      id in exactly one client's `Running task` lines per attempt.
  - CLI: `--zone` parse and missing-value cases added to the `OPTION_PAIR` tables in
    `test_submit.py` and `test_cluster.py`; `--zone` with `-q` / `--no-db` is a usage error; an
    empty or control-character name is a usage error.
- [x] `core/dispatch.py`: per-zone pools, the demand map, `forget` → unstage marking.
- [x] `server.py`:
  - `Scheduler` loops the demanded zones.
  - `Zone.ensure(default)` at start in DB mode.
  - The held-group log.
  - The zone-loss unstage via `Task.revert_all`.
- [x] `client.py`: `--zone` (shared validator) and the zone in the request.
- [x] `submit.py`: `--zone` (DB mode; `GatedSource.zone_id`; `submit_one`); rejected with `-q`.
- [x] `task.py`: `hs task submit --zone`.
- [x] `cluster/__init__.py`, `local.py`, `remote.py` (remote and autoscale), `ssh.py`
      (`shlex.quote`): `--zone` propagation and cluster-submit stamping; rejected with `--no-db`.
- [x] Docs and completions (same commit):
  - `submit_usage/help`, `task_submit_usage/help`, `client_usage/help`, `cluster_usage/help`.
  - bash `_hs_submit`, `_hs_task_submit`, `_hs_client`, `_hs_cluster`; zsh likewise.
- **Build notes (amendments, 2026-11-02):**
  - P2 already built the dispatcher zone-generic (demand map, per-zone pools and cut, forget and
    unstage marking), so `test_request_registers_demand`, `test_zoned_request_gets_only_its_zone`,
    `test_scheduler_fills_demanded_zones_only`, the unstage tests and `test_new_zone_wakes_the_scheduler`
    were green on arrival; red on P3 were the held-group line, the CLI (`--zone` parse, usage
    errors, stamping) and the mixed-zone workload.
  - The shared name check is `zone_name()` in `data/model.py`, beside `zone_id()` (amended touch):
    a name is stripped, non-empty, at most 256 characters and printable.
  - The held-group line counts a zone's unscheduled tasks of the group (plus, for the default
    zone, its staged rows) and fires once per (group, zone) for a zone no client serves. The
    default zone is reported only while some client serves another zone, so a run without zones
    logs nothing new.
  - `Zone.ensure('default')` runs in `Scheduler.start` (the scheduler thread, DB mode only).
  - Sphinx: the build runs outside the sandbox (an extension binds a local socket); its two
    warnings (`cli/task_submit.rst`, `manual.rst` not in a toctree) are not from this phase.
- **Touches:** `core/dispatch.py`, `server.py`, `client.py`, `submit.py`, `task.py`,
  `cluster/{__init__,local,remote,ssh}.py`, `tests/test_zone_dispatch.py`,
  `tests/test_zone_workload.py`, `tests/test_submit.py`, `tests/test_cluster.py`,
  `docs/_include/*`, `share/*`.

## Phase P5 — R1 gate against 2.8.3
**Satisfies:** R1, R14 · **Depends on:** P4
**Goal:** the default path shows ≥ 95% of 2.8.3's throughput (median of 7) with no more server
processes or open descriptors, and parity holds against 2.8.3.

- [x] `tests/bench/bench_queue.py gate`:
  - `--min-ratio R`: pass iff `feature_median ≥ R × base_median` per config, plus Z1's footprint
    checks.
  - Add configs where the client gets **no** `-b` (the R1 default path), next to the existing
    explicit-`-b` ones.
  - Report both.
- [x] Run the gate. If a config misses only because of `index_tasks_zone`, remove the index (P1
      leaves it declared separately) and record that in the commit body.
- [x] Gate record in this section: the table, interpreter, and host load.
- **Gate record (2026-11-02, this host).** `bench_queue.py gate --base 2.8.3 --feature <P5 candidate
  6d11e5c> --repeats 7 --min-ratio 0.95`, interleaved; CPython 3.14.6, start method spawn. Started at
  1-minute load 5.4 (rule: below 6); other worktrees' agents then pushed it to a peak of 47.2 (38
  one-minute samples: min 4.7, mean 14.6; 11.6 at the end), which both builds shared run for run.
  Verdict PASS on all nine configs; tree children 0 vs 2 and tree FDs lower on every config.

  | config | base median [min-max] | feature median [min-max] | delta | ratio >= 0.95 | throughput | tree children b/f | tree FDs b/f | tree threads b/f | own threads b/f | own FDs b/f | tree RSS MB b/f | clean | verdict |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 277.6 [253.8-287.4] | 305.2 [300.1-316.8] | +27.6 | 1.1 | PASS | 2/0 | 118/22 | 25/20 | 7/20 | 53/23 | 195.1/96.1 | True | PASS |
  | 4x4 b=1 no-tls | 210.1 [161.2-285.9] | 221.1 [176.0-324.4] | +11.0 | 1.05 | PASS | 2/0 | 117/24 | 25/20 | 7/20 | 53/24 | 192.5/92.2 | True | PASS |
  | 16x1 b=1 tls | 163.1 [156.2-294.8] | 175.0 [172.9-294.3] | +11.9 | 1.07 | PASS | 2/0 | 154/60 | 61/56 | 7/56 | 53/60 | 201/100 | True | PASS |
  | 16x1 b=1 no-tls | 262.7 [157.6-273.3] | 297.3 [174.5-306.3] | +34.6 | 1.13 | PASS | 2/0 | 154/60 | 61/56 | 7/56 | 53/60 | 194.7/94.6 | True | PASS |
  | 4x4 b=50 tls | 847.8 [839.9-855.2] | 849.9 [844.8-853.6] | +2.1 | 1 | PASS | 2/0 | 116/22 | 25/20 | 7/20 | 51/22 | 202.2/99 | True | PASS |
  | 32x1 b=1 tls | 268.2 [263.9-274.9] | 289.1 [286.8-292.1] | +20.9 | 1.08 | PASS | 2/0 | 201/107 | 109/104 | 7/104 | 52/107 | 203.9/103.5 | True | PASS |
  | 4x4 b=1 client-default tls | 258.8 [253.9-290.8] | 288.6 [284.5-320.1] | +29.8 | 1.12 | PASS | 2/0 | 118/22 | 25/20 | 7/20 | 53/23 | 197.2/95.9 | True | PASS |
  | 16x1 b=1 client-default no-tls | 292.7 [270.5-299.9] | 333.0 [312.6-339.1] | +40.3 | 1.14 | PASS | 2/0 | 154/60 | 61/56 | 7/56 | 52/59 | 193/94.2 | True | PASS |
  | 4x4 b=50 client-default tls | 557.1 [530.0-586.6] | 546.3 [542.7-580.4] | -10.8 | 0.981 | PASS | 2/0 | 117/22 | 25/20 | 7/20 | 52/23 | 198.3/97.2 | True | PASS |

- **Build notes (amendments, 2026-11-02):**
  - The first probe ran at 0.89-0.92 of 2.8.3 on `b=1` (the Z1 base itself measured 0.954).
    Removing `index_tasks_zone` did not help (0.92 of the indexed build). The cost was the
    default-zone selection's sort: `WHERE zone IS NULL AND group = ? AND schedule_time IS NULL
    ORDER BY submit_time` sorted every unscheduled row per load. `index_tasks_zone` now ends in
    `submit_time` (zone, group, schedule_time, submit_time), so SQLite reads rows in submit order
    with no temporary B-tree; the index is new in this cycle, so redefining it is free.
  - Waiting requests now each wait on their own condition, and only the head of a zone's line is
    woken by a stage or by the request before it leaving (was `notify_all` on every waiter).
    Touch amended: `core/dispatch.py`.
- **Touches:** `tests/bench/bench_queue.py`, `src/hypershell/data/model.py` (index only),
  `src/hypershell/core/dispatch.py` (amended), `spec/zone-dispatch/TECH.md`.

## Phase P6 — Multi-node drive, run A
**Satisfies:** R1, R4, R14 · **Depends on:** P4 · parallel with P5
**Goal:** extend the cluster-agnostic drive so the foreman can run a heterogeneous, mixed-zone
multi-node run plus a homogeneous throughput run, and hand the drive back. The foreman runs the
cluster; this phase only builds and smoke-tests locally.

- [x] `tests/cluster/drive.sh` knobs (environment only, generic):
  - Server: `HSD_ZONES` (e.g. `default:50,cpu:25,gpu:25`, the task mix by zone), `HSD_GROUPS`,
    `HSD_RETRIES`, `HSD_CAP` (`server.bundlemax`).
  - Client: `HSD_ZONE`, `HSD_CLIENT_ADVERTISE` (1 = pass `-b`, 0 = omit it), `HSD_CLIENT_LOG`
    (DEBUG for zoned runs).
  - Zoned tasks check a per-client zone marker and exit 7 when run in the wrong zone, so a
    cross-zone run shows in the exit histogram without per-task data leaving the node.
  - Every `HSD_FAIL_EVERY`-th task fails on attempt 1 only.
- [x] Records (no host, user or path):
  - `server.json` gains per-zone task counts, retry rows whose zone differs from their parent
    (must be 0), evictions, rows unfinished, and a group-order flag.
  - `client-N.json` gains its zone, advertised size (or null), tasks received, largest bundle,
    and exit-7 count.
- [x] `tests/cluster/compare.py`:
  - `--zoned LABEL` checks:
    - every task completed;
    - zero exit-7;
    - zero zone-mismatched retries;
    - the per-client sums equal the row count, so no double delivery per attempt;
    - each client's largest bundle ≤ `min(advertised or server -b, cap)`;
    - group order held.
  - `--min-ratio R` for throughput, judged only on homogeneous runs with
    `HSD_CLIENT_ADVERTISE=0` against the 2.8.3 baseline.
  - Run grouping keys on zones, cap and advertise, so modes never pool.
- [x] `tests/cluster/smoke.sh`: add a local zoned rehearsal: 2 zones + default, 4 clients (2
      advertise, 2 don't), `HSD_TASKS=300`, one retry per zone. `compare.py --zoned` on it must
      pass.
- [x] Hand back to the foreman the run matrix it must execute (all 3 builds via
      `build_archive.sh`, TLS on):
  1. **Homogeneous throughput**, `HSD_CLIENT_ADVERTISE=0`, no zones. 2.8.3, 2.9.0a2 and
     feature, interleaved, ≥ 3 repeats each, same node count. Judges R1 at scale.
  2. **Heterogeneous zoned**, feature only:
     - ≥ 4 client nodes.
     - Zones `cpu` and `gpu` plus unzoned clients.
     - At least one client per zone with `HSD_CLIENT_ADVERTISE=1` and a distinct `-b` (e.g. 1,
       16, 64), and at least one per zone with 0.
     - `HSD_ZONES=default:40,cpu:30,gpu:30`, `HSD_GROUPS=3`, retries on, `HSD_CAP=32` in one
       repeat (so the 64 advertiser is capped).
     - ≥ 2 repeats.
  3. Results go under `spec/zone-dispatch/evidence/cluster/` (JSON only, scrubbed).
- **Build notes (2026-11-02):**
  - Unset `HSD_ZONES` leaves the task file exactly as before, so 2.8.3 and 2.9.0a2 archives run the
    homogeneous drive unchanged. With zones, each zone's file is submitted with `--zone` (feature
    archives only), and every HSD_FAIL_EVERY-th task *of each zone* fails; with `HSD_RETRIES` it
    fails on attempt 1 only (`$TASK_ATTEMPT`). A wrong-zone run appends to the client's
    node-local mismatch file and exits 7; the client record counts those lines (`exit7`).
  - `HSD_CLIENT_ADVERTISE=1` (the default) keeps today's `-b`; `HSD_CAP` exports
    `HYPERSHELL_SERVER_BUNDLEMAX` to the server. `received`/`largest` need `HSD_CLIENT_LOG=DEBUG`.
  - `compare.py` keys groups on zones, groups, retries, cap and whether clients advertise; a
    homogeneous group with retries expects `rows = M + retried`. Unit tests on synthetic records:
    `tests/test_cluster_compare.py` (14).
  - Plan gate layout (4 nodes, 1 h class): node 0 server, nodes 1-3 clients; Run 1 and Run 2 are
    separate jobs, each guarded to finish inside 54 minutes.
- **Hand-back: how the foreman runs it** (all archives from `build_archive.sh`: labels `2.8.3`,
  `2.9.0a2`, `feature`; same `HSD_RESULTS`; distinct `HSD_RUN_ID` per run; `HSD_TLS=1`,
  `HSD_HOST` the server node's name, `HSD_LOCAL` node-local scratch):
  1. **Run 1 job — homogeneous throughput.** Per run: server on node 0 with `HSD_ROLE=server
     HSD_TASKS=20000 HSD_FAIL_EVERY=100 HSD_BUNDLESIZE=1 HSD_CLIENTS=3 HSD_THREADS=16
     HSD_TIMEOUT=300`; one client per node 1-3 with `HSD_ROLE=client HSD_CLIENT_INDEX=1..3
     HSD_THREADS=16 HSD_CLIENT_ADVERTISE=0`. Interleave labels 2.8.3, 2.9.0a2, feature, three
     repeats each (9 runs); abort the remaining runs if the job passes 54 minutes.
  2. **Run 2 job — heterogeneous zoned, feature only.** Server: `HSD_ZONES=default:40,cpu:30,gpu:30
     HSD_GROUPS=3 HSD_RETRIES=1 HSD_FAIL_EVERY=50 HSD_TASKS=20000 HSD_BUNDLESIZE=4 HSD_CLIENTS=6
     HSD_TIMEOUT=600`. Two client processes per node, each `HSD_CLIENT_LOG=DEBUG HSD_THREADS=8`:
     node 1 `cpu` (ADVERTISE=1, CLIENT_BUNDLESIZE=1) and `gpu` (ADVERTISE=0); node 2 `cpu`
     (ADVERTISE=0) and `gpu` (ADVERTISE=1, CLIENT_BUNDLESIZE=16); node 3 unzoned (ADVERTISE=1,
     CLIENT_BUNDLESIZE=64) and unzoned (ADVERTISE=0); `HSD_ZONE` set for the zoned ones, distinct
     `HSD_CLIENT_INDEX`. Two repeats, the second with `HSD_CAP=32` (caps the 64 advertiser).
  3. Judge (P7's verify): `uv run python tests/cluster/compare.py spec/zone-dispatch/evidence/cluster
     --baseline 2.8.3 --candidate feature --control 2.9.0a2 --min-ratio 0.95 --zoned feature`
     after copying both jobs' JSON (scrubbed: `smoke.sh`'s host/user grep) into that directory.
- **Touches:** `tests/cluster/{drive.sh,compare.py,smoke.sh}`, `tests/test_cluster_compare.py` (new).

## Phase P7 — Multi-node drive, run B
**Satisfies:** R1, R4, R14 · **Depends on:** P5, P6
**Goal:** judge the foreman's evidence. Blocked (external wait) until
`spec/zone-dispatch/evidence/cluster/` exists.

- [x] Run the verify, and record the summary table and the run matrix actually executed.
- **P6 closed (2026-11-04).** The foreman executed the hand-back matrix (below), and ran P6's local
  smoke on `a040e1d` (`smoke: PASS`, recorded under Build status), so P6 is marked done.
- **Harness note (foreman, verbatim):**

  > Run 1 ran on the second test cluster and Run 2 on the third (the second became contended, the
  > first was down); each job is self-contained, and Run 1 interleaves its own baseline and control.
  > Both used 4 whole nodes, server on node 0 bound to 16 CPUs, TLS on, SQLite, archives built by
  > `build_archive.sh` (label `feature` = `3d7231e`, `2.9.0a2` = `d996579`, `2.8.3` = `4d5771b`), and
  > the run scripts implement TECH P6's hand-back exactly, with `HSD_TIMEOUT=600` for Run 1 (TECH
  > said 300; raised so a slow run could not time out). Run 1: 3 clients × 16 threads, advertise off.
  > Run 2: six clients per the P6 table, `HSD_ZONES=default:40,cpu:30,gpu:30`, `HSD_GROUPS=3`,
  > retries on, `HSD_FAIL_EVERY=50`, second repeat with `HSD_CAP=32`.

- **Matrix executed** (every run: Python 3.13.18, start method fork, TLS on, 20,000 tasks; the
  records carry no host, user or path, and the re-scrub — `smoke.sh`'s host/user grep plus cluster
  and user names and site path prefixes — found nothing):
  - **Run 1, homogeneous** (`run1-<label>-r<n>`): server `HSD_BUNDLESIZE=1 HSD_FAIL_EVERY=100
    HSD_THREADS=16 HSD_TIMEOUT=600`, 3 clients × 16 threads, `HSD_CLIENT_ADVERTISE=0`. Nine runs in
    the order 2.8.3, 2.9.0a2, feature, repeated three times (client start times confirm it).
  - **Run 2, zoned, feature only:** `run2-feature-r1` and `run2-feature-r2-cap32` (`HSD_CAP=32`).
    Server `HSD_BUNDLESIZE=4 HSD_RETRIES=1 HSD_FAIL_EVERY=50 HSD_TIMEOUT=600`, six clients × 8
    threads, `HSD_CLIENT_LOG=DEBUG`:

    | client | zone | advertised | r1 received / largest | r2 (cap 32) received / largest |
    |---|---|---|---|---|
    | 1 | cpu | 1 | 1546 / 1 | 1641 / 1 |
    | 2 | gpu | — | 565 / 4 | 773 / 4 |
    | 3 | cpu | — | 4574 / 4 | 4479 / 4 |
    | 4 | gpu | 16 | 5555 / 16 | 5347 / 16 |
    | 5 | default | 64 | 7690 / 64 | 7515 / **32** |
    | 6 | default | — | 470 / 4 | 645 / 4 |

    Each repeat: 20,400 rows (400 retried, exit histogram `0: 20000, 3: 400`), received sums to
    20,400, zones default 8,160 / cpu 6,120 / gpu 6,120, zero exit 7, zero zone-mismatched retries,
    zero evictions, group order held. p10-p90 throughput 458.2 and 488.9 tasks/s.
  - Departures from the hand-back: Run 1's `HSD_TIMEOUT` 600 (was 300), per the note. The two zoned
    repeats differ in cap, so `compare.py` keys them as two groups of one run each; each run is
    judged on its own, as `--zoned` does.
- **Verify** (2026-11-04): `uv run python tests/cluster/compare.py spec/zone-dispatch/evidence/cluster
  --baseline 2.8.3 --candidate feature --control 2.9.0a2 --min-ratio 0.95 --zoned feature` →
  **verdict: PASS**, exit 0. Plain `uv run` could not take its cache lock in this build's sandbox;
  the same command ran as `uv run --frozen --offline` against a frozen dev venv (warm uv cache,
  CPython 3.13.18). `compare.py` is standard-library only.

  | label (Run 1) | runs | median p10-p90 tasks/s [min-max] | ratio to 2.8.3 | tree children | tree FDs | tree threads | tree RSS MB |
  |---|---|---|---|---|---|---|---|
  | 2.8.3 | 3 | 140.6 [140.3-140.7] | 1 | 1 | 43 | 21 | 155 |
  | 2.9.0a2 (control) | 3 | 132.8 [130.1-132.8] | 0.944 | 1 | 44 | 21 | 157 |
  | feature | 3 | 201.3 [199.5-201.4] | **1.432** (min 0.95) PASS | 0 | 19 | 17 | 93 |

  Results identical across all nine runs (`0: 19800, 3: 200`), every rc 0. Attribution: 2.9.0a2 vs
  2.8.3 −7.8 tasks/s; feature vs 2.9.0a2 +68.5 tasks/s. Footprint feature vs 2.8.3: children 0 vs
  1, FDs 19 vs 43, PASS. Zoned: both runs PASS on every check.
- **Touches:** `spec/zone-dispatch/TECH.md`, `spec/zone-dispatch/evidence/cluster/` (as delivered).

## Phase P8 — (Deferrable) Zone by name
**Satisfies:** R12 · **Depends on:** P4 · cut to Z3 cleanly if the cycle overruns
**Goal:** users never need the raw zone id.

- [x] **Red first:** `tests/test_zone_display.py` (`@mark.unit`, `temp_site`):
  - `hs info` normal view shows `zone: gpu (<id>)` and `zone: default` for unzoned tasks.
  - The card shows the zone row.
  - `hs list` plain/table/json/csv and `hs info --json` / `-x zone` show the name.
  - `Task.to_json()` is unchanged.
- [x] `task.py`: `resolve_zone`, `format_task_fields`, the normal template, the card submission
      region, and the list printers.
- [x] `data/model.py`: `Zone.names_for_ids`, if not already added in P1.
- **Build notes (2026-11-02):** `Zone.names_for_ids` landed in P1, so `data/model.py` is untouched
  here. The normal view prints `zone: NAME (ID)` for a named zone and `zone: default` for NULL;
  the card adds a `zone` row to the submission region; `hs list` plain/table/json/csv and
  `hs info --json`/`--yaml`/`-x zone` print the name (an id with no zone row prints as the id).
  `Task.to_json()` and the task environment (`TASK_ZONE`, the id) are unchanged.
- **Touches:** `task.py`, `data/model.py`, `tests/test_zone_display.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, the build precondition met.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/`).
4. Run the phase's `verify:`. Never advance on a checkbox alone.
5. Amend this file if reality diverges, and note it in the commit body. STOP and escalate only on a
   `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and
   report.
