Submit one or more tasks.

Submit one task as positional arguments.
If a single positional argument refers to a non-executable file path,
tasks will be read from the file, one per line (use ``-f``/``--task-file`` to be explicit).

Tasks are accumulated and published in bundles to the database by default.
With ``-q``/``--queue``, tasks are submitted directly to a live queue, bypassing the database;
the server must be running without a database (``--no-db``).
The ``-b``/``--bundlesize`` and ``-w``/``--bundlewait`` options control the
size of these bundles and how long to wait before flushing tasks regardless of
how many have accumulated.

Pre-format tasks at submit time with template expansion using ``--template``.
Any tags specified with ``-t``/``--tag`` are applied to all tasks submitted.

Use the special comment syntax, ``# HYPERSHELL: ...``, to include resource limits or tags inline.
If the comment is alone on the line it will be applied to all tasks that follow.

An inline directive and ``-t``/``--tag`` take each item as ``KEY:VALUE`` or ``KEY=VALUE``, split at
the first ``:`` or ``=``; a bare ``KEY`` is a tag with no value. The *declarable fields*, ``group``,
``zone``, ``cores``, ``memory`` and ``timeout``, set the task's own field with either separator,
read as the option that sets it reads it (``-g``, ``--zone``, ``-c``, ``-m``, ``-W``), and are never
stored as tags; ``cores:4.0`` is 4. A field with no value is refused, and so is ``none`` for every
field but ``zone``. Any other key is a tag.

.. code-block:: shell

    hs submit -t site=lab -- stage.sh
    echo 'train.sh  # HYPERSHELL: zone=gpu cores=8 timeout:2h model:resnet' | hs submit

``KEY=VALUE`` says that the key names a field, so the key is checked against the task's columns: a
key one edit from a column name, letter case aside (``grop=2``, ``Cores=4``), is refused as a likely
typo, naming the column it is near; write ``grop:2`` to store a tag by that name. A column that task
input does not set (``exit_status=0``, ``attempt=2``, ``id=...``) is refused; ``exit_status:0`` is a
tag. ``KEY:VALUE`` is never refused for being near a column name. Every tag is checked by the tag
rules wherever it stands, so a tag with no value does not end the check: ``naked other:a/b`` is
refused as ``other:a/b`` is.

A ``zone`` places the task in that zone, as ``--zone`` does: the name is the text as written
(``zone:007`` is a zone called 007, ``zone:none`` a zone called none, ``zone:default`` the default
zone), and the zone is recorded in the database. A ``--from-json`` record's ``zone`` key does the
same when it is a JSON string; any other JSON value is refused, as a ``group`` that is not a JSON
integer is. Zones require the database, so with ``--queue`` a zone in the input is refused, as
``--zone`` is.

When more than one source gives a field, the most specific wins: the line's own directive (or the
record's key), then a tag-only line before it, then ``-t``, then the field's option.

Each of these refusals takes the form a refused group takes (below): input read in full is refused
whole, with nothing submitted, exit status 5 and an error naming the value and its line or record;
on a stream only that line is refused, with an error naming it, and a refused tag-only line sets
nothing; ``-t`` is refused before any input is read, with exit status 2.

.. versionchanged:: 2.9.0
   A directive and ``-t`` take ``KEY=VALUE`` as well as ``KEY:VALUE``, with a likely typo of a field
   refused. ``zone`` from a directive, ``-t`` or a ``--from-json`` record sets the task's zone; it
   was stored as a tag. A tag after one with no value is checked. On a stream, a directive the tag
   rules refuse refuses its line alone; it ended the input.

A line whose first non-blank character is ``#`` is a comment. A ``#`` anywhere else is part of the
command and is submitted and run as written, whether in quotes, in a URL fragment or in ``$#``; a
trailing ``# note`` stays in the command and the shell ignores it. The ``# HYPERSHELL:`` marker is
recognised anywhere on a line, in quotes too, so a command that must contain that text breaks it
with shell quoting (``echo '#'' HYPERSHELL:'``). With ``--template``, each line is split before the
template is applied, so comment, blank and tag-only lines never become tasks.

.. versionchanged:: 2.9.0
   :pr:`118`: a ``#`` after a line's first non-blank character is part of the command; earlier
   releases cut the command at the first ``#``. Under ``--template``, comment, blank and tag-only
   lines are no longer submitted as tasks.

