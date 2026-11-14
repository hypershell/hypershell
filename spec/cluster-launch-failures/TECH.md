---
slug: cluster-launch-failures
title: A cluster whose clients all fail says so and exits; one advertised address
  for every launch mode
kind: fix
appetite: big
status: in_review
branch: fix/cluster-launch-failures
base: develop
current_phase: done
last_updated: '2026-11-14'
phases:
- id: P1
  name: 'One advertised address: advertised_host at every launch site, --advertise
    and server.advertise, their docs'
  status: done
  satisfies:
  - R6
  - R7
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_cluster_advertise.py tests/test_cluster_launch.py
    tests/test_cluster.py tests/test_relay_launch.py tests/test_completions.py &&
    .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-failures/drive/launch_drive.sh"
    advertise && .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-failures/drive/launch_drive.sh"
    healthy && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group
    docs sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html
- id: P2
  name: 'Launcher, --mpi and --ssh: watch every launched process, warn at each failure,
    stop and exit when all are gone with work left'
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_cluster_exit.py tests/test_cluster_launch.py
    tests/test_cluster.py tests/test_submit.py tests/test_restart.py && for m in fail
    fail-db zero healthy ssh-fail ssh-partial; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/cluster-launch-failures/drive/launch_drive.sh" $m || exit 1; done
    && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html
- id: P3
  name: 'Autoscaling: autoscale.failed_launches stops a cluster whose launches all
    fail; relay fleets never stop'
  status: done
  satisfies:
  - R2
  - R3
  - R5
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_autoscale_failed_launches.py tests/test_autoscale.py
    tests/test_zone_autoscale.py tests/test_relay_launch.py tests/test_relay_topology.py
    tests/test_topology_server.py && for m in autoscale-fail autoscale-off advertise;
    do .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-failures/drive/launch_drive.sh"
    $m || exit 1; done && for mode in founding fixed; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" $mode || exit 1; done && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
- id: P4
  name: 'Gate: agent docs, every drive, the suite under 3.14 and 3.11, the relay smoke,
    the docs build'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: for m in fail fail-db zero healthy ssh-fail ssh-partial autoscale-fail autoscale-off
    advertise; do .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-failures/drive/launch_drive.sh"
    $m || exit 1; done && sh spec/relay/drive/relay_smoke.sh && uv run pytest && UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311
    uv run --python 3.11 pytest && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run
    --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html
review:
  last_reviewed_commit: 81c4de6314e8dfdde41988aca4d42da81cca5a30
  verdict: changes-requested
  blocked_reason: 'review cycle 1: R1 stop exceeds 10 s in scheduler back-off (F1);
    blocks on idle stdin (F2)'
  cycle: 1
---
# TECH.md — A cluster whose clients all fail says so and exits; one advertised address for every launch mode

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/cluster-launch-failures/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract (R1 as narrowed
  at the GOAL gate: only when at least one launched process ended non-zero or by a signal).
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–04.
- **Drive:** [`drive/launch_drive.sh`](drive/launch_drive.sh) (nine modes; written and dry-run at
  plan time on `develop`'s code, where `zero`, `healthy` and `autoscale-off` pass and the other six
  fail as the defects predict). The builder may amend it; a change to a check is noted in the commit.

## Running the verify commands here

- `export UV_CACHE_DIR=$TMPDIR/uv-cache` first; the `.venv` is Python 3.14 (`uv sync --python 3.14`).
- This host's own name does not resolve inside the sandbox; the drive therefore binds or advertises
  `127.0.0.1`/`localhost` throughout. A client told the host name dies with `HostAddressInfo`.
- The Sphinx build runs with the sandbox off. The 3.11 run uses its own environment
  (`UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311`) so the 3.14 `.venv` is never re-synced; its first run
  installs from PyPI.
