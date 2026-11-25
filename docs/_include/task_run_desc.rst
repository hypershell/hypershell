Submit individual task and wait for completion.

A database must be configured. The task will not run until scheduled
by the server process. The <stdout> and <stderr> are written locally
as if run locally, requires the client to have ``--capture`` enabled.

The resource tags given with ``-t``/``--tag`` (``cores``, ``memory`` and ``timeout``) take their
ranges as on ``hs submit``: a value outside its range, or not of its kind, is refused before the
database is opened, with one line naming the tag's key (e.g., ``task.memory``), the range and the
value (exit status 2). A ``cores`` tag past 1,024 runs, with one warning.

See ``info`` and ``wait`` commands.
