# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Version skew on the work-request protocol: old clients are refused, old servers are named.

An old client is one that registers only the four queues and creates `_get_scheduled()` with no
argument first, as every release before the work-request protocol does. An old server is one whose
registry serves only the four queues.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Tuple

# Standard libs
import os
import json
import time
import signal
import logging
import subprocess
from queue import Queue
from pathlib import Path
from multiprocessing.managers import BaseManager, RemoteError, AutoProxy

# External libs
import pytest
from pytest import mark, fixture

# Internal libs
from hypershell.core import queue as queue_module
from hypershell.core.queue import (QueueServer, QueueClient, QueueConfig, QueueVersionError, SecureManager,
                                   QUEUE_EXPOSED, DISPATCH_EXPOSED, PROTOCOL)
from hypershell.client import ClientScheduler
from hypershell.data.model import DEFAULT_ZONE_ID


AUTH = 'dispatch-version-key-0123456789'
NAMES = ('scheduled', 'completed', 'heartbeat', 'confirmed')


def config(port: int = 0) -> QueueConfig:
    """Loopback queue configuration without TLS."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2)


class OldClient(BaseManager):
    """A client of a release before the work-request protocol: four queues, `_get_scheduled()` first."""


for _name in NAMES:
    OldClient.register(f'_get_{_name}')


@fixture
def records() -> Iterator[List[logging.LogRecord]]:
    """Every record the queue module logs, at any level."""
    captured: List[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger(queue_module.__name__)
    handler, level = ListHandler(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)


def too_old(records: List[logging.LogRecord]) -> List[logging.LogRecord]:
    """The refusal warnings among `records`."""
    return [r for r in records if r.levelno == logging.WARNING and r.getMessage().startswith('Client too old')]


def ask(dispatcher, wait: float = 0.0) -> dict:
    """One default-zone work request, decoded."""
    return json.loads(dispatcher.request(json.dumps({'protocol': PROTOCOL, 'client': 'new', 'host': 'h',
                                                     'zone': None, 'size': 5, 'wait': wait})))


@mark.unit
def test_dispatcher_remote_surface_is_pinned() -> None:
    """A remote client may call exactly `request` and `takes_submissions` on the dispatcher, nothing else.

    The surface is pickle-framed RPC: adding a method to it or dropping one must change this test, both
    the declared tuple and the surface a connected client is handed.
    """
    surface = ('request', 'takes_submissions')
    assert DISPATCH_EXPOSED == surface
    with QueueServer(config()) as server:
        client = QueueClient(config(server.address[1]))
        client.connect()
        assert type(client.dispatcher)._exposed_ == surface
        assert ask(client.dispatcher)['tasks'] == []
        assert isinstance(client.dispatcher.takes_submissions(), bool)
        assert not hasattr(client.dispatcher, 'stage')


@mark.unit
def test_old_client_is_refused_while_new_client_works(records: List[logging.LogRecord]) -> None:
    """An old client's first create is refused as too old and logged once; a new client is served meanwhile."""
    with QueueServer(config()) as server:
        server.dispatcher.stage(DEFAULT_ZONE_ID, [('t1', '{"id": "t1"}'), ('t2', '{"id": "t2"}')])
        new = QueueClient(config(server.address[1]))
        new.connect()
        old = OldClient(address=('127.0.0.1', server.address[1]), authkey=AUTH.encode())
        old.connect()
        start = time.monotonic()
        with pytest.raises(RemoteError, match='too old'):
            old._get_scheduled()
        assert time.monotonic() - start < 5
        assert [task['id'] for task in ask(new.dispatcher)['tasks']] == ['t1', 't2']
        assert wait_for(lambda: len(too_old(records)) == 1)
        time.sleep(0.2)
        assert len(too_old(records)) == 1
        assert 'work-request protocol' in too_old(records)[0].getMessage()


def provider(obj):
    """A registry callable taking no arguments, as releases before the protocol register."""
    return lambda: obj


def stock_server() -> Tuple[SecureManager, dict]:
    """A server whose registry has only the four queues, as releases before the protocol serve."""
    queues = {name: Queue(maxsize=2) for name in NAMES}
    manager = SecureManager(address=('127.0.0.1', 0), authkey=AUTH.encode())
    registry = {f'_get_{name}': (provider(q), QUEUE_EXPOSED, None, AutoProxy) for name, q in queues.items()}
    manager._serve_in_process(registry)
    return manager, queues


@mark.unit
def test_new_client_against_old_server_names_the_mismatch() -> None:
    """A new client connecting to a server without the protocol fails at once with a version error."""
    manager, _ = stock_server()
    try:
        client = QueueClient(config(manager.address[1]))
        start = time.monotonic()
        with pytest.raises(QueueVersionError, match='work-request protocol'):
            client.connect()
        assert time.monotonic() - start < 5
    finally:
        manager.shutdown()


class NewerServer:
    """A dispatcher answering every request with a newer protocol's mismatch."""

    @staticmethod
    def request(text: str) -> str:
        return json.dumps({'protocol': 2, 'error': 'protocol', 'server_version': '9.9.9',
                           'message': f'work-request protocol {json.loads(text)["protocol"]} is not supported '
                                      f'(this server speaks protocol 2)'})


@mark.unit
def test_reply_from_another_protocol_raises_with_both_numbers() -> None:
    """A reply in another protocol raises a version error naming both protocol numbers."""
    queue = type('Remote', (), {'dispatcher': NewerServer(), 'confirmed': Queue()})()
    scheduler = ClientScheduler(queue=queue, local=Queue())
    with pytest.raises(QueueVersionError) as error:
        scheduler.request_bundle()
    assert 'protocol 2' in str(error.value) and f'protocol {PROTOCOL}' in str(error.value)


@mark.integration
def test_client_command_against_old_server_exits_with_one_error(tmp_path: Path) -> None:
    """`hs client` against a server without the protocol exits non-zero with exactly one critical line."""
    manager, _ = stock_server()
    proc = None
    try:
        env = {**os.environ, 'HYPERSHELL_SITE': str(tmp_path), 'HYPERSHELL_LOGGING_LEVEL': 'INFO'}
        proc = subprocess.Popen(['hs', 'client', '-H', '127.0.0.1', '-p', str(manager.address[1]), '-k', AUTH,
                                 '--no-tls'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        start = time.monotonic()
        _, stderr = proc.communicate(timeout=30)
        elapsed = time.monotonic() - start
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        manager.shutdown()
    lines = stderr.decode().splitlines()
    critical = [line for line in lines if line.startswith('CRITICAL')]
    assert proc.returncode != 0
    assert elapsed < 15
    assert len(critical) == 1, lines
    assert 'QueueVersionError' in critical[0] and 'work-request protocol' in critical[0]


def wait_for(predicate, timeout: float = 5.0) -> bool:
    """Poll `predicate` until it holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
