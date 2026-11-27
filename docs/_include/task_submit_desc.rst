Submit individual task to the database.

A database must be configured. The task will not run until scheduled
by the server process. The task UUID will be printed to standard out.

The resource tags given with ``-t``/``--tag`` take their ranges as on ``hs submit``: a value
outside its range is refused before the database is opened (exit status 2).

See ``hs info`` command.
