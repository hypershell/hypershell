---
slug: zone-dispatch
title: Zone-aware, demand-driven dispatch (zone, cycle 2)
kind: feature
appetite: big
status: in_progress
branch: feature/zone-dispatch
base: develop
current_phase: P2
last_updated: '2026-11-02'
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
  status: pending
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
  status: pending
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
  status: pending
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
  status: pending
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
  status: pending
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
  status: pending
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
  status: pending
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
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
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

- [ ] **Red first:**
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
- [ ] `core/dispatch.py` (new): `Staging` / `Dispatcher` (PLAN §2.1–2.2), `PROTOCOL = 1`,
      `request`, `stage`, `finish`, `forget`, `take_unstaged`, `cap`.
- [ ] `core/queue.py`:
  - The `_get_dispatch` registry entry.
  - `QueueClient` registers it and creates it first, wrapped by `_defer_decref`.
  - `_get_scheduled` is created with `PROTOCOL` by the new client; the server side still accepts
    the no-argument form in this phase (the refusal is P3).
- [ ] `server.py`:
  - `Scheduler` PACK/POST → STAGE (fill rule, `need.wait`, wake on new zone).
  - `HeartMonitor.signal_clients` → `dispatcher.finish()`.
  - `dispatcher.forget` on client finish and eviction (separate lines).
  - Leftover unstage after the heart monitor joins.
  - `in_memory` wiring.
  - The `-b` help text.
- [ ] `core/config.py`: `server.bundlemax = 1000`.
- [ ] `client.py`:
  - `GET_REMOTE` → `REQUEST`.
  - UNPACK from JSON dicts.
  - The advertise rule (N4).
  - `-b` default `None` and help text.
  - `ClientThread`/`run_client` take `zone=None`, unused until P4.
- [ ] `tests/test_server_fsm.py`: re-point the five N3 tests at the dispatcher; nothing else.
- [ ] `tests/bench/bench_queue.py`: `parity` gains `--skip-help` and the group-order check from
      `test_queue_workload.py`.
- [ ] Docs (R13, same commit): `client_help.rst`, `client_desc.rst`, `server_help.rst`,
      `server_desc.rst`, `config_param_ref.rst` (`server.bundlemax`, client `-b`); the zsh `_hs`
      client `-b` description.
- [ ] `AGENTS.md` (Architecture, Queue transport, Shutdown ordering) and `invariants.md` §6
      prose: dispatcher and `finish()` (PLAN §3 deviation row 5). Flag it in the commit body.
