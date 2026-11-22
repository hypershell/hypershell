# 00 — Digest: decisions from the research pass

Five briefs, fanned out on 2026-11-21 against `46dcb430` (tree = develop `dfdbaed5`):
[`01`](01-whole-input-precheck.md) whole-input pre-check placement,
[`02`](02-stream-line-refusal.md) per-line refusal on streams,
[`03`](03-reached-group.md) the reached group,
[`04`](04-hash-parser-and-group-parse.md) the `#` parser and integer group parsing,
[`05`](05-tests-and-harness.md) tests, harness and docs build. Where they differ, the ruling below
is the plan's. Plan-time probes added after the briefs are marked **(plan probe)**.

## The split (C1): does not apply

Both triggers came in light. The reached group is one read on both dialects (probes S0–S10 identical
on SQLite and PostgreSQL 18; 65 ms and 78 ms on 1,000,000 rows, next to 57 ms and 46 ms for the
`lowest_incomplete_group` the scheduler already runs every pass). Reading `hs submit`'s stdin in full
is `list(stream)` in a worker thread. All three seeds ship in this cycle.

## Decisions

1. **`#` parser (R1, R2).** `Task.split_argline` keeps its marker regex `#\s*HYPERSHELL:?`, found
   anywhere on a line as today (the quote-blind non-goal). A line whose first non-blank character is
   `#` and which does not start with the marker is a comment (`''`, no tags). No other `#` is cut.
   Two odd lines change: `## HYPERSHELL: …` and `# c # HYPERSHELL: …` are tasks today and become
   comments (04). Shell check: a kept ` # note` is ignored by `/bin/sh`; `echo '#'' HYPERSHELL:'`
   works after the change (04).
2. **Split the raw line before the template (R1, R2 under `--template`).** `Loader.load_line_task` and
   `line_is_task` split the raw line, then expand the template on the command only, as `hs cluster`
   already does (the server ingests raw lines; the client expands). Today `hs submit --template 'echo
   {}'` turns a comment line, a blank line and a tag-only line into `echo` tasks (04). After the change
   they are a comment, a skip and a tag line. Fingerprints are unchanged (already taken over the split
   raw line). Drift: a `# HYPERSHELL:` inside the *template text* is no longer parsed as tags
   (`--template '{} # HYPERSHELL: group:3'` set group 3 before; 01 P6). Release-note line.
3. **Loader tags fix.** `Loader.__init__` copies its tags (`dict(tags or {})`). Today a tag-only line
   crashes `hs cluster` and `hs server` with `TypeError` (exit 5) because `ServerThread` passes
   `tags=None`, and the Loader writes into `SubmitApp.tags`, a class attribute (02, 04). R2 and R8
   cannot be tested without it.
4. **The integer rule (R3, C5).** One predicate in `data/model.py`: `ensure_group(value) -> int` accepts
   an `int` that is not a `bool` and lies in the 32-bit range of PostgreSQL's `INTEGER`. Tag text
   reaches it after `smart_coerce`, which returns `int` exactly when `int()` (and so `-g`) accepts the
   text (01, 04), so one predicate serves tags and JSON. **Range (plan ruling):** a value outside
   `-2**31 .. 2**31-1` is refused on both dialects and through `-g` (exit 2): `group:99999999999999999999`
   kills the committer and hangs `hs submit -f` today, and `-g 3000000000` is stored on SQLite but
   is out of range for PostgreSQL (04). R5's "no group value SHALL hang a run" and C8's "same rule on
   both databases" require it.
5. **Where it is enforced.** `TaskGroupError(RuntimeError)` (exit 5 through the shared mapping, no FSM
   traceback) carries the value *as written* (`1e3`, not `1000.0`) and an origin (`line 7`,
   `record 3`). It is raised by `split_argline` for an inline or tag-only group (not wrapped in the
   generic "Failed to parse inline tags"), and by `Task.new` for a group that came from the `tag`
   dict. `Task.new(group=…)` is never checked: queue mode passes `None`, retries copy legacy text
   groups, and a legacy text group can still be auto-selected (02, 03). `-g` uses the same rule as its
   argparse `type`. `-t group:…` is checked in `SubmitApp.check_tags`, now run in both modes before
   any input is read (exit 2, R4); queue mode otherwise keeps ignoring `-t` as today (AGENTS.md notes
   the asymmetry; applying tags there is out of scope).
6. **Read-in-full input is scanned before anything is written (R4, C6, C8).** A pure
   `scan_task_lines(lines, *, template, group, tags, name)` in `submit.py` replays the Loader's line
   rules (shared helpers, so they cannot drift), numbers physical lines from 1, raises
   `TaskGroupError` at the first refused group, and returns the count (replacing the count half of
   `source_fingerprint_and_count`) and each task line's group. The fingerprint is computed in a second
   pass only when the gate returned a non-empty skip set **and** a reached-group check needs it.
   `validate_json_groups(records, name)` does the same for `--from-json` (record numbers 0-based, as
   `validate_json_expansion`). The scan runs with and without a database: `hs cluster --no-db FILE`
   today returns before any scan (01).
7. **One order for every read-in-full input (01).** Parse `-t` → read the input → resolve the default
   group (C9; a read, database mode only) → scan (R3, exit 5) → gate **decision** (read-only; may exit 2) → reached-group check
   on the tasks to be inserted (R6/R7, exit 2) → write the Source row / reserved source and
   `Zone.ensure` → Loader. `apply_source_gate` is split into `decide_source_gate` (reads, logs, may
   raise) and the commit that writes the Source row; `apply_source_gate` stays as a wrapper. A refusal
   at any step leaves no task, Source or Zone row.
