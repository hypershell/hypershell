# 02 — How a stream refuses one line without hanging (R5, and the `group:nan` hang)

Read-only pass over `46dcb430` (develop `dfdbaed5` plus the GOAL). Line numbers are this worktree's.

## Question

How does a streamed `hs cluster`/`hs server` (with and without a database) and `hs submit --queue`
refuse one line whose group R3 refuses: no insert, no run, an ERROR with the value and line number,
every other line as if the refused line were absent, the exit status unchanged, and no hang?

## Findings

1. **A Loader exception ends the run promptly with exit 5. The lines before it stay committed and
   are never run, and the lines after it are dropped.** `Task.split_argline` raises `RuntimeError`
   for a malformed tag (`model.py:597-599`). The error passes through `Task.new` (`:531`) and
   `Loader.load_line_task` (`submit.py:358`) to the FSM, which re-raises it (`fsm.py:57-62`; no
   CRITICAL traceback for a RuntimeError). `Thread.run` stores it, and `join` re-raises it
   (`thread.py:35-40,54-58`). `LoaderThread.join_unless_left` re-raises before `put_end`
   (`submit.py:468-473`), so `SubmitThread` dies (`:656-664`) and so does `ServerThread.wait_submitter`
   (`server.py:1274-1278`, called at `:1224`). `with self.queue` closes the queue, the local client
   is cut off, `LocalCluster.server.join` re-raises (`local.py:261-267`), and `run_cluster` calls
   `stop` (`local.py:47-49`). The shared mapping turns the RuntimeError into exit 5
   (`exceptions.py:137`). `LiveSubmitThread` does the same (`submit.py:928-940`). Probes P1 and Q1.
2. **The `group:nan` hang is the committer dying with nobody watching.** `smart_coerce('nan')` gives
   `float('nan')` (`types.py:47-48`), and SQLite stores NaN as NULL. `DatabaseCommitter.commit` →
   `Task.add_all` raises `IntegrityError: NOT NULL constraint failed: task.group`
   (`submit.py:545-552`, `model.py:235-244`), and the committer thread ends. Its queue holds one
   bundle (`Queue(maxsize=bundlesize)`, `submit.py:649`). The loader then either spins in `put_task`
   (`:410-417`, which never checks the committer) or finishes, after which `SubmitThread.put_end`
   spins (`:696-710`: it gives up only once the loader is `left`). `SubmitThread` never ends, so the
   server blocks at `wait_submitter`, `submission_complete()` stays False (`server.py:203`), and the
   scheduler backs off forever. This matches `issues/database-locked-during-ingest-hangs.md`
   exactly. Probe P2 hung at 30 s. Without a database, `nan` passes through and runs (P3, Q2).
3. **The Loader does not count lines.** `get_task` (`submit.py:343-352`) pulls one item per `next()`.
   For a text stream that item is one universal-newline physical line, the same split as
   `source_fingerprint_and_count`'s text-mode loop (`:158-161`). `source_name` exists twice, on
   `SubmitThread` (`:666-676`) and on `LiveSubmitThread` (`:949-959`), and returns
   `GatedSource.name` (`'<stdin>'` or an absolute path), `'<stdin>'`, or the file's `.name`.
4. **One Loader serves every mode.** `LoaderThread` is the only place a `Loader` is built
   (`submit.py:445`). `ServerThread` uses `LiveSubmitThread` when `in_memory` (`--no-db`, or no
   database configured) and `SubmitThread` otherwise (`server.py:1199-1206`). `hs cluster` reaches it
   through `LocalCluster`/`serve_from`, `hs server` through `serve_from` (`server.py:1872`), and
   `hs submit --queue` through `submit_from` → `LiveSubmitThread` (`submit.py:1063-1084`). Every
   text line goes through `load_line_task`.
5. **New pre-existing bug, which R2, R5 and R8 depend on: a tag-only line crashes `hs cluster` and
   `hs server`.** `ServerThread` passes no `tags` (`server.py:1200-1206`), so `Loader.tags` is
   `None` (`submit.py:324`), and `self.tags[k] = v` (`:371-374`) raises `TypeError`. The run exits 5
   with a traceback, for file and stream input alike (P5, P5b). `hs submit` works because
   `check_tags` sets `{}`. The Loader also mutates the caller's dict. With `--queue` that dict is the
   class-level `SubmitApp.tags = {}` (`:1401`), because `check_tags` runs only in database mode
   (`:1445`). By reading (not probed), `hs submit --queue -t …` drops `-t` silently, which bears on
   R4's `-t group:x` → exit 2 with `--queue`.