- The drive stops every process it starts by PID (its process tree); never stop `uv run` with SIGKILL
  alone (its Python child survives).

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); consult [`invariants.md`](../../.agents/factory/invariants.md)
  §4, §5, §6, §10, §11, §12 for this change.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[fix] Build cluster-launch-failures P<n>: …`, signed, **no
  `Co-Authored-By` trailer**.
- A CLI change updates `docs/_include/cluster_*.rst` and `share/` completions **in the same commit**;
  a user-visible change also updates its prose page with `.. versionadded::`/`.. versionchanged::
  2.9.0`. Man pages are not edited (rebuilt by `hs-release`).
- **Stay out of #103's files:** no edit to `server.py`, `core/dispatch.py`, `data/model.py`,
  `core/reconnect.py`, `relay/__init__.py`, `relay/upstream.py`. Relay edits are `relay/site.py`'s
  address line and its docstrings only. If a phase finds it needs one of those files, stop and
  escalate. *Exception (review-cycle-1 rulings, maintainer sign-off):* `server.py`'s scheduler wait
  and halt and `ServerThread.stop`, for F1 and F2 only; `core/dispatch.py` stays untouched.
- No spec ids in source; comments are declarative statements. Tests are `@mark.unit` or
  `@mark.integration`.
- Error wording the drive checks (keep these phrases): R1 `every launched client has exited with
  tasks remaining`, each process with `status N` or `signal N (SIGNAME)`, and `HOST:PORT`; R3
  `N launched clients in a row exited non-zero`; R2 under autoscaling keeps `exited with status N`;
  the local refusal `Cannot use --advertise with a local cluster`.

---

## Phase P1 — One advertised address
**Satisfies:** R6, R7, R8 (address parts) · **Depends on:** —
**Goal:** every launch mode and a relay's site clients are told the address the one rule gives;
`--advertise` and `server.advertise` override it in every remote `hs cluster` mode; documented.

- [x] `cluster/launch.py`: `WILDCARD_BINDS`, `advertised_host(bind, advertise=None)` (exported),
      importing `HOSTNAME`; doctest-style example in the docstring.
- [x] `RemoteCluster`, `AutoScalingCluster`, `SSHCluster`: `advertise: Optional[str] = None`; build
      with `advertised_host(bind[0], advertise)`; keep `self.advertised = (host, port)`; docstrings
      (`bind`, new `advertise`). Constructors never read configuration.
- [x] `relay/site.py::zone_launch`: `(advertised_host(queue.host), queue.port)`; module and
      `site_cluster` docstrings say the clients dial the bind, or the host name for a wildcard bind.
- [x] `core/config.py`: `server.advertise = PARAM_UNSET`.
- [x] `ClusterApp`: `--advertise` (default `None`); refuse it (and an empty value) in `local` mode with
      `ArgumentError('Cannot use --advertise with a local cluster (no launched clients)')`;
      `advertise_host` (option > key > None; the key unset for `None`/`''`/`PARAM_UNSET`); pass
      `advertise=` from `run_launch`, `run_mpi`, `run_ssh`, `run_autoscaling`.
- [x] Help and docs: `APP_USAGE`/`APP_HELP`; `docs/_include/cluster_usage.rst`, `cluster_help.rst`
      (the `-H` rule; `--advertise` with the TLS sentence), `cluster_desc.rst` (the address rule);
      `docs/cli/cluster.rst` (`--advertise` in the `versionadded:: 2.9.0` list; `versionchanged::
      2.9.0` for the rule); `config_param_ref.rst` (`server.advertise` after `.host`, `.bind` text;
      `.. only:: html` + `versionadded`); `docs/security.rst` (Peer Verification Modes sentence);
      `docs/_include/relay_help.rst:42`.
- [x] Completions: `share/bash_completion.d/hs` and `hsx` (keep identical), `share/zsh/site-functions/_hs`.
- [x] Tests: new `tests/test_cluster_advertise.py` (helper table incl. `''`; each mode's `-H` for a
      wildcard bind, a specific bind, the option, the key, option over key; `zone_launch` for a relay
      bound to one address and to `0.0.0.0`; local refusal exit 2 naming the option; the key ignored
      locally). Update the tests that pin the old rule: `tests/test_cluster_launch.py:636` and `:955`
      (loopback bind now advertises loopback) and the stand-in ssh's `-H HOSTNAME` expectation
      (`:134`) where its test binds a specific address. The contract test stays green unchanged.
- **Verify:** see frontmatter (unit tests, `advertise` and `healthy` drives, docs build).
- **Touches:** `src/hypershell/cluster/{launch,remote,ssh,__init__}.py`, `src/hypershell/relay/site.py`,
  `src/hypershell/core/config.py`, `docs/…`, `share/…`, `tests/…`.

## Phase P2 — Launcher, `--mpi` and `--ssh`: every client gone with work left
**Satisfies:** R1, R2, R4, R8 (exit docs) · **Depends on:** P1
**Goal:** a cluster whose launched processes have all exited, at least one failed, with work the
server would otherwise wait on forever, stops its server and exits 5 after one error; each failure
is warned about as it happens; a run whose clients do the work ends as today.

- [x] `submit.py`: `LiveSubmitThread.stop()` halts loader and committer first; one `put_end()` offers
      the sentinel with a timeout, retried only while the committer lives; used by `stop()` and the
      run path (`:881`, `:909`). Unit test: stopping a `ServerThread(in_memory=True)` with more tasks
      than its queue holds returns within a bound.
- [x] New `cluster/watch.py`: `ClusterLaunchError(RuntimeError)`, `exit_text(code)`,
      `work_remains(server)` (barrier with `serialize_tasks([])` on `queue.completed` until taken and
      `receiver.machine.held` empty, skipped if the Receiver has ended; database: scheduler alive and
      `count_remaining`, else `count_retryable(attempts, group=current_group, eager)`, on two reads
      ~2 s apart; no database: server alive ~3 s after the barrier and the `LiveSubmitThread` alive or
      has submitted a task), `watch_launched(server, launched, advertised, interval=1.0)`.
- [x] `RemoteCluster.run_with_exceptions` and `SSHCluster.run_with_exceptions` use
      `watch_launched`; both `stop()`s skip a server no longer alive.
- [x] `ClusterApp`: confirm `ClusterLaunchError` maps to one CRITICAL line and exit 5 through the
      shared mapping (no new mapping entry unless needed).
- [x] Docs: `cluster_desc.rst` and `docs/cli/cluster.rst` (`versionchanged:: 2.9.0`): a cluster whose
      launched clients are all gone with work remaining exits non-zero, naming them; a launcher that
      exits 0 leaves the cluster waiting.
- [x] Tests: new `tests/test_cluster_exit.py` — `exit_text`; `watch_launched` with `sh -c 'exit N'`
      launches and a real temp-site server (`--no-db` and database): all fail → server stopped, one
      error naming every process, status and address, raised ≤ 10 s after the last exit (timed); all
      zero → still waiting at a bound; one failing while another runs → its warning before the other
      exits; the server ending on its own → no error, warnings only; a signal-killed process named
      `signal 9 (SIGKILL)`; database `forever`: nothing pending → waits, then a task submitted →
      fires. Update `test_launcher_mode_without_environment_names_the_cause` and
      `test_ssh_mode_without_key_names_the_cause` to expect exit 5 (no `until=`).
- **Verify:** see frontmatter (unit tests, six drives, docs build). If the `--no-db` exit misses
  10 s, trim the settle (≥ 2 s) or the submitter's stop wait and note it.
- **Built (amendments):** the `--no-db` R1 path measured 7.9 s from the launcher's exit to the
  cluster's exit (1 s poll, 3 s settle, ~4 s stop), so neither the settle nor the stop wait was
  trimmed. `QueueCommitter.halt()` now also ends a final commit still waiting for room (a halted
  committer left that loop only once a client drained the server's queue, so `stop` hung with
  `-b` above 1 and a partial last bundle); its uncommitted tasks are named in one warning.
- **Known gap (plan-gate ruling 3, accepted):** without a database, a client that returned every
  result but exited non-zero in the moment before its FINISHED heartbeat makes R1 fire where today
  the run would end 0 after eviction; the exact fix is a result counter in the Receiver
  (`server.py`), deferred. Stated in `cluster/watch.py::work_remains`.
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/cluster/{watch,remote,ssh}.py`, `docs/…`,
  `tests/…`.

