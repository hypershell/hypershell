Submit one or more tasks.

Submit one task as positional arguments.
If a single positional argument refers to a non-executable file path,
tasks will be read from the file, one per line (use ``-f``/``--task-file`` to be explicit).

Tasks are accumulated and published in bundles to the database by default.
With ``-q``/``--queue``, tasks are submitted directly to a live queue, bypassing the database.
The ``-b``/``--bundlesize`` and ``-w``/``--bundlewait`` options control the
size of these bundles and how long to wait before flushing tasks regardless of
how many have accumulated.

Pre-format tasks at submit time with template expansion using ``--template``.
Any tags specified with ``-t``/``--tag`` are applied to all tasks submitted.

Use the special comment syntax, ``# HYPERSHELL: ...``, to include resource limits or tags inline.
If the comment is alone on the line it will be applied to all tasks that follow.

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