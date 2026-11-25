Update tasks in the database.

Include any number of `FIELD=VALUE` or tag `KEY:VALUE` positional arguments.
Each argument is split at its first ``=`` or ``:`` (so ``note:a=b`` is the tag ``note``).

Any key that is not a column is a tag: ``color=blue`` sets the tag ``color``, as ``color:blue`` does,
and so does ``hs update ID color blue``. A misspelled column therefore becomes a tag; without
``-f`` the prompt lists each change as a column, a tag or a removal before anything is written, so
it shows there. A key one edit from a column (a letter added, dropped, changed or two swapped,
letter case aside, as in ``exit_stauts``, ``Exit_Status``, ``Cores``, ``tags`` or ``ip``) is refused
with exit status 2, naming the column it is near, and nothing is written. ``KEY:VALUE`` always sets a
tag, even when the key is close to or equal to a column's name: it is the way to set a tag named
like a column (``hs update ID tag KEY:VALUE`` in the single-task form). ``tag=VALUE``, which would
replace every tag at once, is refused: change one tag with ``KEY=VALUE`` or ``KEY:VALUE``, and remove
one with ``--remove-tag``. Every tag is checked by the tag rules (as ``hs submit -t`` checks it), and
one that breaks them exits with status 2 before anything is counted, asked or written.

Every column is read by its kind, in both forms and on every database, within the range listed
below. Any other value exits with status 2, naming the column, the range or form and the value,
before anything is counted, asked or written, and no other change given with it is written.
``none`` or ``null``, in any case, clears a column that may be empty; for ``attempt``, ``part``,
``retried``, ``submit_id``, ``submit_time`` and ``args`` it is refused. A change to ``id`` is
refused in both forms.

- ``cores``: a whole number, as ``hs submit -c`` reads it. Range: 0 (unconstrained) to 32,767.
  Above 1,024 it is written with a warning.
- ``memory``: a size, as ``hs submit -m`` reads it (e.g., ``4G``, ``512MB``, ``1.5GiB``, or a number of bytes). Range: 0 bytes (unconstrained) to 9,223,372,036,854,775,807 bytes.
- ``timeout``: a duration, as ``hs submit -W`` reads it (e.g., ``90m``, ``1h30m``, ``1:30:00``, ``1-12``, or a number of seconds), rounded up to whole seconds. Range: 0 s to 2,147,483,647 s.
- ``waited``: a duration in seconds, read as ``timeout`` is. Range: 0 s to 2,147,483,647 s.
- ``duration``: a duration in seconds, read as ``timeout`` is. Range: 0 s to 2,147,483,647 s.
- ``exit_status``: a whole number. Range: -32,768 to 32,767.
- ``attempt``: a whole number; not ``none``. Range: 1 to 32,767.
  Above 100 it is written with a warning.
- ``part``: a whole number; not ``none``. Range: 0 to 2,147,483,647.
- ``cores_max``: a number, the peak cores measured. Range: 0 to 32,767.
- ``memory_max``: a number of bytes, the peak memory measured. Range: 0 to 9,223,372,036,854,775,807 bytes.
- ``retried``: a boolean, true/false, yes/no, on/off or 1/0 in any case; not ``none``.
- ``submit_id``, ``server_id``, ``client_id``, ``previous_id``, ``next_id`` and ``source``: a uuid,
  the 8-4-4-4-12 hexadecimal form in any case, stored in lower case (e.g.,
  ``0198f3a2-1b2c-7d3e-8f40-0123456789ab``); ``submit_id`` not ``none``. A ``source`` is the id of
  a source as ``hs info`` shows it in parentheses; the value is not checked against the sources.
- ``zone``: a zone name, read as ``hs submit --zone`` reads it; ``default`` or ``none`` puts the task in
  the default zone, so a zone named ``none`` or ``null`` cannot be set by ``hs update``. A new name
  is recorded once the change is written, as ``hs submit --zone`` records it.
- ``submit_time``, ``schedule_time``, ``start_time`` and ``completion_time``: ISO 8601 only, a date
  (``2026-01-01``, midnight) or a date and a time joined by ``T`` or a space, with optional seconds
  and fraction (up to six digits) and an optional offset or ``Z`` (e.g., ``2026-01-01T09:00``,
  ``2026-01-01 09:00:00.5``, ``2026-01-01T09:00:00-05:00``); ``submit_time`` not ``none``. A value
  without an offset is local time, the zone ``hs info`` and ``hs list`` show times in; a local time
  that does not exist, in the hour skipped by a change to daylight-saving time, is refused, and one
  that occurs twice is the first. A value is the same instant on SQLite and PostgreSQL. ``now``,
  ``today``, ``epoch`` and other words are refused; for the time now, use ``$(date -Iseconds)``.
- ``args``: any text, as given; not ``none``. The other text columns (``command``, ``outpath``,
  ``errpath``, ``csvpath``, ``submit_host``, ``server_host``, ``client_host``, ``fingerprint``) take
  any text, and ``none`` clears them.

A group change (``group=VALUE``, or ``ID group VALUE`` in the single-task form) takes an integer as
``hs submit -g`` does, in the 32-bit range; any other value exits with status 2 before anything is
counted, asked or written, and no other change given with it is written.

The ``-w``/``--where`` and ``-t``/``--with-tag`` operate just as in the search command.
Each takes the words that follow it up to the next option, so the changes go before them
(e.g., ``hs update group=7 color:red -t color:blue``).

Every change given is written together, onto each task that matches the filter at the time
it is written: a task another process changed so that it no longer matches is left as it is.
The `Updated N tasks` (or `Deleted N tasks`) line counts the tasks actually written; when
that is fewer than the count taken at the prompt, a warning says how many tasks
no longer matched and were left as they are, and the exit status is still 0.

If another writer holds a task through every retry, the update writes nothing at all, logs
an error naming the task, and exits with status 5, so running the same command again is safe.
With ``--limit``, each task written stays locked until the whole update finishes; on SQLite the
database is locked for writing that long, and another writer that waits past its busy timeout
fails. On PostgreSQL, an update without ``--limit`` holds the ids of the tasks it wrote in
memory (about 100 bytes each) to find any it missed.

Using ``--cancel`` marks tasks as cancelled: `schedule_time` and `completion_time`
are set to now and `exit_status` to -1. A cancelled task is terminal - it will not be
scheduled, retried, or reverted on ``--restart``.

Using ``--revert`` reverts everything as if the task was new again.

Using ``--delete`` drops the row from the database entirely.
``--delete`` takes no change: given a ``FIELD=VALUE``, a ``KEY:VALUE`` or ``--remove-tag``,
``hs update`` exits with status 2 before it counts or asks, and deletes nothing.

The legacy interface for updating a single task with the `ID`, `FIELD`,
and `VALUE` as positional arguments remains valid.
The single-task form takes no ``--cancel``, ``--revert``, ``--delete`` or ``--remove-tag``;
given one, it exits with status 2 and writes nothing.
The single-task form takes no option that selects tasks (``-w``, ``-t``, ``-g``, ``-l``, ``-s``,
``--desc``, ``-F``, ``-S``, ``-C``, ``-R``, ``-X``, ``--retries``, ``--signal``); given one, it exits
with status 2 and writes nothing. To write a task only if it matches, use the filtered form:
``hs update FIELD=VALUE -w "id == ID" -F``.
