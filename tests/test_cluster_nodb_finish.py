# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
A cluster without a database tells "every result is in" from "work was left" by counting.

The server's receiver counts the results it has written. Once every launched client has exited,
at least one non-zero, a `--no-db` cluster reads that count behind the barrier that waits for every
returned result to be written, and compares it with the tasks its submitter put on the queue once
the input has been read to its end. A client that returned every result and then exited non-zero
is warned about, and the run ends as it did before the cluster watched its clients: once the server
evicts that client.

The new names are reached through their module, so each test here fails on its own (not the whole
module at import) on a tree without them.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Optional, Final

# Standard libs
import os
import sys
import time
import shlex
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from subprocess import Popen, TimeoutExpired, DEVNULL

# External libs
import pytest
from pytest import mark

# Internal libs
from tests.conftest import free_port
from tests.test_cluster_exit import kill_group
from tests.test_writeback import task_db, submit, dispatch_one, finish, confirm, CLIENT_A  # noqa: fixture
from hypershell.data.model import Task, serialize_tasks
from hypershell.server import Receiver, ReceiverState
from hypershell.cluster import watch


ADDRESS: Final[str] = '127.0.0.1'
"""Bind address of every cluster here, so nothing depends on resolving this host's name."""

HS: Final[str] = os.path.join(os.path.dirname(sys.executable), 'hs')
"""This tree's `hs`."""

TASKS: Final[int] = 5
"""Tasks in the command-line cluster's input."""


# --- The receiver's count ------------------------------------------------------------------------------------


def receive(receiver: Receiver, completed: queue.Queue, tasks: List[Task]) -> None:
    """Return one bundle of `tasks` through the completed queue, and step the receiver through it."""
    completed.put(serialize_tasks(tasks))
    assert receiver.unload_bundle() is ReceiverState.UNPACK
    assert receiver.unpack_bundle() is ReceiverState.UPDATE
    assert receiver.update_tasks() is ReceiverState.UNLOAD


@mark.unit
def test_receiver_counts_written_results_in_memory() -> None:
    """Without a database every returned result is written and counted; an empty bundle adds nothing."""
    completed: queue.Queue = queue.Queue()
    receiver = Receiver(queue=SimpleNamespace(completed=completed), in_memory=True)
    assert receiver.written == 0
    receive(receiver, completed, [Task.new(args=f'echo {n}') for n in range(3)])
    assert receiver.written == 3
    receive(receiver, completed, [])
    assert receiver.written == 3
    receive(receiver, completed, [Task.new(args=f'echo {n}') for n in range(2)])
    assert receiver.written == 5


@mark.unit
def test_receiver_counts_written_results_with_a_database(task_db) -> None:
    """With a database a result applied once counts once; its duplicate and a stale result do not count."""
    completed: queue.Queue = queue.Queue()
    receiver = Receiver(queue=SimpleNamespace(completed=completed))
    task_id = submit('echo counted')
    copy = finish(dispatch_one(task_id), 0, CLIENT_A)
    confirm(CLIENT_A, [task_id])
    receive(receiver, completed, [copy])
    assert receiver.written == 1
    receive(receiver, completed, [copy])
    assert receiver.written == 1
    cancelled_id = submit('echo cancelled')
    cancelled = finish(dispatch_one(cancelled_id), 0, CLIENT_A)
    Task.cancel(cancelled_id)
    receive(receiver, completed, [cancelled])
    assert receiver.written == 1


# --- The predicate, against a stand-in server ----------------------------------------------------------------


class MemoryStandIn:
    """A running server without a database: its receiver has written `written` results, its submitter as given."""

    def __init__(self, written: int, submitter: Optional[SimpleNamespace]) -> None:
        self.ended = threading.Event()
        self.scheduler = None
        self.receiver = SimpleNamespace(is_alive=lambda: False, machine=SimpleNamespace(written=written, held=[]))
        self.submitter = submitter

    def is_alive(self) -> bool:
        return not self.ended.is_set()

    def join(self, timeout: Optional[float] = None) -> None:
        self.ended.wait(timeout)


