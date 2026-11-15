# GOAL — Every task the input gives runs as given: date-like lines run as text, and a streamed run waits for its input to end

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** task-input-fidelity
- **kind:** fix
- **appetite:** small  ·  *Two `small` seeds promoted together (Clarifications). Each defect is
  local: one decode step on the task wire (`data/model.py`, used by `client.py` and `server.py`)
  and one stop condition in the scheduler (`server.py`). Shaping already reproduced both on
  `develop` and mapped every path that reads timestamps off the wire, so the plan has no open
  research question. No new option, configuration key or wire change. If the plan finds the
  stream fix needs new plumbing beyond the scheduler's existing submitter reference, it says so at
  the plan gate and the foreman may split the cycle (R5–R7 as the follow-up).*

## Problem

Two ways a line the user gave HyperShell never runs, with no error that points at the cause and,
in both, exit status 0. Both are **pre-existing** on `develop` (`48cc505`, 2.9.0a8) and were
reproduced for this shaping on Python 3.11 and 3.14, on SQLite and on PostgreSQL 18 under both the
`postgres` and `timescale` providers.

**1. A task whose line looks like a date fails before it runs.** Task bundles and results travel as
JSON. On the way in, `Entity.from_json` (`src/hypershell/data/model.py:194-196`) passes every field
through `from_json_type` (`src/hypershell/core/types.py:66-74`), which turns any string longer than
five characters that `datetime.fromisoformat` accepts into a `datetime`. That covers extended dates
(`2026-11-02`) on every Python, and from 3.11 (the supported floor) basic dates (`20261102`), ISO
week dates (`2026W45`, `2026-W45-1`) and date-times (`20261102T1200`, `2026-11-02 12:00`). So a
client receives such an `args` as a `datetime`, template expansion fails (`can only concatenate
str (not "datetime.datetime") to str`), and the task is recorded as a template error (`-1001`)
with no command. `2026W45` would not even round-trip: it decodes to 2 November. A list of dates is
an ordinary `hsx` input.

```shell
printf '20261102\n2026-11-02\n2026W45\nhello\n' > dates.in
hsx dates.in -N1 -t 'echo {}' --initdb        # prints only "hello"; exit 0
hs list args exit_status command              # the three dates: -1001, command null
```

The same three template errors occur with the default template, with `--no-db`, and on stdin.
`args` itself is stored intact (the whole-row writeback that used to rewrite it was closed by
`writeback-scoped-conditional`).

