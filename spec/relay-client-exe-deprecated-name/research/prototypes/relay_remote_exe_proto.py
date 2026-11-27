# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Prototype of R5's end-to-end case: a relay started from a symlinked `bin/hs`, with no `hyper-shell`
on `PATH`, runs its site clients as that `hs`, and they complete the tasks. Named so the suite never
collects it; P1 copies these tests into `tests/test_relay_remote_exe.py`.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Iterator, List

# Standard libs
import os
import shlex
import shutil
import signal
import subprocess
import sysconfig
import time
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests.conftest import free_port

DEADLINE = 60.0  # Seconds for the tasks to complete, and again for the relay to exit once drained.
UPSTREAM_KEY = 'relay-remote-exe-upstream-key-0123456789'
TASKS = 3


class Installation:
    """A `bin/` holding only `hs` and `hsx`, symlinks to this interpreter's own, with a site of its own."""

    def __init__(self, root: Path) -> None:
        scripts = Path(sysconfig.get_path('scripts'))
        self.bin = root / 'inst' / 'bin'
        self.bin.mkdir(parents=True)
        for name in ('hs', 'hsx'):
            (self.bin / name).symlink_to(scripts / name)
        self.hs = str(self.bin / 'hs')
        self.root = root
        site = root / 'site'
        site.mkdir()
        self.env = {name: value for name, value in os.environ.items()
                    if not name.startswith('HYPERSHELL_') and name != 'PATH'}
        self.env.update({'PATH': f'{self.bin}:/usr/bin:/bin', 'HYPERSHELL_SITE': str(site),
                         'HYPERSHELL_CONFIG_FILE': ''})
        self.procs: List[subprocess.Popen] = []

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.hs, *args], env=self.env, capture_output=True, text=True, timeout=DEADLINE)

    def start(self, name: str, args: List[str], **env: str) -> subprocess.Popen:
        log = open(self.root / f'{name}.log', mode='w')
        proc = subprocess.Popen([self.hs, *args], env={**self.env, **env}, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(proc)
        return proc

    def text(self, name: str) -> str:
        return (self.root / f'{name}.log').read_text()

    def end(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait(DEADLINE)


@pytest.fixture
def installation(tmp_path: Path) -> Iterator[Installation]:
    inst = Installation(tmp_path)
    try:
        yield inst
    finally:
        inst.end()


@mark.integration
@mark.parametrize('database', [pytest.param(False, id='plain'), pytest.param(True, id='database')])
def test_relay_from_a_symlinked_hs_runs_its_clients(installation: Installation, database: bool) -> None:
    """A relay from a `bin/` with only `hs` and `hsx` launches its clients as that `hs`, and they run the tasks."""
    inst = installation
    assert shutil.which('hyper-shell', path=inst.env['PATH']) is None
    (inst.root / 'tasks.in').write_text(''.join(f'echo {n}\n' for n in range(TASKS)))
    assert inst.run('submit', str(inst.root / 'tasks.in')).returncode == exit_status.success
    up = free_port()
    server = inst.start('server', ['server', '--forever', '--no-tls', '-H', '127.0.0.1', '-p', str(up)],
                        HYPERSHELL_SERVER_AUTH=UPSTREAM_KEY)
    extra = ['--database', str(inst.root / 'relay.db')] if database else []
    relay = inst.start('relay', ['relay', '-H', '127.0.0.1', '-p', str(up), '--no-tls', '--bind', '127.0.0.1',
                                 '--port', str(free_port()), '-N', '1', *extra],
                       HYPERSHELL_RELAY_UPSTREAM_AUTH=UPSTREAM_KEY, HYPERSHELL_LOGGING_LEVEL='DEBUG')
    deadline = time.monotonic() + DEADLINE
    while inst.run('list', '--remaining', '--count').stdout.strip() != '0':
        assert relay.poll() is None, inst.text('relay')
        assert server.poll() is None, inst.text('server')
        assert time.monotonic() < deadline, f'tasks remain:\n{inst.text("relay")}'
        time.sleep(0.5)
    text = inst.text('relay')
    assert f'Autoscale launcher (zone default): {shlex.quote(inst.hs)} client ' in text
    assert 'hyper-shell' not in text
    assert ('Re-executing to bind relay database' in text) is database
    relay.send_signal(signal.SIGUSR1)
    assert relay.wait(DEADLINE) == exit_status.success, inst.text('relay')
