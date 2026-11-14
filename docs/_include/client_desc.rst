Launch client directly, run tasks in parallel.

The client connects to the server and, whenever it is ready for work, requests a bundle of tasks
of its own size (``-b``/``--bundlesize``; the server's size when not given).
These tasks are run locally by some number of a parallel task executors.

The environment for tasks are the same as for the client. Standard output and error
for tasks are forwarded to that of the client, unless ``--capture`` is used, in which
these are directed to individual files for each task.

The client takes the key to connect to the server from the ``HYPERSHELL_SERVER_AUTH`` environment
variable or from ``server.auth`` in a configuration file (or its ``auth_env`` and ``auth_eval``
forms), never from the command line. A client that ``hs cluster`` launches receives its key
in that environment variable.
