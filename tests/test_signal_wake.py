# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A signal taken by any thread is acted on while the main thread waits on its worker.

CPython runs a Python signal handler only on the main thread, and only once that thread runs
bytecode again. A long-running command parks its main thread in a join on its worker, so a signal
the kernel hands to another thread must still wake it. The unit tests cover the shared bounded-step
join. The integration tests run each command under an injector: a wrapper named ``hs`` (so a relay
does not re-execute itself) whose own thread waits for a go-file and for the main thread to be
parked in that join, then sends SIGUSR1 to itself with ``signal.pthread_kill``. The signal can only
ever land on the injector's thread, and only while the main thread waits, on Linux and macOS alike.
Every process a test starts runs in its own session and is stopped by its recorded PID.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Iterator, List, Optional

# Standard libs
import os
import sys
import time
import signal
import threading
import subprocess
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status

# Internal libs
from hypershell.core import thread as core_thread  # Looked up per test, so the file collects on any build.
from hypershell.core.queue import QueueServer
from tests import main
from tests.test_relay_db import upstream, UPSTREAM_AUTH, NO_CLIENT, READY, free_port  # noqa: F401


DEADLINE: float = 30.0  # Seconds for a command to come up, and again for it to exit once signalled.
GRACE: float = 5.0      # Seconds a stopped process is given after SIGTERM before SIGKILL.
POLL: float = 0.05      # Seconds between looks at a log.

SLACK: float = 1.5  # Seconds allowed past the wake interval for a handler to run (scheduling, a loaded host).

AUTH: str = 'signal-wake-test-key-0123456789abcdef'
REGISTERED: str = 'Registered client'
INTERRUPTED: str = 'Signal interrupt (SIGUSR1)'
INJECTED: str = 'Injecting SIGUSR1 at the injector thread'

# Runs `hs` with a thread that sends SIGUSR1 to itself once the go-file exists and the main thread is
# parked in the join on its worker, inside one of the command entry points named below. Everything sits
# under the main guard, since a spawned helper process (the queue manager) imports this module again.
INJECTOR = '''\
if __name__ == '__main__':
    import os, sys, time, signal, threading

    CALLERS = {'run', 'run_client', 'serve_from', 'serve_forever', 'serve_with_sites',
               'run_local', 'run_ssh', 'run_cluster', 'submit_from'}

    def parked(ident):
        frame = sys._current_frames().get(ident)
        if frame is None or not frame.f_code.co_filename.endswith('threading.py'):
            return False
        if frame.f_code.co_name not in ('join', '_wait_for_tstate_lock'):
            return False
        while frame is not None:
            code = frame.f_code
            if code.co_name in CALLERS and os.sep + 'hypershell' + os.sep in code.co_filename:
                return True
            frame = frame.f_back
        return False

    def inject():
        ident = threading.main_thread().ident
        go = os.environ['SIGNAL_WAKE_GO']
        while not (os.path.exists(go) and parked(ident)):
            time.sleep(0.05)
        print('Injecting SIGUSR1 at the injector thread', file=sys.stderr, flush=True)
        signal.pthread_kill(threading.get_ident(), signal.SIGUSR1)

    threading.Thread(target=inject, name='injector', daemon=True).start()

    from hypershell import main
    sys.exit(main(sys.argv[1:]))
'''


# --- The shared bounded-step join -----------------------------------------------------------------


class Failing(core_thread.Thread):
    """A worker that fails once released."""

    def __init__(self, release: threading.Event, error: Exception) -> None:
        super().__init__(name='failing')
        self.release = release
        self.error = error

    def run_with_exceptions(self) -> None:
        self.release.wait(DEADLINE)
        raise self.error


def main_parked(ident: int) -> bool:
    """True while the thread `ident` waits inside a `threading` join."""
    frame = sys._current_frames().get(ident)
    return (frame is not None and frame.f_code.co_filename.endswith('threading.py')
            and frame.f_code.co_name in ('join', '_wait_for_tstate_lock'))


@mark.unit
def test_join_waking_returns_at_once_for_a_finished_thread() -> None:
    """A thread that has already ended is joined without waiting out an interval."""
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join()
    start = time.monotonic()
    core_thread.join_waking(worker, interval=60.0)
    assert time.monotonic() - start < 1.0


@mark.unit
def test_join_waking_re_raises_the_worker_failure_unchanged() -> None:
    """The failure that ended a worker is raised to the waiting thread as the same object."""
    release = threading.Event()
    error = RuntimeError('worker failed')
    worker = Failing(release, error)
    worker.start()
    threading.Timer(3 * core_thread.WAKE_INTERVAL, release.set).start()  # Fails after several wakes.
    with pytest.raises(RuntimeError) as caught:
        core_thread.join_waking(worker)
    assert caught.value is error
    assert not worker.is_alive()


