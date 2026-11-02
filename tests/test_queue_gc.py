# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A same-process client left to the cyclic garbage collector never stalls the in-process queue server.

A :class:`QueueClient` and its proxies refer to each other, so a dropped client waits for the cyclic
collector, which runs the proxies' decref finalizers in whichever thread triggers the collection,
server threads included. Each probe runs in its own process with a lowered collection threshold, so
collections are frequent. A watchdog ends a probe that stalls, so a stall fails the test within its
time limit instead of hanging the suite.
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
import threading
import subprocess
import faulthandler
from queue import SimpleQueue
from types import SimpleNamespace
from multiprocessing.managers import BaseProxy

# External libs
from pytest import fixture, mark

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


def run_probe(name: str, tls_args: List[str]) -> None:
    """Run probe `name` in its own process group and assert it finished every round."""
    command = [sys.executable, os.path.abspath(__file__), name, str(ROUNDS), *tls_args]
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
    assert proc.returncode == 0 and f'OK {ROUNDS}' in output, \
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
    """A decref for a port an in-process server holds is handed off; any other is the stock one."""
    calls = []
    monkeypatch.setattr(BaseProxy, '_decref', staticmethod(lambda *args: calls.append(args)))
    handed_off = SimpleQueue()
    monkeypatch.setattr(queue_module, '_decrefs', handed_off)
    monkeypatch.setattr(queue_module, '_serving', {})
    token = SimpleNamespace(address=('127.0.0.1', 50001), id='1')

    queue_module._decref(token, 'rest')
    assert calls == [(token, 'rest')] and handed_off.empty()

    queue_module._serving[object()] = 50002
    queue_module._decref(token, 'rest')
    assert len(calls) == 2 and handed_off.empty()

    queue_module._serving[object()] = 50001
    queue_module._decref(token, 'rest')
    assert len(calls) == 2 and handed_off.get_nowait() == (token, 'rest')

    queue_module._serving.clear()
    queue_module._serving[object()] = 0  # A server binding an ephemeral port may take any port
    queue_module._decref(token, 'rest')
    assert len(calls) == 2 and handed_off.get_nowait() == (token, 'rest')


@mark.unit
def test_dropped_client_never_stalls_server(tls_args: List[str]) -> None:
    """Each round a short-lived same-process client puts one item and is dropped as cyclic garbage."""
    run_probe('dropped-client', tls_args)


@mark.unit
def test_same_port_restart_never_stalls(tls_args: List[str]) -> None:
    """Each round serves a dropped same-process client, shuts down, and starts again on the same port."""
    run_probe('same-port-restart', tls_args)


# The probes, run as ``python test_queue_gc.py NAME ROUNDS [CERT KEY]`` by the tests above.


def probe_config(port: int = 0, tls: Optional[TLSConfig] = None) -> QueueConfig:
    """Queue configuration for the probes (port 0 binds an ephemeral port)."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls)


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


PROBES = {'dropped-client': probe_dropped_client, 'same-port-restart': probe_same_port_restart}


def main(argv: List[str]) -> None:
    """Run one probe, then exit at once (lingering daemon threads are not waited on)."""
    name, rounds, *tls_files = argv
    tls = None
    if tls_files:
        cert, key = tls_files
        tls = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    PROBES[name](int(rounds), tls)
    faulthandler.cancel_dump_traceback_later()
    print(f'OK {rounds} rounds', flush=True)
    os._exit(0)


if __name__ == '__main__':
    main(sys.argv[1:])
