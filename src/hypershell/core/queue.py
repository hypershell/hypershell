# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Queue server/client implementation."""


# Type annotations
from __future__ import annotations
from typing import Dict, Callable, Optional, Any, Iterable, Type, Final, Tuple
from types import TracebackType

# Standard libs
import io
import os
import ssl
import select
import socket
import struct
import threading
import selectors
from functools import cache
from abc import ABC, abstractmethod
from dataclasses import dataclass
from multiprocessing import util, ProcessError
from multiprocessing.managers import BaseManager, BaseProxy, Server, State, AutoProxy, listener_client
from multiprocessing.connection import _ConnectionBase, answer_challenge, deliver_challenge, BUFSIZE
from multiprocessing import JoinableQueue

# Internal libs
from hypershell.core.config import default, config as _config
from hypershell.core.logging import Logger, close_inherited_slot_locks
from hypershell.core.types import NoneType, serialize
from hypershell.core.tls import (TLSConfig, install_process_context,
                                 get_server_context, get_client_context, get_tls_config,
                                 verify_peer_fingerprint, from_namespace as _tls_from_namespace)

# Public interface
__all__ = [
    'DEFAULT_REMOTE_TIMEOUT', 'DEFAULT_LOCAL_TIMEOUT', 'TLS_SERIALIZER',
    'SENTINEL', 'make_sentinel',
    'SecureConnection', 'SecureListener', 'secure_client', 'SecureManager',
    'QueueConfig', 'QueueInterface', 'QueueServer', 'QueueClient',
]

# Initialize logger
log = Logger.with_name(__name__)


TLS_SERIALIZER: Final[str] = 'hypershell-tls'
"""Serializer key registered with :data:`multiprocessing.managers.listener_client`.
When a :class:`BaseManager` is constructed with this serializer, both ends route their
traffic through :class:`SecureListener` / :func:`secure_client` instead of the cleartext
stock pair."""


class SecureConnection(_ConnectionBase):
    """
    A :class:`multiprocessing.connection.Connection`-compatible wrapper around an :class:`ssl.SSLSocket`.

    Implements the same wire framing as the stock connection (a 4-byte network-order signed
    length, with a ``-1`` sentinel + 8-byte payload length for messages larger than 2 GiB)
    so it is a drop-in replacement, but every byte read or written passes through the TLS
    layer. ``send`` / ``recv`` (which transparently pickle Python objects) are inherited
    from :class:`_ConnectionBase` for free since they are layered on top of
    ``_send_bytes`` / ``_recv_bytes``.
    """

    def __init__(self: SecureConnection, ssl_sock: ssl.SSLSocket,
                 readable: bool = True, writable: bool = True) -> None:
        """Wrap `ssl_sock` and expose it under the multiprocessing connection protocol."""
        self._ssl_sock = ssl_sock
        fd = ssl_sock.fileno()
        if fd < 0:
            raise ValueError('ssl_sock is closed')
        super().__init__(fd, readable=readable, writable=writable)

    def _close(self: SecureConnection) -> None:
        # Skip ssl.SSLSocket.unwrap(): on a blocking socket it would wait indefinitely for the
        # peer's close_notify. Multiprocessing's Connection contract doesn't require a graceful
        # TLS shutdown, so just drop the socket - the peer will observe a short read.
        sock = self._ssl_sock
        self._ssl_sock = None
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass

    def _send(self: SecureConnection, buf) -> None:
        self._ssl_sock.sendall(bytes(buf))

    def _recv(self: SecureConnection, size: int) -> io.BytesIO:
        """Read exactly `size` bytes from the TLS socket, raising EOFError at clean close."""
        buf = io.BytesIO()
        remaining = size
        while remaining > 0:
            chunk = self._ssl_sock.recv(min(BUFSIZE, remaining))
            if not chunk:
                if remaining == size:
                    raise EOFError
                raise OSError('got end of file during message')
            buf.write(chunk)
            remaining -= len(chunk)
        return buf

    def _send_bytes(self: SecureConnection, buf) -> None:
        n = len(buf)
        if n > 0x7fffffff:
            self._send(struct.pack('!i', -1))
            self._send(struct.pack('!Q', n))
            self._send(buf)
        else:
            header = struct.pack('!i', n)
            # Small messages are concatenated to avoid Nagle's algorithm interactions; this
            # mirrors what :class:`multiprocessing.connection.Connection` does.
            if n > 16384:
                self._send(header)
                self._send(buf)
            else:
                self._send(header + bytes(buf))

    def _recv_bytes(self: SecureConnection, maxsize=None):
        size, = struct.unpack('!i', self._recv(4).getvalue())
        if size == -1:
            size, = struct.unpack('!Q', self._recv(8).getvalue())
        if maxsize is not None and size > maxsize:
            return None
        return self._recv(size)

    def _poll(self: SecureConnection, timeout: float) -> bool:
        """Report readability accounting for the TLS layer's internal buffer."""
        sock = self._ssl_sock
        if sock is None:
            return False
        if sock.pending() > 0:
            return True
        readers, _, _ = select.select([sock], [], [], timeout)
        return bool(readers)


