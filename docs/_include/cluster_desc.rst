Start cluster locally or with remote clients over *ssh* or a custom *launcher*.
This mode should be the most common entry-point for general usage. It fully encompasses all of the
different agents in the system in a concise workflow.

The input source for tasks is file-like, either a local path, or from *stdin* if no argument is
given. The command-line tasks are pulled in and either directly published to a distributed queue
(see ``--no-db``) or committed to a database first before being scheduled later.

Alternatively, use ``--from-json`` to read tasks from a JSON file (``FILE[@path]``); each task
object's keys become named ``{key}`` fields in the ``--template`` (and task tags). In this mode the
template is expanded when tasks are ingested rather than by the clients. A JSON source participates
in the same re-submission gating as a plain *FILE* — ``--restart`` (idempotent requeue),
``--repeat``, and ``--update --restart`` all apply — keyed by the file's absolute path together
with any ``@path`` selector.

For large, long running workflows, it might be a good idea to configure a database and run an
initial ``submit`` job to populate the database, and then run the cluster with ``--restart`` and no
input *FILE*. If the cluster is interrupted for whatever reason it can gracefully restart where it
left off.

Alternatively, pass the *FILE* directly with ``--restart``: the submission is detected, only tasks
that never landed are submitted, and interrupted tasks are re-run — so a requeued
``hsx <FILE> --restart`` job is safe to run repeatedly. Use ``--repeat`` to deliberately submit a
known file's tasks again as a new source, or ``--update --restart`` to add only the tasks that are
new since the file was last seen.

De-dup cost scales with the file's own source lineage rather than the whole database, so requeues stay
fast at scale; for very large workflows, a one-time ``submit`` followed by a bare ``hsx --restart`` (no
file re-read) is cheaper than re-feeding the *FILE* each run.

Clients launched on other hosts (``--ssh``, ``--mpi``, ``--launcher`` and ``--autoscaling``) are
told one address to dial in every mode: the bind address (``-H``) when the server binds one
specific address, and this host's own name when it binds every interface (*0.0.0.0*, the default
for remote clients). Use ``--advertise`` (or ``server.advertise``) to tell them another, such as
a name on the server's certificate; it must be reachable from wherever the clients run.

A client process the cluster launched that exits with a non-zero status is warned about when it
exits, naming it (its host under ``--ssh``). When every process launched with ``--ssh``, ``--mpi``
or ``--launcher`` has exited, at least one of them non-zero, while tasks remain that no client is
left to run, the cluster stops its server and exits non-zero with one error naming each process,
how it exited, and the address the clients were told to dial. A launcher that exits 0 with tasks
remaining (one that hands its clients to a scheduler and returns, say) leaves the cluster waiting
for them to connect.

Use ``--autoscaling`` with either *fixed* or *dynamic* to run a persistent, elastically scalable
cluster using an external ``--launcher`` to bring up clients as needed. Clients are scaled per zone:
each zone with tasks in the active group gets its own clients, and the launcher is told which zone
each one is for.

Use ``hsx`` in place of ``hs cluster``.