A task's group must be a non-negative integer, as ``-g``/``--group`` takes it, whether it comes from
``-t group:N``, an inline ``group:`` tag or a ``--from-json`` record. A task file, a JSON source,
the arguments or standard input holding any other group value is refused whole, with nothing
submitted, naming the value and its line (or record); ``-t group:x`` is refused before any input is
read. With the database, standard input is read in full before anything is submitted, so a pipe
submits nothing until it ends, and its lines are held in memory meanwhile.

With the database, a task below the group the current run has reached (the *reached group*, see
*Task Groups*) is refused: the whole submission is refused with nothing submitted, naming the lowest
such group and the reached group. Submit at the reached group or higher, wait for the run to finish,
or use a fresh database. Tasks that ``--update`` skips as already present are not checked. A
submission that names no group (no ``-g`` and no group tag) takes the reached group when the most
recently scheduled group is below it, so it is never refused.

.. versionchanged:: 2.9.0
   :pr:`118`: a group that is not an integer is refused; a task below the reached group is refused;
   with the database, standard input is read in full before anything is submitted.

.. versionchanged:: 2.9.0
   A group below 0 is refused, as a group that is not an integer is; earlier releases accepted it.

The resource tags ``cores``, ``memory`` and ``timeout`` take the ranges of the ``-c``, ``-m`` and
``-W`` options, and are refused as a group is, wherever they come from (an inline tag, ``-t`` or a
``--from-json`` record): input read in full is refused whole, with nothing submitted, naming the
value and its line (or record); on a stream only that line is refused, with an error; ``-t`` is
refused before any input is read (exit status 2), here and on ``hs run``.

A tag is read as its option reads it, with one difference: ``cores`` written as a decimal with a
zero fraction (``cores:4.0``) is that whole number, while a non-zero fraction (``cores:4.5``) is
refused. A ``timeout`` with a fraction of a second is rounded up to whole seconds, as ``-W`` rounds
it (``timeout:90.5`` is 91 seconds), and a ``memory`` with a fraction of a byte is rounded down, as
``-m`` rounds it. A ``--from-json`` record's value, a JSON number or a string, is read the same way
and stored as a whole number (``"cores": "4.0"`` and ``"cores": 4.0`` are 4, ``"timeout": 90.5``
is 91); a template still expands the record's field as written (``{memory}`` of ``"2G"`` is
``2G``).

Tasks past the advisory limit run as before; one warning for the whole submission gives their
count and the first such line (or record, or the command line for one task given as arguments).

.. versionchanged:: 2.9.0
   :pr:`125`: a resource tag outside its range, or not of its kind, is refused as a group that is
   not an integer is; before, it was stored as given, or stopped the submission while it was
   written. ``hs run -t`` refuses one the same way. A ``cores`` tag of ``4.0`` is still read as 4. A
   ``--from-json`` record's resource value is stored as a whole number: before, a decimal
   ``timeout`` or ``memory`` number was stored as given.

With ``--from-json``, read tasks from a JSON file instead. The value is
``FILE[@path]`` where the optional dotted ``path`` locates the list of task objects
within the document (e.g. ``plan.json@chunks``); with no ``@`` the file's top level
must itself be the list, and a ``FILE`` of ``-`` reads from ``<stdin>``. Each task
object's keys become named ``{key}`` template fields (and task tags), and an optional
``args`` key provides the base command (reachable as ``{}``). All ``{key}`` fields are
validated against every task object up front, so submission is rejected before any task
is committed if a field is missing. The ``args`` value is taken as written: ``#`` and
``# HYPERSHELL:`` text in it are part of the command, and a failed task's retry runs that same
command in the task's group, with its tags and its cores, memory and walltime.

.. versionchanged:: 2.9.0
   :pr:`118`: a retry runs its task's command as stored, in the task's group and with its tags;
   earlier releases read the command of a ``--from-json`` task again for its retry, cutting it at a
   ``#``.

Re-submitting a named task file is guarded to prevent accidental double submission. Each
named file is recorded as a *source* (its absolute path, a content fingerprint, and the
task count); submitting the same file again with no flag is refused, and if the path was
seen but the content changed the refusal suggests ``--update``. Pass ``--repeat`` to
deliberately submit all tasks again as a new source, or ``--update`` to submit only tasks
not already present from earlier versions of the same file. JSON task files (``--from-json``)
are gated the same way, keyed by their absolute path together with any ``@path`` selector.
Single-command and ``<stdin>`` submissions (including ``--from-json -``) are treated as
explicit intent and are exempt from these checks.

Detection and ``--update`` de-dup are index-backed on the source lineage, so their cost scales with
that file's own size, not the size of the database: re-submitting a normal file stays fast even against
a very large database, while ``--update`` on a source of millions of tasks pays to re-read and
fingerprint the entire file to find what is new.