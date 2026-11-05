# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The client's request loop, driven one action at a time against a fake dispatcher."""


# Type annotations
from __future__ import annotations
from typing import List, Optional

# Standard libs
import json
import time
from queue import Queue
from types import SimpleNamespace
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.config import Configuration, Namespace

# Internal libs
import hypershell.client
import hypershell.core.signal
from hypershell.core.signal import SIGUSR1
from hypershell.core.config import default
from hypershell.core.logging import INSTANCE
from hypershell.core.queue import QueueVersionError
from hypershell.client import ClientScheduler, SchedulerState, advertised_size
from hypershell.data.model import Task


TIMEOUT = 0.05


@fixture(autouse=True)
def short_timeout(monkeypatch) -> None:
    """Shorten the client's remote timeout so re-entry is observed quickly."""
    monkeypatch.setattr(hypershell.client, 'DEFAULT_REMOTE_TIMEOUT', TIMEOUT)


class FakeDispatcher:
    """Answers each request with the next scripted reply, recording the requests."""

    def __init__(self, *replies: dict) -> None:
        self.replies = list(replies)
        self.requests: List[dict] = []

    def request(self, text: str) -> str:
        assert isinstance(text, str)
        self.requests.append(json.loads(text))
        reply = self.replies.pop(0) if self.replies else {'protocol': 1, 'tasks': [], 'shutdown': False}
        return json.dumps(reply)


def machine(dispatcher: FakeDispatcher, bundlesize: Optional[int] = None, zone: Optional[str] = None,
            timeout: Optional[int] = None, no_confirm: bool = False) -> ClientScheduler:
    """A client scheduler on a fake dispatcher and plain local queues."""
    queue = SimpleNamespace(dispatcher=dispatcher, confirmed=Queue())
    return ClientScheduler(queue=queue, local=Queue(), no_confirm=no_confirm, timeout=timeout,
                           bundlesize=bundlesize, zone=zone)


def tasks(*args: str) -> List[dict]:
    """Task records as the server sends them."""
    return [Task.new(args=arg).to_json() for arg in args]


@mark.unit
def test_request_carries_identity_zone_and_size() -> None:
    """A request is JSON text naming the protocol, this client, its zone and its advertised size."""
    fake = FakeDispatcher()
    assert machine(fake, bundlesize=7, zone='gpu').request_bundle() is SchedulerState.REQUEST
    request, = fake.requests
    assert request['protocol'] == 1
    assert request['client'] == INSTANCE
    assert request['zone'] == 'gpu'
    assert request['size'] == 7
    assert request['wait'] == TIMEOUT
    assert machine(fake).request_bundle() is SchedulerState.REQUEST
    assert fake.requests[-1]['size'] is None and fake.requests[-1]['zone'] is None


@mark.unit
def test_reenters_on_empty_reply() -> None:
    """An empty reply sends the machine back to REQUEST."""
    fake = FakeDispatcher({'protocol': 1, 'tasks': [], 'shutdown': False})
    scheduler = machine(fake)
    start = time.monotonic()
    assert scheduler.request_bundle() is SchedulerState.REQUEST
    assert time.monotonic() - start < 1.0


@mark.unit
def test_tasks_are_unpacked_and_confirmed() -> None:
    """A reply with tasks is unpacked, confirmed and handed to the local queue in order."""
    fake = FakeDispatcher({'protocol': 1, 'tasks': tasks('echo a', 'echo b'), 'shutdown': False})
    scheduler = machine(fake)
    assert scheduler.request_bundle() is SchedulerState.UNPACK
    assert scheduler.unpack_bundle() is SchedulerState.PUT_CONFIRM
    assert [task.args for task in scheduler.tasks] == ['echo a', 'echo b']
    assert scheduler.put_confirm() is SchedulerState.POP_TASK
    assert scheduler.pop_task() is SchedulerState.PUT_LOCAL
    assert scheduler.put_local() is SchedulerState.POP_TASK
    assert scheduler.pop_task() is SchedulerState.PUT_LOCAL
    assert scheduler.put_local() is SchedulerState.POP_TASK
    assert scheduler.pop_task() is SchedulerState.REQUEST
    assert [scheduler.local.get_nowait().args for _ in range(2)] == ['echo a', 'echo b']


@mark.unit
def test_shutdown_reply_is_final() -> None:
    """A shutdown reply ends the request loop."""
    fake = FakeDispatcher({'protocol': 1, 'tasks': [], 'shutdown': True})
    assert machine(fake).request_bundle() is SchedulerState.FINAL


@mark.unit
def test_halts_on_signal(monkeypatch) -> None:
    """SIGUSR1 ends the request loop before another request is sent."""
    fake = FakeDispatcher()
    monkeypatch.setattr(hypershell.core.signal, 'RECEIVED', SIGUSR1)
    assert machine(fake).request_bundle() is SchedulerState.FINAL
    assert fake.requests == []


@mark.unit
def test_client_timeout_is_final() -> None:
    """With a client timeout, empty replies past the timeout end the loop."""
    scheduler = machine(FakeDispatcher(), timeout=1)
    assert scheduler.request_bundle() is SchedulerState.REQUEST
    scheduler.previous_received = datetime.now() - timedelta(seconds=2)
    assert scheduler.request_bundle() is SchedulerState.FINAL


@mark.unit
def test_error_reply_raises_version_error() -> None:
    """An error reply raises a version error carrying the server's message."""
    fake = FakeDispatcher({'protocol': 1, 'error': 'protocol', 'message': 'protocol 2 not supported (server speaks 1)',
                           'server_version': '0.0.0'})
    with pytest.raises(QueueVersionError, match='protocol 2'):
        machine(fake).request_bundle()


def layered(**layers: dict) -> Configuration:
    """A configuration with the built-in defaults and the given extra layers."""
    return Configuration(default=default, **{name: Namespace(data) for name, data in layers.items()})


@mark.unit
def test_advertise_only_an_explicit_size() -> None:
    """The command line, the environment or a config file is advertised; the built-in default is not."""
    assert advertised_size(None, layered()) is None
    assert advertised_size(4, layered()) == 4
    assert advertised_size(1, layered()) == 1
    assert advertised_size(None, layered(env={'submit': {'bundlesize': 7}})) == 7
    assert advertised_size(None, layered(env={'submit': {'bundlesize': 1}})) == 1
    assert advertised_size(None, layered(local={'submit': {'bundlesize': 3}})) == 3
    assert advertised_size(None, layered(user={'client': {'bundlesize': 9}})) is None
    assert advertised_size(5, layered(env={'submit': {'bundlesize': 7}})) == 5
