# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Queue server/client implementation."""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Callable, Union, Optional, Any, Iterable, Type
from types import TracebackType

# Standard libs
from multiprocessing.managers import BaseManager
from multiprocessing import JoinableQueue
from abc import ABC, abstractmethod
from dataclasses import dataclass

# Internal libs
from hypershell.core.config import default, config as _config

# Public interface
__all__ = ['QueueConfig', 'QueueInterface', 'QueueServer', 'QueueClient']


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


class QueueInterface(BaseManager, ABC):
    """The queue interface provides access to three managed distributed queues."""

    config: QueueConfig
    scheduled: JoinableQueue[Optional[List[bytes]]]
    completed: JoinableQueue[Optional[List[bytes]]]
    heartbeat: JoinableQueue[Optional[bytes]]
    confirmed: JoinableQueue[Optional[bytes]]
    ready: bool = False

    def __init__(self: QueueInterface, config: QueueConfig) -> None:
        """Initialize queue interface."""
        self.config = config
        super().__init__(address=(self.config.host, self.config.port), authkey=self.config.auth.encode())

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
        self.register('_get_scheduled', callable=self._get_scheduled)
        self.register('_get_completed', callable=self._get_completed)
        self.register('_get_heartbeat', callable=self._get_heartbeat)
        self.register('_get_confirmed', callable=self._get_confirmed)
        super().start()
        self.ready = True

    def _get_scheduled(self: QueueServer) -> JoinableQueue[Optional[List[bytes]]]:
        return self.scheduled

    def _get_completed(self: QueueServer) -> JoinableQueue[Optional[List[bytes]]]:
        return self.completed

    def _get_heartbeat(self: QueueServer) -> JoinableQueue[Optional[bytes]]:
        return self.heartbeat

    def _get_confirmed(self: QueueServer) -> JoinableQueue[Optional[bytes]]:
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

    _get_scheduled: Callable[[], JoinableQueue[Optional[List[bytes]]]]
    _get_completed: Callable[[], JoinableQueue[Optional[List[bytes]]]]
    _get_heartbeat: Callable[[], JoinableQueue[Optional[bytes]]]
    _get_confirmed: Callable[[], JoinableQueue[Optional[bytes]]]

    def connect(self) -> None:
        """Connect to server."""
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
