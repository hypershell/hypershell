# PLAN — Submitted input is checked: `#` is part of the command, a group is an integer, and a group below the reached one is refused

> **Status:** Draft for review · **Last updated:** 2026-11-21
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (`appetite: big`). Every design element traces to a GOAL R-ID.

## 1. Summary

One ingest change in three layers. The line parser stops cutting at a bare `#` and splits the raw
line before any template (R1, R2). A single integer rule refuses a non-integer group wherever a group
is taken from input; read-in-full input is scanned whole before anything is written (exit 5), and a
stream refuses only the offending line (R3–R5). A pass-scoped *reached group*, read from task columns
in one statement, lets `hs submit`, `hs cluster FILE` and `hs server FILE` refuse a group below it
before anything is written (exit 2), and gives `hs submit` a default that is never refused (R6, R7,
C9). The research pass found both of C1's split triggers light, so **all three seeds ship here**
([`research/00-digest.md`](research/00-digest.md)). Eight phases, tests first.

## 2. Design

### 2.1 The `#` parser (R1, R2) — `data/model.py`, `submit.py`

- `Task.split_argline(args)`: strip; if the line starts with `#` and does **not** start with the
  marker `#\s*HYPERSHELL:?` it is a comment (`''`, `{}`). Otherwise, if the marker is found anywhere
  (unchanged, quote-blind: the non-goal), the command is the text before it and the tags are parsed
  from the text after it, as today. Otherwise the whole line is the command: **no other cut**. A group
  error from the tag parse propagates as `TaskGroupError` (2.2), not wrapped in "Failed to parse inline
  tags".
- **Split before the template.** `Loader.load_line_task` and `line_is_task` split the *raw* line;
  an empty command is a tag-only line or a skip; otherwise the template expands the command, and
  `Task.new(args=expanded, raw_args=command, parse_inline=False, tag={**running, **inline})` builds the
  task. This is what `hs cluster`/`hs server` already do (raw lines on the server, the template on the
  client), so R2 now holds under `hs submit --template`, and the fingerprint is unchanged (it was
  already taken over the split raw line).
- `Loader.__init__` copies its tags: `self.tags = dict(tags or {})`. A tag-only line no longer crashes
  `hs cluster`/`hs server` (they pass `tags=None`), and the Loader no longer writes into
  `SubmitApp.tags`.
- `SubmitApp.submit_one` keeps building the one argument task with `Task.new(parse_inline=True)` (it
  needs the marker after a command); after the parser change `hs submit -- echo 'direct #7'` stores
  `echo "direct #7"`.

### 2.2 The integer rule (R3, C5, C8) — `data/model.py`

- `GROUP_MIN, GROUP_MAX = -2**31, 2**31 - 1` (PostgreSQL `INTEGER`; one rule on both dialects).
- `class TaskGroupError(RuntimeError)`: `.value` (the text as written when it came from text, else the
  JSON value), `.origin` (`'line 7'`, `'record 3'`, or none), message
  `task group must be an integer, not "<value>"` plus origin; `.at(origin)` returns a copy with the
  origin. Subclassing `RuntimeError` gives exit 5 through `core/exceptions.py`'s shared mapping with no
  new entry.
- `ensure_group(value) -> int`: an `int`, not a `bool`, within the bounds; else `TaskGroupError`.
  `smart_coerce` turns tag text into `int` exactly when `int()` accepts it, which is `-g`'s rule, so
  this one predicate covers tag text (`07`, `+5`, `-3` accepted; `x`, `''`, `1.5`, `1e3`, `0x10`,
  `true`, `null`, `nan`, `inf` refused) and JSON values (a JSON integer only; `"7"`, `3.0`, `true`,
  `null` refused). `parse_group(text) -> int` is `-g`'s argparse `type` (out of range or not an
  integer: exit 2).
- **Where:** `split_argline` (inline and tag-only groups; the text token kept for the message);
  `Task.new` checks a group taken from the **tag dict** only (`if 'group' in tag`), never its `group=`
  parameter (queue mode passes `None`, retries copy legacy text groups). `SubmitApp.check_tags` runs
  in both modes, before any input is read, and turns a `TaskGroupError` from `-t` into
  `ArgumentError` (exit 2). Queue mode otherwise keeps ignoring `-t`, as today.