### Review-cycle-1 remediation (reopened; rulings in `PLAN.md`)

- [x] **F1:** `Scheduler.halt` flags `halting` and wakes the dispatcher's `need` condition under its
      lock; `Scheduler.wait_need` (both waits in `load_bundle`) waits on that condition only while not
      halting and the generation is unchanged, so a halt is seen before the wait or ends it at once
      (`server.py`). `SchedulerThread.stop` reaches it through `machine.halt()`. The shutdown order
      (§6) is unchanged: the staged and handed-back reverts still run in `wait_heartbeat`, after the
      scheduler has stopped. `core/dispatch.py` is not touched (the wait reads its public `need` and
      `generation`).
- [x] **F2:** `ServerThread.stop(leave_input=False)` passes `leave_input` to the submitter;
      `SubmitThread.stop` and `LiveSubmitThread.stop` then call `LoaderThread.leave()` (halt, no
      join) instead of `stop`, and both `run` paths wait with `LoaderThread.join_unless_left()`, which
      returns once the loader ends or is left. Committed rows stay committed. Only `watch_launched`
      (R1's failure stop) and R3's failure stop (below) pass `leave_input=True`; every other stop and
      a normal run read the input to its end as before (`submit.py`, `server.py`, `cluster/watch.py`).
- [x] Tests (`tests/test_cluster_exit.py`): unit — a halt ends the scheduler's 30 s wait (during and
      before it); a left loader is not waited on and enqueues nothing more; a loader not left is
      waited on to the end of its input; the stand-in server records `leave_input`. Integration
      (database, default `server.poll`) — one task with a launcher that fails at 17 s; a client killed
      at 16 s holding both its tasks; `--forever` with a task added at 17 s; each exits with
      `ClusterLaunchError` within 10 s of the exit or the submit. CLI — `hs cluster --launcher false`
      reading one line on standard input from a producer that stays open, with and without `--no-db`:
      exit 5 within 15 s of the start, no fatal error at interpreter exit.
