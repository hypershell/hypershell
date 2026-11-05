"""Prototype v3: in-process serve loop with lock-guarded live-connection set, no accepter join.

Usage: python proto3.py <fork|spawn> [full]
"""
from __future__ import annotations

import sys
import time
import queue
import socket
import threading
import selectors
import multiprocessing as mp
from multiprocessing import util
from multiprocessing.managers import Server, State, AutoProxy, BaseProxy

from hypershell.core.queue import QueueServer, QueueClient, QueueConfig, make_sentinel
from hypershell.core.tls import install_process_context, clear_process_context

import proto  # helpers: free_port, tls_cfg, nfd, AUTH

POLL = 0.25
EXPOSED = ('put', 'get', 'put_nowait', 'get_nowait', 'task_done', 'join', 'qsize', 'empty', 'full')


def _listening_socket(listener) -> socket.socket:
    sock = getattr(listener, '_socket', None)                       # SecureListener
    return sock if sock is not None else listener._listener._socket  # stock Listener -> SocketListener


class _InProcessServer(Server):
    """multiprocessing.managers.Server served by threads of the calling process."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)                 # binds + listens (backlog 128)
        self.stop_event = threading.Event()               # read by serve_client(); never set (see brief)
        self._halted = False
        self._live = set()
        self._lock = threading.Lock()

    def handle_request(self, conn):                       # stock body + live-set removal
        try:
            self._handle_request(conn)
        except SystemExit:                                # serve_client() sys.exit()s on EOF / send failure
            pass
        finally:
            with self._lock:
                self._live.discard(conn)
            conn.close()

    def shutdown(self, c):                                # remote 'shutdown' RPC: stop serving, never exit
        c.send(('#RETURN', None))
        threading.Thread(target=self.stop, daemon=True).start()

    def serve(self):                                      # accept loop (daemon thread)
        with selectors.DefaultSelector() as sel:
            sel.register(_listening_socket(self.listener), selectors.EVENT_READ)
            while not self._halted:
                if not sel.select(timeout=POLL):
                    continue
                try:
                    conn = self.listener.accept()
                except Exception:                          # OSError, SSLError, closed listener: next peer
                    continue
                with self._lock:
                    if self._halted:
                        conn.close()
                        break
                    self._live.add(conn)
                threading.Thread(target=self.handle_request, args=(conn,), daemon=True).start()

    def stop(self):
        with self._lock:                                  # sweep under the lock: a listed conn is not yet closed,
            if self._halted:                              # so its fd cannot have been reused
                return
            self._halted = True
            self.listener.close()                         # release the port now
            for conn in self._live:                       # wake handlers: recv() sees EOF, send() fails
                try:
                    with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def _finalize(server, state, addresses):
    server.stop()
    state.value = State.SHUTDOWN
    for address in addresses:                             # mirror _finalize_manager's proxy-cache drop
        BaseProxy._address_to_local.pop(address, None)


class Q3(QueueServer):

    def start(self, initializer=None, initargs=()):
        if self._state.value != State.INITIAL:
            raise mp.ProcessError('Already started or shut down')
        self.scheduled = queue.Queue(maxsize=self.config.size)
        self.completed = queue.Queue(maxsize=self.config.size)
        self.heartbeat = queue.Queue(maxsize=0)
        self.confirmed = queue.Queue(maxsize=0)
        registry = {f'_get_{n}': ((lambda q=getattr(self, n): q), EXPOSED, None, AutoProxy)
                    for n in ('scheduled', 'completed', 'heartbeat', 'confirmed')}
        if self._tls is not None:
            install_process_context(self._tls)
        server = _InProcessServer(registry, self._address, self._authkey, self._serializer)
        requested, self._address = self._address, server.address
        threading.Thread(target=server.serve, daemon=True, name='QueueServer').start()
        self._process = None
        self._state.value = State.STARTED
        self.shutdown = util.Finalize(self, _finalize, exitpriority=0,
                                      args=(server, self._state, {requested, server.address}))
        self.ready = True


def cfg(host, port, tls=None):
    return QueueConfig(host=host, port=port, auth=proto.AUTH, size=2, tls=tls)


def check_localhost_alias(pop_requested: bool):
    """Server and same-thread client both use 'localhost'; listener reports 127.0.0.1."""
    port = proto.free_port()
    s1 = Q3(cfg('localhost', port)); s1.start()
    c1 = QueueClient(cfg('localhost', port)); c1.connect()
    s1.scheduled.put(b'a'); c1.scheduled.get(timeout=1)
    keys = sorted(k for k in BaseProxy._address_to_local if k[1] == port)
    if not pop_requested:   # emulate stock behaviour: drop only the listener's reported address
        server = s1.shutdown._args[0]
        s1.shutdown.cancel()
        _finalize(server, s1._state, {s1._address})
    else:
        s1.shutdown()
    s2 = Q3(cfg('localhost', port)); s2.start()
    try:
        c2 = QueueClient(cfg('localhost', port)); c2.connect()
        s2.scheduled.put(b'b'); r = c2.scheduled.get(timeout=1); res = f'ok {r!r}'
    except Exception as e:
        res = f'{type(e).__name__}'
    s2.shutdown()
    print(f'[alias pop_requested={pop_requested}] proxy-cache keys={keys} server.address={s1._address} '
          f'-> same-thread client on 2nd server: {res}')


def check_two_instances():
    p1, p2 = proto.free_port(), proto.free_port()
    a, b = Q3(cfg('127.0.0.1', p1)), Q3(cfg('127.0.0.1', p2))
    a.start(); b.start()
    ca, cb = QueueClient(cfg('127.0.0.1', p1)), QueueClient(cfg('127.0.0.1', p2))
    ca.connect(); cb.connect()
    a.scheduled.put(b'A'); b.scheduled.put(b'B')
    ra, rb = ca.scheduled.get(timeout=1), cb.scheduled.get(timeout=1)
    a.shutdown(); b.shutdown()
    print(f'[two instances] a->{ra!r} b->{rb!r}; QueueServer._get_scheduled still a plain method: '
          f'{QueueServer.__dict__["_get_scheduled"].__qualname__}')


def check_latency(tls, label):
    port = proto.free_port()
    srv = Q3(cfg('127.0.0.1', port, tls)); srv.start()
    res = {}

    def blocked():
        c = QueueClient(cfg('127.0.0.1', port, tls)); c.connect()
        t0 = time.monotonic()
        try:
            r = c.scheduled.get(timeout=2); res['get'] = f'returned {r!r}'
        except Exception as e:
            res['get'] = f'{type(e).__name__} after {time.monotonic()-t0:.2f}s'
        try:
            r = c.scheduled.put(b'x', timeout=0.5); res['next'] = f'returned {r!r}'
        except Exception as e:
            res['next'] = type(e).__name__

    t = threading.Thread(target=blocked); t.start(); time.sleep(0.5)
    out, err = sys.stdout, sys.stderr
    t0 = time.monotonic(); srv.shutdown(); dt = time.monotonic() - t0
    rebind = Q3(cfg('127.0.0.1', port, tls)); rebind.start(); rebind.shutdown()
    t.join()
    print(f'[{label}] shutdown w/ client blocked in get(timeout=2): {dt*1000:.1f}ms; blocked get -> {res["get"]}; '
          f'next call -> {res["next"]}; immediate rebind ok; stdio same {sys.stdout is out and sys.stderr is err}; '
          f'server qsize after {srv.scheduled.qsize()}')


def check_threads(tls, label, n=50):
    port = proto.free_port()
    srv = Q3(cfg('127.0.0.1', port, tls))
    t0, f0 = threading.active_count(), proto.nfd()
    srv.start()
    t1, f1 = threading.active_count(), proto.nfd()
    hold = threading.Event(); ready = threading.Barrier(n + 1)

    def one():
        c = QueueClient(cfg('127.0.0.1', port, tls)); c.connect()
        c.heartbeat.put(b'hb', timeout=1)
        ready.wait(); hold.wait()

    ts = [threading.Thread(target=one) for _ in range(n)]
    [x.start() for x in ts]; ready.wait(); time.sleep(0.3)
    t2 = threading.active_count() - n
    hold.set(); [x.join() for x in ts]
    srv.shutdown(); time.sleep(POLL + 0.2)
    print(f'[{label}] threads before={t0} started={t1} with {n} clients (server side)={t2} '
          f'after shutdown={threading.active_count()}; fds before={f0} started={f1} after={proto.nfd()}; '
          f'heartbeat qsize={srv.heartbeat.qsize()}')


def check_child_count(method):
    port = proto.free_port()
    srv = Q3(cfg('127.0.0.1', port))
    b = len(mp.active_children()); srv.start(); a = len(mp.active_children()); srv.shutdown()
    print(f'[{sys.version.split()[0]} {method}] active_children before={b} after={a}')


def check_roundtrip(tls, label):
    port = proto.free_port()
    srv = Q3(cfg('127.0.0.1', port, tls)); srv.start()
    cli = QueueClient(cfg('127.0.0.1', port, tls)); cli.connect()
    ok = []
    for n in ('scheduled', 'completed', 'heartbeat', 'confirmed'):
        s, c = getattr(srv, n), getattr(cli, n)
        s.put(b'down', timeout=1); d = c.get(timeout=1); c.task_done()
        c.put(b'up', timeout=1); u = s.get(timeout=1); s.task_done()
        ok.append(d == b'down' and u == b'up')
    srv.scheduled.put(make_sentinel()); sent = cli.scheduled.get(timeout=1) == make_sentinel()
    try:
        cli.scheduled.get(timeout=0.2); e1 = 'none'
    except queue.Empty:
        e1 = 'queue.Empty'
    cli.scheduled.put(b'1', timeout=0.2); cli.scheduled.put(b'2', timeout=0.2)
    try:
        cli.scheduled.put(b'3', timeout=0.2); e2 = 'none'
    except queue.Full:
        e2 = 'queue.Full'
    print(f'[{label}] roundtrip {ok}; sentinel {sent}; {e1}; {e2}; exposed {sorted(cli.scheduled._exposed_)}')
    srv.shutdown()


if __name__ == '__main__':
    method = sys.argv[1]
    mp.set_start_method(method, force=True)
    check_child_count(method)
    if len(sys.argv) > 2:
        cfg_tls = proto.tls_cfg()
        for tls, label in ((None, 'no-tls'), (cfg_tls, 'tls')):
            check_roundtrip(tls, label)
            check_latency(tls, label)
            check_threads(tls, label)
        clear_process_context()
        check_two_instances()
        check_localhost_alias(pop_requested=False)
        check_localhost_alias(pop_requested=True)