6. **A stream whose every line is refused, on a fresh database, will hang.** This is the empty-input
   hang (`issues/empty-task-file-hangs.md`): the stop needs `Task.count() > 0` (`server.py:265`).
   An empty stream and a comment-only stream both hang today (P6, P7); `--no-db` exits 0 (P8). Once
   `group:nan` is refused, `printf 'x # HYPERSHELL: group:nan\n' | hsx` on a fresh database inserts
   nothing and so meets this hang. Read literally, that conflicts with R5's "no group value SHALL
   hang a run" and with R8's "`group:nan` ending with no hang".
7. **Retries call `Task.new(group=task.group)`** (`model.py:890-897`), on legacy text-group rows
   too, which are a non-goal to refuse. A backstop in `Task.new` must not check the `group`
   parameter. Stored `tag` never holds `group`, because `Task.new` pops it (`:537`).

## Recommendation

**One parser, two policies, decided at the Loader.**

- **Exception.** In `data/model.py`, add `class TaskGroupError(RuntimeError)` carrying `.value`,
  the text as written, e.g. `1e3` and not `1000.0`. R3's check (brief 01's parser) raises it from
  the shared path, `split_argline` plus a `Task.new` check of a group taken **from tags**. That
  covers inline tags, tag-only lines, the `tag=` dict and JSON records. The `group=` parameter is not
  checked, so retries of legacy rows still work (finding 7). Subclassing `RuntimeError` gives exit 5
  and no FSM traceback on every read-in-full path with no further plumbing. Other tag errors stay
  plain `RuntimeError`, as today (Q5: they still end the run, exit 5, `ingest-robustness`).
- **Line count.** Add `self.line = 0` to `Loader`. In `get_task`, increment it for every non-dict
  item before dispatching: 1-based, counting blank, comment, tag-only and de-dup-skipped lines. The
  file pre-scan uses `enumerate(text_stream, start=1)` over the same text-mode read, so the two
  numberings agree. If `hs submit` buffers stdin (C6/R6), it must keep one list element per physical
  line (`list(sys.stdin)`).
- **Policy.** Set `self.refuse_lines = not is_file_input(source)` in `Loader.__init__`. Move
  `is_file_input` into `submit.py`, and have `server.py` re-import it so the imports in
  `cluster/__init__.py:33` and `tests/test_group_order.py:37` keep working. This follows the GOAL's
  own split between streamed and read-in-full input, with no parameter threaded through the
  ServerThread, launcher and LocalCluster constructors (the reason `GatedSource` exists):
  - stdin, a FIFO or a process substitution into `hs cluster`/`hs server`, with or without a
    database: per line;
  - `hs submit --queue` from stdin: per line;
  - a named file, a JSON list, a `submit_one` list or `hs submit` buffered stdin (a list): raise.
    Brief 01's pre-scan refuses these before the Loader runs, so the Loader's raise is only a
    defence.
- **Catch in `get_task`, nowhere else:**
  ```python
  self.line += 1
  try:
      return self.load_line_task(item)
  except TaskGroupError as error:
      if not self.refuse_lines:
          raise
      log.error(f'Refused line {self.line} of {self.source_name}: {error}')   # e.g. task group must be an integer, not "nan"
      return LoaderState.GET
  ```
  `Task.new` runs first in `load_line_task`, so a refused tag-only line raises before the
  `self.tags` loop and sets nothing, and later lines keep their group. Move `source_name` into a
  module function (or onto `Loader`) shared by both threads, so the message says `<stdin>` or the
  path. The message text (`task group must be an integer, not "<value>"`) should come from the
  exception, so brief 01's exit-5 error reads the same, just with the file path and `line N`.
- **Fix the tag-only crash** (finding 5) in the same change: `self.tags = dict(tags or {})`. Without
  it, R5's refused tag-only line and R2/R8's tag-only line through `hs cluster` cannot be tested.
  This is a one-line change, and it stops the Loader from writing to `SubmitApp.tags`. Name it in the
  release note as a fix.
