# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""CLI drives for listener-handshake-deadline (R4, R6, R8): responsiveness and deadline checks of real
`hs` servers while a connection that never starts its TLS handshake is held open.

Run inside a throwaway site, from the repository root:

    .agents/factory/bin/temp_site.sh uv run python "$PWD/spec/listener-handshake-deadline/research/drive.py" SCENARIO [...]

(temp_site.sh runs the command inside the site directory, so the script path must be absolute.)

Scenarios (each prints PASS or FAIL lines and exits non-zero on any FAIL):

    server   [--client HS [--expect-too-old]] [--handshake SEC]
                                               hs server with a task file; a client finishes the tasks
    cluster  [--handshake SEC]                 hs cluster (local), clients start 3 s after the server
    relay    [--handshake SEC]                 hs server upstream, hs relay with its site client delayed 3 s
    refuse                                     hs server, hs cluster and hs relay refuse handshake = 0, -5, abc

Without --handshake the default deadline applies and the held connection must still be open when the
work is done (the server served others while it was pending). With --handshake SEC the held connection
must be closed by the server between SEC and SEC + 3 seconds after it connected. --client names another
`hs` executable (e.g., an older release installed in a scratch environment) to run as the client;
--expect-too-old instead expects that client to connect and authenticate and then be refused as too old
(the work-request protocol) exactly as on develop, which is what a 2.8.x client gets from any 2.9 server.
--no-hold runs a scenario with no connection held (a baseline of the drive itself).
Every process started here is stopped by its recorded PID before exit.
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import socket
import secrets
import argparse
import tempfile
import threading
import subprocess
from typing import List, Optional

KEY = secrets.token_hex(16)
BOUND = 25.0       # Seconds the work may take with a connection held open (below the 30 s default deadline)
TASKS = 4
started: List[subprocess.Popen] = []

DELAY_LAUNCHER = """#!{python}
import os, sys, time
time.sleep(3)
argv = sys.argv[1:]
for i, word in enumerate(argv):
    if word in ('-H', '--host') and i + 1 < len(argv):
        argv[i + 1] = '127.0.0.1'
    elif word.startswith('--host='):
        argv[i] = '--host=127.0.0.1'
os.execvp(argv[0], argv)
"""
failures: List[str] = []


