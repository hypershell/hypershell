# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Queue server/client implementation."""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Callable, Union, Optional, Any, Iterable, Type, Final
from types import TracebackType

# Standard libs
import io
import os
import ssl
import select
import socket
import struct
from functools import cache
from datetime import datetime
from hashlib import sha512
from abc import ABC, abstractmethod
from dataclasses import dataclass
from multiprocessing.managers import BaseManager, listener_client
from multiprocessing.connection import _ConnectionBase, answer_challenge, deliver_challenge, BUFSIZE
from multiprocessing import JoinableQueue, AuthenticationError

# Internal libs
from hypershell.data.model import Task
from hypershell.core.config import default, config as _config
from hypershell.core.logging import Logger, INSTANCE
from hypershell.core.types import NoneType, JSONData
from hypershell.core.uuid import uuid
from hypershell.core.cipher import (serialize, create_salt, encrypt_token, decrypt_uuid,
                                    derive_secure_key, set_secure_key, get_secure_key)
from hypershell.core.tls import (TLSConfig, install_process_context,
                                 get_server_context, get_client_context, get_tls_config,
                                 verify_peer_fingerprint)

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
    A :class:`multiprocessing.connection.Connection`-compatible wrapper around an
    :class:`ssl.SSLSocket`.

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


def _tls_bootstrap(cfg: TLSConfig, user_init, user_initargs) -> None:
    """Initializer used by :class:`SecureManager.start` to install TLS state in the subprocess."""
    install_process_context(cfg)
    if user_init is not None:
        user_init(*user_initargs)


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
                 address: Optional[tuple] = None,
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
        """Spawn the server subprocess, ensuring the TLS context is installed inside it."""
        if self._tls is not None:
            super().start(initializer=_tls_bootstrap,
                          initargs=(self._tls, initializer, tuple(initargs)))
        else:
            super().start(initializer=initializer, initargs=initargs)

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

    @classmethod
    def from_dict(cls, data: Dict[str, Union[str, int]]) -> QueueConfig:
        """Load config from existing dictionary values."""
        return cls(**data)

    @classmethod
    def load(cls: Type[QueueConfig]) -> QueueConfig:
        """Initialize from global configuration."""
        return cls.from_dict({
            'host': _config.server.host,
            'port': _config.server.port,
            'auth': _config.server.auth,
            'size': _config.server.queuesize,
        })


# Registry of connected client identifiers and timestamp of connection.
# We do not clear this registry (despite how many clients may connect over time)
# because we need to be able to detect replay attempts.
sessions: Dict[str, datetime] = {}


class QueueAuthenticator:
    """
    Authenticate queue connections.

    On top of the built-in authentication, we also require a call to this auth.hello() method
    to enforce a single-use policy with tokens generated by the actual key.
    """

    @staticmethod
    def hello(salt: bytes, token: bytes) -> None:
        """Raises AuthenticationError on failure."""
        client_id = decrypt_uuid(salt, token)
        if client_id not in sessions:
            sessions[client_id] = datetime.now().astimezone()
        else:
            raise AuthenticationError(f'Duplicate session token rejected')


# Timeout in seconds for queue operations
DEFAULT_REMOTE_TIMEOUT: Final[int] = 2
DEFAULT_LOCAL_TIMEOUT: Final[int] = 1


# Special-purpose data object used to signal queue operations shutdown
SENTINEL: Final[NoneType] = None


@cache
def make_sentinel() -> bytes:
    """Create sentinel data object."""
    return serialize(SENTINEL)


class QueueInterface(BaseManager, ABC):
    """The queue interface provides access to four managed distributed queues."""

    config: QueueConfig
    scheduled: JoinableQueue[bytes]
    completed: JoinableQueue[bytes]
    heartbeat: JoinableQueue[bytes]
    confirmed: JoinableQueue[bytes]
    auth: QueueAuthenticator
    ready: bool = False

    def __init__(self: QueueInterface, config: QueueConfig) -> None:
        """Initialize queue interface."""
        self.config = config
        # Ensure the secure key is set before deriving keys - this is the single point
        # of initialization regardless of CLI or library usage
        set_secure_key(config.auth.encode('ascii'))
        # We derive a secure key from the auth string in the same way on the server and client.
        # We don't transmit the original auth string or the key itself but hashed again.
        hashed_auth = sha512(derive_secure_key()).digest()
        super().__init__(address=(self.config.host, self.config.port), authkey=hashed_auth)

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


