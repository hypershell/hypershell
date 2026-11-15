# PLAN — Every task the input gives runs as given: date-like lines run as text, and a streamed run waits for its input to end

> **Status:** Draft for review · **Last updated:** 2026-11-14
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) ([`00-digest.md`](research/00-digest.md)). Every design element traces to a
> GOAL R-ID.

## 1. Summary

Two local changes and their tests. **Seed 1:** `Entity.from_json` decodes a JSON field as a time only
when the entity declares that column a `datetime`; every other field stays exactly as sent. Client and
server both decode through it, so a date-like line reaches the client as text and a date-like result
is written back as text, while the encoder, and so the wire, is untouched. **Seed 2:** the scheduler's
stop in `Scheduler.load_bundle` waits for the submitter to end: with every task terminal and the input
still open it backs off instead of stopping (or of reaching the failed-group halt), and it reads the
submitter before the counts. Both fit `small`: one method in `data/model.py`, one branch in
`server.py`, no option, no configuration key, no wire change. An optional third change (a wake at
the end of input, for a prompt stop) is its own cuttable phase.

## 2. Design

### 2.1 Decode by declared type (`src/hypershell/data/model.py`, R1–R4)

`Entity.from_json` (model.py:193-196) becomes:

```python
return cls.from_dict({key: from_json_type(value) if cls.columns.get(key) is datetime else value
                      for key, value in data.items()})
```

- **What still decodes as a time:** the `Task` columns declared `datetime` in `Task.columns`
  (`submit_time`, `schedule_time`, `start_time`, `completion_time`); for the generic method also
  `Client.connected_at`/`disconnected_at`, `Source.created`, `Zone.created` (none of which is decoded
  from the wire). `from_json_type`'s tolerance is kept for those fields: `None`, a number, or text that
  is not a time passes through unchanged, as today.
- **What changes:** every text column (`args`, `command`, `outpath`, `errpath`, `csvpath`, the host
  columns, ids, `fingerprint`) and anything else is passed as sent. `tag` (a JSON object) was already
  passed through. A key not in `columns` still raises `TypeError` in `cls(**data)`, as today.
- **Callers that inherit it, unchanged:** `deserialize_tasks` (model.py:294-297) → the server's
  `Receiver.unpack_bundle` (server.py:632-641) and the relay's upstream results, which land there;
  the client's `ClientScheduler.unpack_bundle` (`Task.from_json`, client.py:249); `Entity.unpack`.
- **Untouched:** `core/types.py` (`from_json_type`, `to_json_type`), `Entity.to_json`,
  `serialize_tasks`, the dispatch text built in `Scheduler.pack_bundle` and `Dispatcher._add_inbound`,
  and `Task.revert_handed_back`'s direct `from_json_type(stamp)` (model.py:1123), which keeps both the
  revert and "ignore a stamp that is not a time".
- **Wire (R4):** encoding is untouched, so the bytes a server sends and a client returns equal
  2.9.0a8's. Research encoded a fixed task with the `2.9.0a8` tag's code and with HEAD and got equal
  bytes for `serialize_tasks` and the dispatch text; the tests pin recorded 2.9.0a8 literals.
- **No `client.py` or `server.py` change** for this seed; the client's template expansion receives a
  `str` and works as for any other line.

### 2.2 The stream stop (`src/hypershell/server.py`, R5–R7)

In `Scheduler.load_bundle` (server.py:246-294), the branch after `next_tasks` returns nothing:

```python
input_ended = self.submission_complete()  # Read before the counts: an ended submitter has committed every row.
if (not self.forever_mode and Task.count() > 0 and Task.count_remaining() == 0
        and Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0):
    if input_ended:
        return SchedulerState.FINAL
    return self.back_off(generation, 'all tasks done, input still open')
group_info = Task.increment_group(...)   # unchanged from here, ending in self.back_off(...)
```

- `back_off(generation, reason)` is the existing tail (server.py:288-294: grow `poll` unless the
  last wait was cut short, trace, `wait_need`, return LOAD), moved into a method both branches use.