@mark.unit
@pytest.mark.skipif(not hasattr(signal, 'pthread_kill'), reason='Needs signal.pthread_kill')
def test_join_waking_runs_a_handler_tripped_on_the_worker() -> None:
    """A signal sent to the worker's own thread runs its handler on the waiting main thread within the interval."""
    assert threading.current_thread() is threading.main_thread()
    handled = threading.Event()
    seen: Dict[str, float | str] = {}

    def handler(signum: int, frame: object) -> None:  # noqa: unused frame
        seen['at'] = time.monotonic()
        seen['thread'] = threading.current_thread().name
        handled.set()

    def worker() -> None:
        while not main_parked(threading.main_thread().ident):
            time.sleep(0.01)
        seen['sent'] = time.monotonic()
        signal.pthread_kill(threading.get_ident(), signal.SIGUSR1)
        handled.wait(DEADLINE)

    previous = signal.signal(signal.SIGUSR1, handler)
    try:
        thread = threading.Thread(target=worker, name='worker', daemon=True)
        thread.start()
        core_thread.join_waking(thread)
    finally:
        signal.signal(signal.SIGUSR1, previous)
    assert handled.is_set()
    assert seen['thread'] == threading.main_thread().name
    assert seen['at'] - seen['sent'] < core_thread.WAKE_INTERVAL + SLACK


# --- Commands under the injector ------------------------------------------------------------------


class Command:
    """A process in its own session writing its log to a file, stopped by its recorded PID."""

    def __init__(self, argv: List[str], env: Dict[str, str], log: Path, stdin: Optional[int] = None) -> None:
        self.argv = argv
        self.log = log
        with open(log, mode='w') as stream:
            self.proc = subprocess.Popen(argv, env=env, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=stream, start_new_session=True)

    def text(self) -> str:
        return self.log.read_text()

    def wait_for(self, token: str) -> None:
        """Wait until `token` is in the log; fail if the process exits or the deadline passes first."""
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            if token in self.text():
                return
            if self.proc.poll() is not None:
                pytest.fail(f'{self.argv[2]} exited ({self.proc.returncode}) before {token!r}:\n{self.text()}')
            time.sleep(POLL)
        pytest.fail(f'{self.argv[2]}: no {token!r} within {DEADLINE} seconds:\n{self.text()}')

    def wait_exit(self) -> int:
        """Wait for the process to exit by itself; return its status, or fail at the deadline."""
        try:
            return self.proc.wait(DEADLINE)
        except subprocess.TimeoutExpired:
            pytest.fail(f'{self.argv[2]} did not exit within {DEADLINE} seconds of the signal:\n{self.text()}')

    def stop(self) -> None:
        """End whatever is left of the process's session: SIGTERM, then SIGKILL after a grace."""
        if self.proc.poll() is not None:
            return
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, signum)
            except (ProcessLookupError, PermissionError):
                return
            try:
                self.proc.wait(GRACE)
                return
            except subprocess.TimeoutExpired:
                continue


class Harness:
    """Starts plain and injected commands for one test and stops every one of them at the end."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.go = root / 'go'
        self.injector = root / 'inject' / 'hs'  # Named `hs`, so `hs relay` on its command line is recognised.
        self.injector.parent.mkdir()
        self.injector.write_text(INJECTOR)
        self.started: List[Command] = []
        self.env = {**os.environ, 'HYPERSHELL_SERVER_AUTH': AUTH, 'SIGNAL_WAKE_GO': str(self.go)}

    def plain(self, name: str, *args: str, **options) -> Command:
        return self.start(name, ['hs', *args], **options)

    def injected(self, name: str, *args: str, **options) -> Command:
        return self.start(name, [sys.executable, str(self.injector), *args], **options)

    def start(self, name: str, argv: List[str], env: Optional[Dict[str, str]] = None,
              stdin: Optional[int] = None) -> Command:
        command = Command(argv, env or self.env, self.root / f'{name}.log', stdin=stdin)
        self.started.append(command)
        return command

    def fire(self) -> None:
        """Let the injector send its signal (once the main thread is parked in its join)."""
        self.go.touch()

    def stop(self) -> None:
        for command in self.started:
            command.stop()


@fixture
def harness(temp_site: Path) -> Iterator[Harness]:
    """Commands for one test, in the test's site; every process left at the end is stopped."""
    harness = Harness(Path(temp_site))
    try:
        yield harness
    finally:
        harness.stop()


