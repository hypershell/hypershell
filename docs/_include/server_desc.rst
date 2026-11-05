Launch server, schedule directly or asynchronously from database.

The server includes a scheduler component that pulls tasks from the database and stages them
for clients, which request bundles of them when they are ready for work. It also has a receiver that collects the results
of finished tasks. Optionally, the server can submit tasks (*FILE*). When submitting tasks,
the ``-w``/``--bundlewait`` and ``-b``/``--bundlesize`` options are the same as for the
*submit* workflow.

With ``--max-retries`` greater than zero, the scheduler will check for a non-zero exit status
for tasks and re-submit them if their previous number of attempts is less.

Tasks are bundled for each client at the size it asks for, or at the server's ``-b``/``--bundlesize``
when it asks for none. By default the bundle size is one, meaning that at small scales there is
greater concurrency.