- **No committer guard.** Once R3 refuses `nan`/`null`/empty in the parser, and `Task.new` checks
  tag groups for API callers, no group can reach `Task.add_all` as NULL or text. Committer deaths
  from database errors stay with `database-locked-during-ingest-hangs` and `ingest-robustness`.
- **Decide finding 6 at the plan gate.** (a) Narrow fix: in `load_bundle`, with a submitter present
  and ended, not forever, and `Task.count() == 0`, return FINAL. This closes
  `empty-task-file-hangs` too, but it edits the lines #115 just rewrote. (b) Scope it out
  explicitly: the R8 stream tests always carry an accepted line, and the gate records that an
  all-refused stream on a fresh database meets the pre-existing empty-input hang. I lean toward (a)
  only if it is built after rebasing onto develop.
- **Stream stop and #115.** The per-line refusal changes only `submit.py` (and `model.py`'s
  exception). It never touches `submission_complete`, the armed/`finishing` logic,
  `results_landed`, `Receiver(scheduler=)`, the `wake` after `wait_submitter`, or client bundling.
  A refused line simply is not queued, and the loader still ends at EOF. #115's `server.py` hunks
  (`:146-353`, `:605-760`, `:1211`) do not overlap the import line that `is_file_input` would touch.
  Only option (a) overlaps #115.
- **Tests** (unit, no wall clock): a `Loader` over `iter([...])` (a non-list iterable reads as a
  stream) and a plain `Queue`. Assert the queued tasks, `caplog` with line numbers, a refused
  tag-only line leaving the group unchanged, and that a list source raises `TaskGroupError`. Add an
  integration test of `LocalCluster` with a generator source containing `group:nan`, which must end
  inside the join timeout.

## Risks / unknowns

- `is_file_input(list)` means a Python-API `submit_from([...])` raises mid-list after earlier bundles
  are committed, unless the pre-scan also covers API lists. That is acceptable for the API; document
  it.
- A process-substitution or FIFO `.name` may be relative or `/dev/fd/63`. The cluster wraps a named
  stream with its absolute path in database mode (`cluster/__init__.py:556`) but not with
  `--no-db` (`:548`).
- `hs submit --queue -t` being ignored is from reading only (finding 5); brief 01 or the plan should
  confirm it.
- The legacy `task` group's submit and run paths (`task.py:114,445`) also call `Task.new`; check whether R3 covers them.

## Probe evidence

`probe.sh` (scratchpad) runs `printf INPUT | bounded.py N uv run hs …` under `temp_site.sh`, with
`HYPERSHELL_SERVER_AUTH` set.

- **P1** `echo a / echo b # HYPERSHELL: bad@key:1 / echo c | hs cluster -N1 --no-tls`:
  - `exit=5`, `CRITICAL [hypershell.cluster] RuntimeError: Failed to parse inline tags …`;
  - tasks: `echo a 0 null`, committed and never run; `c` dropped.
- **P2** the same with `group:nan`:
  - `exit=124` (hung at 30 s);
  - `CRITICAL … DatabaseCommitter`, then `IntegrityError: NOT NULL constraint failed: task.group`;
    stdout `a`.
- **P3** P2 with `--no-db`: `exit=0`; `a b c` all ran.
- **P5** `echo a / # HYPERSHELL: group:3 / echo c` on a stream: `exit=5`, `TypeError: 'NoneType'
  object does not support item assignment` from `Loader`.
  - **P5b**, the same as a file `hs cluster t.in`: the same TypeError.
  - `hs submit t.in`: `echo a 0`, `echo c 3`.
- **P6/P7** an empty stream and a `# nothing` stream to `hs cluster` on a fresh database: `exit=124`,
  `Empty database - waiting for initial tasks`.
  - **P8** `# nothing` with `--no-db`: `exit=0`.
- **P9** `group:x` on a stream, database mode: `exit=0`. The order run is `a c b`; the stored rows
  are `echo b x 0`.
- **Q1** `hs submit --queue` (to `hs server --no-db --forever -H 127.0.0.1`) with the bad@key line:
  `CRITICAL [hypershell.submit] RuntimeError: Failed to parse inline tags`; `a` delivered.
  - **Q2** `group:nan` via `--queue` with a client: `q2a` and `q2b` ran. The submit hung only because
    no client drained the queue at first (no client, `bounded` 15 s), which is not group-related.
- No processes were left over (`ps` checked with the sandbox off).
