# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The dispatcher: staging pools cut on request, without a database.

Staged rows are fake pre-encoded task records; requests and replies are JSON text, as on the wire.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Iterator, List, Optional

# Standard libs
import json
import time
import random
import logging
import uuid
import threading
from queue import Queue

# External libs
import pytest
from pytest import mark, fixture

# Internal libs
from hypershell.core import dispatch
from hypershell.core.dispatch import Dispatcher, PROTOCOL
from hypershell.core.topology import TOPOLOGY_PROTOCOL, MAX_HANDBACK_TASKS, handback_text, read_handback_reply
from hypershell.data.model import Task, DEFAULT_ZONE_ID, serialize_tasks, zone_id


def ask(dispatcher: Dispatcher, client: str = 'c1', size: Optional[int] = None,
        wait: float = 0.2, zone: Optional[str] = None, **extra) -> Dict:
    """Send one request and decode the reply."""
    text = json.dumps({'protocol': PROTOCOL, 'client': client, 'host': 'h1', 'zone': zone,
                       'size': size, 'wait': wait, **extra})
    return json.loads(dispatcher.request(text))


def entries(count: int, start: int = 0) -> List[tuple]:
    """Pre-encoded fake task records with ids t<n>."""
    return [(f't{n}', json.dumps({'id': f't{n}', 'args': f'echo {n}'})) for n in range(start, start + count)]


def ids(reply: Dict) -> List[str]:
    """Ids of the tasks in a reply."""
    return [task['id'] for task in reply['tasks']]


