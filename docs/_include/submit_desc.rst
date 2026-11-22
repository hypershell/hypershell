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

A line whose first non-blank character is ``#`` is a comment. A ``#`` anywhere else is part of the
command and is submitted and run as written, whether in quotes, in a URL fragment or in ``$#``; a
trailing ``# note`` stays in the command and the shell ignores it. The ``# HYPERSHELL:`` marker is
recognised anywhere on a line, in quotes too, so a command that must contain that text breaks it
with shell quoting (``echo '#'' HYPERSHELL:'``). With ``--template``, each line is split before the
template is applied, so comment, blank and tag-only lines never become tasks.

.. versionchanged:: 2.9.0
   A ``#`` after a line's first non-blank character is part of the command; earlier releases cut
   the command at the first ``#``. Under ``--template``, comment, blank and tag-only lines are no
   longer submitted as tasks.

A task's group must be an integer, as ``-g``/``--group`` takes it, whether it comes from
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
   A group that is not an integer is refused; a task below the reached group is refused; with the
   database, standard input is read in full before anything is submitted.

With ``--from-json``, read tasks from a JSON file instead. The value is
``FILE[@path]`` where the optional dotted ``path`` locates the list of task objects
within the document (e.g. ``plan.json@chunks``); with no ``@`` the file's top level
must itself be the list, and a ``FILE`` of ``-`` reads from ``<stdin>``. Each task
object's keys become named ``{key}`` template fields (and task tags), and an optional
``args`` key provides the base command (reachable as ``{}``). All ``{key}`` fields are
validated against every task object up front, so submission is rejected before any task
is committed if a field is missing.

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