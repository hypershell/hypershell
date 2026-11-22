# 01 — Checking the whole input before anything is inserted (R4, R6, R7 placement; C6, C8, C10)

## Question

Where does each input path of `hs submit`, `hs cluster` and `hs server` read its input, pass the
re-submission gate and reach the Loader? Where should one check-before-insert run so that a refused
input leaves no task, no Source row and no Zone row, and which exceptions give exits 5 and 2?

## Findings

Line numbers are on this branch (`46dcb430`, based on `dfdbaed5`).

**Exceptions → exit status.** cmdkit `Application.main` catches `ArgumentError` → 2. Any other
exception goes to the first `isinstance` match in `cls.exceptions`. The shared mapping
(`core/exceptions.py:127-143`) maps `RuntimeError` → 5, `ConfigurationError` → 3 and other
exceptions → 5 with a traceback file. A thread's exception is re-raised by `Thread.join`
(`core/thread.py:56-60`), so a Loader error keeps its class. A subclass of `RuntimeError` exits 5
in all three apps. Probes P2, P4, P17 and P18 confirm this.

**Group parsing.** `Tag.from_cmdline` runs `smart_coerce` (`core/types.py:36-50`), which returns
`int(text)` whenever `int()` accepts the text. That is the same rule as `-g`'s `type=int`. `true` and
`false` become a bool, `null` becomes None, `1.5`/`1e3`/`nan` become a float, and anything else stays
a string. So **`type(v) is int`** is exactly R3's rule, for tag values and for JSON values alike
(bool fails it). `Task.new` uses `tag.pop('group', group)` unchecked (`data/model.py:537`). The
coerced value loses the text as written (`1e3` → `1000.0`), which matters for the error message.

**Template.** `Loader.load_line_task` expands the template *before* `split_argline`
(`submit.py:356-360`), so `hs submit --template '{} # HYPERSHELL: group:3'` sets group 3 (P6).
`hs cluster` and `hs server` ingest raw lines under `DEFAULT_TEMPLATE` (`server.py:1197`), and the
client applies `-t/--template` later, so a group there can only come from the raw line (P9). The
existing counts already follow this: `hs submit` counts with `--template` (`submit.py:1567`) and
`hs cluster` with `DEFAULT_TEMPLATE` (`cluster/__init__.py:559`). A JSON record never parses inline
tags (`parse_inline=False`, `submit.py:390-391`).

**The upfront scan is already a pre-insert check for malformed tags.** `source_fingerprint_and_count`
(`submit.py:142-164`) calls `line_is_task` → `split_argline` on every line. A malformed inline tag
raises `RuntimeError` before `apply_source_gate`, so the run exits 5 with no Source row and no task
(P2 for `hs submit FILE`, P14 for `hs cluster FILE`). R4 extends this precedent.

### Input-path map