- **Red then green** (`git archive 81c4de6 src`, run with `PYTHONPATH=<base>/src`): late launcher
  15.0 s after the exit (head 2.9 s); killed client 16.4 s (head 2.4 s); late `--forever` task 15.1 s
  after the submit (head 3.0 s); open input, `--no-db` and database, still running at 30 s (head:
  exit 5 after 6.5 s and 4.4 s).
- [x] **R3's stop, the same way (foreman ruling after `fcdc985`):** `AutoScalingCluster.run_with_exceptions`
      stops the server with `leave_input=True` when the autoscaler is exhausted on failed launches
      (`cluster/remote.py`); every other autoscaling stop reads its input as before. Test
      (`tests/test_autoscale_failed_launches.py`): `hs cluster --autoscaling fixed -P 2s --launcher
      false` with a database, one line on standard input from a producer that stays open, exits 5
      within 15 s naming the three launches, with the committed task kept (`hs list --count` → 1).
      Red on `fcdc985` (its src exported): still running at 30 s, its log ending at "Stopping
      (loader)"; head: exit 5 after 8.5 s, one row kept. Re-verified: the P3 verify (its six test
      files 271 passed; `autoscale-fail` 5 after 8 s, `autoscale-off` still running at 30 s,
      `advertise` ok; zone drives `founding` and `fixed` PASS; Sphinx `-W` clean), and
      `test_cluster_exit.py` with `test_autoscale_failed_launches.py`: 59 passed on 3.14 and on 3.11.

