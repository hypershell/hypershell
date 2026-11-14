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
                                               (with --handshake, only once the window has passed)
    cluster  [--handshake SEC]                 hs cluster (local), clients start 3 s after the server
                                               (SEC + 5 s with --handshake)
    relay    [--handshake SEC]                 hs server upstream, hs relay with its site client delayed 3 s
    refuse                                     hs server, hs cluster and hs relay refuse handshake = 0, -5, abc,
                                               30d, 4294968 and a 401-digit integer; hs server and hs cluster
                                               made plain (--no-tls, or server.tls.enabled = false) start
                                               with abc or 30d

Without --handshake the default deadline applies and the held connection must still be open when the
work is done (the server served others while it was pending); a close when the server stops,
after the work, is that stop, not the deadline. With --handshake SEC the held connection
must be closed by the server between SEC and SEC + 3 seconds after it connected, while the server is
still up: no client of `server` or `cluster` runs before the window has passed, so a server without the
deadline fails. --client names another
`hs` executable (e.g., an older release installed in a scratch environment) to run as the client,
without this environment's PYTHONPATH;
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


def start(argv: List[str], env: Optional[dict] = None, drop: tuple = (), **kwargs) -> subprocess.Popen:
    """Start `argv` with this environment plus `env`, less the variables named in `drop`."""
    merged = {name: value for name, value in {**os.environ, **(env or {})}.items() if name not in drop}
    proc = subprocess.Popen(argv, env=merged, **kwargs)
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


def taskfile(tag: str = '') -> str:
    """A task file; a `tag` gives it its own name and content, so a site database takes it as new."""
    path = os.path.join(os.getcwd(), f'tasks{tag}.in')
    with open(path, 'w') as stream:
        stream.writelines(f'echo {n} {tag}\n' for n in range(TASKS))
    return path


def check_held(held: Held, handshake: Optional[float], label: str, finished: Optional[float] = None) -> None:
    """Judge the held connection: open when the work finished (monotonic `finished`), or closed by the
    server between `handshake` and `handshake` + 3 seconds after it connected."""
    if handshake is None:
        closed_at = None if held.closed_after is None else held.opened + held.closed_after
        # Closed no earlier than half a second before the work was seen to finish: the server stopping
        ok = closed_at is None or (finished is not None and closed_at >= finished - 0.5)
        how = 'still open' if closed_at is None else 'closed only once the work was done and the server stopped'
        report(ok, f'{label}: held connection open until the work finished ({how})')
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
    if held is not None and args.handshake is not None:
        # No client runs until the window has passed, and the server waits for its tasks to be done, so
        # it is still up and only the deadline can close the held connection.
        check_held(held, args.handshake, 'hs server')
        report(server.poll() is None, 'hs server still running when the window ended (no client has run yet)')
    time.sleep(0.5)
    began = time.monotonic()
    with tempfile.TemporaryFile('w+') as output:
        # Another client runs its own installed code: PYTHONPATH (set to run the server from other
        # sources) is not passed on to it.
        client = start([args.client or hs, 'client', '-H', '127.0.0.1', '-p', str(port)],
                       env={'HYPERSHELL_SERVER_AUTH': KEY}, drop=('PYTHONPATH',) if args.client else (),
                       stdout=output, stderr=subprocess.STDOUT)
        code = wait(client, BOUND)
        finished = time.monotonic()
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
        if args.handshake is None:
            check_held(held, None, 'hs server', finished)
        held.release()