def submitter_of(task_count: int, alive: bool = False) -> SimpleNamespace:
    """A submitter that has put `task_count` tasks on the queue, still reading its input if `alive`."""
    return SimpleNamespace(is_alive=lambda: alive, task_count=task_count)


def timed_work_remains(server: Any) -> tuple:
    """What the predicate says of `server`, and the seconds it took to say it."""
    start = time.monotonic()
    remains = watch.work_remains(server)
    return remains, time.monotonic() - start


@mark.unit
def test_no_db_work_done_when_every_result_is_written() -> None:
    """Every submitted task has its result written and the input has ended: no work, decided at once."""
    remains, took = timed_work_remains(MemoryStandIn(written=TASKS, submitter=submitter_of(TASKS)))
    assert remains is False
    assert took < 1


@mark.unit
@mark.parametrize('written, alive', [
    pytest.param(TASKS - 1, False, id='one-missing'),
    pytest.param(TASKS, True, id='input-open'),
])
def test_no_db_work_still_remains(written: int, alive: bool) -> None:
    """A submitted task without its result, or input still being read, is work."""
    remains, _ = timed_work_remains(MemoryStandIn(written=written, submitter=submitter_of(TASKS, alive)))
    assert remains is True


@mark.unit
def test_no_db_without_submitter_still_has_no_work() -> None:
    """A server given no input has nothing to wait on."""
    remains, _ = timed_work_remains(MemoryStandIn(written=0, submitter=None))
    assert remains is False


# --- A command-line cluster whose client dies after its last result ------------------------------------------


STILL_RUNNING_AT: Final[float] = 13
"""Seconds from the start at which the cluster must still be running: well past the launch error's 10 s bound."""

# A real client patched to exit 1 in place of its FINISHED heartbeat, once its collector has returned
# every result. The launcher runs it as `python crashy.py <remote-exe> client ...`: the remote
# executable's word is dropped.
CRASHY_CLIENT: Final[str] = """\
import os, sys
import hypershell.client as client
def crash(self):
    sys.stderr.write('crashy client: results returned, exiting 1 before FINISHED\\n')
    sys.stderr.flush()
    os._exit(1)
client.ClientThread.wait_heartbeat = crash
from hypershell import main
sys.argv = ['hs', *sys.argv[2:]]
sys.exit(main())
"""


@mark.integration
def test_no_db_cluster_whose_client_dies_after_its_last_result_keeps_running(temp_site: Path) -> None:
    """Every result is written and the client's exit warned about; no launch error, and the cluster waits on."""
    site = Path(temp_site)
    (site / 'tasks.txt').write_text(''.join(f'echo {n}\n' for n in range(TASKS)))
    crashy = site / 'crashy.py'
    crashy.write_text(CRASHY_CLIENT)
    launcher = f'{shlex.quote(sys.executable)} {shlex.quote(str(crashy))}'
    out_path, err_path = site / 'cluster.out', site / 'cluster.err'
    with open(out_path, mode='w') as out, open(err_path, mode='w') as err:
        proc = Popen([HS, 'cluster', 'tasks.txt', '--no-db', '--no-tls', '-H', ADDRESS, '-p', str(free_port()),
                      '--launcher', launcher], cwd=site, env=dict(os.environ), stdin=DEVNULL, stdout=out,
                     stderr=err, start_new_session=True)
        try:
            proc.wait(timeout=STILL_RUNNING_AT)
            running = False
        except TimeoutExpired:
            running = True
        finally:
            kill_group(proc)
    text = err_path.read_text()
    assert sorted(out_path.read_text().split()) == [str(n) for n in range(TASKS)], text[-4000:]
    assert 'crashy client: results returned' in text, text[-4000:]
    assert 'every launched client has exited with tasks remaining' not in text, text[-4000:]
    assert running, f'exited {proc.returncode}:\n' + text[-4000:]
    assert any('WARNING' in line and 'exited with status 1' in line for line in text.splitlines()), text[-4000:]
