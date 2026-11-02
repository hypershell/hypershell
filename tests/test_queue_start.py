# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Starting a queue server: TLS isolation within one process, and failures surfaced to the operator."""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Optional

# Standard libs
import os
import sys
import time
import errno
import socket
import secrets
import textwrap
import threading
from pathlib import Path
from signal import SIGINT
from subprocess import run, Popen, STDOUT, TimeoutExpired

# External libs
from cmdkit.app import exit_status
from multiprocessing.managers import Server
from pytest import fixture, mark, raises

# Internal libs
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig
from hypershell.core.tls import TLSConfig, ensure_default_materials, clear_process_context, get_tls_config
from tests import create_taskfile


AUTH = 'queue-test-key-0123456789abcdef'
WAIT = 2.0
DEADLINE = 30


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> tuple[str, str]:
    """Generate a self-signed cert+key once per test module."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture(params=['no-tls', 'tls'])
def tls(request, tls_materials) -> Iterator[Optional[TLSConfig]]:
    """Run once with TLS disabled and once with TLS enabled; the process slot is cleared after."""
    if request.param == 'no-tls':
        yield None
    else:
        cert, key = tls_materials
        yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    clear_process_context()


@fixture
def busy_port() -> Iterator[int]:
    """A loopback port held by a listening socket for the duration of the test."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen(1)
        yield sock.getsockname()[1]


