# Invariant gate & footgun checklist

A curated, explicitly-enumerated subset of the load-bearing invariants in
[`AGENTS.md`](../../AGENTS.md), maintained **in lockstep with it** (`AGENTS.md` is ground truth — if
this drifts, fix it). Two consumers:

- **`hs-plan` (gate):** before research *and* after PLAN/TECH is drafted, walk the sections a change
  touches and confirm the design honors each. Record any bend in PLAN's deviation-justification
  table.
- **`hs-review` (footgun list):** a violation of any invariant in §1–§11 is **auto-CRITICAL** and,
  when it touches the high-blast-radius core, forces a human sign-off gate. A §12 project-convention
  violation grades **HIGH** (`review-rubric.md`'s severity table).

Only invoke the sections relevant to the change — do not manufacture findings against untouched
subsystems.

## High-blast-radius files (any CONFIRMED finding here → mandatory human gate)

`data/model.py` · `server.py` · `client.py` · `core/queue.py` · `core/tls.py` · `core/fsm.py` ·
`core/thread.py` · `core/signal.py` · `cluster/remote.py` · `cluster/ssh.py`

---

## 1. Task lifecycle (`data/model.py`) — highest blast radius

- **No status enum.** State is a function of nullable columns:
  - unscheduled: `schedule_time IS NULL`
  - in-flight/interrupted: `schedule_time` set, `completion_time IS NULL`
  - done (terminal): `completion_time` set
  - cancelled (terminal): `exit_status == CANCEL_STATUS` (`-1`) **and** `completion_time` set
- Every new query must reproduce these predicates exactly.
- Setting `schedule_time` without eventually setting `completion_time` creates a task
  `revert_interrupted()` re-runs on next server start.
- **Task-state transition logic belongs in `Task` classmethods**, not scattered into FSM driver
  files.
- A confirmation writes the client only onto a row with `completion_time` NULL (`Task.confirm_all`);
  a late one leaves a complete row's result attribution as it is.
- **A 0-row UPDATE or DELETE of a `task` row is not proof the row is gone or no longer qualifies.**
  On a TimescaleDB 2.28.1 through 2.31.0 hypertable (the `timescale` provider; upstream issue #10902,
  fixed in 2.32.0), an UPDATE or DELETE that waited on another transaction's row lock can match 0
  rows although the row still qualifies. A task-row write another
  writer can contend checks its own count and, on 0, re-runs only while a fresh read says the row
  still qualifies, at most `CONTENDED_RERUNS` times (DEBUG per extra attempt; at the bound a WARNING
  naming the task and path, and a safe outcome: no crash, no silently stranded task, no write that
  lands without its condition). A first-attempt match is neither re-run nor re-checked. Pattern:
  `Task.confirm_all`/`revert_all`, `Task.revert_staged` (re-check on `completion_time IS NULL`, not
  mere existence), the eviction revert `Task.revert_orphaned` (only while still in flight with the
  evicted client), the dispatch stamp in `Task.next` (only while `schedule_time IS NULL`), the
  retry mark in `Task.next` (only while the row still awaits retry, committed with its retry row;
  at the bound the row stays retryable with no retry row), `Task.cancel_all` (by id; returns the ids
  cancelled), `Task.apply_results` (`ResultOutcome.DEFERRED`), and every `hs update` write:
  `Task.update` (legacy, by id), `Task.update_selected`/`delete_selected` (`--limit`, by id under the
  user's filter, a row no longer matching skipped), `Task.update_matching`/`delete_matching` (no
  `--limit`: one set-based statement, each row's first attempt; on PostgreSQL the UPDATE returns
  the ids it wrote (`RETURNING id`) and one read of the ids still matching, less those, finds the
  rows it missed, while the DELETE takes its row count and that one read alone, with no
  `RETURNING` (a row it deleted no longer matches); each row missed is written by id under the
  filter at most `CONTENDED_RERUNS` more times, the one check after a first-attempt match; none on
  SQLite), all-or-nothing at the bound (rollback, `Task.Contended`, exit 5). The
  conditional writes
  (`revert_staged`, the eviction revert, the dispatch stamp, the retry mark) skip a row gone or no
  longer qualifying, never raise on it; `confirm_all` (strict, the default), `revert_all`,
  `cancel_all` and `Task.update` still raise `StaleDataError` on a gone row, by design. Never
  `bulk_update_mappings` or an ORM flush for such a write.

## 2. `exit_status` overloading

| Value | Meaning |
|-------|---------|
| `0` | success |
| `> 0` | real process exit code |
| `-1 .. -64` | killed by signal N (`-N`); **`-1` = `CANCEL_STATUS`** |
| `-1001` | `TASK_TEMPLATE_ERROR` (never ran) |
| `-1002` | `TASK_RESOURCE_ERROR` (never ran) |

- New "never ran" sentinels go **below `-1000`**. **Never** use `-1..-64` (reserved for signals).
- **Every failure/retry/count-as-failed query MUST filter `exit_status != 0 AND exit_status !=
  CANCEL_STATUS`** — or cancelled tasks get resurrected.

## 3. Retry model

- Retries are **new rows** (`attempt+1`, new row's `previous_id` → old row, old row
  `retried=True` and `next_id` → new row) — never mutate a row in place to retry.
- One retry per failed row is held by the single Scheduler thread (sole `Task.next` caller) and
  `Task.query_retryable`'s `retried IS FALSE` (under `select_failed`), **not** by the schema.
  Unguarded, yielding a second retry with the same `previous_id`: a reset `retried` (`hs update`).
  Retry rows and their conditional marks commit in one transaction, so a stop between them leaves
  neither, and two servers on one database (unsupported) no longer double a retry. They can still
  run a task twice: a server starting while another runs reverts that server's in-flight tasks
  (`Task.revert_interrupted`) and dispatches them again.
  Pre-2.9 SQLite DBs keep the `UNIQUE` constraints (forward-only, no table rebuild): never
  tripped in normal operation, an `IntegrityError` in that case.
- A returned result writes only its result columns (`Task.result_columns`), and only for its
  current assignment: the row's `schedule_time` as dispatched, while `completion_time` is NULL
  (`Task.apply_results`). A stale or duplicate result is logged and dropped, so a late result from
  an evicted client cannot reopen a retried or cancelled row.
- `attempts == max_retries + 1`; this relationship spans `server.py`,
  `Task.query_retryable`/`select_failed`/`count_retryable`, and `Task.increment_group` — change
  them together. Every one of those queries keeps the `CANCEL_STATUS` filter (§2).
- Group-gating can stall **by design**: `increment_group` won't advance while the group has
  incomplete tasks or failures still awaiting retry, so a permanently-failing task halts the group
  (and, in non-`forever` mode, the server); don't "fix" it without understanding it. Once the
  group clears, the next group is the smallest one with incomplete tasks, which may be lower when
  tasks arrive late. File input to `hs cluster`/`hs server` is submitted in full (the whole file)
  before the scheduler starts, so its groups run in order. Stream input is best effort: a late
  lower group runs after the current one, with a warning.
- **A task group must be an integer** (`ensure_group`: an `int`, not a `bool`, in PostgreSQL's
  32-bit range) wherever input sets it: an inline or tag-only `group:` tag, `-t group:…` (exit 2,
  before any input is read), a `--from-json` `group` key. Anything else raises `TaskGroupError`
  (exit 5) naming the value and its line or record. `Task.new(group=…)` stays unchecked (queue mode
  passes None; a retry copies a legacy text group). Read-in-full input (a named file to `hs submit`,
  `hs cluster` or `hs server`, `--from-json`, arguments, and `hs submit` stdin with a database) is
  scanned whole (`scan_task_lines`) before any write, with or without a database; a stream's
  `Loader` (`refuse_lines`) refuses only the offending line with an ERROR and reads on, the run's
  exit status untouched. No refused value may reach `Task.add_all`: a NULL or out-of-range group
  kills the committer and hangs the run.
- **The reached group** (`Task.reached_group()`) is the highest group the current pass has
  scheduled, read from task columns: U = the earliest `submit_time` of an incomplete row (none: no
  reached group); a row counts when not cancelled (`CANCEL_STATUS`), integer-typed on SQLite, and
  either scheduled and incomplete at or after U, or a retry row submitted since U. It may
  under-estimate (the late-group warning covers it) and must never over-estimate from the columns:
  an over-estimate refuses users. With a database, `hs submit` refuses a task below it, all or
  nothing (exit 2), and without `-g` defaults to it when the most recently scheduled group is below
  it. `hs cluster FILE`/`hs server FILE` refuse the same way with `in_flight=False` (their scheduler
  reverts in-flight rows at start), read before `ServerThread` exists; `hs cluster` leaves out the
  re-submission gate's skip set, which is why the gate is split into `decide_source_gate` (reads)
  and `GateDecision.commit` (writes the Source row), with every check between them. Streams into
  `hs cluster`/`hs server` are never refused for it.
- **A non-`forever` scheduler stops for having run out of work only once its submitter has ended
  and every task is terminal** (`Scheduler.load_bundle`). It also stops, input open or not, when a
  group fails permanently while a task waits in another group (the halt above), and on
  SIGUSR1/SIGUSR2; each takes effect on its next pass, within `server.poll`, not during a
  back-off, except that the out-of-work stop also takes effect when the result that finishes the run
  lands: a pass that loads nothing with the input ended arms before its counts, and once enough
  results have landed the receiver reads the same counts and wakes the scheduler
  (`Scheduler.results_landed`), whose next pass stops. `submission_complete()` is read **before**
  the counts (a submitter ends only after its last commit); with every task done and the input still
  open it backs off without `increment_group`/`report_held`. A failed group with no task waiting in
  another group is that same case (a result can land between the count and `increment_group`): it
  backs off while the input is open and stops without the halt once the input has ended. So neither
  a pause nor a failed task with no retry left ends a stream run early. `ServerThread` wakes the
  scheduler once the submitter has ended.

## 4. Server modes — guard before assuming DB behavior

- **`in_memory`**: `Scheduler` is `None`; the server skips all DB writeback, retries, revert, and
  group-gating. Guard every server-side DB write with `if not self.in_memory:`.
- **`no_confirm`**: disables orphan recovery (client id never stamped) — a dead client's tasks are
  **not** rescheduled.

## 5. Concurrency (FSM + Thread) — untested, edit with care

- `StateMachine` subclasses need a `HALT` state; every non-HALT state returns the next state and has
  an `actions` entry.
- `Thread.stop()` overrides **must** call `self.machine.halt()` before `super().stop()` (the base
  `__should_halt` flag does not stop an FSM).
- `fsm.py` does **not** poll signals — polling is manual via `check_signal()` in scheduler action
  methods. A new state observes signals only if you add the poll.
- Every blocking queue op inside a state uses a **finite timeout** and **re-enters** its state
  (that timeout is the shutdown-latency bound).
- Shut down with `stop(wait=True)` so `join()` re-raises captured thread exceptions.

## 6. Shutdown / sentinel ordering — load-bearing

- Preserve exact ordering. Client: scheduler → executors → collector → heartbeat. Server: submitter
  → scheduler → `dispatcher.finish()` (in place of the per-client sentinels on `scheduled`: requests
  drain their zone's staged tasks, then get `shutdown`) → heartbeat join → staged revert
  (`ServerThread.revert_staged`: rows still staged, never delivered, go back to unscheduled) → late
  hand-back revert (`ServerThread.revert_handed_back`: what relays handed back once scheduling had
  ended goes back to unscheduled, each only while still that relay's) → sentinels to receiver/confirm. Submit flush: `loader.join()` **then** `queue.put(None)`.
- **A staged revert never touches a completed or cancelled row.** Both staged reverts (at shutdown,
  and `Scheduler.revert_unstaged` when a zone's last client leaves) use `Task.revert_staged`: a
  conditional update per row on `completion_time IS NULL`, so a row cancelled while staged stays
  cancelled and a deleted one is skipped; under contention it is re-run only while a fresh read finds
  the row still without a `completion_time`. Never revert staged ids with the unconditional `Task.revert_all`.
- Stream sentinel is `make_sentinel()` (`serialize(None)`).
- **Remote-queue payloads** go through `serialize_tasks`/`deserialize_tasks` and `heartbeat.pack`
  (JSON) — **never** put a live object or a literal `None` on a remote queue.
- **Payload fields decode by declared column type** (`Entity.from_json`): only a `datetime` column in
  `columns` is read as a time; every other value stays as sent (date-like text stays text). Declare
  a new `DateTime` column `datetime` in `columns`, or it arrives as text.

## 7. Resource accounting (`core/resource.py`)

- Process-global mutable state under `executor_lock`; assumes **one `ClientThread` per process**.
- `acquire`/`release` must be balanced. Tasks that early-exit (template/resource error) never
  acquire → must never release.

## 8. Signals

- `register_handlers()` handles **`SIGUSR1`, `SIGUSR2`, `SIGHUP` only** (no-op on Windows).
- `SIGINT`/`SIGTERM` are **deliberately not captured** (Ctrl-C → `KeyboardInterrupt` via cmdkit).
- `signal.RECEIVED` is a process-global **sticky** flag; USR1/USR2 are never reset. **Do not add
  stray `reset_signal()` calls.**
- **Relay carve-out (sanctioned; `hs relay` only, process-lifetime handlers, one relay per process).**
  SIGUSR1/SIGUSR2 drain the relay through its own handler and **never set `RECEIVED`**. SIGTERM is
  caught: the relay ends its parts and site clients and removes the site config copy, then SIGTERM is
  **re-delivered with the default action** (the process still dies by it). The handler resets SIGTERM
  first, so **a second SIGTERM ends the process at once** (leaving the copy, as SIGKILL does). No other
  command captures SIGTERM; nothing here goes through `check_signal()`.

## 9. Queue transport & security

- **TLS is ON by default**; disable only via `--no-tls` (internally `tls=None`). There is **no
  `cipher.py`**.
- **Auth is ALWAYS required.** Refuse the placeholder key `DEFAULT_AUTH = '<not-secure>'`; enforce
  `AUTH_MINIMUM_LENGTH` (≥16) and `AUTH_ALLOWED_CHARS`. Never weaken; never drop the authkey to
  "disable TLS" (pass `tls=None`).
- Multiprocessing RPC framing is **pickle** (an RCE surface); HyperShell **payloads are JSON**. Both
  facts matter — don't conflate them.
- **No mTLS** (`verify_mode=CERT_NONE`); keep the post-handshake fingerprint check.
- `'<auto>'` TLS materials only work single-host / shared FS; cert/key are **not** forwarded to
  launched clients (only `--no-tls` is propagated).

## 10. Configuration

- `config` is built **once at import** — an effectively immutable per-process singleton. `hs config
  set` writes disk but does **not** mutate the live singleton (needs restart).
- Sentinels: `'<auto>'` (materialize), `'<none>'` (unset). The `X or None` idiom means **`0` =
  unlimited/auto** for many numeric knobs.
- `data/core.py` creates engine + `Session` at import and can `sys.exit(3)` on bad config; SQLite
  uses `check_same_thread=False`.
- The `HYPERSHELL_*` layer is read through `core/config_schema.py` (`KEYS`, `TABLES`): a name is
  matched against the declared keys and tables, never split at every underscore, and its value read
  by the key's kind (text as written; typed keys convert or exit 3 at import naming the variable;
  durations and sizes as given, for their readers). A new documented key gets its kind in `KEYS` and
  its reference entry in the same commit (`tests/test_config_schema.py`). Key variables and relay
  variables are recognised by the matcher wherever HyperShell handles them by name:
  `is_key_variable()` (every form of `server.auth` and `relay.upstream.auth`) drives a task's
  environment, `--ssh --env` exports and every launched client's environment;
  `is_relay_variable()` drives the relay's site fence. An empty variable for a key of
  `EMPTY_UNSET` is not given.
- `hs config set` reads its value by the same rule (`match_path`, the dotted path matched as
  written, then `read_value`): a value that does not convert exits 2 before `update()`, and nothing
  is written; `update()` reads and writes the file as UTF-8 whatever the locale. `server.eager` and
  `server.evict` are read at each command's start check
  (`configured_eager`, `check_evict`), never at import.
- **Relay process:** `core/config.py` replaces the whole `[database]` section with the relay's private
  SQLite file at import (`RELAY_PROCESS`), so one relay runs per process and a late start re-executes;
  never rebind after import. Relay-only tables live on their own `MetaData`, never `Entity`.
  `DISTRIBUTED_ROLES` (commands with their own log, `relay` among them) is in `core/logging.py`.

## 11. Cluster orchestration

- `cluster/launch.py` `client_launch()` is the one client-invocation builder for `remote`/`ssh`/
  `autoscale` (`local` runs clients in-process): a change to launched-client arguments goes there.
  The key is never in a launched argv — environment (`HYPERSHELL_SERVER_AUTH`) for launcher modes,
  ssh's stdin (forced `-T`, bare `-n` dropped) for `--ssh`, whose `--env` exports are built from
  the variables by name (`ssh_env_exports()`: every key variable dropped first by
  `is_key_variable()`, every value `shlex.quote`d), never by filtering words — and only options a
  2.9.0a4 client accepts may be emitted (contract test); the one sanctioned bend is a fractional
  `-R` rate (`plain_rate`: a whole rate is an integer word, a fraction a plain decimal that needs a
  client of 2.9.0 or later). The Open MPI forwarding
  (`OMPI_MCA_mca_base_env_list`, or `-x` beside a
  user's own `-x`/`--x`) is for Open MPI launchers only (`is_mpi_launcher()`: the first word, by
  base name; never `srun mpirun`). The client strips the key variable and its Open MPI entry from
  every task's environment and from every `{% %}` template command's; a task gets the key only
  explicitly, through `config.export` (a key the operator holds) or a `{= =}` lambda (client-side
  Python reading the client's own `os.environ` on purpose).
- In JSON mode, clients must be sent `DEFAULT_TEMPLATE` (never the user template) to avoid double
  expansion.
- One rule gives the address every launch tells its clients (`cluster/launch.py::advertised_host`,
  at each launch site): `--advertise`/`server.advertise` for remote `hs cluster` modes, and
  `hs relay --advertise`/`relay.advertise` for a relay's site clients (never `server.advertise`),
  else the bind address, else `HOSTNAME` for a wildcard bind. Constructors take `advertise=` and
  never read configuration; only `ClusterApp`/`RelayApp` read the keys. A relay's site cluster
  computes the address once (`AutoScalingCluster.advertised`) and every zone's
  `relay/site.py::zone_launch` dials it, so no zone can diverge. It must be routable from clients. The
  autoscaler never terminates clients (they self-exit on idle) and ignores
  `no_confirm`/`in_memory`/`forever`/`restart`.
- A cluster whose launched clients are all gone with work left exits 5 (`ClusterLaunchError`):
  launcher, `--mpi` and `--ssh` through `cluster/watch.py` (all exited, at least one non-zero, and
  `work_remains`; all-zero exits keep waiting); autoscaling after `autoscale.failed_launches`
  (default 3, 0 off) failed launches in a row with no client of this server registered
  (`INSTANCE`-stamped `Client` rows). `AutoScaler`/`AutoScalerThread` default the limit to 0 and
  `site_scaler`/`zone_fleet` never set it, so relay fleets and `hs server --autoscaling` never stop
  for it.
- Autoscaled launches are per zone (`LaunchActuator`, `client_launch(..., expose_zone=True)`):
  `{zone}` in launcher words only (never `-t`), `HYPERSHELL_ZONE` in the launch env and never in
  the Open MPI forward list, no exposure over SSH, and no `--zone` for the own default zone. The
  relay's site cluster uses `discover_zones=False`, and relay launches expose their zone in every
  mode (`relay/site.py::zone_launch`, `expose_zone=True`).
- The autoscaler reads every zone from the scheduler's group (`Scheduler.active_group`; before
  it is known, the start rule `Task.starting_group`), never from `Task.current_group()` alone: the
  most recently scheduled group differs after a restart with a late lower group and at a group
  change. Other zones, and an own zone with no client launched or registered, are read in the
  group the scheduler runs next (`Task.increment_group`, read only).
- A server autoscaling with relay sites (`hs server --autoscaling`, `cluster/topology.py`) launches
  nothing; it publishes. Its publisher reads the zones as above (`demand.scheduler_group`, pinned
  equal to `AutoScaler.current_group` by a test), plans per site and zone within the bounds each
  relay identity advertises on its heartbeat, and publishes a versioned topology on the dispatcher
  (`Dispatcher.topology`): a new version only when its entries change, and the count of checks made
  (`checks`) at every check. It terminates nothing; it is refused with `--no-db` and
  `--no-confirm`, since site failover rests on orphan recovery. A duplicated site is held relay-wide
  (the relay instance that registered last holds every zone it serves, planned on its own clients
  alone; older ones get zero and their clients no longer count against the site), falling back per
  zone for beats with no instance key. A relay whose beats say `retiring` never holds a site or
  causes another to be retired, whenever it registered.
- The relay's fleets reconcile a published topology and never terminate: `relay/site.py::SiteScaler`
  launches `target - max(basis, live)` each time it learns a new basis, the count the server's plan
  rests on (a new version's `seen`; or, while its running clients `live` stay below `target` under an
  unchanged version, `live` once the server has finished two checks past a topology read that
  followed a beat reporting it). Shortfall is measured against running clients, never cumulative
  launches; nothing launches beyond the target in hand or on the relay's own period alone. With no
  topology it is the fixed fleet.
- `SSHCluster` shells out to the plain `ssh` binary (not paramiko; paramiko is only in
  `core/remote.py` for SFTP).
- The relay's site fleets are launched only through `client_launch()`: every zone's by
  `relay/site.py::zone_launch` with the cluster's values (the first zone's set as the
  `AutoScalingCluster`'s launch), and
  every launch passes `fence_site` (no upstream key on a site argv or in a site environment). Nothing
  in a relay dials its own relay-local server in-process.
- `submit.py` queue-mode silently ignores `-t/--tag` (asymmetric vs DB mode).

## 12. Project conventions (same-commit rules)

- **Version is single-sourced from `pyproject.toml`** — never hardcode elsewhere.
- A CLI/feature change updates the affected `docs/_include/*.rst` help snippets **and** the `share/`
  completions **in the same commit**.
- A user-visible change also updates its prose page in the same commit, marked `.. versionadded::`
  / `.. versionchanged::` with the release line. Its user-facing note goes in the PR's
  squash commit body, which `hs-publish` drafts (`AGENTS.md` § Docs). The docs build passes with
  `-W`.
- The wheel ships `share/`; a CI metadata job asserts those paths — keep `share/` and the CI list in
  lockstep.
- Supported Python is **3.11–3.14**; do not reintroduce 3.9/3.10 shims.
- Dependency floors are pinned low for EPEL/RHEL parity — do not raise casually.
- Tests: only `@mark.unit` / `@mark.integration` are real (`--strict-markers`); use pytest's
  `@mark.parametrize`, not the `parameterize` placeholder. Tag every new test.
- Reuse `cmdkit.app.exit_status` constants for return codes — don't invent integer literals.
- Prefer idiomatic/concise Python but verify real equivalence (e.g. `dict.get(k, default)` evaluates
  `default` eagerly).
