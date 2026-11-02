# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A same-process client left to the cyclic garbage collector never stalls the in-process queue server.

A :class:`QueueClient` and its proxies refer to each other, so a dropped client waits for the cyclic
collector, which runs the proxies' decref finalizers in whichever thread triggers the collection,
server threads included. Each probe runs in its own process with a lowered collection threshold, so
collections are frequent. A watchdog ends a probe that stalls, so a stall fails the test within its
time limit instead of hanging the suite.

Two more probes check that each decref is sent once, to the server that issued the reference: a
client of a server in another process on an in-process server's port number, and a forked child of
a serving process.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Optional

# Standard libs
import os
import gc
import sys
import time
import signal
import socket
import threading
import subprocess
import faulthandler
import multiprocessing
from queue import SimpleQueue
from types import SimpleNamespace
from multiprocessing.managers import BaseProxy

# External libs
from pytest import fixture, mark, skip

# Internal libs
from hypershell.core import queue as queue_module
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig
from hypershell.core.tls import TLSConfig, ensure_default_materials, clear_process_context


AUTH = 'queue-test-key-0123456789abcdef'
WAIT = 2.0

# Rounds per probe
ROUNDS = 200

# Collection thresholds (generation 0) that successive rounds of each probe run under. Where a
# collection lands, and so which thread runs the finalizers, shifts with the threshold; these are
# the values under which the stall shows most often.
THRESHOLDS = {'dropped-client': (10, ), 'same-port-restart': (200, 300)}

# Seconds a single round may take before the probe's watchdog ends it
ROUND_LIMIT = 15

# Seconds the whole probe process may take before the test ends its process group
PROBE_LIMIT = 240


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> tuple[str, str]:
    """Generate a self-signed cert+key once per test module."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture(params=['no-tls', 'tls'])
def tls_args(request, tls_materials) -> List[str]:
    """Probe arguments selecting TLS off, or on with the module's cert+key."""
    if request.param == 'no-tls':
        return []
    return list(tls_materials)


def tail(output: str, lines: int = 60) -> str:
    """The last `lines` lines of a probe's output (its last round and any stack dump)."""
    return '\n'.join(output.splitlines()[-lines:])