| Command / input | Read | Gate | To Loader |
| --- | --- | --- | --- |
| `hs submit FILE` / `-f FILE`, DB | `open()` in `check_source` `:1513,1526`; md5 and count read the file again `:1567` | `apply_source_gate` `:1569` (writes Source `_new_source_id` `:167`) | `GatedSource` → `SubmitThread` via `submit_all` `:1458` |
| `hs submit` stdin, `-`, FIFO, `<(…)`, DB | streamed `:1508-1525` | none: reserved `<stdin>` row written `:1563` | `GatedSource(stream)`, streamed today |
| `hs submit` ARGS, DB | `submit_one` `:1465` | none: reserved `<direct>` row written `:1556` | `Task.new` + `Task.add` inline, no Loader, no template |
| `hs submit --from-json F` / `-`, DB | `load_json_source` whole, in `check_source` `:1501`; `validate_json_expansion` `:1502` → `ArgumentError` (2) | `prepare_json_source` `:1574`; `-` is `<stdin>`-exempt | list → `SubmitThread` |
| `hs submit --queue` (any input) | as above | **none**: `prepare_source` runs only in the DB branch `:1440-1454` | `LiveSubmitThread`; `self.group` stays `None` |
| `hs submit`, in-memory DB | refused by `check_database` `:1480-1482` (exit 3, P18) | – | – |
| `hs cluster FILE` (seekable), DB | `input_stream` `:498-509` | `prepare_source` `:531-563`, `DEFAULT_TEMPLATE` count; `--restart/--update/--repeat` | `GatedSource` → `ServerThread` → `SubmitThread`; the scheduler waits for the whole file (`server.py:1256-1260`) |
| `hs cluster` stdin, FIFO, DB | streamed | `<stdin>`-exempt `:550-556` | streamed |
| `hs cluster --no-db FILE` | `input_stream` | **early return, no scan** `:548-549` | raw stream → `LiveSubmitThread` (`server.py:1198-1202`) |
| `hs cluster --from-json` | `json_source` `:511-514`; `validate_json_expansion` in `check_arguments` `:385`, also under `--no-db` | `prepare_json_source` `:565-587` (skipped with `--no-db`) | list |
| `hs server FILE` | `ServerApp.input_stream` `:1964-1970` | **none**: no Source row, tasks get `source` NULL; no `--repeat/--update`; `FILE` with `--restart` is refused (exit 2) `:1901` | raw stream → `ServerThread` `:1204`; the file is a whole file for scheduling (`is_file_input` `:1356-1371`) |
| `hs server` stdin, FIFO | streamed | none | streamed |

`hs server` and `hs cluster` have no `-g` or `-t TAG` (`hs cluster -t` is `--template`). `-t TAG`
belongs to `hs submit` only. In `--queue` mode `check_tags` is never called (`:1440-1445`), so
`-t` is silently dropped today, and nothing about it is checked before the connection attempt (P7).

**Orphan writes before the Loader.** `hs submit` writes `Zone.ensure` (`:1447`), then
`Source.reserved` or `_new_source_id` in `prepare_source`, then the tasks. `hs cluster` writes the
Source row in `apply_source_gate`, then `Zone.ensure` through the `zone_id` cached_property, which is
first read inside the `GatedSource(...)` call (`:563`, `:589-592`). `hs server FILE` has no upfront
pass at all: a bad line 3 with `-b1` left lines 1-2 inserted before exiting 5 (P15). `hs submit`
from stdin did the same (P16).

**02f53777 overlap.** That commit touches `Scheduler` (`load_bundle`, new `results_landed`),
`Receiver`/`ReceiverThread` and the `ReceiverThread(...)` line in `ServerThread.__init__`
(`:1214`), plus help text in `cluster/__init__.py:84`. Nothing proposed below edits those lines. The
`hs server` hook belongs in `ServerApp`, not `ServerThread`. A rebase onto develop before the build is
clean for this design. The reached-group computation (another brief) may touch `Scheduler`, and
that is where the overlap would be.

## Recommendation

1. **One predicate and one error** (`data/model.py`): `is_task_group(value) -> bool` returns
   `type(value) is int`. `TaskGroupError(RuntimeError)` exits 5 through the shared mapping, so no
   change to the mapping is needed. Apply the check **only to a group taken from tags** (inline, a
   tag-only line, `-t`, a JSON key), never to `Task.new(group=…)`: queue mode passes `None`, and
   `Task.current_group()` can return a legacy text group (P5 logged "Auto-selected task group x").
   C9 still has to fix that default.
2. **A pure resolver shared by the Loader and the scan.** Extract from `Task.new` a classmethod
   (e.g. `Task.resolve(args, tag, group, parse_inline, raw_args, cores, memory, timeout) ->
   (args, attrs, tag, fingerprint)`) that does the tag merge, the group/cores/memory/timeout pops
   and the fingerprint, and checks the group. `Task.new` becomes `resolve` plus the ORM constructor,
   so its behaviour is unchanged. The scan calls it without building ORM rows: `Task.new` costs
   19 µs a line against 2.3 µs for `split_argline`, about 19 s per million lines if the scan used
   `Task.new` (measured). Compute the fingerprint only when a skip set is non-empty. `model.py` is
   high blast radius (invariants §1), so the extraction needs a no-behaviour-change test.