def report(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}: {what}', flush=True)
    if not ok:
        failures.append(what)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_port(port: int, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def start(argv: List[str], env: Optional[dict] = None, **kwargs) -> subprocess.Popen:
    proc = subprocess.Popen(argv, env={**os.environ, **(env or {})}, **kwargs)
    started.append(proc)
    print(f'started pid {proc.pid}: {" ".join(argv)}', flush=True)
    return proc


def stop_all() -> None:
    for proc in started:
        if proc.poll() is None:
            proc.terminate()
    for proc in started:
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
        print(f'stopped pid {proc.pid} (exit {proc.returncode})', flush=True)


class Held(threading.Thread):
    """A TCP connection to `port` that never sends a byte; records when the server closes it."""

    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.sock = socket.create_connection(('127.0.0.1', port))
        self.opened = time.monotonic()
        self.closed_after: Optional[float] = None
        self.start()

    def run(self) -> None:
        try:
            while self.sock.recv(4096):
                pass
        except OSError:
            pass
        self.closed_after = time.monotonic() - self.opened

    def release(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def taskfile() -> str:
    path = os.path.join(os.getcwd(), 'tasks.in')
    with open(path, 'w') as stream:
        stream.writelines(f'echo {n}\n' for n in range(TASKS))
    return path


def check_held(held: Held, handshake: Optional[float], label: str) -> None:
    if handshake is None:
        report(held.closed_after is None, f'{label}: held connection still open after the work finished')
    else:
        end = held.opened + handshake + 4
        while held.closed_after is None and time.monotonic() < end:
            time.sleep(0.1)
        after = held.closed_after
        report(after is not None and handshake <= after <= handshake + 3,
               f'{label}: server closed the held connection {after if after is None else round(after, 2)} s '
               f'after it connected (expected {handshake:g} to {handshake + 3:g} s)')


def server_env(handshake: Optional[float]) -> dict:
    env = {'HYPERSHELL_SERVER_AUTH': KEY}
    if handshake is not None:
        env['HYPERSHELL_SERVER_TLS_HANDSHAKE'] = f'{handshake:g}'
    return env


def wait(proc: subprocess.Popen, timeout: float) -> Optional[int]:
    try:
        return proc.wait(timeout)
    except subprocess.TimeoutExpired:
        return None


def scenario_server(args) -> None:
    port = free_port()
    hs = shutil.which('hs')
    server = start([hs, 'server', taskfile(), '-H', '127.0.0.1', '-p', str(port)], env=server_env(args.handshake))
    report(wait_port(port), 'hs server listening')
    held = None if args.no_hold else Held(port)
    time.sleep(0.5)
    began = time.monotonic()
    with tempfile.TemporaryFile('w+') as output:
        client = start([args.client or hs, 'client', '-H', '127.0.0.1', '-p', str(port)],
                       env={'HYPERSHELL_SERVER_AUTH': KEY}, stdout=output, stderr=subprocess.STDOUT)
        code = wait(client, BOUND)
        output.seek(0)
        text = output.read()
    if args.expect_too_old:
        report(code not in (None, 0) and 'client too old' in text,
               f'client {args.client} connected, authenticated and was refused as too old in '
               f'{time.monotonic() - began:.1f} s (exit {code})')
    else:
        report(code == 0, f'client {args.client or "hs"} finished the tasks in {time.monotonic() - began:.1f} s '
                          f'(exit {code}, bound {BOUND:g} s)')
        report(wait(server, BOUND) == 0, 'hs server exited 0')
    if failures:
        print(text[-1500:], flush=True)
    if held is not None:
        check_held(held, args.handshake, 'hs server')
        held.release()


def scenario_cluster(args) -> None:
    port = free_port()
    hs = shutil.which('hs')
    began = time.monotonic()
    cluster = start([hs, 'cluster', taskfile(), '-H', '127.0.0.1', '-p', str(port), '--delay-start', '3'],
                    env=server_env(args.handshake))
    report(wait_port(port), 'hs cluster server listening')
    held = None if args.no_hold else Held(port)
    code = wait(cluster, BOUND)
    report(code == 0, f'hs cluster finished the tasks in {time.monotonic() - began:.1f} s (exit {code})')
    if held is not None:
        check_held(held, args.handshake, 'hs cluster')
        held.release()


def scenario_relay(args) -> None:
    up_port, site_port = free_port(), free_port()
    hs = shutil.which('hs')
    # The site client's launcher: wait 3 s, then run the client against loopback (the relay names its
    # host, which may not resolve in a sandbox).
    delay = os.path.join(os.getcwd(), 'delay.py')
    with open(delay, 'w') as stream:
        stream.write(DELAY_LAUNCHER.format(python=sys.executable))
    os.chmod(delay, 0o755)
    upstream = start([hs, 'server', taskfile(), '-H', '127.0.0.1', '-p', str(up_port)],
                     env={'HYPERSHELL_SERVER_AUTH': KEY})
    report(wait_port(up_port), 'upstream hs server listening')
    began = time.monotonic()
    env = {**server_env(args.handshake), 'HYPERSHELL_RELAY_UPSTREAM_AUTH': KEY}
    env.pop('HYPERSHELL_SERVER_AUTH')
    relay = start([hs, 'relay', '-H', '127.0.0.1', '-p', str(up_port), '--bind', '127.0.0.1',
                   '--port', str(site_port), '--launcher', delay, '--database', os.path.join(os.getcwd(), 'relay.db')],
                  env=env)
    report(wait_port(site_port), 'relay-local server listening')
    held = None if args.no_hold else Held(site_port)
    code = wait(upstream, BOUND)
    report(code == 0, f'upstream finished the tasks through the relay in {time.monotonic() - began:.1f} s (exit {code})')
    if held is not None:
        check_held(held, args.handshake, 'hs relay site-local server')
        held.release()
    relay.terminate()
    wait(relay, 10)


def scenario_refuse(args) -> None:
    hs = shutil.which('hs')
    for value in ('0', '-5', 'abc'):
        env = {'HYPERSHELL_SERVER_AUTH': KEY, 'HYPERSHELL_SERVER_TLS_HANDSHAKE': value,
               'HYPERSHELL_RELAY_UPSTREAM_AUTH': KEY}
        for argv in ([hs, 'server', taskfile(), '-H', '127.0.0.1', '-p', str(free_port())],
                     [hs, 'cluster', taskfile(), '-H', '127.0.0.1', '-p', str(free_port())],
                     [hs, 'relay', '-H', '127.0.0.1', '-p', str(free_port()), '--bind', '127.0.0.1',
                      '--port', str(free_port()), '--database', os.path.join(os.getcwd(), f'refuse-{value}.db')]):
            with tempfile.TemporaryFile('w+') as output:
                proc = start(argv, env=env, stdout=output, stderr=subprocess.STDOUT)
                code = wait(proc, 20)
                if code is None:  # Did not refuse: stop it now, so the next run starts clean
                    proc.terminate()
                    wait(proc, 10)
                output.seek(0)
                text = output.read()
            report(code not in (None, 0) and 'server.tls.handshake' in text,
                   f'{argv[1]} refuses handshake = {value!r} naming server.tls.handshake (exit {code})')
            if code not in (None, 0) and 'server.tls.handshake' not in text:
                print(text[-600:], flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('scenario', choices=['server', 'cluster', 'relay', 'refuse'])
    parser.add_argument('--client', default=None)
    parser.add_argument('--handshake', type=float, default=None)
    parser.add_argument('--no-hold', action='store_true')
    parser.add_argument('--expect-too-old', action='store_true')
    args = parser.parse_args()
    try:
        globals()[f'scenario_{args.scenario}'](args)
    finally:
        stop_all()
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
