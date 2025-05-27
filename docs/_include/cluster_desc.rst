Start cluster locally or with remote clients over *ssh* or a custom *launcher*.
This mode should be the most common entry-point for general usage. It fully encompasses all of the
different agents in the system in a concise workflow.

The input source for tasks is file-like, either a local path, or from *stdin* if no argument is
given. The command-line tasks are pulled in and either directly published to a distributed queue
(see ``--no-db``) or committed to a database first before being scheduled later.

For large, long running workflows, it might be a good idea to configure a database and run an
initial ``submit`` job to populate the database, and then run the cluster with ``--restart`` and no
input *FILE*. If the cluster is interrupted for whatever reason it can gracefully restart where it
left off.

Use ``--autoscaling`` with either *fixed* or *dynamic* to run a persistent, elastically scalable
cluster using an external ``--launcher`` to bring up clients as needed.

Use ``hsx`` in place of ``hs cluster``.