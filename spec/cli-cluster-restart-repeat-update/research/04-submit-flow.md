# Brief 04 — Submit flow integration (R4, R7, R8, R9, R18)

Where a NAMED file gets read, how tasks are counted, where the Source row + task
stamping/dedup must hook in, the writeback ordering that makes R7 meaningful, and the
log points for R18. Scope: the DB-writing submit path only (queue mode is exempt —
see final section).

## The one shared seam: the `Loader` FSM

Both `hs submit <file>` and `hsx <file>` funnel every command line through the **same**
`Loader.load_line_task` (`submit.py:164-198`), which is the *only* place a line becomes a
`Task` via `Task.new` (`submit.py:167`). Path:

- `hs submit`: `SubmitApp.run` → `check_source()` → `submit_all()` → `submit_from()` →
  `SubmitThread`/`LiveSubmitThread` → `LoaderThread` → `Loader` (`submit.py:1063-1091, 697-792, 216-241`).
- `hsx`/cluster: `ClusterApp.run` → `ServerThread(source=...)` (`server.py:799-806`) → same
  `SubmitThread`/`LiveSubmitThread` → same `Loader`. `ClusterApp.source` currently returns
  the raw stream, or `[]` when `--restart` (`cluster/__init__.py:435-441`; `input_stream`
  returns `None` for restart at `:424`).