def server_args() -> List[str]:
    return ['server', '--forever', '--no-tls', '--bind', '127.0.0.1', '--port', os.environ['HYPERSHELL_SERVER_PORT']]


def client_args() -> List[str]:
    return ['client', '--no-tls', '-H', '127.0.0.1', '-p', os.environ['HYPERSHELL_SERVER_PORT'], '-N', '1']


@mark.integration
def test_relay_drains_on_a_signal_taken_by_another_thread(harness: Harness, upstream: QueueServer) -> None:
    """A relay drains and exits when SIGUSR1 lands on a thread other than the main one."""
    site = harness.root / 'relay-site'
    site.mkdir()
    env = {**harness.env, 'HYPERSHELL_SITE': str(site),
           'HYPERSHELL_RELAY_UPSTREAM_HOST': '127.0.0.1',
           'HYPERSHELL_RELAY_UPSTREAM_PORT': str(upstream.address[1]),
           'HYPERSHELL_RELAY_UPSTREAM_AUTH': UPSTREAM_AUTH,
           'HYPERSHELL_RELAY_UPSTREAM_TLS_ENABLED': 'false',
           'HYPERSHELL_RELAY_SIZE': '1', 'HYPERSHELL_RELAY_LAUNCHER': NO_CLIENT}
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    relay = harness.injected('relay', 'relay', '--bind', '127.0.0.1', '--port', str(free_port()), env=env)
    relay.wait_for(READY)
    harness.fire()
    assert relay.wait_exit() == exit_status.success, relay.text()
    assert INJECTED in relay.text()
    assert 'Drain requested' in relay.text()


@mark.integration
def test_client_stops_on_a_signal_taken_by_another_thread(harness: Harness) -> None:
    """A client finishes and exits when SIGUSR1 lands on a thread other than the main one."""
    server = harness.plain('server', *server_args())
    server.wait_for('Server listening')
    client = harness.injected('client', *client_args())
    server.wait_for(REGISTERED)
    harness.fire()
    assert client.wait_exit() == exit_status.success, client.text()
    assert INJECTED in client.text()
    assert INTERRUPTED in client.text()


@mark.integration
def test_server_stops_on_a_signal_taken_by_another_thread(harness: Harness) -> None:
    """A server signals its clients and exits when SIGUSR1 lands on a thread other than the main one."""
    server = harness.injected('server', *server_args())
    server.wait_for('Server listening')
    client = harness.plain('client', *client_args())
    server.wait_for(REGISTERED)
    harness.fire()
    assert server.wait_exit() == exit_status.success, server.text()
    assert INJECTED in server.text()
    assert INTERRUPTED in server.text()
    assert 'Done' in server.text()
    assert client.wait_exit() == exit_status.success, client.text()


@mark.integration
def test_local_cluster_stops_on_a_signal_taken_by_another_thread(harness: Harness) -> None:
    """A local cluster stops its server and exits when SIGUSR1 lands on a thread other than the main one."""
    cluster = harness.injected('cluster', 'cluster', '--forever', '-N', '1')
    cluster.wait_for(REGISTERED)
    harness.fire()
    assert cluster.wait_exit() == exit_status.success, cluster.text()
    assert INJECTED in cluster.text()
    assert INTERRUPTED in cluster.text()


@mark.integration
def test_submit_takes_a_signal_taken_by_another_thread(harness: Harness) -> None:
    """Submit records SIGUSR1 landing on another thread while it waits on input, then finishes as before."""
    submit = harness.injected('submit', 'submit', '-f', '-', stdin=subprocess.PIPE)
    submit.proc.stdin.write(b'echo 1\necho 2\n')
    submit.proc.stdin.flush()
    harness.fire()
    submit.wait_for('Received signal')  # Fails if the process ends first: the input is still open.
    assert 'SIGUSR1' in submit.text()
    submit.proc.stdin.write(b'echo 3\n')
    submit.proc.stdin.close()
    assert submit.wait_exit() == exit_status.success, submit.text()
    assert INJECTED in submit.text()
    assert main(['hs', 'list', '--count']) == (exit_status.success, '3', '')


@mark.integration
def test_worker_failure_keeps_its_exit_status(harness: Harness) -> None:
    """A worker that fails while the main thread waits still ends the command with its error and status."""
    client = harness.plain('client', 'client', '--no-tls', '-H', '127.0.0.1', '-p', str(free_port()),
                           '--delay-start', '2')  # Fails after several wakes of the waiting main thread.
    assert client.wait_exit() == exit_status.runtime_error, client.text()
    assert 'CRITICAL [hypershell.client] ConnectionRefusedError' in client.text()