- **Why a branch, not a swapped condition:** with the condition alone, an all-done run with the input
  open falls through to `Task.increment_group`, which, with the default single attempt and any failed
  task, returns `viable=False` and the scheduler stops at `log.critical('Failed task group …')`,
  dropping the late line (research/02; the drive's `fail-pause` mode). The branch never calls
  `increment_group` or `report_held` while nothing is incomplete, so it logs nothing new.
- **Order:** `submission_complete()` is read first. `SubmitThread.run_with_exceptions` ends only after
  `committer.join()`, which follows the committer's final commit, so a submitter seen ended means
  every row it read is visible to the counts that follow. Today the counts come first and a submitter
  that commits and ends between them is missed.
- `Task.count() > 0` stays, so an input that ends with nothing in the database behaves as on
  `develop` (`issues/empty-task-file-hangs.md` stays a separate seed; R6's last sentence).
- `Scheduler.startup_phase` (server.py:143, 171, 261, 267) is removed: its only reader was the old
  condition. `restart_mode` stays (ServerThread and tests pass it). `HeartMonitor.startup_phase` is a
  different attribute and is untouched.
- **Late lines:** a late line in the current group is loaded by `next_tasks`; in another group,
  `count_remaining > 0` sends the pass through `increment_group` as today (stream input is best
  effort; a late lower group warns once). A failed current group still halts before a late higher
  group, as with file input (R7, invariants §3).
- **Behaviour changes (all intended, R5):** a stream run, with or without `--restart`, keeps
  scheduling until the input ends; a producer that never closes keeps the run open (GOAL gate ruling).
- **Unchanged (R7):** file input (scheduling starts only after the whole file is submitted, so the
  submitter has ended before the first pass); `--no-db` (`in_memory`, no scheduler); `--forever` and
  autoscaling (`forever_mode`, the branch is never entered); a permanently failed group with work
  remaining (the `increment_group` path is unchanged); #107's stops (`ServerThread.stop` halts the
  FSM directly; `LoaderThread.leave`/`SubmitThread.put_end` are untouched).
- **`cluster/watch.py::scheduled_work`** keeps counting committed work only; its docstring gains one
  sentence saying input still being read is not counted. Consequence, probed: every launched client
  failed, every committed task done, input open → the cluster keeps waiting; a late line that lands →
  exit 5 at once (on `develop`: exit 0 at the stream's close, the line dropped); the input closes →
  the run stops as usual.

### 2.3 Optional: a prompt stop at the end of input (`server.py`, `core/dispatch.py`, R6)

Without it, R6 holds within one back-off interval, which `server.poll` bounds (default 30 s; probed:
17 s for `a`, 8 s pause, `b` at the default). With it, `ServerThread.run_with_exceptions` calls
`self.scheduler.machine.wake()` (guarded for `scheduler is None`) between `wait_submitter()` and
`wait_scheduler()`. `Scheduler.wake()` mirrors `halt()`: it calls a new public `Dispatcher.wake()`,
which bumps the need generation under the dispatcher's lock and notifies, so a wake that lands before
the scheduler's wait still ends it (lossless). No `submit.py` change, no reordering of the shutdown
sequence (§6), not on the RPC surface (`DISPATCH_EXPOSED` unchanged). It is `hammerable: true`; the
foreman may cut it at the plan gate.

### 2.4 Docs and the record (R9)

- `docs/cli/cluster.rst` and `docs/cli/server.rst`: one `.. versionchanged:: 2.9.0` beside the
  existing 2.9.0 markers: a run fed by a stream (standard input, a pipe, a named pipe) ends once that
  input ends and every task is done. `docs/_include/task_groups_alt.rst` (stream scheduling, already
  discussing `-T`) gains one sentence: a stream with long pauses wants no `-T` (or autoscaling).
- `AGENTS.md` and `.agents/factory/invariants.md`, in lockstep: §6/§9 — a payload's fields decode by
  their declared column type (only `datetime` columns as times); §3 — a non-`forever` scheduler stops
  only once its submitter has ended (read before the counts) and every task is terminal.
- Seeds (`issues/` + `ROADMAP.md`): the `-T` idle exit during a pause; the `-b N>1` commit wait
  before a pause (research/02, side finding).
- No prose for the date-like fix: no page documents the wire, and the help already says the default
  template runs the line verbatim (research/01 Q6).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `Entity.from_json` by declared type (§2.1) — the client's `Task.from_json` keeps `args` as text; drive modes `dates`, `dates-default`, `dates-stdin`, `dates-nodb` |
| R2   | the same decode on the server's `deserialize_tasks`; `Task.apply_results` binds the text (SQLite, `postgres`, `timescale` probed) |
| R3   | the four `datetime` columns still decode via `from_json_type`; `revert_handed_back` unchanged |
| R4   | encoder untouched; unit test against recorded 2.9.0a8 bytes; drive `cross-old-client`/`cross-old-server` |
| R5   | `load_bundle` waits while the submitter is alive, without `increment_group` (§2.2); drive `pause`, `burst`, `server`, `fifo`, `fail-pause` |
| R6   | FINAL once `submission_complete()` (read first) and every task terminal; latency ≤ `server.poll` (§2.2), prompt with §2.3 |
| R7   | the branch is reached only with nothing incomplete and no retries, outside `forever_mode`, with a database; `scheduled_work` unchanged; drive `file`, `nodb-stream`, `launch-false` |
| R8   | `tests/test_task_wire.py`, `tests/test_stream_input.py`, a PostgreSQL twin in `tests/test_writeback_postgres.py` |
| R9   | §2.4 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(sections §1, §3, §4, §5, §6, §9, §11, §12 named as touched; the intent is sane against each) and again
on this design:

- **§1 task lifecycle.** No predicate is re-implemented: the stop reuses `Task.count`,
  `count_remaining`, `count_retryable` exactly as today. No new task-row write, so the
  `CONTENDED_RERUNS` rule is not engaged; `apply_results`' conditional write is unchanged and receives
  the same `schedule_time` datetime token as before.
- **§2 `exit_status`.** No new sentinel. Date-like lines stop producing `-1001` because they no longer
  fail expansion; nothing reinterprets the ranges.
- **§3 retry model / group gating.** Results still write only `result_columns`, only for their
  dispatch (R2 is about those text columns). The retry chain and `count_retryable`'s `CANCEL_STATUS`
  filter are untouched. The failed-group halt is unchanged wherever work remains; the new branch only
  defers a stop that `develop` takes earlier (and with nothing incomplete there is no group to halt).
  Stream input stays best effort for late lower groups.
- **§4 `in_memory`.** No scheduler exists there; the decode change touches no DB write.
- **§5 FSM + Thread.** No new state; LOAD still returns a state on every path; the back-off is the
  existing finite `wait_need`; `halt()` still ends that wait at once; `check_signal()` stays at the top
  of LOAD, so USR1/USR2 still stop the scheduler during an open input. §2.3's wake uses the same
  condition as `halt()`.
- **§6 shutdown order.** Unchanged: submitter → scheduler → `dispatcher.finish()` → … The optional
  wake runs between the existing `wait_submitter()` and `wait_scheduler()` and changes no order.
  Remote payloads remain JSON through `serialize_tasks`/`deserialize_tasks`.
- **§9 payloads are JSON.** Same encoding, same framing; only the decoder's choice of which fields to
  interpret narrows.
- **§11 cluster orchestration.** The exit-5 rule reads committed work through `scheduled_work`, as
  before; launched-client argv untouched.
- **§12 conventions.** Comments declarative, no R-ids in source; tests tagged `unit`/`integration`;
  docs marker `2.9.0` with `-W` build; no CLI change, so no help snippet or completion change.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A back-off branch in `load_bundle` instead of a one-term change to the stop condition | the one-term change routes an all-done run with a failed task into the failed-group critical stop while the input is open, still dropping the late line | it fails R5 for any run with a failed task (default single attempt); reproduced by `fail-pause` |
| Optional `Dispatcher.wake()` (§2.3) | stop promptly at the end of input, not after up to `server.poll` | not required by R6; kept as a separate cuttable phase so the core fix does not depend on it |

## 4. Rabbit holes (resolved)

- **Does anything rely on a text field being converted?** No caller or test; unknown keys still
  raise; every time a caller reads is a declared `datetime` column
  ([`research/01-wire-decode.md`](research/01-wire-decode.md)).
- **Python version differences.** All six R1 forms parse as dates on 3.11 and 3.14, so every form fails
  on `develop` on every supported Python and the tests need no version branch (research/01 Q4).
- **Is the 2.9.0a8 wire really the current wire?** Yes: the tag's encoder and HEAD's give equal bytes
  for a fixed task (research/01 Q5); the cross-version drives pass on `develop` in both directions.
- **The failed-group trap in the obvious stream fix** → the branch design (research/02; §2.2).
- **A new hang?** None: every submitter source ends at `StopIteration`; JSON input is read in full and
  treated as file input; the remaining hangs (a producer that never closes, a committer that dies)
  are intended or pre-existing ([`research/02-stream-stop.md`](research/02-stream-stop.md) Q5).
- **#107 interplay** → `scheduled_work` unchanged; probed both ways (§2.2).

Planner probes (a scratch copy of `develop`'s `src/` outside this tree, with the §2.1 decode and first
the plain condition swap, then the §2.2 branch; `develop` itself for the "before"):

| Probe | `develop` | prototype |
|-------|-----------|-----------|
| `dates` (six R1 forms + `hello`, `-t 'echo {}'`), 3.14 and 3.11, SQLite | only `hello` printed; three dates `-1001`, command null | all printed; each `echo <line>`, exit 0 |
| `dates-default` (default template) | dates `-1001` | each command recorded verbatim, exit 127 |
| `dates-stdin`, `dates-nodb` | only `hello` | all printed |
| R2: a result `command='2026-11-02'` through `deserialize_tasks` + `apply_results` | `postgres`: stored `'2026-11-02 00:00:00'` | stored `'2026-11-02'` on SQLite, `postgres`, `timescale` |
| `pause` (`a`, 8 s, `b`; `-N1`) | `a` only; 1 remaining; exit 0 at 8.7 s | both; 0 remaining; exit 0 at 17 s |
| `burst` (30, pause, 30; `-N4`), SQLite and `postgres` | (shaping: 30 of 60) | 60 of 60 |
| `pause` on `timescale` | (shaping: `a` only) | both, 17 s |
| `server` (`hs server -` + `hs client`), `fifo` | (shaping: `a` only) | both; 10 s and 16 s |
| `fail-pause` (`false`, 8 s, `echo b`) | `b` never runs | branch: `b` runs, one failed, no critical log (the plain swap, not probed, reaches the failed-group halt by research/02) |
| `nodb-stream`, `file` | pass | pass |
| `launch-false` (input left open) | exit 5 at 5 s | exit 5 at 5 s |
| `hsx -N1 -T 3`, `a`, 8 s, `b` (client idles out in the pause) | (exit 0, `b` dropped, by the seed) | still running 53 s after the input ended, `b` unscheduled (seed, out of scope) |
| clients run then exit 1 (`-T 2`), input open, late line | waits; late line dropped; exit 0 at close | exit 5 three seconds after the late line |
| `cross-old-client`, `cross-old-server` (2.9.0a8 sources) | pass | (P4) |
| existing suite, 3.14, SQLite (`pytest -q`, serial) | — | branch prototype: 2832 passed, 109 skipped; no existing test changes |

## 5. Risks & open questions

- **Runs that never closed their input now stay open.** A wrapper holding standard input open (a
  `tail -f`, a pipeline kept alive) used to end once its tasks were done; now it waits, as ruled at the
  GOAL gate. The R9 note says so. Ctrl-C and #107's stops still end it.
- **`-T` during a pause** (out of scope): an idle client exits 0, and a late line then waits with no
  one to run it (LocalCluster; launcher modes by design keep waiting on all-zero exits). On `develop`
  the line is silently dropped instead. Probed on the prototype: `a`, 8 s, `b` into `hsx -N1 -T 3`
  printed `a`, then was still running 53 s after the input ended with `b` unscheduled (killed). A
  silent drop becomes a visible wait; recorded as a seed in P4 (GOAL Clarifications), and the R9
  sentence tells stream users to leave `-T` off. The foreman should weigh whether that seed is next.
- **`-b N>1`**: a line read before a pause is committed only when the next line arrives or the input
  ends (pre-existing, `hs submit` too); not a stop defect, recorded as a seed.
- **Mixed versions** (accepted): a 2.9.0a8 client still fails a date-like line, and a 2.9.0a8 server
  still alters a date-like returned command.
- **Overlap with #109 (`fix/contended-writes`, unmerged).** Its `model.py` hunks are in `Task`
  (around `Task.next`, `__write_rows`, `cancel_all`, the retry mark) and `Source`, from line ~758
  down; this cycle's `model.py` edit is `Entity.from_json` (lines 193-196) only — no textual overlap,
  and no semantic one (neither touches the other's paths). #109 also edits `AGENTS.md` (Retry model,
  Data layer specifics, footguns), `.agents/factory/invariants.md` (§1, §3 retry bullets) and
  `ROADMAP.md`; P4 here edits `AGENTS.md`/`invariants.md` in other paragraphs (§3 group-gating
  paragraph, §6, §9) and appends to `ROADMAP.md` — expect at most an adjacent-line conflict in
  `ROADMAP.md`/`invariants.md` §3, resolved by keeping both. Tests: #109 changes
  `test_contended_*.py` and `test_source.py`; this cycle adds new files and one test to
  `test_writeback_postgres.py`, which #109 does not touch. Whichever lands second rebases cleanly.
- **Open for the foreman:** keep or cut P3 (prompt stop); confirm `scheduled_work` stays committed-work
  only (a run whose launched clients all failed waits on an open, silent input rather than exiting 5).

## 6. Verification strategy

- **Unit (`@mark.unit`)**, `tests/test_task_wire.py`: every R1 form in every text column through both
  decode paths; every time column naive/aware, with/without microseconds; tag values; odd values in a
  time field; unknown keys still rejected; every `DateTime` column declared `datetime`; the encoding of
  a fixed task equals recorded 2.9.0a8 bytes (dispatch text and result bundle; generator committed as
  `tests/fixtures/make-task-wire-2.9.0a8.sh` with a README provenance row); the recorded 2.9.0a8
  dispatch and result decode with text and times intact; template expansion of each form;
  `apply_results` stores a date-like command verbatim (SQLite; PostgreSQL twin under
  `HS_TEST_POSTGRES`, both providers).
- **Unit**, `tests/test_stream_input.py` (driving `Scheduler.load_bundle` in-process, a fake submitter
  whose `is_alive()` the test flips, the `test_group_order.py` helpers): open input holds the stop
  (with and without `restart_mode`), a late line runs, the end of input stops; nothing left at the end
  still stops; a submitter that ends between the counts is seen; a failure with open input waits for
  the late line with no critical log; a failed group still halts before a late higher group;
  `scheduled_work` ignores an open input with nothing committed. **Integration**: a `LocalCluster` fed
  by a generator that yields its second line only after a scheduler pass has seen the first task
  complete (a wrapped `load_bundle`; no sleeps) — both complete and the run ends.
- **Drives** — [`drive/fidelity_drive.sh`](drive/fidelity_drive.sh) in a `temp_site`, modes above, on
  3.14 and 3.11, SQLite, and with `FD_PROVIDER=postgres|timescale` (sandbox off).
- **Suite** on 3.11 and 3.14; PostgreSQL/TimescaleDB tests with `HS_TEST_POSTGRES`,
  `HS_TEST_TIMESCALE=community`; Sphinx `-W`.

## Plan-gate rulings (2026-11-15)

Foreman, gate judge under the maintainer's mandate. Approved as planned.

1. **P3 is kept.** The run stops promptly at the end of input, through a public wake that does not
   touch the shutdown order and is not exposed over RPC.
2. **`scheduled_work` keeps counting committed work only.** An open but silent input is not counted
   as work, so runs that exit 0 on `develop` do not turn into exit 5.
3. **`-T` during a pause is now a visible wait.** It used to be a silent drop. This is accepted as
   the GOAL ruled. P4 files both seeds: the `-T` wait with work left, and the `-b N>1` commit wait
   before a pause. The foreman schedules the `-T` seed after the agreed pre-2.9.0 list.
4. **A producer that never closes keeps the run open,** as ruled at the GOAL gate. R9's note covers
   it.

## Review-cycle-1 ruling (2026-11-15)

Foreman, gate judge under the maintainer's mandate. F1 (LOW) is fixed directly, as wording. The §3
stop sentence in `AGENTS.md` and `invariants.md` is scoped to the out-of-work stop, and it names the
two other stops that apply whether the input is open or not: a group failing permanently, and
SIGUSR1/SIGUSR2. Both are already in the code and pinned by tests.

The reviewer read the finding as triggering the human gate, because it concerns the recorded text
of a lifecycle invariant. The rubric's mandatory gate lists the high-blast-radius core and the
security and DB-lifecycle invariants (auth minimums, TLS posture, the CANCEL_STATUS and exit-status
filters, `completion_time` terminality, one retry row per failure, the `in_memory` guards).
Scheduler stop conditions are not among them; the review's META F3 says so. This finding corrects
prose so it matches code the review found correct. The foreman rules the gate not triggered and
reports the ruling to the maintainer. No code or test change. Review cycle 2 verifies this.

## Review-cycle-2 rulings (2026-11-15)

The review's human gate was triggered by F2, a CONFIRMED race in `server.py`. The maintainer signed
off in session (2026-11-15) to fix both findings. Review cycle 3 is the last.

- **F2.** With the input still open, `Scheduler.load_bundle` must not halt on a group that
  `increment_group` reports not viable without first re-checking the out-of-work condition. Then a
  result that lands between the first count and `increment_group` makes the scheduler back off,
  as with every other open-input case, instead of going FINAL. A permanently failed group with a
  task waiting in another group still halts as R7 requires
  (`test_failed_group_still_halts_late_higher_group` stays green).
  - **Test:** a deterministic test of the interleaving: the failure written right after
    `count_remaining` returns, with the input open. It must reach no FINAL while the input is open,
    and the late line must run. Red on `1a28fb9`, green after.
  - **Drive:** the shipped `fail-pause` drive must also no longer show the CRITICAL on a PostgreSQL
    provider. Repeat it enough times on TimescaleDB to show this.
- **F3.** The §3 sentence in `AGENTS.md` and `invariants.md` stops saying "at once". The failed-group
  halt and the signals take effect on the scheduler's next pass, within `server.poll`. Also make the
  "a failed task with no retry left never ends a stream run early" claim true as built once F2 is
  fixed, or reword it.