### 2.3 Read-in-full input is scanned first (R4, C6, C8) — `submit.py`, `cluster/__init__.py`, `server.py`

- `scan_task_lines(lines, *, template, group, tags, name) -> InputScan` (pure; no database): numbers
  physical lines from 1, applies 2.1's line rules with the same helpers the Loader uses (a tag-only
  line updates a running copy of the tags only after its group passes), raises
  `TaskGroupError.at(f'{name}, line {n}')` at the first refused group, and returns `count` (exactly the
  Loader's task count: it replaces the count half of `source_fingerprint_and_count`, whose md5 half
  stays) and the group of each task line (`group` when no tag sets one). Fingerprints are computed
  only on demand (2.5). `validate_json_groups(records, name)` checks each record's `group` key and
  raises `TaskGroupError.at(f'record {i}')` (0-based, as `validate_json_expansion`).
- **One order for every read-in-full input:** parse `-t` (exit 2) → read the input → resolve the
  default group (database mode, 2.4) → scan (exit 5) → gate **decision** (read-only, may exit 2) →
  reached-group check on the tasks to be inserted (exit 2) → write the Source row (or the reserved
  source) and `Zone.ensure` → hand the input to the Loader. `apply_source_gate` is split into
  `decide_source_gate(...) -> GateDecision` (reads, logs, raises) and `GateDecision.commit(count)`
  (writes the Source row); `apply_source_gate` stays as the composed wrapper for existing callers.
  A refusal at any step leaves no task, no Source row and no Zone row.
- `SubmitApp`: `check_tags` moves before `check_source`. A named file, `--from-json`, arguments
  (as the one-line list `[args]`) and, **with a database**, a stream are read-in-full. A stream
  (stdin, `-`, FIFO, process substitution) is read by `read_in_full(stream) -> List[str]`, which
  reads in a worker thread and waits with `core/thread.py::join_waking`, so a signal still reaches the
  main thread; one element per physical line. It is then a list under the reserved `<stdin>`
  source. `hs submit --queue` keeps streaming (per line, § 2.4; with no database there is no reached
  group, so § 2.5 does not apply). The scan runs in
  both modes for files, JSON and arguments.
- `ClusterApp.prepare_source`: the scan (under `DEFAULT_TEMPLATE`, the server's raw-line split) of a
  seekable file runs **before** the `in_memory`/`--no-db` early return; with a database, decide → R7
  → commit, and `self.zone_id` is first read after the commit. `--from-json`:
  `validate_json_groups` next to `validate_json_expansion` in `check_arguments` (both modes).
- `ServerApp.prepare_source` (new, called in `run` after `check_database`, before `serve_from`): when
  `is_file_input(self.input_stream)`, scan the whole file (both modes) and, with a database, check
  every task line against the reached group (no gate: C10). Streams go unchanged to the Loader.

### 2.4 Streams refuse one line (R5) — `submit.py`

- `is_file_input` moves from `server.py` to `submit.py` (re-exported by `server.py`, so its importers
  keep working). `Loader.__init__` sets `self.refuse_lines = not is_file_input(source)` and
  `self.line = 0`; `self.name` is the source's display name (`<stdin>` or the path, from a module
  function `source_name()` that replaces the two `source_name` properties).
- `Loader.get_task` increments `self.line` for every text item and catches `TaskGroupError` only:
  on a stream, `log.error(f'Refused line {self.line} of {self.name}: {error}')` and return `GET`; on
  whole input, re-raise (the scan already refused; this is a defence for API callers). The task is
  built before any tag-only update, so a refused tag-only line sets nothing and later lines keep their
  group. Every other error still ends the run (the ingest-robustness non-goal). The run's exit status
  is untouched; no committer guard is needed, since no refused value reaches `Task.add_all`.
- No change to `Scheduler`, `submission_complete`, `ServerThread`'s submitter wake, or anything
  #115 (`02f53777`) touched.

### 2.5 The reached group (R6, R7, C9) — `data/model.py`, `submit.py`, `cluster/__init__.py`, `server.py`