@fixture
def records() -> Iterator[List[logging.LogRecord]]:
    """Every record the dispatcher logs, at any level."""
    captured: List[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger(dispatch.__name__)
    handler, level = ListHandler(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)


@mark.unit
def test_cut_follows_advertised_size() -> None:
    """A cut is the advertised size; no size means the server's; any size is clamped to the cap."""
    dispatcher = Dispatcher(bundlesize=2, bundlemax=4)
    dispatcher.stage(DEFAULT_ZONE_ID, entries(20))
    assert ids(ask(dispatcher, size=3)) == ['t0', 't1', 't2']
    assert ids(ask(dispatcher, size=None)) == ['t3', 't4']
    assert ids(ask(dispatcher, size=5000)) == ['t5', 't6', 't7', 't8']
    assert ids(ask(dispatcher, size=0)) == ['t9']
    assert Dispatcher(bundlesize=50, bundlemax=10).cap == 50
    assert Dispatcher(bundlesize=1).cap == 1000


@mark.unit
def test_reply_shape() -> None:
    """A reply is JSON data only: the protocol, task records as mappings, and the shutdown flag."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(1))
    reply = ask(dispatcher, size=1)
    assert reply == {'protocol': PROTOCOL, 'tasks': [{'id': 't0', 'args': 'echo 0'}], 'shutdown': False}


@mark.unit
def test_partial_reply_is_immediate() -> None:
    """Fewer staged than asked: the staged ones come back at once."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(2))
    start = time.monotonic()
    assert ids(ask(dispatcher, size=5, wait=2)) == ['t0', 't1']
    assert time.monotonic() - start < 0.5


@mark.unit
def test_empty_reply_within_wait() -> None:
    """Nothing staged: an empty reply once the wait passes, never later than the server's bound."""
    dispatcher = Dispatcher(max_wait=0.5)
    start = time.monotonic()
    assert ask(dispatcher, wait=0.3) == {'protocol': PROTOCOL, 'tasks': [], 'shutdown': False}
    assert 0.25 < time.monotonic() - start < 0.3 + 0.5
    start = time.monotonic()
    assert ask(dispatcher, wait=60)['tasks'] == []
    assert time.monotonic() - start < 0.5 + 0.5


def requester(dispatcher: Dispatcher, client: str, results: Dict[str, List[str]], **options) -> threading.Thread:
    """Start a thread that sends one request and records the ids it got."""
    thread = threading.Thread(target=lambda: results.__setitem__(client, ids(ask(dispatcher, client, **options))))
    thread.start()
    return thread


@mark.unit
def test_requests_are_served_in_arrival_order() -> None:
    """Three waiters on an empty pool are served first-come-first-served."""
    dispatcher, results = Dispatcher(), {}
    threads = []
    for client in ('w1', 'w2', 'w3'):
        threads.append(requester(dispatcher, client, results, size=1, wait=2))
        time.sleep(0.1)
    dispatcher.stage(DEFAULT_ZONE_ID, entries(3))
    for thread in threads:
        thread.join(timeout=5)
    assert results == {'w1': ['t0'], 'w2': ['t1'], 'w3': ['t2']}


@mark.unit
def test_no_task_is_delivered_twice() -> None:
    """Eight concurrent requesters drain a thousand staged tasks: each is delivered exactly once."""
    dispatcher = Dispatcher(bundlesize=1)
    dispatcher.stage(DEFAULT_ZONE_ID, entries(1000))
    delivered: List[str] = []
    lock = threading.Lock()

    def drain(client: str) -> None:
        rng = random.Random(client)
        while True:
            got = ids(ask(dispatcher, client, size=rng.randint(1, 7), wait=0.05))
            if not got:
                return
            with lock:
                delivered.extend(got)

    threads = [threading.Thread(target=drain, args=(f'c{n}', )) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(delivered) == 1000
    assert sorted(delivered) == sorted(f't{n}' for n in range(1000))


@mark.unit
def test_stage_skips_ids_already_staged() -> None:
    """A row listed twice in one load is staged once."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(2) + entries(1))
    assert ids(ask(dispatcher, size=10)) == ['t0', 't1']


@mark.unit
def test_cut_wakes_the_scheduler() -> None:
    """A scheduler waiting for need is woken by a cut, well before its timeout."""
    dispatcher = Dispatcher(bundlesize=1, queuesize=1)
    dispatcher.stage(DEFAULT_ZONE_ID, entries(2))
    generation, refills = dispatcher.refills()
    assert refills == []
    threading.Timer(0.2, lambda: ask(dispatcher, size=1)).start()
    start = time.monotonic()
    dispatcher.wait_need(generation, timeout=5)
    assert time.monotonic() - start < 1.0
    assert dispatcher.refills()[1] == [(DEFAULT_ZONE_ID, 1)]


@mark.unit
def test_wait_need_does_not_miss_an_earlier_cut() -> None:
    """A cut between reading the refills and waiting is not lost: the wait returns at once."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(2))
    generation, _ = dispatcher.refills()
    ask(dispatcher, size=1)
    start = time.monotonic()
    dispatcher.wait_need(generation, timeout=5)
    assert time.monotonic() - start < 0.5


@mark.unit
def test_refill_mark_bounds_the_pool() -> None:
    """The default zone is always demanded at no less than the server's size, refilled up to queuesize x S."""
    dispatcher = Dispatcher(bundlesize=2, queuesize=1)
    assert dispatcher.refills()[1] == [(DEFAULT_ZONE_ID, 2)]
    dispatcher.stage(DEFAULT_ZONE_ID, entries(2))
    assert dispatcher.refills()[1] == [(DEFAULT_ZONE_ID, 2)]
    dispatcher.stage(DEFAULT_ZONE_ID, entries(2, start=2))
    assert dispatcher.refills()[1] == []
    assert ids(ask(dispatcher, size=5)) == ['t0', 't1', 't2', 't3']
    assert dispatcher.refills()[1] == [(DEFAULT_ZONE_ID, 5)]


@mark.unit
def test_finish_wakes_waiters_and_drains_first() -> None:
    """After finish, staged tasks are still delivered; shutdown comes only once the pool is empty."""
    dispatcher = Dispatcher()
    results = {}
    thread = threading.Thread(target=lambda: results.update(reply=ask(dispatcher, wait=5)))
    thread.start()
    time.sleep(0.2)
    start = time.monotonic()
    dispatcher.finish()
    thread.join(timeout=5)
    assert time.monotonic() - start < 1.0
    assert results['reply'] == {'protocol': PROTOCOL, 'tasks': [], 'shutdown': True}

    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(3))
    dispatcher.finish()
    first = ask(dispatcher, size=2)
    assert (ids(first), first['shutdown']) == (['t0', 't1'], False)
    second = ask(dispatcher, size=2)
    assert (ids(second), second['shutdown']) == (['t2'], False)
    assert ask(dispatcher, size=2)['shutdown'] is True


@mark.unit
@mark.parametrize('text, kind', [
    ('not json', 'request'),
    ('[]', 'request'),
    ('{}', 'protocol'),
    (json.dumps({'protocol': PROTOCOL}), 'request'),
    (json.dumps({'protocol': PROTOCOL, 'client': 'c1', 'size': 'many'}), 'request'),
    (json.dumps({'protocol': PROTOCOL, 'client': 'c1', 'size': True}), 'request'),
    (json.dumps({'protocol': PROTOCOL, 'client': 'c1', 'wait': 'soon'}), 'request'),
    (json.dumps({'protocol': PROTOCOL, 'client': 'c1', 'zone': 7}), 'request'),
    (json.dumps({'protocol': PROTOCOL, 'client': 'c1', 'zone': 'a\x00b'}), 'request'),
    (json.dumps({'protocol': PROTOCOL, 'client': 'c' * 100_000}), 'request'),
    (json.dumps({'protocol': PROTOCOL + 1, 'client': 'c1'}), 'protocol'),
    (json.dumps({'protocol': 'one', 'client': 'c1'}), 'protocol'),
])
def test_bad_request_gets_an_error_reply(text: str, kind: str) -> None:
    """A malformed or mismatched request is answered with an error reply; the call never raises."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(1))
    reply = json.loads(dispatcher.request(text))
    assert reply['protocol'] == PROTOCOL
    assert reply['error'] == kind
    assert reply['message'] and reply['server_version']
    if text.startswith('{"protocol": 2'):
        assert '1' in reply['message'] and '2' in reply['message']
    assert ids(ask(dispatcher, size=1)) == ['t0']  # Nothing was cut for the bad request


@mark.unit
def test_non_text_request_gets_an_error_reply() -> None:
    """A request that is not text is answered with an error reply."""
    assert json.loads(Dispatcher().request(b'{}'))['error'] == 'request'  # noqa: deliberate wrong type


@mark.unit
def test_dispatch_is_logged(records: List[logging.LogRecord]) -> None:
    """Each non-empty reply is logged at DEBUG with its size, client and zone."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(3))
    ask(dispatcher, client='abc', size=2)
    ask(dispatcher, client='abc', size=2, wait=0)
    lines = [r.getMessage() for r in records if r.getMessage().startswith('Dispatched')]
    assert lines == ['Dispatched 2 tasks to client abc (zone default)', 'Dispatched 1 tasks to client abc (zone default)']
    assert all(r.levelno == logging.DEBUG for r in records if r.getMessage().startswith('Dispatched'))


@mark.unit
def test_in_memory_drains_and_recuts_inbound_bundles() -> None:
    """In memory, default-zone requests drain bundles from the inbound queue and re-cut them to size."""
    inbound: Queue = Queue()
    first = [Task.new(args=f'echo {n}') for n in range(3)]
    second = [Task.new(args=f'echo {n}') for n in range(3, 6)]
    inbound.put(serialize_tasks(first))
    inbound.put(serialize_tasks(second))
    dispatcher = Dispatcher(inbound=inbound)
    assert ids(ask(dispatcher, size=2)) == [task.id for task in first[:2]]
    assert ids(ask(dispatcher, size=4)) == [task.id for task in first[2:] + second]
    assert ask(dispatcher, size=4, zone='gpu', wait=0.1)['tasks'] == []
    start = time.monotonic()
    assert ask(dispatcher, size=1, wait=0.3)['tasks'] == []
    assert time.monotonic() - start < 0.3 + 0.5
    later = [Task.new(args='echo later')]
    threading.Timer(0.1, lambda: inbound.put(serialize_tasks(later))).start()
    assert ids(ask(dispatcher, size=1, wait=2)) == [later[0].id]
    dispatcher.finish()
    assert ask(dispatcher, size=1)['shutdown'] is True
    assert ask(dispatcher, size=1, zone='gpu')['shutdown'] is True


@mark.unit
def test_in_memory_shutdown_waits_for_inbound() -> None:
    """In memory, shutdown waits until the inbound queue is drained too."""
    inbound: Queue = Queue()
    task = Task.new(args='echo last')
    inbound.put(serialize_tasks([task]))
    dispatcher = Dispatcher(inbound=inbound)
    dispatcher.finish()
    reply = ask(dispatcher, size=1)
    assert (ids(reply), reply['shutdown']) == ([task.id], False)
    assert ask(dispatcher, size=1)['shutdown'] is True


@mark.unit
def test_staged_rows_are_taken_back_for_revert() -> None:
    """Rows still staged at shutdown are handed back by id; unstamped (in-memory) rows are not."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, entries(3))
    ask(dispatcher, size=1)
    assert dispatcher.take_staged() == ['t1', 't2']
    assert dispatcher.take_staged() == []
    assert ask(dispatcher, size=5, wait=0)['tasks'] == []


# --- Hand-back of a retired relay's tasks -------------------------------------------------------------

RELAY = str(uuid.uuid4())
STAMP = '2026-11-13 10:15:42.123456-05:00'


def held(count: int) -> List[tuple]:
    """`count` (task id, schedule time) assignments a relay holds."""
    return [(str(uuid.uuid4()), STAMP) for _ in range(count)]


@mark.unit
def test_withdraw_takes_only_the_named_rows_still_staged_in_the_zone() -> None:
    """Named rows still staged leave the pool, in order for the rest; a row already cut for a client, one in
    another zone and one never staged are not returned, and a zone with no pool returns nothing."""
    dispatcher = Dispatcher()
    gpu = zone_id('gpu')
    rows = [(str(uuid.uuid4()), '{}') for _ in range(4)]
    dispatcher.stage(gpu, rows)
    other = str(uuid.uuid4())
    dispatcher.stage(DEFAULT_ZONE_ID, [(other, '{}')])
    cut = json.loads(dispatcher.request(json.dumps({'protocol': PROTOCOL, 'client': 'a', 'zone': 'gpu',
                                                    'size': 1, 'wait': 0})))['tasks']
    assert len(cut) == 1
    named = [rows[0][0], rows[2][0], other, str(uuid.uuid4())]
    assert dispatcher.withdraw(gpu, named) == [rows[2][0]]
    assert dispatcher.withdraw(gpu, named) == []
    assert dispatcher.staged(gpu) == 2 and dispatcher.staged(DEFAULT_ZONE_ID) == 1
    assert dispatcher.withdraw(str(uuid.uuid4()), named) == []
    remaining = json.loads(dispatcher.request(json.dumps({'protocol': PROTOCOL, 'client': 'a', 'zone': 'gpu',
                                                          'size': 5, 'wait': 0})))['tasks']
    assert len(remaining) == 2


@mark.unit
def test_handback_is_queued_for_the_scheduler_in_arrival_order() -> None:
    """Each hand-back is queued as given and acknowledged with its count; the scheduler takes them all at once,
    oldest first, and a second take finds none. An empty hand-back is acknowledged and queues nothing."""
    dispatcher = Dispatcher()
    first, second = held(3), held(1)
    other = str(uuid.uuid4())
    assert read_handback_reply(dispatcher.handback(handback_text(RELAY, first))) == 3
    assert read_handback_reply(dispatcher.handback(handback_text(other, second))) == 1
    assert read_handback_reply(dispatcher.handback(handback_text(RELAY, []))) == 0
    assert dispatcher.take_handbacks() == [(RELAY, first), (other, second)]
    assert dispatcher.take_handbacks() == []


@mark.unit
def test_handback_wakes_the_scheduler() -> None:
    """A scheduler waiting for need is woken by a hand-back, well before its timeout; an empty one wakes nothing."""
    dispatcher = Dispatcher()
    generation, _ = dispatcher.refills()
    dispatcher.handback(handback_text(RELAY, []))
    assert dispatcher.generation == generation
    threading.Timer(0.2, lambda: dispatcher.handback(handback_text(RELAY, held(2)))).start()
    start = time.monotonic()
    assert dispatcher.wait_need(generation, timeout=5) is True
    assert time.monotonic() - start < 1.0
    assert len(dispatcher.take_handbacks()[0][1]) == 2


@mark.unit
def test_bad_handback_gets_an_error_reply(records: List[logging.LogRecord]) -> None:
    """A hand-back the server cannot read is refused with an error reply and a warning, never raised, and
    nothing is queued."""
    dispatcher = Dispatcher()
    over = json.loads(handback_text(RELAY, held(MAX_HANDBACK_TASKS)))
    over['tasks'].append({'id': str(uuid.uuid4()), 'schedule_time': STAMP})
    for text in ('nope', None, json.dumps({'protocol': 2, 'client': RELAY, 'tasks': []}),
                 json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'client': 'relay-a', 'tasks': []}),
                 json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'client': RELAY, 'tasks': [{'id': RELAY}]}),
                 json.dumps(over), 'x' * (300 * 1024)):
        reply = json.loads(dispatcher.handback(text))
        assert reply['protocol'] == TOPOLOGY_PROTOCOL and reply['error'] == 'request' and reply['message']
    assert dispatcher.take_handbacks() == []
    refusals = [r for r in records if r.getMessage().startswith('Refused hand-back')]
    assert len(refusals) == 7 and all(r.levelno == logging.WARNING for r in refusals)


@mark.unit
def test_handback_is_refused_in_memory(records: List[logging.LogRecord]) -> None:
    """A server without a database has nothing to revert: the hand-back is refused, so the relay keeps its rows."""
    dispatcher = Dispatcher(inbound=Queue())
    generation = dispatcher.generation
    reply = dispatcher.handback(handback_text(RELAY, held(2)))
    assert json.loads(reply)['error'] == 'request'
    with pytest.raises(ValueError, match='no database'):
        read_handback_reply(reply)
    assert dispatcher.take_handbacks() == [] and dispatcher.generation == generation
    assert any('Refused hand-back from relay' in r.getMessage() and r.levelno == logging.WARNING for r in records)
    configured = Dispatcher().configure(in_memory=True)
    assert 'error' in json.loads(configured.handback(handback_text(RELAY, held(1))))
