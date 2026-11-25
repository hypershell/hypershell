Make hs update read every column it writes by the column's kind

`hs update` now reads each value by the kind of column it goes into, the same way in the filtered
form (`hs update COLUMN=VALUE ...`) and the single-task form (`hs update ID COLUMN VALUE`), and the
same way on SQLite and PostgreSQL, so the same change leaves the same value on both. A value that is
not of the column's kind, or lies outside its range, exits with status 2 and one line naming the
column, the range or form, and the value, before anything is counted, asked or written. No other
change given with it is written, and `-f` does not change that. No value given to any column ends
in a traceback or exit status 5 any more.

Behaviour changes:

- `cores`, `memory` and `timeout` are read as `hs submit -c`, `-m` and `-W` read them.
  `memory=4G` and `timeout=1:30:00` store bytes and seconds. Earlier releases stored the text on
  SQLite, where a text `cores` or `memory` stopped the client that took the task, and failed with
  exit status 5 and a traceback on PostgreSQL.
- A value that is not of the column's kind, or is out of its range, is refused on every column with
  exit status 2. Earlier releases stored text, fractions and out-of-range numbers on SQLite, took
  `true` as 1, and failed with exit status 5 on PostgreSQL (rounding a fraction there instead).
  Scripts that pass `cores=1.5`, `exit_status=true` and the like now fail instead of storing 1.5, 2
  or 1. `cores=4.0` is refused, as `hs submit -c 4.0` is.
- The ranges are those of the range table: `cores` 0 to 32,767, `memory` 0 to 2^63-1 bytes,
  `timeout`, `waited` and `duration` 0 to 2,147,483,647 seconds, `attempt` 1 to 32,767, `part` 0 to
  2,147,483,647, and three new rows: `exit_status` -32,768 to 32,767, `cores_max` 0 to 32,767 and
  `memory_max` 0 to 2^63-1 bytes. `cores` above 1,024 and `attempt` above 100 are written with one
  warning.
- `waited` and `duration` take durations, as `timeout` does (`1h30m` is 5,400 seconds).
- `cores_max` and `memory_max` take numbers and are stored as decimals.
- `retried` takes `yes`/`no` and `on`/`off`, in any case, as well as `true`/`false` and `1`/`0`.
- `none` (or `null`, in any case) on a column that may not be empty (`args`, `submit_id`,
  `submit_time`, `attempt`, `retried`, `part`) exits with status 2 naming the column, instead of
  exit status 5 and a traceback. On every other column it clears the value, as before.
- `submit_time`, `schedule_time`, `start_time` and `completion_time` take ISO 8601, on both
  databases: a date, or a date and a time joined by `T` or a space, with optional seconds, fraction
  and offset (`2026-01-01T09:00:00-05:00`, or `Z`). A value without an offset is local time, and the
  same value is the same instant on SQLite and PostgreSQL. A local time that does not exist, in the
  hour skipped by a change to daylight-saving time, is refused, and so is an instant within a day of
  either end of the calendar (years 1 and 9999), which a reader in some zone could not show. Earlier releases refused every value
  with a traceback on SQLite, and on PostgreSQL stored whatever the server read, in the server
  session's zone. `now`, `today` and `epoch`, which PostgreSQL stored, now exit with status 2: use
  `$(date -Iseconds)`.
- `submit_id`, `server_id`, `client_id`, `previous_id`, `next_id` and `source` take a uuid in the
  8-4-4-4-12 form, in any case, stored in lower case. Earlier releases stored any text on SQLite.
- `zone=` takes a zone name, as `hs submit --zone` reads it, and records a new zone so `hs list`
  and `hs info` show its name. `zone=default` and `zone=none` put the task back in the default zone.
  Earlier releases stored the text on SQLite, refused a name with exit status 5 on PostgreSQL, and
  on both stored the default zone's reserved id when given it, which the default zone's clients do
  not match. A zone literally named `none` or `null` cannot be set by `hs update`.
- `hs update ID id VALUE` exits with status 2, as `id=VALUE` does; earlier it failed with a
  traceback.
- `tag=VALUE` is refused with exit status 2, pointing to `KEY=VALUE`, `KEY:VALUE` and
  `--remove-tag`. Earlier releases replaced the task's whole tag with the value as JSON text (`"x"`,
  or `null`). The single-task `hs update ID tag KEY:VALUE` still merges one tag.
- `KEY=VALUE` with a key that is not a task column is now a tag change, the same as `KEY:VALUE`, in
  both forms (`hs update ID KEY VALUE` too). Earlier releases refused it (`Unrecognized task field`,
  `Invalid field name`). So a misspelled column becomes a tag: without `-f`, the prompt now lists
  each change as a column, a tag or a removal before asking, so it shows there; take care under
  `-f` and in the single-task form, which do not prompt.
- A key one edit from a column is refused with exit status 2, naming the key and the nearest column,
  with nothing written: one letter added, dropped, changed, or two neighbours swapped, ignoring
  letter case (`exit_stauts=0`, `exitstatus=0`, `Exit_Status=0`, `Cores=4`). Short column names count
  too, so `tags=`, `ip=`, `pid=`, `port=`, `zones=` and `groups=` are refused. Only a misspelling
  farther than one edit becomes a tag.
- `KEY:VALUE` always sets a tag and is never checked against the columns, even when its key is close
  to or equal to a column's name. It is the way to set a tag named like a column: the refusal says
  so (`write exit_stauts:VALUE`, or `write ID tag exit_stauts:VALUE` in the single-task form).
- An argument holding both `=` and `:` is split at the first of them, so `note:a=b` is the tag
  `note` (its value then refused by the tag rules) and `start_time=2026-01-01T09:00` is a column.
- Every tag `hs update` writes is checked by the tag rules, as `hs submit -t` checks them. A filtered
  `KEY:VALUE` that breaks them exits with status 2 instead of 5 with a traceback, and
  `hs update ID tag KEY:VALUE`, which stored any tag before (`a b:1`, `memory:4X`), is checked too.
- The bash and zsh completions no longer offer `tag=`.