- `Task.reached_group(in_flight: bool = True) -> Optional[int]`, one statement (U in a CTE or a
  scalar computed once in the same transaction):
  - U = `min(submit_time)` over rows with `completion_time IS NULL`; NULL → None (no reached group).
  - R = `max("group")` over rows with `exit_status IS NULL OR exit_status != CANCEL_STATUS`, on SQLite
    also `typeof("group") = 'integer'`, that are either scheduled and (`completion_time IS NULL`
    [only when `in_flight`] or `schedule_time >= U` or `completion_time >= U`), or retry rows
    (`previous_id IS NOT NULL`) with `submit_time >= U`.
  - Every row counted was scheduled in the current pass, so R never exceeds the true reached group
    (an over-estimate would refuse users, which seed Q3 rejected); the gaps are under-estimates that
    the shipped late-group warning covers ([`research/03`](research/03-reached-group.md)).
    Exhausted failures are finished; a pending retry counts once its retry row exists; the server's
    `-r` is never guessed; cancelled rows (whose cancel stamps `schedule_time`) are excluded; groups
    are global.
- **R6** (`SubmitApp`, database mode): after the scan and the gate decision, R =
  `Task.reached_group()`; if any task to be inserted has a group below R, raise
  `ArgumentError(f'Task group {lowest} is below the reached group {R} ...')` naming the workarounds
  (submit at R or higher, wait for the run to finish, or use a fresh database). Holds for a file,
  arguments, `--from-json` and buffered stdin; tasks the `--update` skip set drops are not counted.
- **C9:** with no `-g`, `G = Task.current_group().value` as today, unless R exists and G is not an
  `int` or G < R; then R (logged as the reason). Untagged lines take it, so a submission that names
  no group is never refused.