def make_config(port: int = 0, tls: Optional[TLSConfig] = None) -> QueueConfig:
    """Queue configuration on loopback (port 0 binds an ephemeral port)."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls)


def connect(port: int, tls: Optional[TLSConfig] = None) -> QueueClient:
    """Connect a client to a running server on loopback."""
    client = QueueClient(make_config(port, tls))
    client.connect()
    return client


def critical_lines(text: str) -> List[str]:
    """The CRITICAL log lines in `text`."""
    return [line for line in text.splitlines() if 'CRITICAL' in line]


@mark.unit
def test_same_process_client_tls_config_leaves_server_alone(tls_materials) -> None:
    """A client in the server's process with a different TLS configuration doesn't break later accepts."""
    cert, key = tls_materials
    server_tls = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    client_tls = TLSConfig(enabled=True, cafile=cert)  # Verifies the server; holds no cert or key
    try:
        with QueueServer(make_config(tls=server_tls)) as server:
            port = server.address[1]
            first = connect(port, client_tls)
            second = connect(port, client_tls)
            server.scheduled.put(b'first', timeout=WAIT)
            server.scheduled.put(b'second', timeout=WAIT)
            assert first.scheduled.get(timeout=WAIT) == b'first'
            assert second.scheduled.get(timeout=WAIT) == b'second'
    finally:
        clear_process_context()


@mark.unit
def test_server_start_leaves_process_tls_config(tls_materials) -> None:
    """Starting a TLS server leaves the process-wide TLS configuration that clients use untouched."""
    cert, key = tls_materials
    clear_process_context()
    try:
        with QueueServer(make_config(tls=TLSConfig(enabled=True, cert=cert, key=key, cafile=cert))):
            assert get_tls_config() is None
    finally:
        clear_process_context()


@mark.unit
def test_busy_port_raises_from_start(tls: Optional[TLSConfig], busy_port: int) -> None:
    """A bind failure reaches the caller of ``start()`` naming the address and the system error."""
    threads = threading.active_count()
    server = QueueServer(make_config(port=busy_port, tls=tls))
    with raises(RuntimeError) as info:
        server.start()
    assert f'127.0.0.1:{busy_port}' in str(info.value)
    assert os.strerror(errno.EADDRINUSE) in str(info.value)
    assert not server.ready
    assert threading.active_count() == threads


@mark.unit
@mark.parametrize('drift', ['missing-handle-request', 'serve-client-ignores-stop-event'])
def test_unsupported_internals_fail_start(drift: str, monkeypatch) -> None:
    """A server refuses to start when the multiprocessing internals it relies on have changed."""
    if drift == 'missing-handle-request':
        monkeypatch.delattr(Server, '_handle_request')
    else:
        monkeypatch.setattr(Server, 'serve_client', lambda self, conn: None)
    server = QueueServer(make_config())
    try:
        with raises(RuntimeError, match="multiprocessing internals are not supported"):
            server.start()
        assert not server.ready
    finally:
        if server.ready:
            server.shutdown()


def server_command(site: Path, port: int, tls_enabled: bool) -> List[str]:
    """Command line for ``hs server`` on loopback with a one-task file."""
    taskfile = create_taskfile(site, ['echo a'])
    return ['hs', 'server', str(taskfile), '-H', '127.0.0.1', '-p', str(port),
            '-k', secrets.token_hex(16), *([] if tls_enabled else ['--no-tls'])]


@mark.integration
@mark.parametrize('tls_enabled', [True, False], ids=['tls', 'no-tls'])
def test_server_cli_busy_port_exits(temp_site: Path, busy_port: int, tls_enabled: bool) -> None:
    """``hs server`` on an occupied port exits promptly, non-zero, with one CRITICAL line naming the cause."""
    result = run(server_command(temp_site, busy_port, tls_enabled), cwd=temp_site,
                 capture_output=True, text=True, timeout=DEADLINE)
    output = result.stdout + result.stderr
    assert result.returncode == exit_status.runtime_error, output
    lines = critical_lines(output)
    assert len(lines) == 1, output
    assert f'127.0.0.1:{busy_port}' in lines[0] and os.strerror(errno.EADDRINUSE) in lines[0], output
    assert 'Server listening' not in output


@mark.integration
def test_server_cli_unsupported_internals_exits(temp_site: Path) -> None:
    """``hs server`` on a Python whose multiprocessing internals have changed exits non-zero with one CRITICAL line."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    script = textwrap.dedent("""\
        import sys
        from multiprocessing.managers import Server
        del Server._handle_request
        from hypershell import main
        sys.exit(main(sys.argv[1:]))
    """)
    command = [sys.executable, '-c', script, *server_command(temp_site, port, True)[1:]]
    result = run(command, cwd=temp_site, capture_output=True, text=True, timeout=DEADLINE)
    output = result.stdout + result.stderr
    assert result.returncode == exit_status.runtime_error, output
    lines = critical_lines(output)
    assert len(lines) == 1, output
    assert 'multiprocessing internals are not supported' in lines[0], output
    assert 'Server listening' not in output


@mark.integration
def test_server_cli_tls_context_failure_is_logged(temp_site: Path) -> None:
    """``hs server`` whose TLS context cannot be built says so once at ERROR under the default log level."""
    site = Path(str(temp_site))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    key = secrets.token_hex(16)
    env = {name: value for name, value in os.environ.items() if name != 'HYPERSHELL_LOGGING_LEVEL'}
    address = ['-H', '127.0.0.1', '-p', str(port), '-k', key]
    log_path = site / 'server.log'
    with open(log_path, mode='w') as stream:
        server = Popen(['hs', 'server', str(create_taskfile(site, ['echo a'])), *address, '--tls-cert', '<none>'],
                       cwd=site, env=env, stdout=stream, stderr=STDOUT)
    try:
        deadline = time.monotonic() + DEADLINE
        while 'Server listening' not in log_path.read_text() and server.poll() is None:
            assert time.monotonic() < deadline, log_path.read_text()
            time.sleep(0.1)
        for _ in range(2):
            client = run(['hs', 'client', *address], cwd=site, env=env, capture_output=True, text=True,
                         timeout=DEADLINE)
            assert client.returncode != exit_status.success, client.stdout + client.stderr
    finally:
        if server.poll() is None:
            server.send_signal(SIGINT)
        try:
            server.wait(timeout=DEADLINE)
        except TimeoutExpired:
            server.kill()
            server.wait()
    output = log_path.read_text()
    errors = [line for line in output.splitlines() if 'ERROR' in line]
    assert len(errors) == 1, output
    assert 'TLSConfig.cert and TLSConfig.key are required for the server side' in errors[0], output