class QueueServer(QueueInterface):
    """Server for managing queue."""

    def start(self: QueueServer,
              initializer: Optional[Callable[..., Any]] = None,
              initargs: Iterable[Any] = ()) -> None:
        """Initialize queues and start server."""
        self.scheduled = JoinableQueue(maxsize=self.config.size)
        self.completed = JoinableQueue(maxsize=self.config.size)
        self.heartbeat = JoinableQueue(maxsize=0)
        self.confirmed = JoinableQueue(maxsize=0)
        self.auth = QueueAuthenticator()
        self.register('_get_scheduled', callable=self._get_scheduled)
        self.register('_get_completed', callable=self._get_completed)
        self.register('_get_heartbeat', callable=self._get_heartbeat)
        self.register('_get_confirmed', callable=self._get_confirmed)
        self.register('_get_auth', callable=self._get_auth)
        super().start(initializer=set_secure_key, initargs=(get_secure_key(), ))
        self.ready = True

    @staticmethod
    def _require_session(salt: bytes, token: bytes) -> None:
        """Ensure proxy objects are not accessed before the session is authenticated."""
        client_id = decrypt_uuid(salt, token)
        if client_id not in sessions:
            raise AuthenticationError('Client must authenticate before accessing queues')
        connected_at = sessions[client_id]
        if (datetime.now().astimezone() - connected_at).total_seconds() > 60:
            raise AuthenticationError(f'Session token expired (client: {client_id})')

    def _get_auth(self: QueueServer) -> QueueAuthenticator:
        return self.auth

    def _get_scheduled(self: QueueServer, salt: bytes, token: bytes) -> JoinableQueue[bytes]:
        try:
            self._require_session(salt, token)
            return self.scheduled
        except AuthenticationError as error:
            log.critical(str(error))
            raise

    def _get_completed(self: QueueServer, salt: bytes, token: bytes) -> JoinableQueue[bytes]:
        try:
            self._require_session(salt, token)
            return self.completed
        except AuthenticationError as error:
            log.critical(str(error))
            raise

    def _get_heartbeat(self: QueueServer, salt: bytes, token: bytes) -> JoinableQueue[bytes]:
        try:
            self._require_session(salt, token)
            return self.heartbeat
        except AuthenticationError as error:
            log.critical(str(error))
            raise

    def _get_confirmed(self: QueueServer, salt: bytes, token: bytes) -> JoinableQueue[bytes]:
        try:
            self._require_session(salt, token)
            return self.confirmed
        except AuthenticationError as error:
            log.critical(str(error))
            raise

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

    _get_scheduled: Callable[[bytes, bytes], JoinableQueue[bytes]]
    _get_completed: Callable[[bytes, bytes], JoinableQueue[bytes]]
    _get_heartbeat: Callable[[bytes, bytes], JoinableQueue[bytes]]
    _get_confirmed: Callable[[bytes, bytes], JoinableQueue[bytes]]
    _get_auth: Callable[[], QueueAuthenticator]

    def connect(self: QueueClient) -> None:
        """Connect to server using unique client ID for token generation."""
        self.register('_get_scheduled')
        self.register('_get_completed')
        self.register('_get_heartbeat')
        self.register('_get_confirmed')
        self.register('_get_auth')
        super().connect()
        self.auth = self._get_auth()
        salt = create_salt()
        token = encrypt_token(salt, f'{INSTANCE}::{uuid()}'.encode('ascii'))
        self.auth.hello(salt, token)  # Raises AuthenticationError client-side if RPC fails
        self.scheduled = self._get_scheduled(salt, token)
        self.completed = self._get_completed(salt, token)
        self.heartbeat = self._get_heartbeat(salt, token)
        self.confirmed = self._get_confirmed(salt, token)
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
