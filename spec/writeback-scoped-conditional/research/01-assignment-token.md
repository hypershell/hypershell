# Research 01: `schedule_time` as the assignment token

**Verdict: keep it.** `schedule_time` works as the assignment token. It round-trips exactly on
the wire and on SQLite, which was checked empirically. On PostgreSQL the same follows from the
types, though it was not run here. It changes on every dispatch, revert, and cancel, and no client
version writes it. A conditional
`UPDATE ... WHERE id = :id AND schedule_time = :echoed AND completion_time IS NULL` with a
rowcount check meets R1 to R5, R7, and R8 with no schema change and no wire change. The risks
below are all narrow.

## 1. Wire round-trip

- Encoding: `to_json_type` uses `value.isoformat(sep=' ')` and `from_json_type` uses
  `datetime.fromisoformat` (`core/types.py:58-72`). The offset survives when the value is aware.
  When the microsecond is 0, isoformat drops the `.ffffff` part, but `fromisoformat` restores
  microsecond 0. The entity hooks are `to_json` and `from_json` (`data/model.py:132-144`), and
  the bundle hooks are `serialize_tasks` and `deserialize_tasks` (`model.py:237-245`).
- Session: `sessionmaker(bind=engine)` (`data/core.py:268`) keeps the default
  `expire_on_commit=True`. `Task.next` stamps `datetime.now().astimezone()` and then commits
  (`model.py:641-647`), which expires the attributes. When `pack_bundle` calls
  `serialize_tasks(self.tasks)` (`server.py:246-249`), the attributes are reloaded from the
  database. So the bundle carries the value as the database returns it:
  - **SQLite:** a naive local wall-clock time. The probe printed
    `echoed: datetime(2026, 11, 2, 17, 17, 7, 212078) tzinfo=None` and the wire value
    `"2026-11-02 17:17:07.212078"`.
  - **PostgreSQL** (`timestamptz`): an aware value in the session's TimeZone.

  The design holds even if `expire_on_commit` changes later: an aware echo carrying the local
  offset also matched (rowcount 1).

## 2. What a client writes

The current code is `client.py:718-737`, `751-763`, `779-786`, and `806-808`. Tag `2.8.3`
matches line for line (`git show 2.8.3:src/hypershell/client.py`, lines 705-791). A client sets
exactly these columns: `client_id`, `client_host`, `command`, `start_time`, `completion_time`,
`exit_status`, `waited`, `duration`, `outpath`, `errpath`, `csvpath`, `cores_max`, and
`memory_max`. That is R1's result set.

- The only code that writes `schedule_time`, `server_id`, or `server_host` on a Task object is
  `Task.next` (`model.py:643-645`). Grep over `src/` finds no client-side write in either version.
- `waited = int((start_time - submit_time.astimezone()).total_seconds())`, computed on the client
  (`client.py:755`). It is not set on the template-error and resource-error paths.
- Every task a client returns goes through `put_local`, and every path into `put_local` sets
  `completion_time`:
  - template error, which sets `exit_status = TASK_TEMPLATE_ERROR` (`client.py:726-730`)
  - resource error, which sets `TASK_RESOURCE_ERROR` (`client.py:731-737`)
  - normal completion (`client.py:782-783`)

  No client version returns a task with `completion_time` None. A task the client never ran is
  not returned: either it is still in the local queue at shutdown, or its executor halted because
  the process ignored SIGKILL (`client.py:858-861`). An empty bundle (`[]`) can arrive after
  `bundlewait` expires; it is harmless. The server should still decide what to do with a returned
  copy whose `completion_time` is None: refuse it, or apply it without logging a completion.

## 3. Storage equality (proved on SQLite)

- `DATETIME = DateTime(timezone=True)` (`model.py:98`), unchanged since at least 2.4.0. The SQLite
  dialect ignores `timezone`. Its bind processor formats the fields of whatever value it is given
  as `YYYY-MM-DD HH:MM:SS.ffffff`, with no offset. Naive and aware-local binds both produced
  `2026-11-02 17:17:07.213367`, and a microsecond-0 value bound as `...:07.000000`. The raw stored
  value was `('2026-11-02 17:17:07.212078', 'text')`.
- PostgreSQL `timestamptz` stores a UTC instant at microsecond resolution, the same resolution as
  Python's datetime, so comparing aware values is exact by instant. A naive bind is read in the
  session's TimeZone, the same zone the value was returned in. This is from the docs; no
  PostgreSQL server was available locally.
- Probe: `$TMPDIR/wb-plan/token_check.py` and `token_usec0.py`, outside the repo. They used an
  isolated site and `ensuredb(auto_init=True)`, and ran
  `Session.execute(update(Task).where(id, schedule_time == echoed, completion_time IS NULL).values(...).execution_options(synchronize_session=False))`.

| Case | rowcount |
|---|---|
| Echo shifted by +1 µs | 0 |
| Aware echo, local offset (same wall clock) | 1 |
| Aware echo converted to UTC (same instant, different wall clock) | **0** |
| Stale echo after a revert (`schedule_time` NULL) | 0 |
| Stale echo after a revert and a second `Task.next` | 0 |
| Current echo (applied) | 1 |
| Replay of the current echo once completed (R5) | 0 |
| Echo after `Task.cancel` | 0 (`CANCEL_STATUS` kept) |
| Microsecond-0 value through the wire (`"2026-11-01 01:30:00"`) | 1 |

