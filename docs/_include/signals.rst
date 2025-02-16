``SIGUSR1``
    Sending the ``SIGUSR1`` signal will trigger the schedulers to halt and begin
    shutdown procedures. On the client side, this means that all current tasks
    (and any in the local queue) will be allowed to complete, but the system will
    drain and shutdown at the completion of these tasks.

``SIGUSR2``
    Sending the ``SIGUSR2`` signal implies the same, but on the client side will
    set a flag to send local interrupts to tasks to come down faster. As described
    in the previous release with regard to the ``task.timeout`` feature, we send
    ``SIGINT``, ``SIGTERM``, and ``SIGKILL`` in an escalating fashion to halt
    running tasks.