Fixing only the client exposes a second instance on the return path. The server decodes results
the same way (`Receiver.unpack_bundle`, `src/hypershell/server.py:632-641`, via `deserialize_tasks`,
`model.py:294-297`), so a result whose `command` is date-like text is written back altered: a
probe on this branch's base that returns `command='2026-11-02'` through today's decoder stores
`'2026-11-02 00:00:00'` on SQLite (with Python's deprecated default datetime adapter) and on
PostgreSQL alike. Under the default template `{}`, that is exactly the command a fixed client
reports for the line `2026-11-02`.

**What relies on the conversion today** (the fix must keep these working; shaping's inventory):

- *Through `from_json_type`, and needing a datetime:* the client reads `submit_time` to compute
  `waited` (`src/hypershell/client.py:812-813`) and carries `schedule_time` back as the result's
  assignment token; the server matches a result on `schedule_time` and writes `start_time` and
  `completion_time` into `DateTime` columns (`Task.apply_results`, `model.py:1188-1250`). The
  relay hand-back revert decodes its `schedule_time` stamp with `from_json_type` directly
  (`Task.revert_handed_back`, `model.py:1123`). `Entity.unpack` (`model.py:207-209`) also uses
  `from_json`, with no caller in `src/`.
- *Already decoded by declared type, not through `from_json_type`:* heartbeats (`time`,
  `src/hypershell/core/heartbeat.py:145`), the topology hand-back check
  (`src/hypershell/core/topology.py:340-347`), relay inbound task rows (by `Task.columns`,
  `src/hypershell/relay/store.py:192-210`) and relay results upstream (encode only,
  `store.py:401-420`), work-request replies (`src/hypershell/relay/upstream.py:290`), and
  `ClientInfo` confirmations (no timestamps). `Client` rows are never decoded from the wire.
- *Not affected today:* `tag` is a JSON object, which `from_json_type` passes through untouched,
  values and all.

**2. A run fed by a stream stops scheduling before the stream ends.** With a database, `hs cluster`
and `hs server` read stream input (standard input, a pipe, a FIFO, a process substitution, or a
Python iterable; `is_file_input`, `server.py:1332-1347`) while scheduling. The stop branch of
`Scheduler.load_bundle` (`server.py:265-271`) consults the submitter only while `startup_phase` is
set, and the first scheduled bundle clears it (`:261`). After that, once every committed task is
done, the scheduler reaches FINAL even though the submitter is still reading. The process does not
exit then: `ServerThread.run_with_exceptions` (`server.py:1197-1206`) waits for the submitter
first, so it exits when the stream ends, after committing the late lines that nothing will
schedule, with exit status 0 and no warning.

```shell
(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1   # prints "a"; exit 0 at ~8.8 s
hs list --remaining                               # echo b, never scheduled
```

A debug log of that run shows `Done (scheduler)` 1 s after `echo a` completes and 6.4 s before
the stream ends, then `Submitted 1 tasks` for `echo b`. The same holds for `hs server -` with a
separate `hs client`, and for a process substitution named as FILE. The pre-existing note in
`spec/cluster-launch-failures/REVIEW.md` ("a healthy database cluster on a pausing stdin stream
exiting 0 with tasks left (30 of 60)") is **this defect**: 30 lines, a 10 s pause, then 30 more,
into `hsx -N4`, exits 0 with 30 completed and 30 never scheduled, on SQLite, `postgres` and
`timescale` alike. Not affected: `--no-db` (both lines run; it has no scheduler), file input (read
in full before scheduling starts), and `--forever` and autoscaling clusters (they run until
stopped).

## Outcome / vision

The line a user submits is the line that runs. A task's text reaches the client as the same text,
and comes back recorded as the client reported it, whatever it looks like; timestamps still decode
as timestamps. A run fed by a stream keeps scheduling until the stream ends and every task it
delivered is done, then stops as it does today. Peers of 2.9.0a8 keep working with this release in
either direction.

## Acceptance criteria (the contract)

**Date-like task text (seed 1).**

- **R1** — WHEN a task's line is date-like text (at least `20261102`, `2026-11-02`, `2026W45`,
  `2026-W45-1`, `20261102T1200` and `2026-11-02 12:00`), the client SHALL run the command expanded
  from that exact text, under a user template and the default template, with and without a
  database, on Python 3.11 through 3.14: `hs list` shows the line unchanged, its command, and the
  command's own exit status, never a template error.
- **R2** — WHEN a client returns a result, the server SHALL record each text field of it (`command`,
  `outpath`, `errpath`, `client_host`, and the rest) exactly as the client sent it; a command
  `2026-11-02` is stored as `2026-11-02`.
- **R3** — Every timestamp a task carries on the wire (`submit_time`, `schedule_time`,
  `start_time`, `completion_time`) SHALL still be read as a time where it is read today, so a
  result still matches only the dispatch that produced it, `waited` and `duration` are still
  computed, and the relay hand-back still reverts its assignments and still ignores a stamp that is
  not a time.
- **R4** — The task wire SHALL be unchanged: what a server sends and a client returns is
  byte-for-byte what 2.9.0a8 sends and returns for the same task, so a 2.9.0a8 client runs against
  this release's server, and this release's client against a 2.9.0a8 server, each to completion
  with timestamps and result matching intact.

**Streamed input (seed 2).**

- **R5** — WHILE a non-`forever` `hs cluster` or `hs server` run with a database is still reading
  stream input, the run SHALL NOT stop scheduling, whatever the timing of the input: every task the
  input delivers, however late, is scheduled and reaches a terminal state before the run stops.
  `(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1` runs both, and the 30-pause-30 stream runs
  all 60.
- **R6** — WHEN the stream has ended and every task has reached a terminal state (complete,
  cancelled, or failed with no retry left), the run SHALL stop with the exit status it has on
  `develop`, within one scheduler back-off interval (bounded by `server.poll`) of the later of the
  two. A run whose input ends with nothing left to do still stops as it does on `develop`.
- **R7** — Runs that `develop` already ends correctly SHALL end as they do today: file input,
  `--no-db`, `--forever` and autoscaling runs; the stops added in `cluster-launch-failures` (every
  launched client failed: exit 5, without waiting for an open stream); and a group that fails
  permanently.

**Both.**

- **R8** — Tests SHALL pin both fixes: a unit test of the wire for every R1 form on the Python it
  runs under (the suite runs on 3.11 and 3.14), the R2 result path, and R4's encoding against a
  recorded 2.9.0a8 record; and a deterministic test that replays a pause in the input after the
  first task completes (R5) and the stop once it ends (R6), with no reliance on wall-clock races.
- **R9** — The user-visible change SHALL be documented per `AGENTS.md` § Docs: the cluster and
  server pages say a stream run ends once its input ends and every task is done, marked
  `.. versionchanged:: 2.9.0`. (The date-like fix restores documented behaviour and needs no prose
  of its own unless the plan finds a page that implies otherwise.)

## Non-goals (no-gos)

- **No wire or protocol change**, no version bump of the request protocol (R4). Consequently a
  mixed run keeps the old behaviour on the old side: a 2.9.0a8 client still fails a date-like line,
  and a 2.9.0a8 server still alters a date-like returned command (Clarifications).
- Submit-time coercion of `-t key:value` tag values (`smart_coerce`: `20261102` becomes an integer)
  is intended and separate.
- The `(cancelled)` wording in the client's template-error log line, though the task is recorded
  `-1001`, not cancelled.
- A failure-aware exit status for runs with failed tasks (declined, `ROADMAP.md` § Settled
  questions).
- The empty-input hang (`issues/empty-task-file-hangs.md`); R6 only requires this cycle not to
  change it.
- Signals (`SIGUSR1`/`SIGUSR2`) during a stream run, and a group that fails permanently while the
  stream is still open: unchanged (R7), not re-examined here.
- Clients that idle out on `-T` during a long pause in the input (Clarifications).
- A 2.8.x port: 2.8's scheduler has no submitter reference.
- The relay's own decoders, which already read timestamps by declared type; they stay as they are.

## Clarifications

Proposed in shaping on 2026-11-14 by the shaping agent; each is **pending the maintainer's sign-off
via the foreman** before `/hs-plan`.

- **Q:** Two seeds in one cycle, and what appetite? — **A:** One cycle, as the foreman asked: both
  are task-input fidelity (a submitted line that silently never runs), share the drive harness and
  the high-blast-radius review, and each fix is local. Each seed says `small` and together they
  stay `small` (header), because shaping did the evidence work a `big` research pass would. If the
  foreman prefers a split, R1–R4 are the pilot and R5–R7 the follow-up.
- **Q:** What does "the run stops before the input ends" mean precisely, and what must the server
  wait for? — **A:** The scheduler reaches FINAL while the submitter is still reading; the process
  then waits for the stream to end and exits 0 with the late tasks committed and unscheduled. The
  server must wait for both: the input has ended (end of stream, and every line read has been
  committed) **and** every task is terminal, as the stop branch already checks. A producer that
  never closes (`tail -f`, a pipeline that stays open) therefore keeps the run open, as a service;
  closing the input, Ctrl-C, or the `cluster-launch-failures` stops end it. That is the meaning of
  stream input, and the seed's own outcome.
- **Q:** Is the "30 of 60" exit noted by `cluster-launch-failures`' review this defect? — **A:**
  Yes; reproduced exactly (Problem, item 2) and included under R5.
- **Q:** Clients with an idle timeout. — **A:** With R5, a client started with `-T` shorter than a
  pause in the input can exit idle, and a task arriving after every client has gone has no one to
  run it; with launcher modes, all-zero client exits keep the cluster waiting by design
  (`cluster-launch-failures`). Today the same run instead stops early and drops the task. The
  default `-T` is never, and autoscaling relaunches. **Proposed:** out of scope; the plan verifies
  what happens and, if it is a wait with work left, records a seed rather than widening this cycle.
  The R9 docs may say that a stream with long pauses wants no `-T` or autoscaling.
- **Q:** How fast must the run stop once the input ends (R6)? — **A:** Within one scheduler
  back-off interval, which `server.poll` (default 30 s) bounds, matching how fast a newly committed
  row is noticed today. The plan may do better (waking on the end of input) but is not required to.
- **Q:** Mixed-version residue (R4). — **A:** Accepted. Keeping the wire byte-identical is the only
  way to keep 2.9.0a8 peers working in both directions, and it means an old peer keeps its old
  decoding. The fix lands on both sides in this release; mixed runs are transitional.
- **Q:** Should R2 cover fields a result does not write? — **A:** No. A result writes only
  `Task.result_columns` (invariants §3); R2 is about those text columns. `args` is never written
  from a result.

## Evidence plan

What `hs-review` should be able to run against the branch (PostgreSQL drives need the sandbox off
and `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`, with
`HS_TEST_TIMESCALE=community` for the `timescale` provider):

- **Unit:** the task codec round-trips every R1 form in `args`, `command`, `outpath`, `errpath` and
  each host field as text, and every timestamp field as a time (aware and naive, with and without
  microseconds); `Task.apply_results` stores a date-like `command` verbatim on SQLite and
  PostgreSQL; the encoded bytes of a task equal a recorded 2.9.0a8 encoding of the same task, and
  a recorded 2.9.0a8 bundle and result decode with timestamps intact (R4); the hand-back revert
  still reverts and still ignores a stamp that is not a time (R3). The stream test feeds the
  server a source that blocks on an event after its first line until the first task completes,
  then releases a second line and ends: both complete, and the run stops (R5, R6).
- **`.agents/factory/bin/temp_site.sh` drives, Python 3.11 and 3.14, SQLite, `postgres` and
  `timescale`:**
  - `printf '20261102\n2026-11-02\n2026W45\nhello\n' > dates.in; hsx dates.in -N1 -t 'echo {}'
    --initdb` prints all four lines; `hs list args exit_status command` shows exit 0 and the
    expanded command for each; the same with the default template (each line's command recorded
    verbatim, exit 127 for the unknown command), with `--no-db`, and on stdin.
  - `(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1` prints both and `hs list --remaining
    --count` is 0; the 30-pause-30 producer into `hsx -N4` completes 60; the same through `hs
    server -` with a separate `hs client`, and through a process substitution named as FILE.
  - Guards (R7): a file input and a `--no-db` stream run as on `develop`; `hs cluster --launcher
    false` fed by a stream that stays open still exits 5 promptly.
  - Timing: the stream run stops within `server.poll` of the input ending (R6).
- **Cross-version (R4):** this branch's server with a 2.9.0a8 client from the `2.9.0a8` tag, and
  the reverse, each running a small task file to completion with `hs list` showing schedule and
  completion times and no stale or dropped result.
- The suite under Python 3.11 and 3.14; the PostgreSQL and TimescaleDB tests with the variables
  above; the Sphinx `-W` build.

## Related materials

- Seeds: [`issues/date-like-args-coerced.md`](../../issues/date-like-args-coerced.md) (primary),
  [`issues/stream-run-stops-before-input-ends.md`](../../issues/stream-run-stops-before-input-ends.md).
- Sources of the seeds: `spec/writeback-scoped-conditional/research/01-assignment-token.md` (side
  finding; the `schedule_time` assignment token R3 must keep),
  `spec/group-advance-strands-lower-group/PLAN.md` §5 risk 1, and
  `spec/cluster-launch-failures/REVIEW.md` (the "30 of 60" note; #107's failure stops,
  `LoaderThread.leave`, `SubmitThread.put_end`).
- Related seeds: [`issues/empty-task-file-hangs.md`](../../issues/empty-task-file-hangs.md),
  [`issues/submit-comment-truncation.md`](../../issues/submit-comment-truncation.md) (another
  silent change to the submitted line).
- Invariants: `.agents/factory/invariants.md` §1 (task lifecycle predicates, contended writes), §3
  (results write only `result_columns`, only for their dispatch; group gating), §4 (`in_memory`),
  §5 and §6 (FSM halts, the server's shutdown order and the submit flush), §9 (payloads are JSON).
- Source: `src/hypershell/core/types.py` (`from_json_type`, `to_json_type`),
  `src/hypershell/data/model.py` (`Entity.from_json`, `deserialize_tasks`, `Task.columns`,
  `Task.apply_results`, `Task.revert_handed_back`), `src/hypershell/client.py`
  (`ClientScheduler.unpack_bundle`, `TaskExecutor`), `src/hypershell/server.py` (`Scheduler.start`,
  `Scheduler.load_bundle`, `Scheduler.submission_complete`, `Receiver.unpack_bundle`,
  `ServerThread`), `src/hypershell/submit.py` (`SubmitThread`, `LoaderThread`).