3. **`scan_task_lines(lines, *, template, group, tags, skip=None, name) -> InputScan`**
   (`submit.py`). It replays `load_line_task` line by line: expand → split → tag-only lines update
   a *copy* of the running tags (only after they pass R3) → task lines resolve their group. It
   returns `count` (today's `line_is_task` count, unchanged for R2), `groups` (the set of groups of
   tasks **not** in `skip`) and `min_group`. On the first bad group it raises
   `TaskGroupError(f'{name}, line {n}: group "{text}" is not an integer')`. Keep `text` as written:
   take it from the line's tag token, not from the coerced value. Replace the count half of
   `source_fingerprint_and_count` with it. The raw-bytes md5 stays a separate function.
   `validate_json_groups(records, name)`: `TaskGroupError(f'record {i}: …')`. Choose 0- or 1-based
   to match `validate_json_expansion`, which is 0-based today.
4. **Split the gate into a decision and a commit.** `decide_source_gate(...) -> GateDecision`
   (`reuse_id | None`, `skip`), which only reads, logs and may raise `ArgumentError`. Then
   `GateDecision.commit(count) -> source_id` calls `_new_source_id`. `apply_source_gate` stays a
   wrapper for callers and tests that don't need the split.
5. **One order for every read-in-full input:** parse `-t` → read the input → md5 → gate decision →
   scan with skip (R3, exit 5) → reached-group check on `scan.groups` (R6/R7: `ArgumentError`,
   exit 2) → `GateDecision.commit` / `Source.reserved` → `Zone.ensure` → Loader. A refusal at any
   step leaves no row.
   - **`SubmitApp.run`:** move `check_tags` before `check_source` and out of the DB branch. A group
     from `-t` that fails R3 raises `ArgumentError` (exit 2, before any read, both modes); this also
     fixes `-t` being dropped under `--queue`. In `check_source`, a stream with a database
     (`not queue_mode`; stdin, `-`, or a non-seekable named input) is read in full:
     `self.source = list(stream)` (C6), keeping its name for messages, with an INFO line
     `Read N tasks from <stdin>`. Scan named seekable files, buffered streams and ARGS (as the
     one-line list `[args]` under `DEFAULT_TEMPLATE`, which is exactly `submit_one`'s semantics) in
     **both** modes (C8). R6 applies only with a database. Move `Zone.ensure` (`:1447`) and the
     reserved-source writes after the checks. `--from-json`: call `validate_json_groups` right after
     `validate_json_expansion` (`:1502`).
   - **`ClusterApp.prepare_source`:** run the scan (under `DEFAULT_TEMPLATE`) for a seekable file
     **before** the `in_memory` early return (C8). With a database: decide → scan with skip → R7 →
     commit. Read `self.zone_id` only after the commit. `--from-json`: `validate_json_groups` in
     `check_arguments` next to `:385`, which already runs under `--no-db`.
   - **`ServerApp`:** add a `prepare_source` called in `run` after `check_database` and before
     `serve_from`. When `is_file_input(self.input_stream)`, scan in full (R3, both modes); with a
     database, apply R7 to every task in the file (no gate, so no skip set). Streams go unchanged to
     R5 in the Loader.
6. **Streams (R5, for context):** the Loader needs a line counter and has to catch `TaskGroupError`
   per line *before* the tag-only branch mutates `self.tags` (`submit.py:373-374`), log an ERROR and go on.
   The current `RuntimeError` for a malformed tag still ends the run, as P15/P16 show; that is
   out of scope here.

## Risks / unknowns

- **Buffering stdin in memory:** about 100+ bytes of Python overhead per line, so 10M lines is on
  the order of 1-2 GB. The alternative is to spool to a `tempfile.TemporaryFile(mode='w+')` and treat
  it as a seekable file (scan, seek(0), Loader). That is more code and a disk dependency. Recommended:
  the list, with a release-note line. `SubmitThread.stop(leave_input=…)` is used only by the cluster
  watch/remote paths (`cluster/watch.py:219`, `cluster/remote.py:1053`), not by `hs submit`, so
  buffering does not interact with it. Ctrl-C during the read inserts nothing. task-input-fidelity
  is not affected: lines keep their text, and the Loader strips them either way.
- **Lines the gate skips and R3:** recommended, R3 checks every line, skipped or not (a parse
  rule); R7 counts only the lines it does not skip (R7 text). A `--restart` of a pre-2.9 file
  holding `group:x` is then refused. The plan should state that.
- **TOCTOU:** the file is read twice, once by the scan and once by the Loader. Its md5 and count are
  read twice already. A change between the two reads is not detected.
- **Exact replay:** if `resolve` is not shared, the scan and the Loader drift. Make the Loader call
  the same helper and test that the scan and the Loader agree.
- `hs submit -- echo 'x # HYPERSHELL: group:1'`: `quote_arg` puts the closing quote into the tag
  (P5, exit 5 today). A known non-goal (quote-unaware marker); the R8 argument tests must avoid it.

## Probe evidence

Every probe ran from `temp_site.sh` with `bounded.py` on SQLite with Python 3.14.

- **P1** `hs submit textgroup.in` (`echo b # HYPERSHELL: group:x`; tag-only `group:y`; `echo c`):
  exit 0. Rows were `('echo b','x','text')` and `('echo c','y','text')`, Source `task_count` 3.
- **P2** `hs submit -b1 badtag.in` (a malformed tag on line 3): exit 5, `RuntimeError: Failed to
  parse inline tags`, no "Found N tasks", **no task and no Source row**.
- **P3** `hs submit -t group:x -- echo hi`: exit 0, stored as text `x`. **P4** `-g x`: exit 2.
- **P5** `hs submit -- echo 'direct # HYPERSHELL: group:z'`: exit 5 (the quote ended up in the tag),
  and the log said "Auto-selected task group x".
- **P6** `hs submit --template '{} # HYPERSHELL: group:3' plain.in`: both tasks went to group 3.
- **P7** `hs submit -q -t group:x …` (no server): exit 5 `ConnectionRefusedError`. `-t` was never
  checked.
- **P8** `hs submit --from-json` with groups `"x"`, `1.5`, `true`: exit 0, stored as text `x`,
  REAL `1.5` and integer `1`.
- **P9** `hs cluster srv.in -N1` (groups 0, 2, `x`): exit 0, "starting task group x"; `x` ran last.
  **P10** the same with `--no-db`: exit 0, all three ran.
- **P11** `hs server srv.in` twice (bounded at 8 s each): 6 rows with `source` NULL and no Source
  row. The second run reported "Reverted 1 previously interrupted tasks" and would run beside the
  first run's unfinished work.
- **P12** `hs server FILE --restart`: exit 2, "Cannot specify both FILE and --restart".
  **P13** `--repeat`: exit 2, unrecognized.
- **P14** `hs cluster badtag.in`: exit 5, no rows. **P15** `hs server -b1 badtag.in`: exit 5 with
  **`echo 1` and `echo 2` inserted**. **P16** `cat badtag.in | hs submit -b1`: exit 5 with `echo 1`
  inserted under `<stdin>`.
- **P17** submitting a file that was already submitted: exit 2 (gate `ArgumentError`).
  **P18** `HYPERSHELL_DATABASE_FILE=:memory: hs submit`: exit 3 (`ConfigurationError`).
- Timing: `Task.new` 19.0 µs a line, `split_argline` 2.3 µs a line (100k lines).