8. **`hs submit` reads a stream in full with a database (C6).** stdin, `-`, a FIFO or a process
   substitution is read into a list by `read_in_full(stream)`, which runs the read in a worker thread
   and waits with `join_waking`, so a signal still reaches the main thread
   (`test_signal_wake.py::test_submit_takes_a_signal_taken_by_another_thread`, 05). One element per
   physical line keeps the line numbers. The list then goes through decision 7 under the reserved
   `<stdin>` source. Memory is about 100 bytes a line; documented. `hs submit --queue` keeps
   streaming (no database, per-line rule).
9. **Streams refuse one line (R5).** The Loader counts physical lines (1-based, every line), and its
   policy is `refuse_lines = not is_file_input(source)` (`is_file_input` moves from `server.py` to
   `submit.py` and is re-exported by `server.py`). `get_task` catches `TaskGroupError` only: on a
   stream it logs `ERROR … Refused line N of <name>: …` and reads on; on whole input it re-raises (a
   defence: the scan already refused). A refused tag-only line raises before `self.tags` is written,
   so it sets nothing. Other tag errors still end the run (ingest-robustness non-goal). No committer
   guard: no refused value reaches it (02).
10. **Reached group (R6, R7; 03).** `Task.reached_group(in_flight=True) -> Optional[int]`:
    U = `min(submit_time)` over incomplete rows (NULL → no reached group); R = `max(group)` over rows
    not cancelled (`exit_status IS NULL OR exit_status <> -1`), on SQLite only `typeof("group") =
    'integer'`, that are either scheduled and (in flight, or `schedule_time >= U`, or `completion_time
    >= U`), or retry rows (`previous_id IS NOT NULL`) with `submit_time >= U`. U is computed once (CTE
    or one transaction). Every row counted was scheduled in the current pass, so R never exceeds the
    true reached group; its gaps are under-estimates that the shipped late-group warning covers.
    Exhausted failures are finished; a pending retry counts once its retry row exists; the server's
    `-r` is never guessed from configuration (that over-refuses for the life of the database).
    `in_flight=False` drops the bare in-flight disjunct: R7 uses it, because `hs cluster`/`hs server`
    revert in-flight rows at start (every start with incomplete work), so a stranded group no longer
    holds lower groups back. Groups are global (not per zone).
11. **C9 default.** `hs submit` without `-g` takes `G = Task.current_group().value` as today, unless a
    reached group R exists and G is not an `int` or G < R; then it takes R. Unchanged on a database
    with no reached group (03 S0, S3, S6, S7). Other `current_group()` callers stay (invariants §11).
12. **R7 placement.** `ClusterApp.prepare_source`/`prepare_json_source` check after the gate decision,
    on the tasks it would insert (not the skip set); a same-file `--restart` onto a stranded database
    inserts nothing and is never refused. `hs server FILE` (no gate, C10) gets a
    `ServerApp.prepare_source` that scans the whole file (R3, both modes) and, with a database,
    checks every task line against R (`in_flight=False`). Reads happen before `ServerThread` exists, so
    before any revert (03).
13. **Exit statuses (01, probed).** `TaskGroupError` → 5; `ArgumentError` → 2 (`-t`, `-g`, R6, R7, the
    gate). The R6/R7 refusal is an `ArgumentError` naming the lowest offending group, the reached
    group and the three workarounds (GOAL non-goal: no `--force`).

## Edges ruled (for the foreman's sign-off)

- **All-refused stream on a fresh database.** It inserts nothing and meets the pre-existing
  empty-input hang (`issues/empty-task-file-hangs.md`; `Task.count() > 0` in `Scheduler.load_bundle`,
  02 P6/P7). Ruled out of scope: the group value no longer hangs anything; an empty stream hangs today
  for any reason and its outcome (exit 0 or an error) is that seed's shaping question; the fix would
  edit the lines #115 just rewrote. R8's stream tests always carry an accepted line.
- **R3 also checks lines the gate skips** (a parse rule); R7 counts only inserted lines. A `--restart`
  of a pre-2.9.0 file holding `group:x` is refused (exit 5).
- **C4 meets R7.** A line whose identity drifted (a `#` after its first character) is not skipped, so
  a `--restart` or `--update` of an old file can be refused by R7 on an advanced database when that
  line's group is below R. GOAL's letter; documented with the audit note. Also documented: an old cut
  row that failed keeps its group failed until it is cancelled or updated (04).
- **PostgreSQL today (plan probe).** A file line `group:x` hangs `hs submit -b 1 FILE` on PostgreSQL
  (the insert is refused, the committer dies), as `group:nan` does on SQLite. R3 at parse time
  removes both.
- **Python API.** `submit_from([...])` (a list is whole input) with a bad group raises in the Loader
  after earlier bundles committed; the CLI scans first. Docstring note only.
- **Merge develop first.** `02f53777` (#115) merges cleanly (`git merge-tree`). Nothing here edits
  its lines (`Scheduler.load_bundle`, `results_landed`, `Receiver`, client bundling). P1 merges it so
  the build runs on current develop; the red list is proved against `dfdbaed5`.
