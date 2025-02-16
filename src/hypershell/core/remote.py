# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Manage remote connections and data."""


# Type annotations
from __future__ import annotations
from typing import Tuple, Optional, Type, Union, IO
from types import TracebackType

# Standard libs
import os
import sys
from dataclasses import dataclass

# External libs
from paramiko import SSHClient, SFTPClient, ProxyCommand, AutoAddPolicy, SSHConfig as SSHConfigParser
from paramiko.channel import ChannelStdinFile, ChannelFile, ChannelStderrFile

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.config import config
from hypershell.core.thread import Thread

# Public interface
__all__ = ['SSHConfig', 'SSHConnection', 'RemoteProcess', ]

# Initialize logger
log = Logger.with_name(__name__)


# Default file path to ssh configuration
DEFAULT_SSH_CONFIG = config.ssh.config


@dataclass
class SSHConfig:
    """Connection details for an SSHConnection."""

    hostname: str
    timeout: Optional[float] = None
    username: Optional[str] = None
    password: Optional[str] = None
    key_filename: Optional[str] = None
    sock: Optional[str] = None  # proxy-command

    @staticmethod
    def check_config(hostname: str, filepath: str = DEFAULT_SSH_CONFIG) -> Optional[dict]:
        """Check to see if `hostname` is defined in `filepath`, return `paramiko.SSHConfig`."""
        if not os.path.exists(filepath):
            return None
        with open(filepath, mode='r') as stream:
            ssh_config = SSHConfigParser()
            ssh_config.parse(stream)
            return ssh_config.lookup(hostname)

    @classmethod
    def from_config(cls: Type[SSHConfig], hostname: str, filepath: str = DEFAULT_SSH_CONFIG) -> SSHConfig:
        """Read configuration from file."""
        if profile := cls.check_config(hostname, filepath):
            return cls(**{
                'hostname': profile.get('hostname', hostname),
                'username': profile.get('user', None),
                'key_filename': profile.get('identityfile', None),
                'sock': None if 'proxycommand' not in profile else ProxyCommand(profile['proxycommand']),
            })
        else:
            return cls(hostname=hostname)


class SSHConnection:
    """Connect to remote machine over SSH protocol."""

    config: SSHConfig
    client: SSHClient = None

    sftp: Optional[SFTPClient] = None

    def __init__(self: SSHConnection, hostname_or_config: Union[str, SSHConfig]) -> None:
        """Initialize with hostname or prepared SSHConfig."""
        if isinstance(hostname_or_config, SSHConfig):
            self.config = hostname_or_config
        else:
            self.config = SSHConfig.from_config(hostname_or_config)

    def __enter__(self: SSHConnection) -> SSHConnection:
        """Open connection."""
        self.open()
        return self

    def __exit__(self: SSHConnection,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Automatically close connection."""
        self.close()

    def open(self: SSHConnection) -> None:
        """Open connection."""
        log.debug(f'Starting SSH ({self.config.hostname})')
        self.client = SSHClient()
        self.client.set_missing_host_key_policy(AutoAddPolicy())
        self.client.connect(**vars(self.config))

    def close(self: SSHConnection) -> None:
        """Close connection."""
        if self.client:
            log.debug('Stopping SSH')
            self.client.close()
        if self.sftp:
            log.debug('Stopping SFTP')
            self.sftp.close()

    def run(self: SSHConnection, *args, **kwargs) -> Tuple[ChannelStdinFile, ChannelFile, ChannelStderrFile]:
        """Run remote command and return <stdin>, <stdout> and <stderr>."""
        return self.client.exec_command(*args, **kwargs)

    def open_sftp(self: SSHConnection) -> None:
        """Establish SFTP connection."""
        if not self.sftp:
            log.debug('Starting SFTP')
            self.sftp = self.client.open_sftp()

    def get_file(self: SSHConnection, remote_path: str, local_path: str) -> None:
        """Use SFTP to copy remote file to local file."""
        self.open_sftp()
        log.trace(f'GET {self.config.hostname}:{remote_path} -> {local_path}')
        self.sftp.get(remote_path, local_path)

    def put_file(self: SSHConnection, local_path: str, remote_path: str) -> None:
        """Use SFTP to copy local file to remote file."""
        self.open_sftp()
        log.trace(f'PUT {local_path} -> {self.config.hostname}:{remote_path}')
        self.sftp.put(local_path, remote_path, confirm=True)


class RemoteProcess(Thread):
    """Run a command remotely over SSH and connect local file descriptors."""

    conn: SSHConnection
    command: str
    options: dict

    stdout: IO
    stderr: IO

    def __init__(self: RemoteProcess,
                 host_or_config: Union[str, SSHConfig],
                 command: str, stdout: IO = None, stderr: IO = None, **options) -> None:
        """Initialize with connection and command details."""
        super().__init__('hypershell-remote-command')
        self.conn = SSHConnection(host_or_config)
        self.command = command
        self.options = options
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

    def run_with_exceptions(self: RemoteProcess) -> None:
        """Continuously redirect output."""
        with self.conn as conn:
            stdin, stdout, stderr = conn.run(self.command, **self.options)
            stdin.close()
            stdout_thread = BufferedTransport.new(stdout, self.stdout)
            stderr_thread = BufferedTransport.new(stderr, self.stderr)
            stdout_thread.join()
            stderr_thread.join()

    @classmethod
    def run_and_wait(cls: Type[RemoteProcess], *args, **kwargs) -> None:
        """Run command in blocking mode."""
        thread = cls.new(*args, **kwargs)
        thread.join()


class BufferedTransport(Thread):
    """Continuously copy data from input channel to local file stream."""

    remote: ChannelFile
    local: IO

    def __init__(self: BufferedTransport, remote: ChannelFile, local: IO) -> None:
        """Initialize with local and remote file descriptors."""
        super().__init__('hypershell-buffered-transport')
        self.remote = remote
        self.local = local

    def run_with_exceptions(self: BufferedTransport) -> None:
        """Start reading data."""
        while not self.remote.channel.exit_status_ready():
            buffer = self.remote.readline()
            self.local.write(buffer)
