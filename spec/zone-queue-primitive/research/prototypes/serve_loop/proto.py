"""Prototype: serve QueueServer's managed queues in-process (no manager child).

Usage: python proto.py <fork|spawn> [base]
"""
from __future__ import annotations

import os
import sys
import time
import queue
import socket
import tempfile
import threading
import selectors
import multiprocessing as mp
from multiprocessing import util
from multiprocessing.managers import Server, State, AutoProxy, BaseProxy, public_methods

from hypershell.core.queue import QueueServer, QueueClient, QueueConfig
from hypershell.core.tls import TLSConfig, ensure_default_materials, install_process_context, clear_process_context

AUTH = 'abcdefghijklmnopqrstuvwxyz012345'
POLL = 0.2


# ---------------------------------------------------------------- the loop (condensed in brief)
def _listening_socket(listener) -> socket.socket:
    sock = getattr(listener, '_socket', None)               # SecureListener
    return sock if sock is not None else listener._listener._socket  # stock Listener -> SocketListener


class _InProcessServer(Server):
    """Server whose handler connections are tracked so stop() can sever them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_event = threading.Event()   # serve_client() reads it; we never set it (see brief)
        self._halt = threading.Event()        # our own accept-loop flag
        self._live = set()
        self._live_lock = threading.Lock()

    def handle_request(self, conn):           # CPython's, plus live-set bookkeeping
        with self._live_lock:
            self._live.add(conn)
        try:
            self._handle_request(conn)
        except SystemExit:                    # serve_client() sys.exit()s on EOF / send failure
            pass
        finally:
            with self._live_lock:
                self._live.discard(conn)
            conn.close()

    def shutdown(self, c):                    # remote 'shutdown' RPC: reply, then stop serving (no exit)
        c.send(('#RETURN', None))
        threading.Thread(target=self.stop, daemon=True).start()

    def accept_loop(self):
        listener = self.listener
        with selectors.DefaultSelector() as sel:
            sel.register(_listening_socket(listener), selectors.EVENT_READ)
            while not self._halt.is_set():
                if not sel.select(timeout=POLL):
                    continue
                try:
                    conn = listener.accept()
                except Exception:             # OSError, ssl.SSLError, ...: drop this peer, keep serving
                    continue
                threading.Thread(target=self.handle_request, args=(conn,), daemon=True,
                                 name='QueueServer-handler').start()

    def stop(self, accepter=None):
        self._halt.set()
        if accepter is not None:
            accepter.join(timeout=2 * POLL + 1)   # bounded; handler threads are never joined
        self.listener.close()                      # release the port
        with self._live_lock:                      # wake handlers blocked in recv() with EOF
            for conn in list(self._live):
                try:
                    with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def _stop_in_process(server, thread, address, state) -> None:
    server.stop(thread)
    state.value = State.SHUTDOWN
    BaseProxy._address_to_local.pop(address, None)   # mirror BaseManager._finalize_manager


class InProcessQueueServer(QueueServer):

    def start(self, initializer=None, initargs=()):
        if self._state.value != State.INITIAL:
            raise mp.ProcessError('Already started or shut down')
        self.scheduled = queue.Queue(maxsize=self.config.size)
        self.completed = queue.Queue(maxsize=self.config.size)
        self.heartbeat = queue.Queue(maxsize=0)
        self.confirmed = queue.Queue(maxsize=0)
        registry = {name: ((lambda q=q: q), None, None, AutoProxy)        # per-instance; no class mutation
                    for name, q in (('_get_scheduled', self.scheduled), ('_get_completed', self.completed),
                                    ('_get_heartbeat', self.heartbeat), ('_get_confirmed', self.confirmed))}
        if self._tls is not None:
            install_process_context(self._tls)                           # was _child_bootstrap's job
        server = _InProcessServer(registry, self._address, self._authkey, self._serializer)  # binds
        self._address = server.address
        thread = threading.Thread(target=server.accept_loop, daemon=True,
                                  name='QueueServer-accept')
        thread.start()
        self._process = None
        self._server = server
        self._state.value = State.STARTED
        self.shutdown = util.Finalize(self, _stop_in_process,
                                      args=(server, thread, self._address, self._state), exitpriority=0)
        self.ready = True


# ---------------------------------------------------------------- checks
def client_for(port, tls):
    c = QueueClient(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls))
    c.connect()
    return c


def free_port():
    with socket.socket() as s:
        s.bind(('localhost', 0))
        return s.getsockname()[1]


def tls_cfg():
    d = tempfile.mkdtemp(prefix='zqp-tls-')
    cert, key = ensure_default_materials(d, hostnames=['localhost', '127.0.0.1'])
    return TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)


def nfd():
    return len(os.listdir('/dev/fd'))


def roundtrip(tls, label):
    port = free_port()
    srv = InProcessQueueServer(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls))
    srv.start()
    cli = client_for(port, tls)
    ok = []
    for name in ('scheduled', 'completed', 'heartbeat', 'confirmed'):
        s, c = getattr(srv, name), getattr(cli, name)
        s.put(b'down-' + name.encode(), timeout=1)
        got_c = c.get(timeout=1); c.task_done()
        c.put(b'up-' + name.encode(), timeout=1)
        got_s = s.get(timeout=1); s.task_done()
        ok.append(got_c == b'down-' + name.encode() and got_s == b'up-' + name.encode())
    # join returns once every item task_done (remote join)
    srv.scheduled.put(b'x'); t0 = time.monotonic()
    threading.Timer(0.3, lambda: (srv.scheduled.get(), srv.scheduled.task_done())).start()
    cli.scheduled.join(); join_dt = time.monotonic() - t0
    # sentinel reaches remote consumer
    from hypershell.core.queue import make_sentinel
    srv.scheduled.put(make_sentinel()); sent_ok = cli.scheduled.get(timeout=1) == make_sentinel()
    # (c) Empty / Full raised remotely within timeout
    t0 = time.monotonic()
    try:
        cli.heartbeat.get(timeout=0.3); empty = 'no-raise'
    except queue.Empty as e:
        empty = f'{type(e).__module__}.{type(e).__name__} is queue.Empty={type(e) is queue.Empty} in {time.monotonic()-t0:.2f}s'
    cli.completed.put(b'1', timeout=0.3); cli.completed.put(b'2', timeout=0.3)
    t0 = time.monotonic()
    try:
        cli.completed.put(b'3', timeout=0.3); full = 'no-raise'
    except queue.Full as e:
        full = f'{type(e).__module__}.{type(e).__name__} is queue.Full={type(e) is queue.Full} in {time.monotonic()-t0:.2f}s'
    exposed = sorted(cli.scheduled._exposed_)
    print(f'[{label}] roundtrip 4 queues: {ok}; remote join {join_dt:.2f}s; sentinel {sent_ok}')
    print(f'[{label}] get-empty: {empty}; put-full: {full}')
    print(f'[{label}] exposed: {exposed}')
    # (d) shutdown hygiene + rebind same port + same-thread client against 2nd server
    out, err = sys.stdout, sys.stderr
    t0 = time.monotonic(); srv.shutdown(); dt = time.monotonic() - t0
    srv2 = InProcessQueueServer(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls))
    srv2.start()
    cli2 = client_for(port, tls)
    srv2.confirmed.put(b'second')
    second = cli2.confirmed.get(timeout=1)
    # old client (connection from 1st server) after shutdown
    try:
        r = cli.heartbeat.put(b'stale', timeout=0.5); stale = f'put returned {r!r}'
        try:
            r = cli.heartbeat.put(b'stale2', timeout=0.5); stale += f'; 2nd put returned {r!r}'
        except Exception as e:
            stale += f'; 2nd -> {type(e).__name__}'
    except Exception as e:
        stale = f'raises {type(e).__name__}'
    srv2.shutdown()
    print(f'[{label}] shutdown {dt*1000:.0f}ms; stdout/stderr same: {sys.stdout is out and sys.stderr is err}; '
          f'rebind+same-thread client on 2nd server: {second == b"second"}; old client after its server shut: {stale}')
    print(f'[{label}] srv2 got stale put? {srv2.heartbeat.qsize()} ; srv1 heartbeat qsize {srv.heartbeat.qsize()}')


def latency(tls, label):
    port = free_port()
    srv = InProcessQueueServer(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls))
    srv.start()
    res = {}

    def blocked():
        cli = client_for(port, tls)
        t0 = time.monotonic()
        try:
            r = cli.scheduled.get(timeout=2); res['get'] = f'returned {r!r}'
        except Exception as e:
            res['get'] = f'{type(e).__name__} after {time.monotonic()-t0:.2f}s'
        try:
            r = cli.scheduled.get(timeout=0.5); res['next'] = f'returned {r!r}'
        except Exception as e:
            res['next'] = f'{type(e).__name__}'

    t = threading.Thread(target=blocked); t.start(); time.sleep(0.5)
    t0 = time.monotonic(); srv.shutdown(); dt = time.monotonic() - t0
    t.join()
    print(f'[{label}] shutdown with client blocked in get(timeout=2): {dt*1000:.0f}ms; '
          f'blocked get -> {res["get"]}; next call -> {res["next"]}')


def threads(tls, label, n=50):
    port = free_port()
    srv = InProcessQueueServer(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls))
    t_before, f_before = threading.active_count(), nfd()
    srv.start()
    t_started, f_started = threading.active_count(), nfd()
    hold = threading.Event(); ready = threading.Barrier(n + 1)

    def one():
        cli = client_for(port, tls)
        cli.heartbeat.put(b'hb', timeout=1)
        ready.wait(); hold.wait()

    ts = [threading.Thread(target=one) for _ in range(n)]
    [x.start() for x in ts]; ready.wait(); time.sleep(0.5)
    t_after = threading.active_count() - n
    hold.set(); [x.join() for x in ts]
    srv.shutdown(); time.sleep(0.5)
    print(f'[{label}] threads: before={t_before} after start={t_started} with {n} connected clients '
          f'(server-side, client threads subtracted)={t_after}; after shutdown={threading.active_count()}; '
          f'fds before={f_before} after start={f_started}')


def child_count(method, base):
    cls = QueueServer if base else InProcessQueueServer
    port = free_port()
    srv = cls(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=None))
    before = len(mp.active_children()); t0 = time.monotonic(); srv.start(); dt = time.monotonic() - t0
    after = len(mp.active_children())
    srv.shutdown()
    print(f'[{sys.version.split()[0]} {method} {"base" if base else "proto"}] active_children before={before} '
          f'after start={after} (start {dt*1000:.0f}ms)')


def exposed_compare():
    from multiprocessing import JoinableQueue
    jq, qq = public_methods(JoinableQueue()), public_methods(queue.Queue())
    print(f'JoinableQueue only: {sorted(set(jq) - set(qq))}; queue.Queue only: {sorted(set(qq) - set(jq))}; '
          f'common: {sorted(set(jq) & set(qq))}')
    import multiprocessing.queues as mq
    print(f'mp.queues Empty/Full are queue.Empty/Full: {mq.Empty is queue.Empty and mq.Full is queue.Full}')


if __name__ == '__main__':
    method = sys.argv[1]
    mp.set_start_method(method, force=True)
    base = len(sys.argv) > 2 and sys.argv[2] == 'base'
    child_count(method, base)
    if not base and method == 'spawn':
        exposed_compare()
        cfg = tls_cfg()
        for tls, label in ((None, 'no-tls'), (cfg, 'tls')):
            roundtrip(tls, label)
            latency(tls, label)
            threads(tls, label)
        clear_process_context()