- **R7** (`ClusterApp`, `ServerApp`, database mode): R = `Task.reached_group(in_flight=False)` —
  in-flight rows are reverted when the server starts, so they do not hold lower groups back — and
  the same refusal over the tasks the file would insert (the gate's skip set excluded). Read before
  `ServerThread` exists. When R exists and the file holds a group below it and the skip set is
  non-empty, a second pass computes fingerprints to exclude skipped lines (cost paid only then).

### 2.6 Exit statuses and errors

| Case | Status | Raised as |
| --- | --- | --- |
| Non-integer group in read-in-full input (file, JSON, arguments, `hs submit` stdin) | 5 | `TaskGroupError`, naming value and `line N` / `record N` |
| `-t group:x`, `-g x`, `-g` out of range | 2 | `ArgumentError` (before any input is read) |
| R6 / R7 refusal | 2 | `ArgumentError`, naming the lowest offending group and the reached group |
| A refused stream line (R5) | unchanged | `ERROR … Refused line N of <name>: task group must be an integer, not "<value>"` |

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | 2.1 `split_argline` (no bare cut); split before the template in `Loader.load_line_task`/`line_is_task`; `submit_one` unchanged path; same Loader for `hs cluster`/`hs server`/`--queue` |
| R2 | 2.1 comment rule on the raw line; marker found anywhere as today; Loader tags copy (tag-only lines under `hs cluster`/`hs server`); count from `scan_task_lines` equals the Loader's |
| R3 | 2.2 `ensure_group`/`parse_group`/`TaskGroupError`; checks in `split_argline`, `Task.new` (tag dict), `check_tags` (both modes), `-g`, `validate_json_groups`; same rule with and without a database |
| R4 | 2.3 scan before any write in `SubmitApp`, `ClusterApp` (also `--no-db`), `ServerApp`; `read_in_full` for `hs submit` stdin; exit 5 naming value and line/record; `-t` exit 2 before input |
| R5 | 2.4 Loader line counter, `refuse_lines` policy, `TaskGroupError` caught per line on streams; refused tag-only line sets nothing; exit status untouched; no value reaches the committer |
| R6 | 2.5 `Task.reached_group()`; refusal in `SubmitApp` after scan and gate decision, before writes; stdin read in full (C6); C9 default |
| R7 | 2.5 `Task.reached_group(in_flight=False)` in `ClusterApp.prepare_source`/`prepare_json_source` and `ServerApp.prepare_source`; skip set excluded; streams unchanged |
| R8 | TECH P1: `tests/test_command_hash.py`, `test_group_integer.py`, `test_group_stream.py`, `test_reached_group.py`, `test_resubmit_lower_group.py`, SQLite and PostgreSQL through one fixture; red list `drive/red.txt`, guards `drive/guards.txt` |
| R9 | TECH P7: `submit_desc.rst`, `docs/cli/cluster.rst`, `docs/cli/server.rst`, `task_groups_alt.rst`, `AGENTS.md`, `invariants.md`, each `.. versionchanged:: 2.9.0`; release note drafted in TECH P7 for `hs-publish` |

## 3. Invariant gate (AGENTS.md constitution check)

Walked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before the
research (gate #1: §1, §2, §3, §4, §5, §6, §12 touched; the GOAL is sane against each) and again on
this design (gate #2):

- **§1 Task lifecycle.** `Task.reached_group` is a `Task` classmethod and reproduces the predicates
  exactly: incomplete = `completion_time IS NULL`; scheduled = `schedule_time IS NOT NULL`. It only
  reads. No task-state transition moves out of `Task`. `data/model.py` is high blast radius: the
  parser and the group check change `split_argline`/`Task.new`, pinned by unit tests that hold every
  existing marker form (P1/P2), and the human gate applies at review.
- **§2 `exit_status`.** The reached-group query filters `CANCEL_STATUS` (a cancel stamps
  `schedule_time`, research 03 S4). No sentinel is added.
- **§3 Retry model and group gating.** No change to `increment_group`, `count_retryable`,
  `starting_group` or the stop predicate. Retries keep copying the parent's group through
  `Task.new(group=…)`, which is not checked. The text "file input runs in order; a stream's late lower
  group runs with a warning" stays true and gains the refusal and the integer rule (P7).
- **§4 Server modes.** R6/R7 run only with a database (`DATABASE_ENABLED` / not `in_memory`); the
  scan (R3) runs without one by design (C8) and writes nothing.
- **§5 Concurrency.** No FSM state is added; `Loader.get_task` catches one exception class and
  returns `GET`. `read_in_full` waits with `join_waking` (§8's main-thread signal rule). No blocking
  queue operation changes.
- **§6 Shutdown ordering.** Submit flush (`loader.join()` then the end marker) is unchanged; a refused
  line is simply not queued and the loader still ends at end of input.
- **§11 Cluster.** `Task.current_group()` callers other than `hs submit` are untouched (the
  autoscaler reads `Scheduler.active_group`/`Task.starting_group`).
- **§12 Conventions.** `-g` keeps its help text (`NUM`); no new option, no completion change. Docs
  carry `.. versionchanged:: 2.9.0` beside the includes or in hand-written includes; new tests are
  tagged `unit`/`integration`; `cmdkit` exit statuses only.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Loader copies its tags (`dict(tags or {})`), fixing a tag-only line's `TypeError` under `hs cluster`/`hs server` | R2 ("as today") and R8's tag-only tests through `hs cluster` cannot pass while it crashes | Leaving it: R8 untestable; it is a one-line, behaviour-preserving fix (named in the release note) |
| Groups bounded to the 32-bit range, `-g` included | A huge group kills the committer and hangs `hs submit -f` (R5: no value hangs); C8: one rule on both dialects | Unbounded: SQLite takes 64-bit values PostgreSQL refuses, and a hang remains |
| `hs submit --template` splits the raw line before expanding (blank, comment and tag-only lines stop becoming tasks; a marker inside the template text is no longer parsed) | R1 and R2 "under `--template`": with expand-then-split a comment line becomes `echo # comment`, a task | Classifying only `#` lines on the raw text would keep blank lines as `echo` tasks and a template's tail parsed as tags (`'{} > out'` exits 5 today) — two parsers for one line |
| `-t` is validated in queue mode, where it is otherwise ignored | R4/C8: `-t group:x` exits 2, with or without a database | Applying `-t` in queue mode: a behaviour change outside the GOAL (AGENTS.md documents the asymmetry) |
| `is_file_input` moves to `submit.py` | The Loader decides its policy from its source, with no parameter threaded through `ServerThread`, the launchers and `LocalCluster` | A constructor flag: about ten coupled-core signatures for one boolean |
| `Task.reached_group(in_flight=False)` counts only completed rows in its scheduled branch (`completion_time IS NOT NULL AND (schedule_time >= U OR completion_time >= U)`); the retry-row branch is unchanged (build, P1 amendment, applied in P5) | Under realistic times an in-flight row's `schedule_time` is at or after its own `submit_time`, hence `>= U`, so § 2.5's sketch (dropping only the bare `completion_time IS NULL` disjunct) gives the `in_flight` value in S1, S4, S10 and S11 and makes `in_flight=False` a no-op; R7 would then refuse a stranded run's lower group that the scheduler is about to run in order | The sketch as written: R7 over-refuses after every hang that left rows in flight, which seed Q3's asymmetry rejects |
| The SQLite `typeof` filter is chosen from the session's bind (`Session.get_bind().dialect.name`), not `DATABASE_DIALECT` (build, P1 amendment) | The in-process tests bind the session to PostgreSQL while the configured dialect is SQLite; `Source.reserved` already decides by the bind | The module constant: a PostgreSQL bind under a SQLite configuration would get `typeof`, which PostgreSQL lacks |

## 4. Rabbit holes (resolved)

- **The reached group's definition and cost** → pass-scoped max over task columns, never above the
  true value, one statement, identical on both dialects in eleven scenarios; 65/78 ms at 1M rows
  ([`research/03`](research/03-reached-group.md)).
- **How a stream refuses one line without hanging** → catch `TaskGroupError` in `Loader.get_task`
  under a stream policy; the hang was the committer dying on a NULL or out-of-range group, which R3
  now refuses at parse time ([`research/02`](research/02-stream-line-refusal.md)).
- **Check-before-insert placement in three commands** → one scan, one order, the gate split into a
  decision and a commit; `hs server FILE` gets its own `prepare_source`; `hs cluster --no-db` scans
  before its early return ([`research/01`](research/01-whole-input-precheck.md)).
- **The `#` parser and the template** → comment rule on the raw line, split before the template,
  marker unchanged; identity drifts only for lines with a `#` after their first character
  ([`research/04`](research/04-hash-parser-and-group-parse.md)).
- **Buffering `hs submit` stdin vs the signal tests** → a worker-thread read under `join_waking`
  ([`research/05`](research/05-tests-and-harness.md)).
- **C1's split** → not needed ([`research/00-digest.md`](research/00-digest.md)).

## 5. Risks & open questions

Each needs the foreman's sign-off at the plan gate (listed in the report):

1. **An all-refused stream on a fresh database hangs** — as an empty stream does today
   (`issues/empty-task-file-hangs.md`, the stop needs `Task.count() > 0`). Ruled out of scope: the
   group value hangs nothing; that seed decides the empty outcome; its fix edits #115's lines. R8's
   stream tests carry an accepted line.
2. **C4 meets R7.** A drifted line is not skipped by the gate, so a `--restart`/`--update` of a pre-2.9.0
   file on an advanced database is refused when that line's group is below the reached group. GOAL
   letter; documented, with the note that an old cut row that failed keeps its group failed until it
   is cancelled or updated.
3. **R3 also refuses lines the gate skips** (a parse rule): a `--restart` of an old file holding
   `group:x` exits 5.
4. **Under-estimates of the reached group** (research 03: a pass kept open only by rows that have
   since finished; a failure still waiting for its retry row; deleted rows) accept a late group, which
   then runs with the shipped warning. **Over-estimates** arise only from inputs: `hs update --revert`
   of a finished row, clock skew between hosts, and SQLite's naive local time across the DST
   fall-back hour (PostgreSQL is immune). Documented in the task-group page.
5. **Memory for `hs submit` stdin** read in full: about 100 bytes a line (documented). A spool file is
   the alternative if the reviewer asks.
6. **`-b`/`-w` help** for `hs submit` still reads "Number of lines to buffer" / "Wait before flushing";
   both still govern commit bundles, so the help stays.
7. **Merge develop first** (P1): `02f53777` merges cleanly; nothing here edits its lines.

## 6. Verification strategy

- **Tests first (P1).** Five new test files and two flipped tests, through one fixture parametrized
  over SQLite and PostgreSQL (`needs_server`, lazy `ts_database`/`ts_site`, `hs initdb --yes`), every
  subprocess bounded (`timeout=`, `pytest.fail` on expiry, so the base's hangs fail by assertion).
  Streams into `hs cluster` reuse `tests/test_cluster_exit.py::run_cli`; `--queue` reuses the
  `hs server --no-db` pattern of `tests/test_env_config.py`. Reached-group states are built by
  writing task columns directly through a private engine. Unit tests of new names import them inside
  the test. The red list ([`drive/red.txt`](drive/red.txt), proved with the landed
  `spec/relay-hardening/drive/red_check.sh` on `dfdbaed5`) and the guards
  ([`drive/guards.txt`](drive/guards.txt), green before and after) bracket the change.
- **CLI drive.** [`drive/input_drive.sh`](drive/input_drive.sh) (`hash`, `group`, `stream`,
  `reached`) drives the real CLI in a `temp_site` on SQLite; each mode fails on the base (plan dry
  run) and must pass after its phase.
- **Each phase** runs its new tests and the existing suites around the code it touches, with
  `HS_TEST_POSTGRES` set; the last phase runs the whole suite serially (bounded 7200 s) and re-proves
  the red list. Docs build with `-W` (html and man).

## Plan-gate rulings (2026-11-21)

Foreman, gate judge under the maintainer's mandate. Approved as planned. The C1 split is not taken;
all three seeds ship. D1–D9 are accepted:

- **D1:** a stream whose every line is refused, on a fresh database, still hangs. That is the
  existing empty-input hang, and it stays with `issues/empty-task-file-hangs.md`. R8's stream tests
  always include an accepted line.
- **D2:** an old task file whose `#` lines changed identity can be refused on `--restart` or
  `--update` by R7. The docs and release note say so, beside C4's drift note.
- **D3:** the integer check also covers lines the source gate skips.
- **D4:** groups are limited to PostgreSQL's 32-bit range, `-g` included.
- **D5:** `hs submit --template` stops turning blank, comment and tag-only lines into tasks. The
  release note names this.
- **D6:** `-t` is checked in queue mode, but queue mode does not apply tags (unchanged).
- **D7:** JSON errors number records from 0.
- **D8:** P1 merges the current `origin/develop`, which now holds #115 and #116. The red list is
  proved against `dfdbaed5`.
- **D9:** the tag-only crash fix and moving `is_file_input` are accepted deviations.

`server.py` is high-blast-radius: if the design touches it, change only what TECH names. The
review's human gate applies as the rubric says.

## Review-cycle-1 rulings (2026-11-22)

The review's human gate was triggered: F1 and F2 are both in `data/model.py`. The maintainer
ruled in session (2026-11-22) "Approve both", approving review cycle 2 and a merge on approval.

- **F1.** `Task.reached_group` takes the pass start as an uncorrelated scalar subquery instead of
  a CTE joined `ON true`, so SQLite computes it once. A bounded test pins the cost at scale (at least
  10k rows on SQLite) and is red on `44e177b4`. PostgreSQL keeps its result.
- **F2.** A retry copies the failed task's group and tags and does not re-parse the command's inline
  tags. A retry of a task whose command carries `# HYPERSHELL:` text keeps its command and group,
  and the run goes on. Tests are red on `44e177b4`.
- **Retry window (maintainer, same session: "Accept, documented").** A failed task whose retry row
  does not yet exist counts as finished for the reached group. The docs say so, and the late-group
  warning covers it.
- **Release note.** `RELEASE-NOTE.md` names D2: an old file can be refused on `--restart` or
  `--update`.

## Review-cycle-2 rulings (2026-11-22)

The review's human gate was triggered by G1 (`data/model.py`). The maintainer ruled in session
(2026-11-22) "Approve the fix", approving review cycle 3 (the last) and a merge on approval.

- **G1.** A retry takes its group from the failed task's `group` column, and a `group` key in the
  copied tags is ignored. A text group tag (written by `hs update`'s tag form) no longer stops the
  run. An integer group tag no longer moves the retry. Tests for both are red on `3dd066a4`.
- **N2 (triaged).** On PostgreSQL, a table where every task has finished is read in linear time,
  slower than before (results identical). It is filed as a 2.9.x seed by the foreman. A retry not
  carrying cores, memory or walltime is R7 of `update-client-retry`.

## Review-cycle-3 ruling (2026-11-22)

Review cycle 3 approved at `f699c6f8` with no finding; the loop converged at the last cycle. The
maintainer's review-cycle-2 sign-off approved merging on approval. The cycle publishes and merges
on green.