class SecureListener:
    """
    A multiprocessing-compatible listener that wraps every accepted connection with TLS.

    Owns its bound listening socket directly rather than reaching into the internals of
    :class:`multiprocessing.connection.Listener`; the constructor signature mirrors that
    of the stock listener so this class slots into :data:`listener_client`.
    """

    def __init__(self: SecureListener,
                 address=None, family=None, backlog: int = 1, authkey: Optional[bytes] = None) -> None:
        """Bind and listen at `address`; the server-side TLS context is fetched lazily on accept."""
        if family is not None and family != 'AF_INET':
            raise ValueError(f'SecureListener only supports AF_INET, got {family!r}')
        host, port = address if address is not None else ('', 0)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == 'posix':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(True)
            sock.bind((host, port))
            sock.listen(backlog)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self._address = sock.getsockname()
        self._last_accepted = None
        # `authkey` is part of the stock Listener interface but unused here; the manager
        # performs the challenge handshake explicitly after we hand back the connection.
        self._authkey = authkey

    @property
    def address(self: SecureListener):
        return self._address

    @property
    def last_accepted(self: SecureListener):
        return self._last_accepted

    def accept(self: SecureListener) -> SecureConnection:
        """Block on the listening socket and return a TLS-wrapped connection."""
        raw, self._last_accepted = self._socket.accept()
        raw.setblocking(True)
        try:
            ssl_sock = get_server_context().wrap_socket(raw, server_side=True)
        except Exception:
            raw.close()
            raise
        return SecureConnection(ssl_sock)

    def close(self: SecureListener) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            sock.close()

    def __enter__(self: SecureListener) -> SecureListener:
        return self

    def __exit__(self: SecureListener,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        self.close()


def secure_client(address, family=None, authkey: Optional[bytes] = None) -> SecureConnection:
    """
    Client factory installed in :data:`listener_client` under :data:`TLS_SERIALIZER`.

    Matches the signature of :func:`multiprocessing.connection.Client` so it slots in as a
    drop-in replacement. Opens a TLS connection, optionally verifies the peer fingerprint,
    then performs the BaseManager challenge/response handshake over the encrypted channel.
    """
    cfg = get_tls_config()
    ctx = get_client_context()
    raw = socket.create_connection(address)
    try:
        servername = (cfg.servername if cfg is not None and cfg.servername else None)
        if servername is None and ctx.check_hostname:
            servername = address[0] if isinstance(address, tuple) else None
        ssl_sock = ctx.wrap_socket(raw, server_hostname=servername)
    except Exception:
        raw.close()
        raise
    if cfg is not None:
        verify_peer_fingerprint(ssl_sock, cfg)
    conn = SecureConnection(ssl_sock)
    if authkey is not None:
        if not isinstance(authkey, bytes):
            raise TypeError('authkey should be a byte string')
        answer_challenge(conn, authkey)
        deliver_challenge(conn, authkey)
    return conn


# Register our TLS-aware (Listener, Client) pair so BaseManager + BaseProxy automatically
# use them whenever a manager is constructed with serializer=TLS_SERIALIZER. Per-proxy RPC
# connections opened by BaseProxy._connect / _incref / _decref also pick this up via
# `self._Client = listener_client[serializer][1]`, so every byte on the wire is encrypted.
listener_client[TLS_SERIALIZER] = (SecureListener, secure_client)


def _child_bootstrap(cfg: Optional[TLSConfig], user_init, user_initargs) -> None:
    """Initializer run in the forked manager child before it serves.

    Closes log-slot lock descriptors inherited across the fork so the parent stays the sole
    holder (a killed parent never leaves a ghost lock), installs the per-process TLS context
    when TLS is active, then runs any user initializer. Ordered so the fd hygiene and TLS state
    are in place before serving; touches neither the RPC framing, authkey, nor the handshake.
    """
    close_inherited_slot_locks()
    if cfg is not None:
        install_process_context(cfg)
    if user_init is not None:
        user_init(*user_initargs)


# Seconds between checks of the halt flag in the in-process accept loop
POLL: Final[float] = 0.25


def _listening_socket(listener) -> socket.socket:
    """The bound socket behind `listener`: our own for TLS, the stock listener's otherwise."""
    sock = getattr(listener, '_socket', None)
    if isinstance(sock, socket.socket):
        return sock
    return listener._listener._socket


def _cut(conn) -> None:
    """Shut down both directions of a live connection's socket so its handler wakes and exits.

    The TLS socket is shut down at the plain-socket level, leaving its SSL object to the handler
    thread that owns it. A stock connection is reached through a duplicate of its descriptor.
    Platforms that offer neither are skipped; their handlers exit when the peer disconnects.
    """
    try:
        ssl_sock = getattr(conn, '_ssl_sock', None)
        if ssl_sock is not None:
            socket.socket.shutdown(ssl_sock, socket.SHUT_RDWR)
        else:
            with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.shutdown(socket.SHUT_RDWR)
    except (OSError, ValueError, AttributeError):
        pass


class _InProcessServer(Server):
    """
    A :class:`multiprocessing.managers.Server` served by daemon threads of the calling process.

    Construction (bind and listen) and per-connection request handling are the stock ones, so the
    wire protocol, authentication handshake and TLS layer are exactly those of a forked manager.
    The accept loop is our own: unlike ``serve_forever`` it never exits the process or touches
    ``sys.stdout``/``sys.stderr``, and :meth:`stop` releases the port and cuts live connections
    without waiting on any thread.
    """

    def __init__(self: _InProcessServer, *args: Any) -> None:
        """Bind and listen; the accept loop starts with :meth:`serve`."""
        super().__init__(*args)
        # Read by serve_client() between requests. Never set: setting it desynchronises a
        # connection's request/reply pairing; stopping cuts the sockets instead.
        self.stop_event = threading.Event()
        self._halted = False
        self._live = set()
        self._lock = threading.Lock()

    def serve(self: _InProcessServer) -> None:
        """Accept connections until stopped, one daemon handler thread per connection."""
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(_listening_socket(self.listener), selectors.EVENT_READ)
                while not self._halted:
                    if not selector.select(timeout=POLL) or self._halted:
                        continue
                    try:
                        conn = self.listener.accept()
                    except Exception as error:
                        # A failed accept (reset, handshake or TLS error, closed listener) drops that peer only.
                        log.debug(f'Queue server dropped a connection: {error!r}')
                        continue
                    with self._lock:
                        if self._halted:
                            conn.close()
                            break
                        self._live.add(conn)
                    threading.Thread(target=self.handle_request, args=(conn, ), daemon=True,
                                     name='hypershell-queue-handler').start()
        except Exception as error:
            if not self._halted:
                log.error(f'Queue server stopped accepting connections: {error!r}')

    def handle_request(self: _InProcessServer, conn) -> None:
        """Serve one connection, then forget and close it."""
        try:
            self._handle_request(conn)
        except SystemExit:
            # The stock serve_client() ends its thread with sys.exit() on EOF or a failed send.
            pass
        finally:
            with self._lock:
                self._live.discard(conn)
            conn.close()

    def shutdown(self: _InProcessServer, c) -> None:
        """Remote shutdown request: stop serving after the reply, without exiting the process."""
        with self._lock:
            # The requesting connection is left to its handler so the reply can be sent.
            self._live.discard(c)
        threading.Thread(target=self.stop, daemon=True, name='hypershell-queue-stop').start()

    def stop(self: _InProcessServer) -> None:
        """Stop accepting, release the port and cut live connections. Idempotent; never blocks."""
        with self._lock:
            # Swept under the lock: a listed connection is not yet closed, so its descriptor
            # cannot have been reused.
            if self._halted:
                return
            self._halted = True
            try:
                _listening_socket(self.listener).shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
            self.listener.close()
            for conn in self._live:
                _cut(conn)
            self._live.clear()


def _finalize_in_process(server: _InProcessServer, state: State, addresses: Iterable[Tuple[str, int]]) -> None:
    """Stop an in-process server and drop cached proxy connections to every address it answered on."""
    server.stop()
    state.value = State.SHUTDOWN
    with BaseProxy._mutex:
        for address in addresses:
            BaseProxy._address_to_local.pop(address, None)


class SecureManager(BaseManager):
    """
    :class:`BaseManager` that transparently wraps queue traffic with TLS when configured.

    When constructed with an active :class:`TLSConfig`, selects the ``hypershell-tls``
    serializer so that the stock multiprocessing machinery automatically picks our
    :class:`SecureListener` and :func:`secure_client` for both the server-side listener and
    every per-proxy RPC connection. When ``tls`` is ``None`` (or inactive), defers entirely
    to :class:`BaseManager` with the default ``pickle`` serializer.
    """

    def __init__(self: SecureManager,
                 address: Optional[Tuple[str, int]] = None,
                 authkey: Optional[bytes] = None,
                 *,
                 tls: Optional[TLSConfig] = None,
                 **kwargs: Any) -> None:
        """Initialize with optional `tls`; flip to TLS serializer only when `tls` is active."""
        self._tls = tls if (tls is not None and tls.is_active()) else None
        if self._tls is not None:
            kwargs.setdefault('serializer', TLS_SERIALIZER)
        super().__init__(address=address, authkey=authkey, **kwargs)

    def start(self: SecureManager,
              initializer: Optional[Callable[..., Any]] = None,
              initargs: Iterable[Any] = ()) -> None:
        """Start a manager server in a child process, closing inherited log-slot locks (and installing
        the TLS context when active) inside the child before any user initializer runs.

        This is the generic manager path. :class:`QueueServer` serves its queues in-process instead.
        """
        super().start(initializer=_child_bootstrap,
                      initargs=(self._tls, initializer, tuple(initargs)))

    def _serve_in_process(self: SecureManager, registry: Dict[str, tuple]) -> None:
        """Serve `registry` from daemon threads of this process instead of a child process.

        Installs the TLS context first when active, since this process now accepts connections.
        Keeps the manager's shape: :attr:`address` is the bound address and ``shutdown()`` is an
        idempotent finalizer that also runs at interpreter exit.
        """
        if self._state.value != State.INITIAL:
            if self._state.value == State.STARTED:
                raise ProcessError('Already started server')
            raise ProcessError('Manager has shut down')
        if self._tls is not None:
            install_process_context(self._tls)
        server = _InProcessServer(registry, self._address, self._authkey, self._serializer)
        # Clients cache connections by the address they dial, which may name the host differently
        # (e.g., 'localhost') than the bound address does; both are dropped at shutdown.
        dialed = (self._address[0], server.address[1])
        self._address = server.address
        self._process = None
        threading.Thread(target=server.serve, daemon=True, name='hypershell-queue-server').start()
        self._state.value = State.STARTED
        self.shutdown = util.Finalize(self, _finalize_in_process, exitpriority=0,
                                      args=(server, self._state, (dialed, server.address)))

    def connect(self: SecureManager) -> None:
        """Install the local-process TLS context, then perform the manager handshake."""
        if self._tls is not None:
            install_process_context(self._tls)
        super().connect()


@dataclass
class QueueConfig:
    """Connection details for queue interface."""

    host: str = default.server.bind
    port: int = default.server.port
    auth: str = default.server.auth
    size: int = default.server.queuesize
    tls: Optional[TLSConfig] = None  # Optional transport-layer TLS for the queue

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QueueConfig:
        """Load config from existing dictionary values."""
        return cls(**data)

    @classmethod
    def load(cls: Type[QueueConfig]) -> QueueConfig:
        """Initialize from global configuration."""
        # `_config.server.get('tls')` returns None when the TOML/env config has not defined
        # any `[server.tls]` keys; from_namespace then treats that as "TLS off".
        tls_ns = _config.server.get('tls') if hasattr(_config.server, 'get') else None
        return cls.from_dict({
            'host': _config.server.host,
            'port': _config.server.port,
            'auth': _config.server.auth,
            'size': _config.server.queuesize,
            'tls': _tls_from_namespace(tls_ns),
        })


# Timeout in seconds for queue operations
DEFAULT_REMOTE_TIMEOUT: Final[int] = 2
DEFAULT_LOCAL_TIMEOUT: Final[int] = 1


# Special-purpose data object used to signal queue operations shutdown
SENTINEL: Final[NoneType] = None


@cache
def make_sentinel() -> bytes:
    """Create sentinel data object."""
    return serialize(SENTINEL)


class QueueInterface(SecureManager, ABC):
    """The queue interface provides access to four managed distributed queues."""

    config: QueueConfig
    scheduled: JoinableQueue[bytes]
    completed: JoinableQueue[bytes]
    heartbeat: JoinableQueue[bytes]
    confirmed: JoinableQueue[bytes]
    ready: bool = False

    def __init__(self: QueueInterface, config: QueueConfig) -> None:
        """Initialize queue interface."""
        self.config = config
        # `tls` is forwarded to SecureManager: when None or inactive, SecureManager behaves
        # exactly like a stock BaseManager (cleartext TCP), preserving the existing default.
        super().__init__(address=(self.config.host, self.config.port),
                         authkey=self.config.auth.encode('ascii'), tls=self.config.tls)

    @classmethod
    def new(cls: Type[QueueInterface]) -> QueueInterface:
        """Create new interface from global configuration."""
        return cls(config=QueueConfig.load())

    @abstractmethod
    def __enter__(self: QueueInterface) -> QueueInterface:
        """Start server or connect from client."""

    @abstractmethod
    def __exit__(self: QueueInterface,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Stop or disconnect."""


def _provider(obj: Any) -> Callable[[], Any]:
    """Registry callable handing the manager `obj`; lock-free, since it runs under the server's mutex."""
    return lambda: obj


class QueueServer(QueueInterface):
    """Server for managing queue, served from within the calling process."""

    def start(self: QueueServer,
              initializer: Optional[Callable[..., Any]] = None,
              initargs: Iterable[Any] = ()) -> None:
        """Initialize queues and serve them from this process."""
        self.scheduled = JoinableQueue(maxsize=self.config.size)
        self.completed = JoinableQueue(maxsize=self.config.size)
        self.heartbeat = JoinableQueue(maxsize=0)
        self.confirmed = JoinableQueue(maxsize=0)
        # Per-instance registry: the class registry is shared, so two servers in one process
        # would otherwise serve each other's queues.
        registry = {f'_get_{name}': (_provider(getattr(self, name)), None, None, AutoProxy)
                    for name in ('scheduled', 'completed', 'heartbeat', 'confirmed')}
        if initializer is not None:
            initializer(*initargs)
        self._serve_in_process(registry)
        self.ready = True

    def _get_scheduled(self: QueueServer) -> JoinableQueue[bytes]:
        return self.scheduled

    def _get_completed(self: QueueServer) -> JoinableQueue[bytes]:
        return self.completed

    def _get_heartbeat(self: QueueServer) -> JoinableQueue[bytes]:
        return self.heartbeat

    def _get_confirmed(self: QueueServer) -> JoinableQueue[bytes]:
        return self.confirmed

    def __enter__(self: QueueServer) -> QueueServer:
        """Start the server."""
        self.start()
        return self

    def __exit__(self: QueueServer,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Shutdown the server."""
        self.shutdown()


class QueueClient(QueueInterface):
    """Client connection to queue manager."""

    _get_scheduled: Callable[[], JoinableQueue[bytes]]
    _get_completed: Callable[[], JoinableQueue[bytes]]
    _get_heartbeat: Callable[[], JoinableQueue[bytes]]
    _get_confirmed: Callable[[], JoinableQueue[bytes]]

    def connect(self: QueueClient) -> None:
        """Connect to server using unique client ID for token generation."""
        self.register('_get_scheduled')
        self.register('_get_completed')
        self.register('_get_heartbeat')
        self.register('_get_confirmed')
        super().connect()
        self.scheduled = self._get_scheduled()
        self.completed = self._get_completed()
        self.heartbeat = self._get_heartbeat()
        self.confirmed = self._get_confirmed()
        self.ready = True

    def __enter__(self: QueueClient) -> QueueClient:
        """Connect to server."""
        self.connect()
        return self

    def __exit__(self: QueueClient,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Disconnect from server."""
