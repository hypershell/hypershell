Options
^^^^^^^

``-N``, ``--num-tasks`` *NUM*
    Number of task executors (default: 1).

``-t``, ``--template`` *CMD*
    Command-line template pattern (default: "{}").

    This is expanded by the client just before execution. With the default "{}" the input
    command-line will be run verbatim. Specifying a template pattern allows for simple input
    arguments (e.g., file paths) to be transformed into some common form; such as
    ``-t './some_command.py {} >outputs/{/-}.out'``.

    See section on `templates`.

``-b``, ``--bundlesize`` *SIZE*
    Size of task bundle (default: 1).

    Using larger bundles is a good idea for large distributed workflows; specifically, it is best
    to coordinate bundle size with the number of executors in use by each client.

    See also ``--num-tasks`` and ``--bundlewait``.

``-w``, ``--bundlewait`` *SEC*
    Seconds to wait before flushing tasks (default: 5).

    The `client` collector thread that accumulates finished task bundles to return to
    the `server` will push out a bundle after this period of time regardless of whether
    it has reached the preferred bundle size.

    See also ``--bundlesize``.

``-H``, ``--host`` *ADDR*
    Hostname or IP address to connect with server (default: localhost).

``-p``, ``--port`` *NUM*
    Port number to connect with server (default: 50001).

``-k``, ``--auth`` *KEY*
    Cryptographic authentication key to connect with server (default: <not secure>).

    The default *KEY* used by the server and client is not secure and only a place holder.
    It is expected that the user choose a secure *KEY*. The `cluster` automatically generates
    a secure one-time *KEY*.

``-d``, ``--delay-start`` *SEC*
    Delay time in seconds before connecting to server (default: 0).

    At larger scales it can be advantageous to uniformly delay the client launch sequence.
    Hundreds or thousands of clients connecting to the server all at once is a challenge.
    Even if the server could handle the load, your task throughput would be unbalanced,
    coming in waves.

    Use ``--delay-start`` with a negative number to impose a uniform random delay up to the
    magnitude specified (e.g., ``--delay-start=-600`` would delay the client up to ten minutes).
    This also has the effect of staggering the workload. If your tasks take on the order of 30
    minutes and you have 1000 nodes, choose ``--delay-start=-1800``.

``--no-confirm``
    Disable client confirmation of task bundle received.

    To achieve even higher throughput at large scales, optionally disable confirmation
    payloads from clients. Consider using this option when also using ``--no-db``.

``-o``, ``--output`` *PATH*
    File path for task outputs (default: <stdout>).

``-e``, ``--errors`` *PATH*
    File path for task errors (default: <stderr>).

``-c``, ``--capture``
    Capture individual task <stdout> and <stderr>.

    By default, the `stdout` and `stderr` streams of all tasks are fused with that of the `client`
    thread, and in turn the `cluster`. If tasks are producing output that needs to be isolated, the
    tasks need to manage their own output, you can specify a redirect as part of a ``--template``,
    or use ``--capture`` to capture these as ``.out`` and ``.err`` files.

    These are stored local to the `client` under `<prefix>/lib/task/<uuid>.[out,err]`.
    Task outputs can be automatically retrieved via SFTP, see *task* usage.

    Mutually exclusive with both ``--output`` and ``--errors``.

``--timeout`` *SEC*
    Timeout in seconds for client. Automatically shutdown if no tasks received (default: never).

    This feature allows for gracefully scaling down a cluster when task throughput subsides.


``--task-timeout`` *SEC*
    Task-level walltime limit (default: none).

    Executors will send a progression of SIGINT, SIGTERM, and SIGKILL.
    If the process still persists the executor itself will shutdown.

``-R``, ``--ratelimit`` *NUM*
    Maximum allowed tasks per second per client. (default: none).
    Individual tasks are throttled if exceeding this average limit.

``-S``, ``--signalwait`` *SEC*
    Task-level signal escalation wait period in seconds (default: 10).

    When tasks fail to halt following an initial SIGINT, the program waits
    this interval in seconds before escalating to the next level of interrupt.

    See also ``--task-timeout``.