def scenario_cluster(args) -> None:
    port = free_port()
    hs = shutil.which('hs')
    began = time.monotonic()
    # With a deadline, the clients start only after the window has passed, so the server is still up then
    delay = 3 if args.handshake is None else args.handshake + 5
    cluster = start([hs, 'cluster', taskfile(), '-H', '127.0.0.1', '-p', str(port), '--delay-start', f'{delay:g}'],
                    env=server_env(args.handshake))
    report(wait_port(port), 'hs cluster server listening')
    held = None if args.no_hold else Held(port)
    code = wait(cluster, BOUND)
    finished = time.monotonic()
    report(code == 0, f'hs cluster finished the tasks in {time.monotonic() - began:.1f} s (exit {code})')
    if held is not None:
        check_held(held, args.handshake, 'hs cluster', finished)
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
                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # The relay stops its local server (cutting the held connection) once upstream says it is shutting
    # down, which it does only when the work is done, so that line marks the end of the work here.
    upstream_done: List[float] = []

    def echo() -> None:
        for line in relay.stdout:
            if not upstream_done and 'Upstream server is shutting down' in line:
                upstream_done.append(time.monotonic())
            print(line, end='', flush=True)

    threading.Thread(target=echo, daemon=True).start()
    report(wait_port(site_port), 'relay-local server listening')
    held = None if args.no_hold else Held(site_port)
    code = wait(upstream, BOUND)
    finished = upstream_done[0] if upstream_done else time.monotonic()
    report(code == 0, f'upstream finished the tasks through the relay in {time.monotonic() - began:.1f} s (exit {code})')
    if held is not None:
        check_held(held, args.handshake, 'hs relay site-local server', finished)
        held.release()
    relay.terminate()
    wait(relay, 10)


REFUSED = ('0', '-5', 'abc', '30d', '4294968', '1' + '0' * 400)


def shown(value: str) -> str:
    return repr(value) if len(value) <= 20 else f'<{len(value)}-digit integer>'


def scenario_refuse(args) -> None:
    """Each TLS server command refuses a bad value naming the key; a server made plain, by --no-tls or by
    server.tls.enabled = false, starts whatever the value."""
    hs = shutil.which('hs')
    for n, value in enumerate(REFUSED):
        env = {'HYPERSHELL_SERVER_AUTH': KEY, 'HYPERSHELL_SERVER_TLS_HANDSHAKE': value,
               'HYPERSHELL_RELAY_UPSTREAM_AUTH': KEY}
        for argv in ([hs, 'server', taskfile(f'-refuse-{n}-server'), '-H', '127.0.0.1', '-p', str(free_port())],
                     [hs, 'cluster', taskfile(f'-refuse-{n}-cluster'), '-H', '127.0.0.1', '-p', str(free_port())],
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
                   f'{argv[1]} refuses handshake = {shown(value)} naming server.tls.handshake (exit {code})')
            if code not in (None, 0) and 'server.tls.handshake' not in text:
                print(text[-600:], flush=True)
    for value in ('abc', '30d'):
        for how in ('--no-tls', 'enabled = false'):
            env = {'HYPERSHELL_SERVER_AUTH': KEY, 'HYPERSHELL_SERVER_TLS_HANDSHAKE': value}
            extra = ['--no-tls'] if how == '--no-tls' else []
            if how != '--no-tls':
                env['HYPERSHELL_SERVER_TLS_ENABLED'] = 'false'
            tag = f'-{value}-{"no-tls" if extra else "disabled"}'
            for argv in ([hs, 'server', taskfile(f'{tag}-server'), '-H', '127.0.0.1', '-p', str(free_port()), *extra],
                         [hs, 'cluster', taskfile(f'{tag}-cluster'), '-H', '127.0.0.1', '-p', str(free_port()), *extra]):
                with tempfile.TemporaryFile('w+') as output:
                    proc = start(argv, env=env, stdout=output, stderr=subprocess.STDOUT)
                    code = wait(proc, 5)
                    if code is None:  # Started and still serving: that is the pass; stop it now
                        proc.terminate()
                        wait(proc, 10)
                    output.seek(0)
                    text = output.read()
                report(code in (None, 0) and 'server.tls.handshake' not in text,
                       f'{argv[1]} made plain by {how} starts with handshake = {value!r} '
                       f'({"still serving after 5 s" if code is None else f"exit {code}"})')
                if code not in (None, 0):
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
