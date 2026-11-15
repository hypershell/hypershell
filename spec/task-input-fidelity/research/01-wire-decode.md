# Research 01 — decoding the task wire by declared type

Read-only research for `hs-plan` (R1–R4, R8). Base: `ae83e66` (22 commits after the `2.9.0a8` tag,
`2ffb5cc`). Probes ran on Python 3.11.18 and 3.14.6.

## Findings

**The wire, both directions.** Server → client: each staged task is `json.dumps(task.to_json())`
(`server.py:382`; `dispatch.py:146-149` splices the texts into the reply), or, with `--no-db`, the
submitted record re-dumped by `Dispatcher._add_inbound` (`dispatch.py:292-298`). The client loads the
reply (`client.py:228`) and decodes each record with `Task.from_json` (`client.py:249`). Client →
server: `serialize_tasks` (`client.py:419`) → `Receiver.unpack_bundle` → `deserialize_tasks`
(`server.py:634`, `model.py:294-297`) → `Task.from_json`. Relay upstream results are pre-encoded
record texts (`relay/upstream.py:872`, `relay/store.py:401-420`) that land in the same
`deserialize_tasks`.

**Every decode caller** (src and tests):

| Caller | Datetimes it relies on | Kept under column-typed decode |
|---|---|---|
| `client.py:249` `ClientScheduler.unpack_bundle` | `submit_time` for `waited` (`client.py:812-813`); `schedule_time` carried back as the assignment token; `start_time` (`client.py:560`) is set locally first | yes: both are `datetime` in `Task.columns` |
| `server.py:634` via `deserialize_tasks` | `schedule_time` binds `r_schedule_time` (`model.py:1243`); `start_time`/`completion_time` bind `DateTime` columns (`model.py:1225-1231`) | yes |
| `Entity.unpack` (`model.py:207-209`) | none; no caller in src or tests | n/a |
| `Task.revert_handed_back` (`model.py:1123`) calls `from_json_type(stamp)` directly | the stamp must read as a time; a non-time is ignored (`:1124-1126`) | untouched by the fix (does not go through `from_json`) |
| tests: `test_writeback.py:82,121,504,510`, `test_writeback_statement.py:78,117,228`, `test_writeback_postgres.py:107,133,184,231`, `test_contended_recovery.py:151,186`, `test_contended_write.py:110,141`, `test_relay_return.py:143,571` | `schedule_time` token and result times | yes; all use plain args (`echo …`, `exit 3`) |

Other `fromisoformat` readers already decode by declared field and stay as they are:
`core/heartbeat.py:145`, `core/topology.py:345`, `relay/store.py:202-205` (`local_row`, by
`Task.columns`). `task.py:814,824` build from DB rows via `from_dict`, not JSON.

**`columns` maps.** Checked at runtime: for `Task`, `Client`, `Source`, `Zone`, `list(cls.columns)`
equals the mapped columns in order. Declared `datetime`: `Task` `submit_time`, `schedule_time`,
`start_time`, `completion_time` (`model.py:384-419`); `Client` `connected_at`, `disconnected_at`;
`Source` `created`; `Zone` `created`. `Entity.columns` defaults to `{}` (`model.py:159`); `Client`,
`Source`, `Zone` are never decoded from JSON.

**Q2, nothing relies on a non-datetime conversion.** No test or source path expects a text column
to decode to a `datetime` (grep of tests: no date-like args or hosts, no `from_json_type` use). A key
not in `columns` is passed to `cls(**data)` and SQLAlchemy raises `TypeError: 'extra' is an invalid
keyword argument for Task`, today and under the fix (probe). Nothing on the wire carries extra keys:
`to_json` and `result_record` both iterate `Task.columns`; `_add_inbound` re-dumps the submitter's own
records.

**Q3, odd values in a datetime field.** `from_json_type` passes `None`, ints, floats, bools, dicts,
lists and strings of ≤5 chars through, and returns a non-ISO string unchanged (probe, both Pythons).
Under the fix these stay as they are (an int `submit_time` stays `12`). Keep `from_json_type`
unchanged so `revert_handed_back`'s "ignore a stamp that is not a time" holds
(`test_topology_server.py:617` pins `'not-a-time'`).

**Q4, round trip and forms.** `to_json_type` (`isoformat(sep=' ')`) → JSON → `from_json_type`
returns an equal `datetime` with the same `utcoffset` for naive, naive+µs, aware (−05:00),
aware+µs (+00:00) and local-aware, on 3.11 and 3.14. `fromisoformat` accepts all six R1 forms on
**both** 3.11 and 3.14 (`20261102`, `2026-11-02`, `2026W45`, `2026-W45-1`, `20261102T1200`,
`2026-11-02 12:00`), each to 2 Nov 2026; it rejects `2026-11`, `12:00:00`, `hello`. So every R1 form
is a live failure on every supported Python and the R8 test needs no version branch.

