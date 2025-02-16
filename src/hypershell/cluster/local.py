# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Local cluster implementation."""


# Type annotations
from __future__ import annotations
from typing import Iterable, IO

# Standard libs
import time
import secrets

# Internal libs
from hypershell.core.thread import Thread
from hypershell.core.logging import Logger
from hypershell.core.template import DEFAULT_TEMPLATE
from hypershell.submit import DEFAULT_BUNDLEWAIT
from hypershell.server import ServerThread, DEFAULT_BUNDLESIZE, DEFAULT_ATTEMPTS
from hypershell.client import ClientThread, DEFAULT_DELAY, DEFAULT_SIGNALWAIT, set_client_standalone

# Public interface
__all__ = ['run_local', 'LocalCluster']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


def run_local(**options) -> None:
    """
    Run local cluster until completion.

    All function arguments are forwarded directly into a
    :class:`~hypershell.cluster.local.LocalCluster` thread.

    Example:
        >>> from hypershell.cluster import run_local
        >>> run_local(['echo AAA', 'echo BBB', 'echo CCC'],
        ...           num_tasks=16, in_memory=True, no_confirm=True)

    See Also:
        - :class:`~hypershell.cluster.local.LocalCluster`
    """
    thread = LocalCluster.new(**options)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise


class LocalCluster(Thread):
    """
    Run server with single local client thread.

    Args:
        source (Iterable[str], optional):
            Any iterable of command-line tasks.
            A new `source` results in a :class:`~hypershell.submit.SubmitThread` populating
            either the database or the queue directly depending on `in_memory`.

        num_tasks (int, optional):
            Number of parallel task executor threads.
            See :const:`~hypershell.client.DEFAULT_NUM_TASKS`.

        template (str, optional):
            Template command pattern.
            See :const:`~hypershell.client.DEFAULT_TEMPLATE`.

        bundlesize (int optional):
            Size of task bundles returned to server.
            See :const:`~hypershell.server.DEFAULT_BUNDLESIZE`.

        bundlewait (int optional):
            Waiting period in seconds before forcing return of task bundle to server.
            See :const:`~hypershell.server.DEFAULT_BUNDLEWAIT`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            Disable client confirmation of tasks received.

        forever_mode (bool, optional):
            Regardless of `source`, if enabled schedule forever.
            Conflicts with `restart_mode` and `in_memory`. Default is `False`.

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        max_retries (int, optional):
            Number of allowed task retries.
            See :const:`~hypershell.server.DEFAULT_ATTEMPTS`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`~hypershell.server.DEFAULT_EAGER_MODE`.

        redirect_failures (IO, optional):
            Open file-like object to write failed tasks.

        redirect_output (IO, optional):
            Optional file-like object for <stdout> redirect.

        redirect_errors (IO, optional):
            Optional file-like object for <stderr> redirect.

        delay_start (float, optional):
            Delay in seconds before connecting to server.
            See :const:`~hypershell.client.DEFAULT_DELAY`.

        capture (bool, optional):
            Isolate task <stdout> and <stderr> in discrete files.
            Defaults to `False`.

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds.
            By default, the client waits indefinitely on tasks.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`~hypershell.client.DEFAULT_SIGNALWAIT`.

    Example:
        >>> from hypershell.cluster import LocalCluster
        >>> cluster = LocalCluster.new(
        ...     ['echo AAA', 'echo BBB', 'echo CCC'],
        ...     num_tasks=16, in_memory=True, no_confirm=True
        ... )
        >>> cluster.join()

    See Also:
        - :class:`~hypershell.server.ServerThread`
        - :class:`~hypershell.client.ClientThread`
        - :meth:`~hypershell.cluster.local.run_local`
    """

    server: ServerThread
    client: ClientThread

    def __init__(self: LocalCluster,
                 source: Iterable[str] = None,
                 num_tasks: int = 1,
                 template: str = DEFAULT_TEMPLATE,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 in_memory: bool = False,
                 no_confirm: bool = False,
                 forever_mode: bool = False,
                 restart_mode: bool = False,
                 max_retries: int = DEFAULT_ATTEMPTS,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 redirect_output: IO = None,
                 redirect_errors: IO = None,
                 delay_start: float = DEFAULT_DELAY,
                 capture: bool = False,
                 client_timeout: int = None,
                 task_timeout: int = None,
                 task_signalwait: int = DEFAULT_SIGNALWAIT) -> None:
        """Initialize with server and single client thread."""
        auth = secrets.token_hex(64)
        self.server = ServerThread(source=source,
                                   bundlesize=bundlesize,
                                   bundlewait=bundlewait,
                                   auth=auth,
                                   in_memory=in_memory,
                                   no_confirm=no_confirm,
                                   max_retries=max_retries,
                                   eager=eager,
                                   forever_mode=forever_mode,
                                   restart_mode=restart_mode,
                                   redirect_failures=redirect_failures)
        self.client = ClientThread(num_tasks=num_tasks,
                                   template=template,
                                   bundlesize=bundlesize,
                                   bundlewait=bundlewait,
                                   auth=auth,
                                   no_confirm=no_confirm,
                                   redirect_output=redirect_output,
                                   redirect_errors=redirect_errors,
                                   delay_start=delay_start,
                                   capture=capture,
                                   client_timeout=client_timeout,
                                   task_timeout=task_timeout,
                                   task_signalwait=task_signalwait)
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: LocalCluster) -> None:
        """Start child threads, wait."""
        set_client_standalone(False)
        self.server.start()
        while not self.server.queue.ready:
            time.sleep(0.1)
        self.client.start()
        self.client.join()
        self.server.join()

    def stop(self: LocalCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        self.server.stop(wait=wait, timeout=timeout)
        self.client.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)
