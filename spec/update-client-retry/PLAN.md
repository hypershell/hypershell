# PLAN — `hs update --delete` refuses changes it would ignore, `hs client` reads its own bundle keys, and a task that never ran is not retried

> **Status:** Draft for review · **Last updated:** 2026-11-22
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (locked at the
> GOAL gate); the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/), consolidated in [`research/00-digest.md`](research/00-digest.md). Every
> design element traces to a GOAL R-ID. Planned against `eb2a4487`, the signed merge of
> `origin/develop` `1f688c7c` (#117) into this branch; its `src`, `tests` and `docs` are `1f688c7c`'s.

## 1. Summary

Three independent halves, landed in the order the GOAL gate ruled. **R1–R2:** `hs update` refuses
`--delete` with a change, and the single-task form with an action option, at the top of `run()`,
before the database is opened. **R3–R4:** `hs client` reads `client.bundlesize` (all three forms,
advertised only when set above the built-in default) and `client.bundlewait`; `[submit]` no longer
reaches it. **R5–R8:** `-1001`/`-1002` join one shared exclusion in the retry selection (and so the
autoscaler's demand), the group gate counts them as failed on any attempt, a retry is built from its
task's stored declaration columns (cores, memory, walltime included), and every Python API entry
point defaults `max_retries` from `server.attempts` through one call-time helper. **R9** lands with
each half. Every test is written first and proved red against `1f688c7c`.

## 2. Design

### 2.1 R1, R2 — `hs update` refusals (`src/hypershell/task.py`)

`TaskUpdateApp.run` today opens the database first (`ensuredb()`, which creates a SQLite file and
schema), then routes three positionals led by a UUID to `update_legacy`. New order:

1. `legacy = len(self.update_args) == 3 and UUID_PATTERN.match(self.update_args[0])`.
2. **R2** — if `legacy` and any of `cancel_mode`, `revert_mode`, `delete_mode`, `remove_tag`:
   `raise ArgumentError(f'Cannot use {options} with the single-task form ID FIELD VALUE')`, the options
   named in the fixed order `--cancel`, `--revert`, `--delete`, `--remove-tag`, joined by `, `.
3. **R1** — elif `delete_mode` and (`update_args` or `remove_tag`):
   `raise ArgumentError(f'Cannot use --delete with changes (given: {words})')`, `words` the positionals
   as typed, then `--remove-tag` and its names. In the filtered form every positional is a change (or
   an error `process_arguments` would refuse anyway, also exit 2), so this is exactly R1.
4. `ensuredb()`, then route as today.

`cmdkit`'s `Application.main` maps `ArgumentError` to `CRITICAL [hypershell] …` and exit 2
(`exit_status.bad_argument`), as the parser's own `--cancel`/`--revert` conflict does. Nothing is
counted, logged ("Searching database"), prompted, opened or written; `-f` only ever skipped the
prompt, so it changes neither path. `--delete` with filters only, and the single-task form without
those options, take the paths they take today. The in-process `hs_update` test fixture
(`tests/test_contended_recovery.py`) monkeypatches `ensuredb` and is unaffected. `UPDATE_HELP`
(`task.py`) gains the two sentences R9 puts on the page. ([03](research/03-update-refusals.md))

### 2.2 R3, R4 — `hs client`'s bundle keys (`src/hypershell/client.py`)

- **`advertised_size(value, base=config)`**: an explicit `value` wins; otherwise find the first form
  of `client.bundlesize` (`bundlesize`, `bundlesize_env`, `bundlesize_eval`) whose `base.which(...)`
  source is neither missing nor `'default'`, catching the `KeyError` cmdkit raises for a form another
  layer displaced (the crash brief 04 found live with `[submit] bundlesize_env`). None found → `None`
  (advertise nothing, as today with nothing set). Found → `base.client.bundlesize` (the attribute
  resolves `_env`/`_eval`); an `int` is returned as is, text goes through
  `config_schema.read_value(Kind.INT, …)`, and a `ValueError` (or an `_env` naming an unset variable,
  `None`) raises `ConfigurationError(f'client.bundlesize: expected a whole number ({blame(...)})')`
  (exit 3, `bad_config`), naming the key and the form's source, never the value (an `_eval` value is
  command output; #117's ruling for `server.eager`). It is still called once, in `ClientApp.run`, so
  an `_eval` runs only for `hs client`. `ClientThread` already uses the advertised size for both the
  request and the returned bundles; no change there. No range check: `-b 0` and its kin are
  `bounded-inputs`' (rubric, "Absurd input is not a finding").
- **`ClientApp.bundlewait = config.client.bundlewait`** in place of `config.submit.bundlewait`
  (class body, argparse's `parse_time` on a text default, a number as given): the mechanism
  `submit.bundlewait` uses today, in the same duration forms.
- `submit.bundlesize`/`submit.bundlewait` are then read by no `hs client` path. No fallback, no
  warning (GOAL gate). `hs cluster` (`-b` from `server.bundlesize`, `-w` from `submit.bundlewait`),
  `hs relay` and `cluster/launch.py::client_options` (always `-b` and `-w`) are unchanged, so every
  launched client keeps its explicit values. ([04](research/04-client-bundle-keys.md))

### 2.3 R5, R6 — never-ran statuses (`data/model.py`, `client.py`)

- **Constants** move to `data/model.py` beside `CANCEL_STATUS`, in `__all__`:
  `TASK_TEMPLATE_ERROR = -1001`, `TASK_RESOURCE_ERROR = -1002`,
  `NO_RETRY_STATUSES: Final[Tuple[int, ...]] = (TASK_TEMPLATE_ERROR, TASK_RESOURCE_ERROR)` with a
  docstring ("never ran on that client and would fail the same way: never retried"). `client.py`
  imports the two names from `hypershell.data.model` (the import direction; still importable from
  `hypershell.client`) and keeps its comment.
- **Retry side (R5)**: `Task.__awaiting_retry`'s failure tuple gains
  `table.c.exit_status.not_in(NO_RETRY_STATUSES)`. Everything that decides "awaits retry" reads that
  tuple: `query_retryable`, `select_failed`, `count_retryable`, `count_retryable_by_zone`, the
  conditional retry mark (`__row_statements('retryable')`), `starting_group`, and through them
  `Scheduler` (`server.py`), the autoscaler's demand (`cluster/demand.py`), `cluster/remote.py` and
  `cluster/watch.py`'s `work_remains`. A never-ran row is therefore never selected, never marked,
  never counted as demand or as work to wait for, and stays its task's last row. The `IN` renders
  identically on SQLite and PostgreSQL; the `@cache`d statements bake it as literals.
- **Gate side (R6)**: `Task.increment_group`'s own failed-count statement keeps its filters and
  replaces `attempt >= attempts` with `or_(attempt >= attempts, exit_status.in_(NO_RETRY_STATUSES))`,
  adding `func.count(case((never_ran, id)))` to the same one statement (count and example still
  cannot disagree). Reason text: exhausted only, unchanged; never-ran only,
  `at least M tasks not retried because they did not run, example: ID`; both,
  `at least N tasks exceeding allowed retries and M not retried because they did not run, example: ID`.
  The order incomplete → retryable → failed is unchanged, so a group holding a never-ran task and a
  failure with retries left waits for those retries, then halts. `Scheduler` logs
  `Failed task group G (…)` as today; with `--forever` it waits, and `hs update --revert` (which nulls
  `exit_status`, `completion_time`, `schedule_time` and leaves `attempt`), `--cancel` or `--delete`
  clears the hold with no new code. With retries off (`attempts == 1`) nothing changes but the
  message.
- **Client line (R5)**: both `TaskExecutor.create_task` errors end `(task did not run)` instead of
  `(cancelled)`. ([01](research/01-terminal-retry-gate.md))

### 2.4 R7 — a retry keeps its declarations (`data/model.py`)

- `Task.retry_columns: Final[Tuple[str, ...]]` beside `result_columns`/`revert_columns`:
  `args`, `tag`, `group`, `fingerprint`, `source`, `zone`, `cores`, `memory`, `timeout`.
- `Task.new_retry(task) -> Task` (classmethod): `Task(id=uuid(), submit_id=INSTANCE,
  submit_host=HOSTNAME, submit_time=now, attempt=task.attempt + 1, retried=False,
  previous_id=task.id, **{name: getattr(task, name) for name in retry_columns})`, `tag` copied as a
  new `dict`. No `Task.new` parsing: the stored command is not re-read for inline tags, the stored tag
  is not re-checked, and no tag is popped into a column. `submit_time` stays fresh (#118's
  `reached_group` counts a retry by it).
- `__schedule_next_failed_tasks`: `retries = {task.id: cls.new_retry(task) for task in failed_tasks}`;
  the mark-then-insert transaction is unchanged.
- **Behaviour note**: a `cores`, `memory` or `timeout` *tag* written by `hs update KEY:VALUE` stays a
  tag on the retry and no longer sets the retry's column (today `Task.new` pops it, and `cores:x`
  stores text in an integer column). The column form (`hs update cores=4`) is what the retry reads.
  This is #118's G1 rule for `group`, applied to the other declarations (§5, sign-off item S2).
- No other place builds a retry: the relay copies upstream rows whole, `--revert` and restarts
  rewrite in place or build nothing. ([02](research/02-retry-declarations-and-118.md))

### 2.5 R8 — one retry default (`server.py`, `cluster/`)

- `server.py`: `configured_retries() -> int` beside `DEFAULT_ATTEMPTS`, returning
  `config.server.attempts - 1` when called; in `__all__`.
- `ServerThread.__init__`, `serve_from`, `serve_file`, `serve_forever`: `max_retries: Optional[int] =
  None`. `ServerThread` resolves `None` through `configured_retries()` first thing, before the
  "Retries disabled when database disabled" warning, which therefore fires only when a retry was
  asked for (explicitly or by configuration).
- `LocalCluster`, `RemoteCluster`, `SSHCluster`: `max_retries: Optional[int] = None`, passed through
  to `ServerThread`. `AutoScalingCluster` and `AutoScalingServer` (`cluster/topology.py`; an entry
  point taking `max_retries` that R8's list leaves out) resolve before their `max_retries + 1`.
  `run_local`, `run_cluster`, `run_ssh` forward `**kwargs` and inherit. The relay's site cluster
  passes `max_retries=0` explicitly and is unchanged.
- `ServerApp.max_retries` and `ClusterApp.max_retries` (class bodies) become `configured_retries()`:
  one expression for every default. `-r`'s help still shows the built-in `DEFAULT_ATTEMPTS - 1`.
- Docstrings: each `max_retries` entry says "Default: `server.attempts` - 1, read when called".
  ([05](research/05-retry-defaults.md))

### 2.6 R9 — docs, help, release note

Docs land in the phase of the behaviour they describe (§3 deviation row 1 is the opposite of #117's
choice). Every changed entry carries `.. versionchanged:: 2.9.0`; docs name top-level commands only.

- **P2 (R1, R2):** `docs/_include/task_update_desc.rst` (the `--delete` paragraph; the single-task
  paragraph), `task_update_help.rst` (the `--delete` entry; "Legacy Mode"), `UPDATE_HELP` in
  `task.py`, one new marker in `docs/cli/task_update.rst`.
- **P3 (R3, R4):** `config_param_ref.rst` `[client].bundlesize` (read by `hs client`; the advertise
  rule moved from `[submit].bundlesize`, with `HYPERSHELL_CLIENT_BUNDLESIZE` and the `_env`/`_eval`
  forms) and `.bundlewait` (read by `hs client`, in the duration forms); `[submit].bundlesize` read by
  `hs submit` only (client paragraph removed); `[submit].bundlewait` by `hs submit`, `hs server` and
  `hs cluster` (digest 13). `client_help.rst` and `ClientApp`'s `HELP`: `client.bundlesize`,
  `HYPERSHELL_CLIENT_BUNDLESIZE`, and `-w`'s key. One new marker in `docs/cli/client.rst`.
  `AGENTS.md`'s "read from `config.submit.*` for CLI defaults" sentence rewritten.
- **P4 (R5–R8):** `[server].attempts` (the never-ran statuses never retried; a retry keeps its
  task's cores, memory and walltime; read by the Python API too), `server_desc.rst`,
  `server_help.rst`/`cluster_help.rst` `-r` and their in-program `HELP`, a marker each in
  `docs/cli/server.rst` and `docs/cli/cluster.rst`; `docs/api/server.rst` and `docs/api/cluster.rst`
  ("Configuration files have no impact…" now excepts `server.attempts`) with a marker each.
  `AGENTS.md` status table and § Retry model, `invariants.md` §2, §3, §10, §11 in lockstep.
- **Release note:** the six user-visible changes of GOAL.md, verbatim in substance, go in the PR's
  squash commit body, which `hs-publish` drafts (`AGENTS.md` § Docs). No `share/` change: option
  names and value lists are unchanged; man pages are rebuilt at release.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `TaskUpdateApp.run`: `--delete` with positionals or `--remove-tag` → `ArgumentError` before `ensuredb()` (§2.1) |
| R2 | `TaskUpdateApp.run`: single-task form with an action option or `--remove-tag` → `ArgumentError` before `ensuredb()` (§2.1) |
| R3 | `advertised_size` reads `client.bundlesize` in all forms above the built-in default, converted, `KeyError` caught (§2.2) |
| R4 | `ClientApp.bundlewait = config.client.bundlewait` (§2.2) |
| R5 | `NO_RETRY_STATUSES` in `__awaiting_retry` (selection, mark, counts, demand); client line (§2.3) |
| R6 | `increment_group` counts never-ran rows as failed on any attempt; halt message names them (§2.3) |
| R7 | `Task.retry_columns` + `Task.new_retry` (§2.4) |
| R8 | `configured_retries()`; `max_retries=None` in every entry point (§2.5) |
| R9 | Docs, help and markers per phase; `AGENTS.md`/`invariants.md` lockstep; release note at publish (§2.6) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(§1, §2, §3, §4, §10, §11, §12 touched; the GOAL is sane against each: R5 keeps the `CANCEL_STATUS`
filter and the retry chain, R6 keeps the gate's design) and again on this design.

- **§1 lifecycle predicates** — no new state; never-ran rows are "done" (`completion_time` set) as
  today. `new_retry` builds an unscheduled row exactly as `Task.new` did (both `schedule_time` and
  `completion_time` NULL). The retry mark's conditional write and its contention handling are
  untouched; it reads the same tuple as the selection, so a row made never-ran between select and
  mark is skipped, never raised on.
- **§2 `exit_status`** — no new sentinel; `-1001`/`-1002` keep their meaning, now with "never
  retried". Every failure/retry query keeps `!= 0 AND != CANCEL_STATUS`; the new term is added beside
  them, never in place of them, and only where `exit_status IS NOT NULL` already holds.
- **§3 retry model** — retries stay new rows linked by `previous_id`/`next_id`; marks and rows still
  commit together. `attempts == max_retries + 1` holds at every site (`ServerThread`,
  `AutoScalingCluster`, `AutoScalingServer`). The relationship's members — `server.py`,
  `query_retryable`/`select_failed`/`count_retryable`, `increment_group` — change together, in P4's
  one commit; the retry side and the gate side read the never-ran set from one constant. The gate
  still stalls by design, now also on a never-ran task.
- **§4 server modes** — `in_memory` still skips retries; its warning fires only when a retry was
  asked for. No new DB write outside a scheduler.
- **§10 configuration** — no new key. `server.attempts` is read when an entry point is built
  (`configured_retries()`); `client.bundlesize` in `ClientApp.run`. Kinds in `config_schema.KEYS` are
  already right (`I`/`D`), so no schema change.
- **§11 cluster orchestration** — `client_launch` is untouched (launched clients keep explicit
  `-b`/`-w`). The constructor sentence is bent (row 2).
- **§12 conventions** — help snippets with their prose pages in the same phase; markers; serial
  tests tagged `unit`/`integration`; `cmdkit` exit constants; no `share/` change (none needed).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Docs land with each code phase, not in one docs phase as #117 did | The GOAL gate ruled "R9 alongside each" phase | One docs phase would leave P2–P4 commits with behaviour and no docs; §12 wants them together |
| §11 "Constructors … never read configuration" bent: `max_retries=None` reads `server.attempts` in `ServerThread`, `AutoScalingCluster`, `AutoScalingServer` | R8 requires every Python entry point to default from the configuration the commands read | Keeping constructors config-free means a default in each `run_*`/`serve_*` wrapper and none in the classes, which R8 lists by name. The sentence already sits in the advertise paragraph and `SSHCluster`/`ServerThread` already read `ssh.args`/`server.queuesize`; §11 is narrowed to `advertise=`/`evict_after=` and §10 lists `server.attempts` as a call-time read |
| `Task.new_retry` replaces `Task.new` for retries (a tag named `cores`/`memory`/`timeout` no longer sets a retry's column) | R7 must copy the three columns; copying them through `Task.new` keeps its tag pop, so a stored tag still overrides the column (and `cores:x` stores text in an integer column) | Passing `cores=`/`memory=`/`timeout=` to `Task.new` and widening #118's `group`-tag pop/restore to four keys gives the same behaviour with more code that #118 and R7 both edit |

## 4. Rabbit holes (resolved)

- **Where the retry filters live, and whether the gate can share them** → one cached tuple feeds
  every retry read; the gate has its own literal and must stay separate, or it never sees a
  never-ran row ([01](research/01-terminal-retry-gate.md)).
- **`NOT IN` and NULL; dialect rendering; cached statements** → harmless where added (every query
  already requires `exit_status IS NOT NULL`); identical SQL on both dialects; literals baked into
  the cached UPDATE. Proved on PostgreSQL 18 by the `pg:` tests at plan time ([01](research/01-terminal-retry-gate.md)).
- **A second retry construction** → none (relay copies rows whole; revert and restart build none)
  ([02](research/02-retry-declarations-and-118.md)).
- **#118 rewriting the same block** → `new_retry` subsumes F2 and G1; one hunk conflicts; resolution
  written for either landing order (§5, [02](research/02-retry-declarations-and-118.md)).
- **"Set above the built-in default" for `_env`/`_eval`** → cmdkit's `which` raises `KeyError` on a
  displaced form; check each form and convert ([04](research/04-client-bundle-keys.md)).
- **Library defaults read at import vs call** → call-time helper, so in-process tests can set
  `server.attempts`; R8's pins build each entry point without starting it
  ([05](research/05-retry-defaults.md)).
- **A probe that survives R5** → R7's end-to-end test uses a walltime kill under a client with no
  `-W`, not the GOAL's `-c 100000` (never retried after R5) ([02](research/02-retry-declarations-and-118.md)).

## 5. Risks & open questions

**Decisions for the foreman's sign-off** (the plan proceeds on these):

- **S1 — `AutoScalingServer` joins R8.** It takes `max_retries` and is not in R8's list; its only
  caller (`hs server --autoscaling`) always passes the value, so only a library caller sees it.
  Included for "every Python API entry point"; dropping it is one line.
- **S2 — `Task.new_retry`.** The deviation row 3 behaviour change (a declaration-named tag stays a
  tag on the retry). The release note gets one clause under change 5.
- **S3 — R9's `[submit]` wording.** The reference will say `submit.bundlewait` is read by
  `hs submit`, `hs server` and `hs cluster`, not "by `hs submit` and `hs server`" (digest 13): the
  GOAL's sentence omits `hs cluster`.
- **S4 — Messages.** R1, R2 and the halt wording as in §2.1 and §2.3 ("single-task form" is the page's
  term; the GOAL's is "legacy form").
- **S5 — A `client.bundlesize` form that is not a whole number exits 3** (`ConfigurationError`,
  `bad_config`) when `hs client` starts, naming the key and its source. Today such a value reaches the
  server, which refuses the request. Not a range check.

**Expected conflicts with #118 (`fix/submit-input-checks`, in flight):**

- `src/hypershell/data/model.py` `Task.__schedule_next_failed_tasks`: the retry-construction block
  (ours `:887-897` at `eb2a4487`; #118 rewrites it as a loop at its `:1007-1027`). Resolution either
  way: `retries = {task.id: cls.new_retry(task) for task in failed_tasks}`, with #118's comments'
  substance (no inline re-parse, no strict tag check, a `group` tag stays a tag) in `new_retry`'s
  docstring. If #118 lands first this cycle's build rebases onto it and replaces its loop; if this
  lands first #118 takes ours. #118's three retry tests (`tests/test_group_integer.py`) read explicit
  columns and pass against `new_retry`; gate them on both dialects at the rebase.
- `model.py`'s `sqlalchemy` import line (`and_` there, `case` here): keep both.
- `AGENTS.md` § Retry model / group-gating paragraph and `invariants.md` §3 (#118 adds a sentence on
  a retry's group; this cycle adds never-ran and declarations): adjacent-line conflicts, keep both.
- `docs/cli/server.rst`, `docs/cli/cluster.rst`: 2.9.0 markers added by both; keep both.
- `server.py`/`cluster/__init__.py`: import lines and `ServerApp.run` neighbours only; no hunk on a
  `max_retries` line. `task.py`, `client.py`, `config_param_ref.rst`: untouched by #118.
- `submit_desc.rst` (#118: "a retry runs that same command in the task's group, with its tags"): if
  #118 lands first, P4 adds "and its cores, memory and walltime" there.

**Residual risks:**

- With R7, a retry of a walltime-killed task is killed again, so `-W` runs with retries spend each
  attempt (release note change 5).
- A library caller gets `server.attempts` but not `server.eager` (digest residuals). Candidate seed.
- PostgreSQL proof needs the local server (sandbox off); CI runs the same `pg:` tests.
- Found at plan time, outside scope: `run_local` on a fresh SQLite site fails (`no such table: task`;
  the library never creates the schema) and then hangs in `stop()`. The drive runs `hs initdb` first.
  Candidate seed.

## 6. Verification strategy

- **Tests first (P1):** five new files from [`research/prototypes/`](research/prototypes/) and three
  edited tests; `red.txt` lists every test that must fail on `1f688c7c` for what it checks
  (`spec/relay-hardening/drive/red_check.sh`, `pg:` lines against PostgreSQL), and
  `spec/contended-writes/drive/rest_green.sh` shows the rest of the touched and neighbouring files
  green.
- **Each fix phase:** its red tests and neighbours green, then the CLI drive
  ([`drive/ucr_drive.sh`](drive/ucr_drive.sh)) in a throwaway site: `update` (R1, R2), `client`
  (R3, R4, a live server and client on `127.0.0.1`), `retry` (R5–R7, `hs cluster -r 2` with a template
  that does not expand, a task needing 100000 cores, and a `-W 2` task's retry), `defaults` (R8,
  `run_local` with and without `HYPERSHELL_SERVER_ATTEMPTS=3`). Doc greps and the `-W` docs build ride
  along.
- **Gate (P5):** the whole suite serially, the red list green, `red_check` re-proved, the `pg:` tests
  and `tests/test_zone_postgres.py` on PostgreSQL, every drive mode, the docs build.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-22)

Foreman, gate judge under the maintainer's mandate. Approved as planned.

- **S1:** `AutoScalingServer` joins R8.
- **S2:** a retry is built from the stored columns by `Task.new_retry`. A `cores`, `memory` or
  `timeout` tag written by `hs update` stays a tag and does not set the retry's column, the same
  rule as #118's G1 for `group`.
- **S3:** the reference names `hs cluster` among the readers of `submit.bundlewait`.
- **S4:** messages say "single-task form".
- **S5:** a `client.bundlesize` that is not a whole number exits 3 at client start.
- **Bends accepted:** docs land with each phase, and §11's "constructors never read configuration" is
  narrowed for `max_retries`.
- **#118** (`submit-input-checks`) is in its last review. If it lands first, merge `develop` before
  P4 and resolve the retry block as `cls.new_retry(task)`, keeping both sides' prose. Otherwise #118
  resolves it.
- **Absurd input:** the review rubric's rule applies. Ranges belong to `bounded-inputs`; do not add
  handlers for absurd values here.
- The `run_local` fresh-SQLite failure is filed as a seed by the foreman.

## P5 gate ruling (2026-11-22)

Foreman, under the maintainer's mandate. P5's first run failed 30 tests, all environmental and
identical on `1f688c7c`. Twenty-eight need PostgreSQL upgrade templates built with Docker, which this
host lacks. Two expect `HS_TEST_TIMESCALE` to match the local TimescaleDB 2.32. P5 runs the way
#118's P8 did:

- the whole suite serially with `HS_TEST_POSTGRES` unset;
- then the PostgreSQL-aware files with it set, excluding `test_upgrade_postgres.py` and the two
  TimescaleDB version-match tests, which CI covers;
- then the rest of the gate as written.

Retune the verify through `set_phase.py` and record it. Before that, merge `develop` (#118 landed
as `2af6a734`). Resolve the retry block as `cls.new_retry(task)`, keep both sides' prose and docs
markers, add "and its cores, memory and walltime" to `submit_desc.rst`, and run #118's three retry
tests on both dialects.

## Review-cycle-1 rulings (2026-11-22)

Review cycle 1 was changes-requested at `0bea846e`. The human gate was not triggered. Foreman's
rulings, under the maintainer's mandate:

- **F1.** `tests/test_retry_defaults.py`'s `ENTRY_POINTS` gains `AutoScalingServer`, so R8's pin
  covers every entry point S1 named. The new case must fail when `topology.py`'s
  `configured_retries()` is replaced by a constant.
- **F2.** `docs/api/server.rst` and `docs/api/cluster.rst` stop calling `server.attempts` the one
  configuration exception. They say which settings the library reads (`server.attempts`, and
  `ServerThread`'s queue size and bundle maximum) and that everything else is passed by the caller,
  checked against the code.
- **Notes (not findings).** `cores:100000` overflowing SMALLINT on PostgreSQL is absurd input
  (`bounded-inputs`). `-m 4GB` overflowing INTEGER is `memory-column-overflows-postgres`.

## Review-cycle-2 ruling (2026-11-22)

Review cycle 2 was changes-requested at `568d35e7`, on F3 alone (LOW, docs; the human gate was not
triggered). Foreman's fix, under the maintainer's mandate: `docs/api/cluster.rst` limits the
in-process clause to `LocalCluster` and says that the launching classes start `hs client`
processes, which read their own configuration for any option not passed, and that launcher-mode
classes give each launch the `[export]` environment. Review cycle 3 is scoped to F3.

## Review-cycle-3 ruling (2026-11-22)

Review cycle 3 approved at `4e495e3c` with no finding; the loop converged. The human gate was
never triggered, so the cycle publishes and merges on green under the maintainer's standing mandate.
