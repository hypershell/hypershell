# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Connect-and-first-call latency of an in-process TLS queue server (R7).

Usage:
    uv run python spec/listener-handshake-deadline/research/latency.py TLS_DIR [SEQUENTIAL] [BURST]
    uv run python spec/listener-handshake-deadline/research/latency.py compare DEVELOP_SRC TLS_DIR [ROUNDS]

TLS_DIR is any scratch directory (self-signed materials are generated there once). `compare` runs the
measurement alternately on develop (DEVELOP_SRC is a `src` directory exported from develop, put first on
PYTHONPATH) and on this checkout, ROUNDS times each (default 3), prints every result and the median of
the medians for each, and exits non-zero unless every burst completed and this checkout's median is
within 10 % or 5 ms (whichever is larger) of develop's (R7).

Starts one in-process TLS QueueServer on loopback, then:
  - sequential: SEQUENTIAL clients one after another (after 5 warm-up connects), each timed from
    before QueueClient.connect() to after its first queue call (completed.qsize()); prints the median;
  - burst: BURST clients started at once from threads; prints how many connected and completed a call.
Prints one JSON line.
"""
import gc, os, sys, json, time, statistics, threading, subprocess


def compare(develop_src: str, directory: str, rounds: int) -> int:
    """Alternate develop and this checkout; judge R7 on the medians."""
    results = {'develop': [], 'branch': []}
    for _ in range(rounds):
        for side in ('develop', 'branch'):
            env = dict(os.environ)
            if side == 'develop':
                env['PYTHONPATH'] = develop_src + os.pathsep + env.get('PYTHONPATH', '')
            out = subprocess.run([sys.executable, __file__, directory, '100', '64'], env=env,
                                 capture_output=True, text=True, timeout=600)
            line = json.loads(out.stdout.strip().splitlines()[-1])
            print(side, json.dumps(line), flush=True)
            results[side].append(line)
    develop = statistics.median(r['median_ms'] for r in results['develop'])
    branch = statistics.median(r['median_ms'] for r in results['branch'])
    allowed = develop + max(0.10 * develop, 5.0)
    bursts = all(r['burst_ok'] == r['burst'] for side in results.values() for r in side)
    ok = bursts and branch <= allowed
    print(f'{"PASS" if ok else "FAIL"}: median develop {develop:.2f} ms, branch {branch:.2f} ms '
          f'(allowed up to {allowed:.2f} ms); every 64-client burst completed: {bursts}', flush=True)
    return 0 if ok else 1


if len(sys.argv) > 1 and sys.argv[1] == 'compare':
    sys.exit(compare(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 3))

from hypershell.core.queue import QueueServer, QueueClient, QueueConfig
from hypershell.core.tls import TLSConfig, ensure_default_materials

AUTH = 'queue-test-key-0123456789abcdef'
directory = sys.argv[1]
sequential = int(sys.argv[2]) if len(sys.argv) > 2 else 100
burst = int(sys.argv[3]) if len(sys.argv) > 3 else 64
cert, key = ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])
tls = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)


def config(port: int = 0) -> QueueConfig:
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls)


def once(port: int) -> float:
    start = time.perf_counter()
    client = QueueClient(config(port))
    client.connect()
    client.completed.qsize()
    elapsed = time.perf_counter() - start
    del client
    gc.collect()
    return elapsed


with QueueServer(config()) as server:
    port = server.address[1]
    for _ in range(5):
        once(port)
    times = [once(port) for _ in range(sequential)]
    done, errors = [], []

    def burst_client() -> None:
        try:
            client = QueueClient(config(port))
            client.connect()
            client.completed.qsize()
            done.append(client)
        except Exception as error:
            errors.append(repr(error))

    threads = [threading.Thread(target=burst_client, daemon=True) for _ in range(burst)]
    start = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    burst_seconds = time.perf_counter() - start
    print(json.dumps({'python': sys.version.split()[0], 'sequential': sequential,
                      'median_ms': round(statistics.median(times) * 1000, 2),
                      'p10_ms': round(statistics.quantiles(times, n=10)[0] * 1000, 2),
                      'p90_ms': round(statistics.quantiles(times, n=10)[-1] * 1000, 2),
                      'burst': burst, 'burst_ok': len(done), 'burst_errors': errors[:3],
                      'burst_seconds': round(burst_seconds, 2)}))
    done.clear()
