# 00 — Digest: the decisions research settled

Two briefs, both read-only, run because both fixes touch high-blast-radius files
(`data/model.py`, `server.py`): [`01-wire-decode.md`](01-wire-decode.md) and
[`02-stream-stop.md`](02-stream-stop.md). The planner probed both designs against a scratch copy of
`develop`'s sources (never this tree's `src/`), on SQLite, `postgres` and `timescale`; the probe
results are in [`PLAN.md`](../PLAN.md) § 4. One recommendation per question:

1. **Decode by declared type, in one place.** `Entity.from_json` runs a value through
   `from_json_type` only when `cls.columns[key] is datetime`; every other value stays as sent.
   `deserialize_tasks`, `Entity.unpack`, the client's `ClientScheduler.unpack_bundle` and the server's
   `Receiver.unpack_bundle` all inherit it. `from_json_type`, `to_json*`, `serialize_tasks` are
   untouched, so the wire is byte-identical (proved against the `2.9.0a8` tag's own encoder).
2. **The fields that still decode as times** are `submit_time`, `schedule_time`, `start_time`,
   `completion_time`. `Task.revert_handed_back` keeps its direct `from_json_type(stamp)` and so still
   ignores a stamp that is not a time. A test pins that every `DateTime` column is declared
   `datetime` in `columns`, so a future column cannot silently decode as text.
3. **The stream stop is a branch, not a swapped condition.** In `Scheduler.load_bundle`, read
   `submission_complete()` before the counts; when every task is terminal and nothing is retryable,
   stop if the input has ended, otherwise back off **without** `increment_group`. Swapping the
   condition alone (the obvious one-liner) sends an all-done run with a failed task into the
   `Failed task group` critical stop while the input is open, which still drops the late line
   (brief 02; reproduced on `develop` by the drive's `fail-pause` mode). `Scheduler.startup_phase`
   becomes dead and is removed; `restart_mode` stays (callers and tests pass it).
4. **Read the submitter first.** `SubmitThread` ends only after its committer has committed every
   row, so `not is_alive()` read before the counts proves those counts include the last rows. Today's
   order (counts first) can miss a submitter that commits and ends between them.
5. **#107's failure stops stay as they are.** `cluster/watch.py::scheduled_work` keeps counting
   committed work only (its docstring says so). An open input with no work committed is not work no
   client can run; counting it would turn a `develop` exit 0 into exit 5 depending on the timing of
   the stream's close. A late line that lands after every launched client failed is work, so that
   run now exits 5 (on `develop` it exited 0 and dropped the line).
6. **Stop latency** is bounded by `server.poll` with no new plumbing. An optional wake at the end of
   input (`ServerThread` → `Scheduler.wake()` → a public `Dispatcher.wake()`, about 8 lines, no
   `submit.py` change) makes it prompt; it is planned as a separate, cuttable phase.
7. **Docs (R9):** one `.. versionchanged:: 2.9.0` each in `docs/cli/cluster.rst` and
   `docs/cli/server.rst`; `docs/_include/task_groups_alt.rst` may say long pauses want no `-T`. The
   date-like fix needs no prose: no page documents the JSON wire, and the help already says the
   default template runs the line verbatim.
8. **Out of scope, recorded as seeds:** a client idle past `-T` during a pause leaves a late line
   with no one to run it (the run waits with work left); with `-b` above 1, a line read before a
   pause is not committed until the next line or the end of input (pre-existing, also `hs submit`).