- **Touches:** `core/dispatch.py` (new), `core/queue.py`, `core/config.py`, `server.py`,
  `client.py`, `tests/test_dispatch.py`, `tests/test_client_request.py`,
  `tests/test_dispatch_workload.py`, `tests/test_server_fsm.py`, `tests/bench/bench_queue.py`,
  `docs/_include/{client_help,client_desc,server_help,server_desc,config_param_ref}.rst`,
  `share/zsh/site-functions/_hs`, `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P3 — Version handshake
**Satisfies:** R5, R14 · **Depends on:** P2
**Goal:** a 2.8.x / 2.9.0a2 client is refused at its first create, exits at once, and is logged
as too old while other clients carry on. A new client against an old server exits non-zero with
one version error.

- [ ] **Red first:**
  - `tests/test_dispatch_version.py` (`@mark.unit`): a stock-manager client registering only the
    four old typeids gets `RemoteError` containing "too old" from `_get_scheduled()`; the server
    logs one WARNING `Client too old`; a concurrent new `QueueClient` round-trips a request.
  - A new `QueueClient` against a stock `BaseManager` serving only the four queues raises
    `QueueVersionError` naming the mismatch, within 5 s.
  - An error reply with `protocol: 2` raises `QueueVersionError` with both numbers.
  - `hs client` against that stock server exits non-zero with exactly one CRITICAL line.
- [ ] `core/queue.py`:
  - The versioned `_get_scheduled` provider and `ClientTooOld`.
  - The thread-local flag and the WARNING in `handle_request`.
  - `QueueVersionError`.
  - Pins added to `unsupported_internals()` and `TestPrivateInternals` (PLAN §2.1).
- [ ] `tests/bench/bench_queue.py`: replace `compat` with
      `skew --new REF --old REF… --bound S`, run TLS on and off. Old client against new server:
      the client runs without `-T`, exits within the bound, the server log says "too old", and a
      concurrent new client finishes all tasks. New client against old server: non-zero within
      the bound, one version error.
- **Touches:** `core/queue.py`, `client.py` (error path only, if needed),
  `tests/test_dispatch_version.py`, `tests/test_queue.py` (new pin test methods appended, existing
  ones unmodified), `tests/bench/bench_queue.py`.

## Phase P4 — Zones end to end
**Satisfies:** R6, R9, R10, R11, R13, R14 · **Depends on:** P3
**Goal:** `--zone` on submit, task submit, client and cluster; per-zone pools with demand
registration and zone-loss unstaging; the held-group log line. A loopback mixed-zone run
completes with every task run only by a client of its zone.

- [ ] **Red first:**
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
- [ ] `core/dispatch.py`: per-zone pools, the demand map, `forget` → unstage marking.
- [ ] `server.py`:
  - `Scheduler` loops the demanded zones.
  - `Zone.ensure(default)` at start in DB mode.
  - The held-group log.
  - The zone-loss unstage via `Task.revert_all`.
- [ ] `client.py`: `--zone` (shared validator) and the zone in the request.
- [ ] `submit.py`: `--zone` (DB mode; `GatedSource.zone_id`; `submit_one`); rejected with `-q`.
- [ ] `task.py`: `hs task submit --zone`.
- [ ] `cluster/__init__.py`, `local.py`, `remote.py` (remote and autoscale), `ssh.py`
      (`shlex.quote`): `--zone` propagation and cluster-submit stamping; rejected with `--no-db`.
- [ ] Docs and completions (same commit):
  - `submit_usage/help`, `task_submit_usage/help`, `client_usage/help`, `cluster_usage/help`.
  - bash `_hs_submit`, `_hs_task_submit`, `_hs_client`, `_hs_cluster`; zsh likewise.
- **Touches:** `core/dispatch.py`, `server.py`, `client.py`, `submit.py`, `task.py`,
  `cluster/{__init__,local,remote,ssh}.py`, `tests/test_zone_dispatch.py`,
  `tests/test_zone_workload.py`, `tests/test_submit.py`, `tests/test_cluster.py`,
  `docs/_include/*`, `share/*`.

## Phase P5 — R1 gate against 2.8.3
**Satisfies:** R1, R14 · **Depends on:** P4
**Goal:** the default path shows ≥ 95% of 2.8.3's throughput (median of 7) with no more server
processes or open descriptors, and parity holds against 2.8.3.

- [ ] `tests/bench/bench_queue.py gate`:
  - `--min-ratio R`: pass iff `feature_median ≥ R × base_median` per config, plus Z1's footprint
    checks.
  - Add configs where the client gets **no** `-b` (the R1 default path), next to the existing
    explicit-`-b` ones.
  - Report both.
- [ ] Run the gate. If a config misses only because of `index_tasks_zone`, remove the index (P1
      leaves it declared separately) and record that in the commit body.
- [ ] Gate record in this section: the table, interpreter, and host load.
- **Touches:** `tests/bench/bench_queue.py`, `src/hypershell/data/model.py` (index only, if
  removed), `spec/zone-dispatch/TECH.md`.

## Phase P6 — Multi-node drive, run A
**Satisfies:** R1, R4, R14 · **Depends on:** P4 · parallel with P5
**Goal:** extend the cluster-agnostic drive so the foreman can run a heterogeneous, mixed-zone
multi-node run plus a homogeneous throughput run, and hand the drive back. The foreman runs the
cluster; this phase only builds and smoke-tests locally.

- [ ] `tests/cluster/drive.sh` knobs (environment only, generic):
  - Server: `HSD_ZONES` (e.g. `default:50,cpu:25,gpu:25`, the task mix by zone), `HSD_GROUPS`,
    `HSD_RETRIES`, `HSD_CAP` (`server.bundlemax`).
  - Client: `HSD_ZONE`, `HSD_CLIENT_ADVERTISE` (1 = pass `-b`, 0 = omit it), `HSD_CLIENT_LOG`
    (DEBUG for zoned runs).
  - Zoned tasks check a per-client zone marker and exit 7 when run in the wrong zone, so a
    cross-zone run shows in the exit histogram without per-task data leaving the node.
  - Every `HSD_FAIL_EVERY`-th task fails on attempt 1 only.
- [ ] Records (no host, user or path):
  - `server.json` gains per-zone task counts, retry rows whose zone differs from their parent
    (must be 0), evictions, rows unfinished, and a group-order flag.
  - `client-N.json` gains its zone, advertised size (or null), tasks received, largest bundle,
    and exit-7 count.
- [ ] `tests/cluster/compare.py`:
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
- [ ] `tests/cluster/smoke.sh`: add a local zoned rehearsal: 2 zones + default, 4 clients (2
      advertise, 2 don't), `HSD_TASKS=300`, one retry per zone. `compare.py --zoned` on it must
      pass.
- [ ] Hand back to the foreman the run matrix it must execute (all 3 builds via
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
- **Touches:** `tests/cluster/{drive.sh,compare.py,smoke.sh}`.

## Phase P7 — Multi-node drive, run B
**Satisfies:** R1, R4, R14 · **Depends on:** P5, P6
**Goal:** judge the foreman's evidence. Blocked (external wait) until
`spec/zone-dispatch/evidence/cluster/` exists.

- [ ] Run the verify, and record the summary table and the run matrix actually executed.
- **Touches:** `spec/zone-dispatch/TECH.md`, `spec/zone-dispatch/evidence/cluster/` (as delivered).

## Phase P8 — (Deferrable) Zone by name
**Satisfies:** R12 · **Depends on:** P4 · cut to Z3 cleanly if the cycle overruns
**Goal:** users never need the raw zone id.

- [ ] **Red first:** `tests/test_zone_display.py` (`@mark.unit`, `temp_site`):
  - `hs info` normal view shows `zone: gpu (<id>)` and `zone: default` for unzoned tasks.
  - The card shows the zone row.
  - `hs list` plain/table/json/csv and `hs info --json` / `-x zone` show the name.
  - `Task.to_json()` is unchanged.
- [ ] `task.py`: `resolve_zone`, `format_task_fields`, the normal template, the card submission
      region, and the list printers.
- [ ] `data/model.py`: `Zone.names_for_ids`, if not already added in P1.
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