def run_probe(name: str, tls_args: List[str], rounds: int = ROUNDS, *options: str) -> None:
    """Run probe `name` in its own process group and assert it finished every round."""
    command = [sys.executable, os.path.abspath(__file__), name, str(rounds), *options, *tls_args]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True, text=True)
    try:
        output, _ = proc.communicate(timeout=PROBE_LIMIT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        raise AssertionError(f'probe {name} did not finish within {PROBE_LIMIT} s:\n{tail(output)}')
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    assert proc.returncode == 0 and f'OK {rounds}' in output, \
        f'probe {name} exited {proc.returncode}:\n{tail(output)}'


@fixture(params=['no-tls', 'tls'])
def tls(request, tls_materials) -> Iterator[Optional[TLSConfig]]:
    """Run once with TLS disabled and once with TLS enabled."""
    if request.param == 'no-tls':
        yield None
    else:
        cert, key = tls_materials
        yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
        clear_process_context()


def wait_until(predicate, timeout: float = WAIT) -> bool:
    """Poll `predicate` until it holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@mark.unit
def test_dropped_client_decref_reaches_server(tls: Optional[TLSConfig]) -> None:
    """A same-process client dropped as cyclic garbage still releases the server's references."""
    with QueueServer(probe_config(tls=tls)) as server:
        port = server.address[1]
        client = QueueClient(probe_config(port, tls))
        client.connect()
        client.heartbeat.put(b'beat', timeout=WAIT)
        counter = QueueClient(probe_config(port, tls))  # Never connected: only asks for the count
        assert counter._number_of_objects() == 4
        del client
        gc.collect()
        assert wait_until(lambda: counter._number_of_objects() == 0)


@mark.unit
def test_finalizer_hands_off_only_when_served_here(monkeypatch) -> None:
    """A decref for an address an in-process server listens on is handed off; any other is the stock one."""
    calls = []
    monkeypatch.setattr(BaseProxy, '_decref', staticmethod(lambda *args: calls.append(args)))
    handed_off = SimpleQueue()
    monkeypatch.setattr(queue_module, '_decrefs', handed_off)
    monkeypatch.setattr(queue_module, '_serving', {})
    monkeypatch.setattr(queue_module, '_dialed', {})
    queue_module._note_dialed(('localhost', 50001))
    queue_module._note_dialed(('192.0.2.1', 50001))  # TEST-NET-1: never an address of this host
    assert queue_module._dialed == {('localhost', 50001): ('127.0.0.1', True),
                                    ('192.0.2.1', 50001): ('192.0.2.1', False)}
    here = SimpleNamespace(address=('localhost', 50001), id='1')
    remote = SimpleNamespace(address=('192.0.2.1', 50001), id='2')

    def handed(token: SimpleNamespace) -> bool:
        """Run the finalizer on `token`: True if it handed the decref off, False if it ran the stock one."""
        before = len(calls)
        queue_module._decref(token, 'rest')
        if len(calls) > before:
            assert calls[-1] == (token, 'rest') and handed_off.empty()
            return False
        assert handed_off.get_nowait() == (token, 'rest')
        return True

    def serving(*listeners: tuple) -> None:
        queue_module._serving.clear()
        for listener in listeners:
            queue_module._serving[object()] = listener

    assert not handed(here) and not handed(remote)
    serving(('127.0.0.1', 50002))
    assert not handed(here) and not handed(remote)
    serving(('127.0.0.1', 50001))
    assert handed(here) and not handed(remote)
    serving(('0.0.0.0', 50001))  # The wildcard address answers at every address of this host
    assert handed(here) and not handed(remote)
    serving(('127.0.0.1', 0))  # A server binding an ephemeral port there may take any port
    assert handed(here) and not handed(remote)
    serving(('192.0.2.7', 50001))  # The same port number at another address
    assert not handed(here) and not handed(remote)
    unknown = SimpleNamespace(address=('unrecorded', 50001), id='3')  # Never recorded: the port decides
    assert handed(unknown) and not handed(SimpleNamespace(address=('unrecorded', 50002), id='4'))


@mark.unit
def test_shared_port_number_leaks_nothing(tls_args: List[str]) -> None:
    """A client of a server in another process, dropped while this process serves the same port number
    at another address, releases its objects on that server."""
    shared = shared_port()
    if shared is None:
        skip('no second local address can listen on a port alongside 127.0.0.1 here')
    port, host = shared
    run_probe('shared-port', tls_args, 1, str(port), host)


@mark.unit
def test_forked_child_never_resends_decrefs(tls_materials: tuple[str, str]) -> None:
    """A forked child of a serving process never sends the decrefs it inherits, so a live client of the
    parent keeps its queues after the child exits."""
    if 'fork' not in multiprocessing.get_all_start_methods():
        skip('fork is not available on this platform')
    run_probe('fork', list(tls_materials), 1)


@mark.unit
def test_dropped_client_never_stalls_server(tls_args: List[str]) -> None:
    """Each round a short-lived same-process client puts one item and is dropped as cyclic garbage."""
    run_probe('dropped-client', tls_args)


@mark.unit
def test_same_port_restart_never_stalls(tls_args: List[str]) -> None:
    """Each round serves a dropped same-process client, shuts down, and starts again on the same port."""
    run_probe('same-port-restart', tls_args)


# The probes, run as ``python test_queue_gc.py NAME ROUNDS [CERT KEY]`` by the tests above.


def probe_config(port: int = 0, tls: Optional[TLSConfig] = None, host: str = '127.0.0.1') -> QueueConfig:
    """Queue configuration for the probes (port 0 binds an ephemeral port)."""
    return QueueConfig(host=host, port=port, auth=AUTH, size=2, tls=tls)


def shared_port() -> Optional[tuple[int, str]]:
    """A port free at 127.0.0.1, and another local address that can listen on it at the same time.

    Linux can bind any of 127.0.0.0/8; macOS binds only 127.0.0.1 there, but lets the wildcard address
    listen on a port that 127.0.0.1 holds. None when neither works.
    """
    for other in ('127.0.0.2', '0.0.0.0'):
        with socket.socket() as first, socket.socket() as second:
            for sock in (first, second):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            first.bind(('127.0.0.1', 0))
            first.listen(1)
            port = first.getsockname()[1]
            try:
                second.bind((other, port))
                second.listen(1)
            except OSError:
                continue
            return port, other
    return None


def start_round(name: str, i: int) -> None:
    """Set round `i`'s collection threshold for probe `name` and arm the watchdog for it."""
    thresholds = THRESHOLDS[name]
    threshold = thresholds[i % len(thresholds)]
    if gc.get_threshold()[0] != threshold:
        gc.set_threshold(threshold, 1, 1)
    arm_watchdog(f'round {i} (threshold {threshold})')


def arm_watchdog(label: str) -> None:
    """Dump every thread's stack and exit the process if the next step takes too long."""
    print(label, flush=True)
    faulthandler.dump_traceback_later(ROUND_LIMIT, exit=True)


def probe_dropped_client(rounds: int, tls: Optional[TLSConfig]) -> None:
    """A same-process client in its own thread puts one item each round, then becomes garbage."""
    server = QueueServer(probe_config(tls=tls))
    server.start()
    port = server.address[1]

    def short_lived_client() -> None:
        client = QueueClient(probe_config(port, tls))
        client.connect()
        client.heartbeat.put(b'beat', timeout=WAIT)

    for i in range(rounds):
        start_round('dropped-client', i)
        thread = threading.Thread(target=short_lived_client, daemon=True)
        thread.start()
        thread.join()
        assert server.heartbeat.get(timeout=WAIT) == b'beat'
    arm_watchdog('shutdown')
    server.shutdown()


def probe_same_port_restart(rounds: int, tls: Optional[TLSConfig]) -> None:
    """A server serves a dropped same-process client and shuts down; a new one takes the same port."""
    for i in range(rounds):
        start_round('same-port-restart', i)
        with QueueServer(probe_config(tls=tls)) as server:
            port = server.address[1]
            client = QueueClient(probe_config(port, tls))
            client.connect()
            client.confirmed.put(b'ok', timeout=WAIT)
            del client
        with QueueServer(probe_config(port, tls)) as again:
            assert again.address[1] == port


def serve(host: str, port: str, *tls_files: str) -> None:
    """Serve queues at `host` and `port` until standard input closes (the other process of a probe)."""
    with QueueServer(probe_config(int(port), tls_from(list(tls_files)), host)):
        print('serving', flush=True)
        sys.stdin.read()


def serve_elsewhere(host: str, port: int, tls_files: List[str]) -> subprocess.Popen:
    """Start :func:`serve` in another process (in this probe's process group) and wait until it serves."""
    command = [sys.executable, os.path.abspath(__file__), 'serve', host, str(port), *tls_files]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == 'serving', 'the other server did not start'
    return proc


def probe_shared_port(rounds: int, port: int, host: str, tls_files: List[str]) -> None:
    """A client of a server in another process is dropped while this process serves the same port number
    at another address. Its decrefs reach that server, which is left holding no objects."""
    tls = tls_from(tls_files)
    other = serve_elsewhere('127.0.0.1', port, tls_files)
    try:
        for i in range(rounds):
            arm_watchdog(f'round {i} (elsewhere at 127.0.0.1:{port}, here at {host}:{port})')
            client = QueueClient(probe_config(port, tls))
            client.connect()
            client.heartbeat.put(b'beat', timeout=WAIT)
            counter = QueueClient(probe_config(port, tls))  # Never connected: only asks for the count
            assert counter._number_of_objects() == 4
            with QueueServer(probe_config(port, tls, host)) as here:
                assert here.address[1] == port
                del client
                gc.collect()
                wait_until(lambda: counter._number_of_objects() == 0)
                left = counter._number_of_objects()
                print(f'objects left elsewhere: {left}; decrefs waiting: {queue_module._decrefs.qsize()}',
                      flush=True)
                assert left == 0
    finally:
        other.stdin.close()
        try:
            other.wait(timeout=WAIT * 5)
        except subprocess.TimeoutExpired:
            other.kill()
            other.wait()


def child_connects(port: int, tls: TLSConfig) -> None:
    """In a forked child: connect a client, use it, drop it, and give anything it handed off time to go."""
    client = QueueClient(probe_config(port, tls))
    client.connect()
    client.heartbeat.put(b'child', timeout=WAIT)
    del client
    gc.collect()
    wait_until(queue_module._decrefs.empty)
    time.sleep(0.5)


def probe_fork(rounds: int, tls_files: List[str]) -> None:
    """A serving process forks while a dropped client's decrefs wait to be sent. After the child exits,
    the parent's live client still holds all four queues."""
    tls = tls_from(tls_files)
    assert tls is not None, 'the probe holds up the accept loop with a TLS handshake that never comes'
    fork = multiprocessing.get_context('fork')
    for i in range(rounds):
        arm_watchdog(f'round {i}')
        with QueueServer(probe_config(tls=tls)) as server:
            port = server.address[1]
            live = QueueClient(probe_config(port, tls))
            live.connect()
            dropped = QueueClient(probe_config(port, tls))
            dropped.connect()
            counter = QueueClient(probe_config(port, tls))  # Never connected: only asks for the count
            assert counter._number_of_objects() == 4
            # A peer that never starts its TLS handshake holds up the accept loop, and so the sender
            silent = socket.create_connection(('127.0.0.1', port))
            time.sleep(0.5)
            del dropped
            gc.collect()
            wait_until(lambda: queue_module._decrefs.qsize() >= 3)
            waiting = queue_module._decrefs.qsize()
            print(f'decrefs waiting at the fork: {waiting}', flush=True)
            assert waiting > 0, 'nothing was waiting to be sent at the fork'
            child = fork.Process(target=child_connects, args=(port, tls))
            child.start()
            silent.shutdown(socket.SHUT_RDWR)
            silent.close()
            child.join(timeout=WAIT * 10)
            assert child.exitcode == 0, f'the child exited {child.exitcode}'
            wait_until(queue_module._decrefs.empty)
            time.sleep(0.5)
            left = counter._number_of_objects()
            failed = []
            for name in ('scheduled', 'completed', 'heartbeat', 'confirmed'):
                try:
                    getattr(live, name).put(b'still', timeout=WAIT)
                except Exception as error:
                    failed.append(f'{name}: {error!r}')
            print(f'objects after the child exited: {left}; failed puts: {failed}', flush=True)
            assert left == 4 and not failed


PROBES = {'dropped-client': probe_dropped_client, 'same-port-restart': probe_same_port_restart}


def tls_from(tls_files: List[str]) -> Optional[TLSConfig]:
    """TLS on with the given cert and key, or off without them."""
    if not tls_files:
        return None
    cert, key = tls_files
    return TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)


def main(argv: List[str]) -> None:
    """Run one probe, then exit at once (lingering daemon threads are not waited on)."""
    name, *rest = argv
    if name == 'serve':
        serve(*rest)
        return
    rounds, *rest = rest
    if name == 'shared-port':
        port, host, *tls_files = rest
        probe_shared_port(int(rounds), int(port), host, tls_files)
    elif name == 'fork':
        probe_fork(int(rounds), rest)
    else:
        PROBES[name](int(rounds), tls_from(rest))
    faulthandler.cancel_dump_traceback_later()
    print(f'OK {rounds} rounds', flush=True)
    os._exit(0)


if __name__ == '__main__':
    main(sys.argv[1:])