⇒ **Source stamping + identity dedup belong in `Task.new`/`Loader`, not duplicated per
entrypoint.** The gate decision (refuse / new-source / dedup) belongs upfront in each app;
the *mechanism* (stamp `source_id` + identity, skip already-present identities) belongs in
`Loader`/`Task.new` so both paths inherit it. This matches invariant §1 ("state/identity
logic in `Task` classmethods").

## Where a NAMED file is opened today

`SubmitApp.check_source` (`submit.py:1124-1162`) resolves the mode and **opens the file but
discards the path**: `-f FILE` → `open()` at `:1141`; implicit non-exec file arg → `open()`
at `:1153`. `<stdin>` → `sys.stdin` (`:1138/1144/1149`); single command → `self.source = None`
(`:1156/1159/1162`, the `<direct>` case). So "named file" ≡ `self.source` is an opened
stream that is **not** `sys.stdin` and **not** `None`. The seam must **capture the abs path**
here (e.g. set `self.source_path = os.path.abspath(filepath)` in the two file branches;
`None` for stdin/direct) — R1 needs the absolute path and R5/R6 key on it.

## Count semantics — NOT one-line-per-task

`Loader.count` is incremented **only in `put_task`** on a successful enqueue (`submit.py:200-205`),
and surfaced as `SubmitThread.task_count` → returned by `submit_from` (`submit.py:452-455,
691-694, 786-792`) → logged at `submit.py:1091`. A line is a task **iff**, after
`template.expand(line.strip())` then `Task.split_argline` comment-strip, `args` is non-empty
(`submit.py:166-183`). Non-tasks:

- empty / whitespace-only lines (`:182`),
- comment-only lines (`#...` stripped to empty by `split_argline`, `model.py:375-377`),
- **inline-tag-only lines** (`# HYPERSHELL: k:v`) — these set *global* tags for subsequent
  tasks and produce **no task** (`submit.py:172-180`).

**Template subtlety (load-bearing for counting):** emptiness is judged *after* template
expansion. With `DEFAULT_TEMPLATE` (`"{}"`) a blank line → empty → not a task; but with e.g.
`--template 'echo {}'` a blank line expands to `"echo "` → **is** a task. So any upfront
count MUST apply the *same template*. ⇒ Factor the "line → task?" predicate into one helper
(taking the `Template`) used by both the count pass and `Loader`, or the counts will diverge.

`md5` is over **raw file bytes** (R1), independent of parsing — a plain
`hashlib.md5(f.read())` streaming pass.

### Pass strategy — recommend TWO streaming passes over the path

Named files are seekable; `<stdin>` is not — which is *exactly* why stdin is exempt (R3/R4).

1. **Pass 1 (upfront, O(1) memory):** stream the file → md5 **and** count real tasks
   (replay the shared predicate). Yields `(md5, expected_count)` with no `Task` objects, no DB.
2. Gate + create `Source` row.
3. **Pass 2:** the existing `Loader`/`SubmitThread` streams the file *again* and submits.

Prefer this over "read whole file into a list" (`ClusterApp.source`/`input_stream` are
streamed today; going in-memory would regress huge-file memory). Reading a local text file
twice is negligible against R17's billions-of-rows target. `hs submit` must reopen by path
in pass 2 (it currently keeps one open handle) — trivial since we now store `source_path`.

## Source writeback ordering — write the Source row BEFORE the tasks

**Recommendation: create the `Source` row with the *expected* count (pass-1 count) and
commit it BEFORE the task bundles.** Rationale — this is what makes R7 meaningful:

- `DatabaseCommitter` commits in **bundles** (`submit.py:313-320`); a crash mid-stream leaves
  a *partial* set of tasks. If `source.task_count` is the expected N (written first) but only
  k<N task rows carry that `source_id`, a later run detects `live COUNT(source_id) < N` →
  **R7 warn "incomplete prior submission"**, and `--restart` (R12) resumes by submitting the
  missing N−k via identity dedup (R9). This is the resume story.
- If instead you wrote the Source *after* with the *actual* count, `recorded == actual`
  always and R7 can never fire. So **expected-first is required, not a style choice.**

R7 compute: `expected = source.task_count` (recorded) vs `actual = COUNT(task WHERE source_id
= …)` (live, indexed per R17). Warn when `actual < expected`. Note per-source count sidesteps
the whole-table scan.

Guard the Source write with the DB-mode / not-`in_memory` check (invariant §4; non-goal
"no source tracking for in_memory").

## R8 (`--repeat`) and R9 (`--update`) at this seam

- **R8 `--repeat`:** always mint a **new** `Source` (fresh uuid, even if an identical prior
  source exists) and submit *all* tasks — i.e. run pass-1→create-source→pass-2 with dedup
  **off**. New tags (R15) flow through the existing `tags`/`Task.new` path unchanged.
- **R9 `--update`:** create a new `Source`, but the `Loader` **skips** tasks whose identity
  fingerprint already exists in the prior same-path source lineage. Give `Loader` an optional
  dedup handle (a pre-loaded identity `set` for that lineage, or an indexed existence check —
  scale tradeoff is a model.py decision). Skipped lines must **not** increment `count`, so
  the dedup check sits in `Loader` before `put_task` and the "K submitted / L skipped" tallies
  come from there.
- **R10/R16 contradiction (`--update`+`--repeat`)** and **R13 (`--update` w/o `--restart` on
  hsx)** are pure arg-validation → `SubmitApp` (no `check_arguments` today; add one) and
  `ClusterApp.check_arguments` (`cluster/__init__.py:335`, which already houses the
  restart/forever/no-db refusals at `:346-361`). Raise `ArgumentError` (cmdkit maps to a
  non-zero exit) — do not invent literals (invariant §12).

## R18 log points to add (levels are suggestions)

Existing: JSON load count (`submit.py:1131`), `Submitted N tasks` (`:1091`), per-task
(`:1104`), bundle debug (`:317`). Add:

- upfront ingest: `info` — `Found N tasks in <abspath> (md5=<hex>)`.
- detection hit: `info` — `Source already submitted <uuid> at <ts> (N tasks)`.
- R7 warn: `warning` — `Incomplete prior submission: k of N tasks landed`.
- dedup (R9/R14): `info` — `M tasks already present; submitting L new tasks`.
- refusals: R5 `error` — `Refusing: file already submitted; use --repeat to resubmit`;
  R6/R12-mismatch `error` — `Refusing: <path> seen with different content; use --update`.

## Queue-mode asymmetry (call out explicitly)

`hs submit -q` / `LiveSubmitThread` uses `QueueCommitter` (`submit.py:468-554`) which pushes
bundles to a live server's `scheduled` queue and **never writes Task rows** — same class as
the "queue mode ignores DB / ignores `-t`" asymmetry (invariant §11; `cluster` §11.132).
There is nothing to gate against, so **source tracking does not apply to queue-mode submit**.
`hsx` *with* a DB uses `SubmitThread` (DB path) → gating applies; `hsx --no-db` (`in_memory`)
is exempt by non-goal. Gate the whole feature on `queue_config is None and not in_memory`.

## Open questions for PLAN

- Dedup carrier into `Loader`: pre-loaded identity `set` (bounded by one lineage's task
  count — could be large) vs per-task/per-bundle indexed `EXISTS` query? R17 scale call —
  belongs to the model brief but the `Loader` signature depends on it.
- Should `hs submit` grow a `check_arguments()` (it has none today) or fold the R10 check
  into `check_source`? Recommend a dedicated `check_arguments` for symmetry with `ClusterApp`.
- Confirm `<direct>`/single-cmd (`self.source is None`, `submit_one` at `:1093`) and `<stdin>`
  bypass Source creation entirely (R3 reserved sources) — yes per this analysis.
