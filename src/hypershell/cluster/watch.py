# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Watching the client processes a cluster launched, while its server runs.

A cluster in launcher, `--mpi` or `--ssh` mode starts its clients as processes and serves them
from a server it runs in-process. Every launched process is polled while the server runs; each
one that exits non-zero is warned about when it exits, while the others still run.

Each cluster mints its key for one run and hands it only to the processes it launches, so once
they have all exited no client can ever connect again. If at least one of them failed and the
server still holds work it would otherwise wait on forever (:func:`work_remains`), the cluster
stops its server and raises :class:`ClusterLaunchError`, naming every process, how it exited and
the address the clients were told to dial. When every process exited 0 (a launcher that hands its
clients to a scheduler and returns), or the server ends on its own, nothing is raised.
"""


# Type annotations
from __future__ import annotations
from typing import List, Optional, Sequence, Tuple, Final

# Standard libs
import time
import signal
from queue import Full as QueueFull
from subprocess import Popen

# Internal libs
from hypershell.core.logging import Logger
from hypershell.data.model import Task, serialize_tasks
from hypershell.server import ServerThread

# Public interface
__all__ = ['ClusterLaunchError', 'exit_text', 'work_remains', 'watch_launched',
           'WATCH_INTERVAL', 'RESULTS_TIMEOUT', 'SCHEDULE_SETTLE', 'MEMORY_SETTLE']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


WATCH_INTERVAL: Final[float] = 1.0
"""Seconds between polls of the launched processes."""

RESULTS_TIMEOUT: Final[float] = 3.0
"""Seconds to wait for the server to write every result already returned to it."""

SCHEDULE_SETTLE: Final[float] = 2.0
"""Seconds between the two reads of the database that must both find work remaining."""

MEMORY_SETTLE: Final[float] = 3.0
"""Seconds a server without a database is given to end on its own once every result is written."""


class ClusterLaunchError(RuntimeError):
    """Every client a cluster launched has exited, at least one failed, and the server still holds work."""


def exit_text(code: int) -> str:
    """
    How a process exited, from its return code: `status N`, or `signal N (SIGNAME)` when a signal ended it.

    Example:
        >>> exit_text(1)
        'status 1'
        >>> exit_text(-9)
        'signal 9 (SIGKILL)'
    """
    if code >= 0:
        return f'status {code}'
    try:
        return f'signal {-code} ({signal.Signals(-code).name})'
    except ValueError:
        return f'signal {-code}'


def results_written(server: ServerThread, timeout: float = RESULTS_TIMEOUT) -> bool:
    """
    Wait until the server has written every result already returned to it; False if it has not within `timeout`.

    Returned results wait on the server's completed queue for its one receiver, which takes bundles
    in order and writes each before it takes the next. An empty bundle put behind them is therefore
    taken only once every result before it is written; a result whose write is held for retry
    stays in the receiver's `held` list until it lands. A receiver that has ended has nothing left
    to write.
    """
    if not server.receiver.is_alive():
        return True
    deadline = time.monotonic() + timeout
    try:
        # The same encoding as a client's bundle of results, with none in it: written as a no-op.
        server.queue.completed.put(serialize_tasks([]), timeout=timeout)
    except QueueFull:
        return False
    while time.monotonic() < deadline:
        # Queue counters are no barrier here: the receiver marks a bundle done before it writes it.
        if server.queue.completed.empty() and not server.receiver.machine.held:
            return True
        if not server.receiver.is_alive():
            return True
        time.sleep(0.1)
    return False


def scheduled_work(server: ServerThread) -> bool:
    """
    True if the server's scheduler is running and would schedule more: tasks not completed, or failures to retry.

    This is the scheduler's own rule for stopping, read the same way: every task not completed, in every
    group and zone, and then the failures it would retry in its current group, under its own attempts
    and eager setting. Incomplete tasks are counted first, so a failure written between the two reads
    is still seen. A scheduler that has ended has finished or failed its run on its own.
    """
    scheduler = server.scheduler
    if not scheduler.is_alive():
        return False
    machine = scheduler.machine
    if Task.count_remaining() > 0:
        return True
    if machine.current_group is None:
        return False  # No group chosen yet: nothing has been scheduled to fail
    return Task.count_retryable(machine.attempts, group=machine.current_group, eager=machine.eager) > 0


def work_remains(server: ServerThread,
                 schedule_settle: float = SCHEDULE_SETTLE,
                 memory_settle: float = MEMORY_SETTLE) -> bool:
    """
    True if `server` would otherwise wait forever on work, once no client can connect to it again.

    Every result already returned is written first (:func:`results_written`). With a database, the
    scheduler must still be running and find work (:func:`scheduled_work`) twice, `schedule_settle`
    seconds apart, so a scheduler woken by its last clients leaving can reach its own end first.
    Without one, the server must still be running `memory_settle` seconds later, with tasks submitted
    or still being submitted: a server whose clients finished the work ends on its own within that
    time, and a server given no tasks has nothing to wait on.

    Without a database, a client that returned every result but exited non-zero before it told the
    server it had finished is not told apart from one that failed with work left: the server ends
    only once it evicts that client, so the work reads as remaining.
    """
    if not results_written(server):
        return False
    if server.scheduler is not None:
        if not scheduled_work(server):
            return False
        server.join(timeout=schedule_settle)
        return server.is_alive() and scheduled_work(server)
    server.join(timeout=memory_settle)
    submitter = server.submitter
    if not server.is_alive() or submitter is None:
        return False
    return submitter.is_alive() or submitter.task_count > 0


def watch_launched(server: ServerThread,
                   launched: Sequence[Tuple[str, Popen]],
                   advertised: Tuple[str, int],
                   interval: float = WATCH_INTERVAL) -> None:
    """
    Watch the processes in `launched`, each with its name, until `server` ends.

    A process that exits non-zero is warned about at once, naming it. Once every process has exited,
    at least one of them non-zero, and the server still has work (:func:`work_remains`), the server
    is stopped and :class:`ClusterLaunchError` is raised, naming every process, how it exited and the
    address in `advertised`. That stop does not wait on input still being read: no client is left to
    run it, and a producer that stays open would hold the stop forever. When the server ends on its
    own, the processes still running are waited on, and any that exits non-zero is warned about. A
    failure that ended the server is raised.
    """
    codes: List[Optional[int]] = [None] * len(launched)

    def record(index: int, code: int) -> None:
        codes[index] = code
        if code != 0:
            log.warning(f'Client {launched[index][0]} exited with {exit_text(code)}')

    while server.is_alive():
        for index, (_, proc) in enumerate(launched):
            if codes[index] is None and (code := proc.poll()) is not None:
                record(index, code)
        if None not in codes and any(code != 0 for code in codes) and work_remains(server):
            server.stop(wait=True, leave_input=True)
            exits = '; '.join(f'{name} exited with {exit_text(code)}' for (name, _), code in zip(launched, codes))
            host, port = advertised
            raise ClusterLaunchError(f'every launched client has exited with tasks remaining: {exits}; '
                                     f'clients were told to dial {host}:{port}')
        server.join(timeout=interval)
    server.join()
    for index, (_, proc) in enumerate(launched):
        if codes[index] is None:
            record(index, proc.wait())