**Q5, R4 is feasible.** Tag `2.9.0a8` exists and is an ancestor of HEAD. `git diff 2.9.0a8 HEAD`
on `types.py` adds only `parse_delay`; on `model.py` it touches `Task.next` and the row-write
statements, not `columns`, `to_json`, `from_json`, `serialize_tasks`; `client.py`, `server.py`,
`dispatch.py`, `relay/` change no encode line. Proven: a fixed `Task` (fixed ids, aware times with
and without µs, `args='2026-11-02'`, a `tag`) encoded by the tag's code (`git archive 2.9.0a8 src |
tar -x -C $TMPDIR/hs-2.9.0a8`, imported via `sys.path.insert`) and by HEAD gives identical bytes for
both `serialize_tasks([t])` and `json.dumps(t.to_json())`.

**Q6, docs.** No page documents the JSON wire or timestamp decoding. `docs/_include/templates.rst:220`
and `templates_alt.rst:155` ("Incoming arguments are intelligently coerced") describe `smart_coerce`
of `x` inside `{= =}` (`core/template.py:22`), not the wire; out of scope (non-goal, as for tags). The
`*_help.rst` pages say the default template runs the line "verbatim", which the fix makes true. No
prose needed for the date-like fix (R9).

**Q7, existing tests.** None changes behaviour: they round-trip non-date-like text, and every
timestamp they rely on is a declared `datetime` column. `test_echoed_token_matches_after_round_trip`
(`test_writeback.py:498-512`, decorator at 498) already pins the token's exactness through the wire.

## Recommended design

One change, in `Entity.from_json` (`model.py:193-196`):

```python
@classmethod
def from_json(cls, data):
    """Build from JSON data: declared datetime columns are read as times, every other value stays as sent."""
    return cls.from_dict({key: from_json_type(value) if cls.columns.get(key) is datetime else value
                          for key, value in data.items()})
```

`from_json_type`, `to_json`, `to_json_type`, `serialize_tasks`, `deserialize_tasks` unchanged
(`Entity.unpack` and `deserialize_tasks` inherit the fix). Unknown keys keep raising `TypeError`.
Fields that must still decode as times: **`submit_time`, `schedule_time`, `start_time`,
`completion_time`** (plus `Client.connected_at`/`disconnected_at`, `Source.created`, `Zone.created`
for the generic method, unused on the wire).

## Risks

- **Drift between `columns` and the mapping.** A future `DateTime` column declared `str` in `columns`
  (or omitted) would silently arrive as text and fail at the bind. Guard with a test that every
  `DateTime` column of each entity is declared `datetime`.
- **Mixed versions** (accepted, GOAL Clarifications): a 2.9.0a8 peer keeps its old decode.
- A datetime column given a date-only string still decodes (to midnight), as today; harmless, since
  only HyperShell's own `isoformat` text reaches those fields.
- `smart_coerce` of `x` in `{= =}` templates still makes `20261102` an int: intended, separate.

## R4 fixture

Generate once from the tag, reproducibly: `git archive 2.9.0a8 src | tar -x -C "$TMPDIR/hs-2.9.0a8"`,
then run a small script with the worktree's `.venv/bin/python -I` that `sys.path.insert(0, …/src)`,
builds `Task(**fields)` from fixed literals (fixed UUIDs, `timezone(timedelta(hours=-5))` aware times
with and without µs, one naive time, `args='2026-11-02'`, `tag={'day': '2026-11-02'}`), and prints
(a) `serialize_tasks([t])`, (b) `json.dumps(t.to_json())` (the dispatch text), and (c) the result
bundle of the same task with `client_id/client_host`, `command='2026-11-02'`,
`start_time`/`completion_time`, `waited`, `duration`, `exit_status=0`. No worktree is left behind;
the extracted tree lives under `$TMPDIR`. Paste the three outputs into `tests/test_task_wire.py` as
`bytes` literals; commit the generator as `tests/fixtures/make-task-wire-2.9.0a8.sh` with a README
provenance row, per `tests/fixtures/README.md`.

## Proposed tests — `tests/test_task_wire.py`, all `@mark.unit`

1. `test_date_like_text_decodes_as_text[form-field]`: every R1 form in `args`, `command`, `outpath`,
   `errpath`, `csvpath`, `submit_host`, `server_host`, `client_host`, through both
   `deserialize_tasks(serialize_tasks(...))` and `Task.from_json(json.loads(json.dumps(to_json())))`.
2. `test_timestamps_decode_as_times[field-kind]`: the four time fields × naive/aware × with/without µs:
   equal value and `utcoffset`.
3. `test_tag_values_stay_as_sent`: a date-like tag value is untouched.
4. `test_odd_timestamp_values_pass_through`: `None`, an int, `'not-a-time'` in a time field stay as sent.
5. `test_unknown_key_still_rejected`: an extra key still raises `TypeError`.
6. `test_datetime_columns_are_declared`: for `Task`, `Client`, `Source`, `Zone`, every SQLAlchemy
   `DateTime` column is `datetime` in `columns`, and `list(columns)` equals the mapped columns.
7. `test_encoding_matches_2_9_0a8`: `serialize_tasks` and the dispatch text of the fixed task equal the
   recorded literals (R4).
8. `test_2_9_0a8_dispatch_decodes`: the recorded dispatch text decodes with `args == '2026-11-02'` and
   the fixed times intact.
9. `test_2_9_0a8_result_decodes`: the recorded result bundle decodes with `command == '2026-11-02'`
   and its times intact.
10. `test_template_expands_date_like_args`: each R1 form decoded through the client path expands under
    `'echo {}'` and the default template to the exact text (R1, no `-1001`).
11. `test_apply_results_stores_date_like_command_verbatim` (SQLite, in-file `task_db` fixture; a
    PostgreSQL twin in `test_writeback_postgres.py` under `HS_TEST_POSTGRES`): a dispatched row given
    the recorded result (its `schedule_time`) is APPLIED and stores `command == '2026-11-02'` (R2, R3).
12. `test_handback_ignores_date_only_stamp_text`: optional; existing `test_topology_server.py:617`
    and `test_contended_write.py:366-378` already pin the revert and the non-time stamp (R3).