## Phase P3 — Autoscaling's failed-launch limit; relay fleets never stop
**Satisfies:** R2, R3, R5, R8 (key docs) · **Depends on:** P2
**Goal:** `hs cluster --autoscaling` stops after `autoscale.failed_launches` launched clients in a row
exit non-zero while no client is or was registered; 0 keeps today's loop; relay fleets and
`hs server --autoscaling` unchanged.

- [x] `core/config.py`: `autoscale.failed_launches = 3`. `cluster/remote.py`:
      `DEFAULT_AUTOSCALE_FAILED_LAUNCHES`; `configured_failed_launches()` (environment path
      `autoscale.failed.launches` when `config.which` says `env`, guarding `KeyError`; else the key;
      bool, non-integer or negative → `ConfigurationError` naming its source).
- [x] `client_registrations()` over `Client.query()` (`server_id == INSTANCE`: all rows, and those
      not disconnected).
- [x] `AutoScaler`/`AutoScalerThread`: `failed_launches=0`, `registrations=None`; `clean()` returns
      the exited processes; the counter in `check()` with `seen`, the run of `(pid, zone, status)`,
      `exhausted` and `FINAL`; nothing read when the limit is 0.
- [x] `AutoScalingCluster`: `failed_launches=DEFAULT_AUTOSCALE_FAILED_LAUNCHES`,
      `registrations=client_registrations`; `run_with_exceptions` stops the server and raises
      `ClusterLaunchError` when the autoscaler is exhausted; `stop()` skips a server no longer alive.
- [x] `LaunchActuator.clean`: the warning names pid and zone, keeping `exited with status N`.
- [x] `ClusterApp`: validate and pass `configured_failed_launches()` in autoscaling mode.
- [x] Docs: `config_param_ref.rst` `autoscale.failed_launches` after `.sites` (with
      `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES`, default, 0 = off, relay fleets exempt;
      `.. only:: html` + `versionadded:: 2.9.0`); the autoscaling prose (`cluster_desc.rst`/
      `docs/cli/cluster.rst`): the cluster exits non-zero after that many failed launches.
- [x] Tests: new `tests/test_autoscale_failed_launches.py` (three failures stop, no fourth launch;
      fail, fail, 0, fail, fail → no stop; `ever` rising resets; `now > 0` never stops; 0 never stops
      and never reads registrations; `site_cluster(...).autoscaler.machine.failed_launches == 0`,
      `zone_fleet(...).machine.failed_launches == 0`, a fixed `SiteScaler` on a failing launch over
      ten checks never returns `FINAL`; `AutoScalingCluster` default 3; the reader's env value and
      refusals; on a temp site `AutoScalingCluster(launcher='false', period=1, bind=loopback)` raises
      naming 3, the statuses and the address, server not alive). Extend `run_checks`' fakes to treat
      `FINAL` as an end.