- **R5 vs R2.** A rowcount of 0 needs a follow-up read to classify. If `schedule_time` equals the
  echo and `completion_time` is set, the result is a duplicate (R5). Anything else is a stale
  result (R2): reverted (NULL), rescheduled, or cancelled (cancel restamps `schedule_time`).
- **Batching.** ORM bulk-by-primary-key updates cannot carry extra WHERE criteria, and an
  `executemany` reports only a total rowcount. Use one `UPDATE` per task inside one transaction.

## 4. Distinctness

- **Same row, two dispatches.** A second dispatch needs a revert first (`revert_all`,
  `model.py:721-741`; `revert_interrupted` on server start, `server.py:196-197`;
  `hs task update --revert`, `task.py:1112-1125`), and then a fresh `datetime.now()`. Under
  eviction the two are at least `evict_after` seconds apart. Clock resolution is microseconds on
  Linux and macOS. Any coarser clock would still need a revert and a re-dispatch inside one tick.
- **DST fall-back on SQLite.** Wall-clock storage repeats an hour, but a collision needs the same
  wall-clock value to the microsecond, exactly one hour apart. An NTP step backwards needs the
  same exact coincidence. Both are negligible. Nothing on the path calls `.astimezone()` on the
  naive echo, which could shift a value in the ambiguous hour.
- **Other code that writes `schedule_time`:**
  - `cancel_all` (`model.py:757-771`) and `hs task update --cancel` (`task.py:1106-1110`) set it
    to now and also set `completion_time`, so both are fenced twice.
  - `hs task update --revert` sets it to NULL.
  - `hs task update schedule_time=...` sets it to an arbitrary value (`task.py:1200-1213`). A
    late result is then refused, which is acceptable as operator intent.
  - The submit and cluster paths do not write it.
- **Server restart.** `Scheduler.start` reverts interrupted rows, so a late result from before
  the restart is now refused, where develop applies it. This is an R2 case by definition. Note it
  for the changelog.

## 5. Relay and alternatives

- **What relay needs.** The relay's local `Task.next` restamps `schedule_time`, so the relay must
  keep the **upstream** value separately and return it exactly. Keeping the received task JSON and
  overlaying only the result columns satisfies that. The probe shows the exactness matters: a
  value converted to another zone stops matching, so the relay must not normalise the timezone.
  Storing the value in a SQLite DATETIME column and reading it back gives a naive wall-clock time.
  That still matches a SQLite upstream, but against a PostgreSQL upstream it relies on the naive
  wall-clock being read in the session's TimeZone, which is ambiguous in the fall-back hour.
  `relay.md` R6 already requires "`schedule_time` as received in the bundle". After this fix,
  the server ignores the returned `server_id` and `server_host` (R1), so "restore the stamps"
  reduces to "echo `schedule_time`". The `client_id` attribution can be the leaf's (R4).
- **Other echoed fields.**
  - `server_id` and `server_host` are identical for every dispatch by one server, so they are not
    distinct.
  - `client_id` is excluded by R4, and no-confirm mode never stamps it.
  - `(schedule_time, server_id)` adds nothing against reverts. It only separates two servers on
    one database, which is a non-goal.
  - `tag` is wire-compatible, but it is user data, and a token there would show up in exports.
- **A new field breaks older peers.** `from_dict` is `cls(**data)` (`model.py:137-139`, the same
  in 2.8.3). An unknown key raises `TypeError: 'brand_new_field' is an invalid keyword argument
  for Task` (probe). A new field would therefore break any older client, and any older server
  receiving from a newer client. That is a direct R9 violation.

  The same mechanism means version pairings are already narrow. 2.9 sends `source` and
  `fingerprint` (added in 2.9.0a1) and `part` (added in 2.9.0a2), and 2.8.3's `Task` has none of
  these columns. So 2.8.x with 2.9.x, and 2.9.0a1 with 2.9.0a2, already fail on develop. This is
  from reading the code; no mixed-version pair was run. The pairings R9 must protect are
  same-schema ones.

## Side finding (pre-existing, outside scope)

`from_json_type` converts any string longer than 5 characters that `fromisoformat` accepts. On
Python 3.11 and later that includes basic-format strings like `"20261102"`, which becomes
`2026-11-02 00:00:00`. A task whose `args` looks like a date therefore reaches the client as a
datetime, and develop's whole-row writeback then rewrites `args`. R1 stops the writeback damage to
`args`, but the client-side `command` expansion still sees the coerced value. Worth a seed.

## Risks to carry into TECH.md

1. Any future change that normalises the timezone (aware vs UTC) on the dispatch or return path
   breaks matching. Add a test that pins the echo-equality property on SQLite.
2. Relay must echo the upstream `schedule_time` verbatim (see §5).
3. Run the Receiver's conditional UPDATE with `synchronize_session=False`, one statement per task
   and one commit per bundle, and do a follow-up read only when rowcount is 0, to separate R5
   from R2.
4. Decide how to handle a returned copy with `completion_time` None (no current client sends one).
5. The PostgreSQL equality is reasoned from the types, not run. CI with PostgreSQL, if any,
   should run the token test.
