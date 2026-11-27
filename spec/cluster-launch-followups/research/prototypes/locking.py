"""Prototype helpers: a second SQLite connection holding BEGIN EXCLUSIVE, and a short-busy-wait test engine."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Dict, Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity

BUSY_TIMEOUT = 0.5
"""Seconds the test engine's connections wait on a lock before `database is locked` (the product's is 5 s)."""


@contextmanager
def locked_db(path: Path, busy_timeout: float = BUSY_TIMEOUT) -> Iterator[Engine]:
    """Bind the global session to a fresh SQLite file at `path` whose reads give up after `busy_timeout`."""
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': busy_timeout})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


@contextmanager
def exclusive(path: Path) -> Iterator[sqlite3.Connection]:
    """Hold BEGIN EXCLUSIVE on `path` from a second, raw connection for the body of the block."""
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    conn.execute('BEGIN EXCLUSIVE')
    try:
        yield conn
    finally:
        conn.execute('COMMIT')
        conn.close()


def count_rows(path: Path, table: str = 'task') -> int:
    """Rows in `table` read through a short-lived connection; -1 while the file or table is missing or busy."""
    if not Path(path).exists():
        return -1
    try:
        conn = sqlite3.connect(str(path), timeout=0.2)
        try:
            return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return -1


def staged(path: Path) -> int:
    """Tasks the scheduler has staged (schedule_time set); -1 while busy."""
    try:
        conn = sqlite3.connect(str(path), timeout=0.2)
        try:
            return conn.execute('SELECT count(*) FROM task WHERE schedule_time IS NOT NULL').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return -1


class LockHolder(threading.Thread):
    """Wait until `path` holds `rows` task rows, take BEGIN EXCLUSIVE, touch `flag`, hold `hold` seconds, release.

    Records monotonic `locked_at` and `released_at`. `abort` ends the wait for rows early. Bounded throughout.
    """

    def __init__(self, path: Path, rows: int, hold: float, flag: Optional[Path] = None,
                 ready_timeout: float = 20, log_path: Optional[Path] = None, sync: Optional[str] = None) -> None:
        super().__init__(daemon=True)
        self.path, self.rows, self.hold, self.flag, self.ready_timeout = Path(path), rows, hold, flag, ready_timeout
        self.log_path, self.sync = log_path, sync
        self.abort = threading.Event()
        self.times: Dict[str, Any] = {'locked_at': None, 'released_at': None, 'error': None}

    def run(self) -> None:
        try:
            deadline = time.monotonic() + self.ready_timeout
            while count_rows(self.path) < self.rows:
                if self.abort.is_set() or time.monotonic() > deadline:
                    self.times['error'] = f'never saw {self.rows} rows'
                    return
                time.sleep(0.02)
            # Wait until the cluster has launched (its server is up, so its scheduler has started) and the
            # scheduler has staged tasks: with no client it then waits on demand and reads nothing, so the
            # lock meets only the cluster's own reads (a scheduler read under it would end the server: out
            # of scope). A short settle covers the last staging write.
            while self.sync is not None and not (self.sync in self.log_path.read_text() and staged(self.path) > 0):
                if self.abort.is_set() or time.monotonic() > deadline:
                    self.times['error'] = f'never saw {self.sync!r} with staged tasks'
                    return
                time.sleep(0.02)
            if self.sync is not None:
                time.sleep(0.5)
            with exclusive(self.path):
                self.times['locked_at'] = time.monotonic()
                if self.flag is not None:
                    self.flag.touch()
                self.abort.wait(self.hold)
            self.times['released_at'] = time.monotonic()
        except Exception as error:  # surfaced by the test
            self.times['error'] = repr(error)