- **Verify:** see frontmatter (unit tests, `autoscale-fail`/`autoscale-off`/`advertise` drives, the
  zone-autoscale drive's `founding` and `fixed` modes, docs build).
- **Built (amendments):** `AutoScaler.clean()` returns each exited process with its zone
  (`(Popen, ZoneRef)`, launch order), so the run can name the zone; `LaunchActuator` keeps zone names
  (`names`) and gains `zone_of(proc)`. `AutoScalingCluster.stop()` also skips halting an autoscaler
  that ended exhausted (no repeated "Stopping" line after the error). `cluster_help.rst`'s
  `--autoscaling` entry gained one sentence on the limit. Measured: the `autoscale-fail` drive exits
  5 after 11 s (`-P 2s`); the cluster-level test (`period=1`) within its 25 s bound.
- **Red on `develop` (`6da633d`):** all 26 tests of `tests/test_autoscale_failed_launches.py` fail
  there (run with `develop`'s `src` first on `PYTHONPATH`); behaviourally, the cluster with a
  failing launcher was still running at the 25 s bound with no error (`elapsed 28.7 s`), the warning
  named a list index, not the pid, and a bad `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES` was not refused.
- **Touches:** `src/hypershell/core/config.py`, `src/hypershell/cluster/{remote,launch,__init__}.py`,
  `docs/…`, `tests/…`.

## Phase P4 — Gate
**Satisfies:** R1–R8 · **Depends on:** P3
**Goal:** the whole contract proven together; agent docs state the new rules.

- [x] `AGENTS.md:415-416`, `:574` and `.agents/factory/invariants.md:185`: the advertised-address
      rule (`cluster/launch.py::advertised_host`; `--advertise`/`server.advertise`; must be routable
      from clients; the relay without override); one line each on R1 (every launched client gone with
      work left → exit 5, `cluster/watch.py`) and R3 (`autoscale.failed_launches`, off for relay fleets).
- [x] Re-read the GOAL's evidence plan against the drive and tests; record any gap.
- [x] If #103 has landed on `develop`, rebase first and run the gate after the rebase.
- **Verify:** see frontmatter (every drive mode, the relay smoke, the suite under 3.14 and 3.11, docs
  build).
- **Built (notes):** the branch was rebased onto `origin/develop` `6da633d` (relay hardening #103 and
  roadmap commits) before the gate, with no conflicts (P1–P3 touched none of #103's hunks; this
  phase's agent-doc edits were made after the rebase); every commit re-signed. Against #103:
  `cluster/watch.py`'s reads (`receiver.machine.held`, `scheduler.machine.current_group/attempts/
  eager`, `server.submitter`) are unchanged by it (`ServerThread.stop` gains `revert_handed_back`
  after `revert_staged`; `HeartMonitor` ages clients by receive time; neither is read here), and
  `Client` rows are still stamped with `INSTANCE` at registration and reconnect. Relay site fleets
  are untouched by its puller changes (`site_scaler`/`zone_fleet` unchanged; relay smoke passes).
- **Gate results (rebased head):** nine drive modes pass (`fail` 5 after 9 s, `fail-db` 5 after 6 s,
  `ssh-fail` 5 after 7 s, `autoscale-fail` 5 after 10 s; `zero`, `autoscale-off` still running at their
  bounds; `healthy`, `ssh-partial`, `advertise` as specified); relay smoke PASS (41 checks); suite
  3.14: 2739 passed, 109 skipped; 3.11: 2736 passed, 112 skipped; Sphinx `-W` build clean.
- **Evidence-plan re-read:** every unit and drive bullet is covered (`test_cluster_advertise.py`,
  `test_cluster_exit.py`, `test_autoscale_failed_launches.py`, the nine drive modes). One gap, as
  ruled at the plan gate (5): the man pages are not regenerated in this cycle; they come from the
  edited snippets and are rebuilt by `hs-release`.
- **Touches:** `AGENTS.md`, `.agents/factory/invariants.md`.
- **Gate re-run after the review-cycle-1 remediation (F1, F2):** nine drive modes pass, one at a
  time (`fail` 5 after 9 s, `fail-db` 5 after 5 s, `ssh-fail` 5 after 4 s, `autoscale-fail` 5 after
  9 s; `zero`, `autoscale-off` still running at their bounds; `healthy`, `ssh-partial`, `advertise`
  as specified); relay smoke PASS (41 checks); suite 3.14: 2747 passed, 109 skipped; 3.11, the
  touched area (`test_cluster_exit`, `test_submit`, `test_cluster`, `test_cluster_launch`,
  `test_restart`, `test_server_fsm`): 289 passed, 1 skipped; Sphinx `-W` build clean (no docs changed).

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
