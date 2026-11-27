Search tasks in the database.

A database must be configured.
Specifying *FIELD* names defines what is included in the output
(by default all fields are included).

As a safeguard against accidentally dumping an entire database, ``hs list`` refuses to return
more than 1,000 tasks unless the intent is made explicit with ``--all`` (dump everything),
``--limit`` *N* (bound the result), or ``--count`` (count only) - much as ``hs update`` confirms
a bulk change before applying it. A bare ``hs list`` with no arguments prints the usage statement.

This command maps directly to underlying SQL queries.

When printing to a terminal, task records are colorized by `exit_status`: successful tasks
(0) are green, cancelled tasks (-1, including those terminated by ``SIGHUP``) are shown faint,
tasks that terminated abnormally (killed by a signal, or that never ran - any other negative
status) are yellow, and tasks that ran and returned a non-zero code are red. Tasks that have
not yet run are left uncolored.
