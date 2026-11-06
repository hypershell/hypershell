# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The server's heart monitor tells every client to disconnect, including late ones.

A disconnect is one sentinel on the shared task queue, taken by whichever client reads next.
Clients are counted by heartbeat, and a client can take (and finish) tasks before its first
heartbeat reaches the server, so the count at the moment of the signal can be short. Each test
drives the monitor's states by hand over in-process queues, in a given order of arrival.
"""


# Type annotations
from __future__ import annotations
from typing import List

# Standard libs
from queue import Queue
from dataclasses import dataclass, field

# Internal libs
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.core.queue import make_sentinel
from hypershell.server import HeartMonitor, HeartbeatState


@dataclass
class FakeQueue:
    """The two server queues the heart monitor uses."""

    heartbeat: Queue = field(default_factory=Queue)
    scheduled: Queue = field(default_factory=Queue)

    def sentinels(self: FakeQueue) -> int:
        """Count of disconnects posted so far (the queue holds nothing else here)."""
        items = []
        while not self.scheduled.empty():
            items.append(self.scheduled.get_nowait())
        for item in items:
            self.scheduled.put(item)
        assert all(item == make_sentinel() for item in items)
        return len(items)


def new_monitor() -> HeartMonitor:
    """A heart monitor over fake queues, without a database."""
    return HeartMonitor(queue=FakeQueue(), in_memory=True)  # noqa: duck-typed queue server


def beat(name: str, state: ClientState = ClientState.RUNNING) -> Heartbeat:
    """A heartbeat from client `name`."""
    return Heartbeat.new(uuid=name, host='host-' + name, state=state)


def deliver(monitor: HeartMonitor, *heartbeats: Heartbeat) -> None:
    """The monitor takes each heartbeat, in order, as its next one."""
    for hb in heartbeats:
        monitor.queue.heartbeat.put(hb.pack())
        assert monitor.get_next() is HeartbeatState.UPDATE
        assert monitor.update_client() is HeartbeatState.SWITCH


def signal(monitor: HeartMonitor) -> None:
    """Scheduling is over: the server tells the connected clients to disconnect."""
    monitor.scheduler_done = True
    monitor.should_signal = True
    assert monitor.switch_mode() is HeartbeatState.SIGNAL
    assert monitor.signal_clients() is HeartbeatState.SWITCH


def finished(monitor: HeartMonitor) -> bool:
    """The monitor would stop now."""
    return monitor.switch_mode() is HeartbeatState.FINAL


def test_signal_posts_one_disconnect_per_registered_client() -> None:
    """Every client registered when the signal is sent gets a disconnect."""
    monitor = new_monitor()
    deliver(monitor, beat('a'), beat('b'))
    signal(monitor)
    assert monitor.queue.sentinels() == 2
    deliver(monitor, beat('a', ClientState.FINISHED), beat('b', ClientState.FINISHED))
    assert monitor.queue.sentinels() == 2
    assert finished(monitor)


def test_client_registered_after_signal_gets_disconnect() -> None:
    """A client whose first heartbeat arrives after the signal is told to disconnect too."""
    monitor = new_monitor()
    deliver(monitor, beat('a'))
    signal(monitor)
    assert monitor.queue.sentinels() == 1
    deliver(monitor, beat('b'))
    assert monitor.queue.sentinels() == 2, 'Late client was never told to disconnect'
    deliver(monitor, beat('b'), beat('a', ClientState.FINISHED), beat('b', ClientState.FINISHED))
    assert monitor.queue.sentinels() == 2
    assert finished(monitor)


def test_client_finished_unregistered_after_signal_replaces_disconnect() -> None:
    """A client that finishes before it ever registered may have taken another's disconnect: one is posted in its place."""
    monitor = new_monitor()
    deliver(monitor, beat('a'))
    signal(monitor)
    monitor.queue.scheduled.get_nowait()  # Client b, not yet registered, takes the one disconnect
    deliver(monitor, beat('b', ClientState.FINISHED))
    assert monitor.queue.sentinels() == 1, 'Registered client was left without a disconnect'
    assert not finished(monitor)
    deliver(monitor, beat('a', ClientState.FINISHED))
    assert finished(monitor)


def test_no_disconnect_before_signal() -> None:
    """Clients that come and go before the signal get no disconnect, and the signal counts only those connected."""
    monitor = new_monitor()
    deliver(monitor, beat('a'), beat('b'), beat('a', ClientState.FINISHED), beat('c', ClientState.FINISHED))
    assert monitor.queue.sentinels() == 0
    signal(monitor)
    assert monitor.queue.sentinels() == 1
