#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Multi-node HyperShell throughput drive: one server and N clients, started by any launcher
# (a batch job step, mpirun, ssh, ...) with the same HSD_RUN_ID. Configured by environment only;
# nothing here is specific to a site or a scheduler.
#
# Everything heavy stays node-local: the archive is unpacked once per node under HSD_LOCAL, and
# the interpreter, packages, sites, task database and logs live there. Only small results are
# written to the shared HSD_RESULTS/<run_id>/: server.ready (the port), server.crt (the server's
# public certificate, TLS only), server.json and client-<index>.json. Logs are copied there, gzipped,
# only when a process fails. No host names, user names or paths are recorded in the JSON.
#
#   HSD_ROLE              server | client                                              (required)
#   HSD_ARCHIVE           archive from build_archive.sh for the version under test     (required)
#   HSD_RESULTS           shared results root                                          (required)
#   HSD_RUN_ID            id shared by a server and its clients, [A-Za-z0-9._-]+       (required)
#   HSD_AUTH              queue key, >= 16 chars of [A-Za-z0-9._+/=-]                  (required)
#   HSD_CLIENT_INDEX      this client's index, [A-Za-z0-9._-]+               (required for clients)
#   HSD_HOST              address clients dial; with TLS, a name in the server's
#                         certificate (its host name)                    (default: localhost)
#   HSD_BIND / HSD_PORT   server bind address / port                (default: 0.0.0.0 / 50001)
#   HSD_LOCAL             node-local scratch root                     (default: $TMPDIR or /tmp)
#   HSD_DB                server task database; must be under HSD_LOCAL unless
#                         HSD_ALLOW_SHARED_DB=1     (default: $HSD_LOCAL/hsd-$HSD_RUN_ID/server/task.db)
#   HSD_CLIENTS           expected client count, recorded                              (default: 0)
#   HSD_TASKS             task count M                                             (default: 10000)
#   HSD_FAIL_EVERY        every k-th task exits 3; 0 = never                         (default: 100)
#   HSD_COMMAND           the passing task                                          (default: true)
#   HSD_THREADS           executors per client                                          (default: 1)
#   HSD_BUNDLESIZE        server bundle size                                            (default: 1)
#   HSD_CLIENT_BUNDLESIZE / HSD_CLIENT_BUNDLEWAIT   client bundle size / wait (s)   (default: 1 / 1)
#   HSD_TLS               1 = TLS, clients pin the server's certificate; 0 = --no-tls  (default: 1)
#   HSD_IDLE              client idle backstop, s (hs client -T)                      (default: 120)
#   HSD_TIMEOUT           server wall limit, s; TERM, then KILL 30 s later           (default: 3600)
#   HSD_WAIT              bound on waiting for the archive unpack and for readiness, s (default: 600)
#
# Exit status: the server's or client's own, or 64 (usage), 70 (unpack wait), 71 (readiness wait).
set -eu

usage() { echo "drive.sh: $*" >&2; exit 64; }
: "${HSD_ROLE:?}" "${HSD_ARCHIVE:?}" "${HSD_RESULTS:?}" "${HSD_RUN_ID:?}" "${HSD_AUTH:?}"
HSD_HOST=${HSD_HOST:-localhost}; HSD_BIND=${HSD_BIND:-0.0.0.0}; HSD_PORT=${HSD_PORT:-50001}
HSD_LOCAL=${HSD_LOCAL:-${TMPDIR:-/tmp}}; HSD_TLS=${HSD_TLS:-1}; HSD_CLIENTS=${HSD_CLIENTS:-0}
HSD_TASKS=${HSD_TASKS:-10000}; HSD_FAIL_EVERY=${HSD_FAIL_EVERY:-100}; HSD_COMMAND=${HSD_COMMAND:-true}
HSD_THREADS=${HSD_THREADS:-1}; HSD_BUNDLESIZE=${HSD_BUNDLESIZE:-1}
HSD_CLIENT_BUNDLESIZE=${HSD_CLIENT_BUNDLESIZE:-1}; HSD_CLIENT_BUNDLEWAIT=${HSD_CLIENT_BUNDLEWAIT:-1}
HSD_IDLE=${HSD_IDLE:-120}; HSD_TIMEOUT=${HSD_TIMEOUT:-3600}; HSD_WAIT=${HSD_WAIT:-600}
export HSD_TLS HSD_TASKS HSD_CLIENTS HSD_THREADS HSD_BUNDLESIZE HSD_CLIENT_BUNDLESIZE HSD_CLIENT_BUNDLEWAIT HSD_RUN_ID

case "$HSD_RUN_ID" in *[!A-Za-z0-9._-]*|'') usage "HSD_RUN_ID must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_TLS" in 0|1) ;; *) usage "HSD_TLS must be 0 or 1" ;; esac
[ -f "$HSD_ARCHIVE" ] || usage "HSD_ARCHIVE not found"
mkdir -p "$HSD_LOCAL"
local_root=$(cd -P "$HSD_LOCAL" && pwd)
run_dir="$HSD_RESULTS/$HSD_RUN_ID"; mkdir -p "$run_dir"
node="$local_root/hsd-$HSD_RUN_ID"; mkdir -p "$node"

# Unpack once per node and archive: the first process to take the lock extracts, the rest wait.
pkg="$local_root/hsd-pkg-$(basename "$HSD_ARCHIVE" .tar.gz)"
mkdir -p "$pkg"
if mkdir "$pkg/unpack.lock" 2>/dev/null; then
    tar -xzf "$HSD_ARCHIVE" -C "$pkg"
    # Byte-compile on the node (a cross-built archive ships none); a no-op when already compiled.
    "$pkg/python/bin/python3" -m compileall -q -s "$pkg" "$pkg/site" > /dev/null 2>&1 || true
    touch "$pkg/unpacked"
else
    i=0; while [ ! -e "$pkg/unpacked" ]; do i=$((i+1)); [ "$i" -gt "$HSD_WAIT" ] && exit 70; sleep 1; done
fi
PY="$pkg/python/bin/python3"; LABEL=$(cat "$pkg/LABEL"); export LABEL
# Only node-local code on sys.path; no user site, no bytecode writes.
export PYTHONPATH="$pkg/site" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
HS_MAIN='import sys; from hypershell import main; sys.exit(main())'
hs() { "$PY" -c "$HS_MAIN" "$@"; }
for v in $(env | sed -n 's/^\(HYPERSHELL_[A-Z0-9_]*\)=.*/\1/p'); do unset "$v"; done
export HYPERSHELL_CONFIG_FILE='' HYPERSHELL_LOGGING_LEVEL=WARNING HYPERSHELL_SERVER_AUTH="$HSD_AUTH"
now() { "$PY" -c 'import time; print(f"{time.time():.3f}")'; }
publish() { cp "$1" "$2.tmp" && mv "$2.tmp" "$2"; }  # Atomic on the shared filesystem.

case "$HSD_ROLE" in
server)
    site="$node/server"; mkdir -p "$site"; cd "$site"
    HSD_DB=${HSD_DB:-$site/task.db}
    mkdir -p "$(dirname "$HSD_DB")"
    db_dir=$(cd -P "$(dirname "$HSD_DB")" && pwd)
    case "$db_dir/" in
        "$local_root"/*) ;;
        *) [ "${HSD_ALLOW_SHARED_DB:-0}" = 1 ] || usage "HSD_DB must be under HSD_LOCAL (or set HSD_ALLOW_SHARED_DB=1)" ;;
    esac
    export HYPERSHELL_SITE="$site" HYPERSHELL_DATABASE_FILE="$db_dir/$(basename "$HSD_DB")"
    "$PY" -c 'import sys
m, k, ok = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
for i in range(1, m + 1):
    print("exit 3" if k and i % k == 0 else ok)' "$HSD_TASKS" "$HSD_FAIL_EVERY" "$HSD_COMMAND" > tasks.in
    t_submit=$(now); hs submit -f tasks.in -b 10000 -w 1 > submit.log 2>&1; t_submitted=$(now)
    tls_opt=; [ "$HSD_TLS" = 1 ] || tls_opt=--no-tls
    t_start=$(now)
    # Started directly (not through hs) so that $! is the server's own process.
    "$PY" -c "$HS_MAIN" server --restart -H "$HSD_BIND" -p "$HSD_PORT" -b "$HSD_BUNDLESIZE" $tls_opt > server.log 2>&1 &
    server_pid=$!
    # Watchdog: ends on its own within a second of the server exiting.
    ( i=0; while kill -0 "$server_pid" 2>/dev/null; do
          sleep 1; i=$((i+1))
          if [ "$i" -eq "$HSD_TIMEOUT" ]; then kill -TERM "$server_pid"; fi
          if [ "$i" -eq $((HSD_TIMEOUT + 30)) ]; then kill -KILL "$server_pid"; fi
      done ) > /dev/null 2>&1 &
    # Waits for LISTEN on the port (no probe connection), publishes readiness, then samples the
    # peak footprint of the server's process tree until it exits.
    "$PY" - "$server_pid" "$HSD_PORT" "$site" "$run_dir" <<'EOF' &
import json, os, sys, time, shutil
import psutil
pid, port, site, run_dir = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
proc = psutil.Process(pid)
keys = ('rss_mb', 'threads', 'fds', 'children', 'tree_rss_mb', 'tree_threads', 'tree_fds')
peak = dict.fromkeys(keys, 0)
def publish(src, name):
    shutil.copy(src, os.path.join(run_dir, name + '.tmp'))
    os.replace(os.path.join(run_dir, name + '.tmp'), os.path.join(run_dir, name))
ready = False
while proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
    try:
        procs = [proc, *proc.children(recursive=True)]
        if not ready and any(c.status == psutil.CONN_LISTEN and c.laddr.port == port
                             for p in procs for c in p.net_connections(kind='tcp')):
            if os.environ['HSD_TLS'] == '1':
                publish(next(os.path.join(d, 'server.crt') for d, _, files in os.walk(site) if 'server.crt' in files),
                        'server.crt')
            with open(os.path.join(site, 'ready'), 'w') as stream:
                print(port, file=stream)
            publish(os.path.join(site, 'ready'), 'server.ready')
            ready = True
        now = dict(rss_mb=proc.memory_info().rss / 2**20, threads=proc.num_threads(), fds=proc.num_fds(),
                   children=len(procs) - 1, tree_rss_mb=0, tree_threads=0, tree_fds=0)
        for p in procs:
            try:
                now['tree_rss_mb'] += p.memory_info().rss / 2**20
                now['tree_threads'] += p.num_threads()
                now['tree_fds'] += p.num_fds()
            except psutil.Error:
                pass
        for key, value in now.items():
            peak[key] = max(peak[key], value)
    except (psutil.Error, StopIteration):
        pass
    time.sleep(0.2)
with open(os.path.join(site, 'peak.json'), 'w') as stream:
    json.dump({key: round(value, 1) for key, value in peak.items()}, stream)
EOF
    sampler_pid=$!
    set +e; wait "$server_pid"; rc=$?; set -e
    t_end=$(now)
    wait "$sampler_pid" || true
    export rc t_submit t_submitted t_start t_end
    "$PY" - "$HYPERSHELL_DATABASE_FILE" "$site/peak.json" "$run_dir/server.json" <<'EOF'
import json, os, sys, sqlite3, multiprocessing
from datetime import datetime
import hypershell
env = os.environ
db = sqlite3.connect(sys.argv[1])
query = lambda sql: db.execute(sql).fetchall()
stamp = lambda text: datetime.fromisoformat(text).timestamp()
done = sorted(stamp(row[0]) for row in query('SELECT completion_time FROM task WHERE completion_time IS NOT NULL'))
first = query('SELECT MIN(schedule_time) FROM task')[0][0]
span = done[-1] - stamp(first) if done and first else None
p10_p90 = None
if len(done) >= 10:
    lo, hi = done[int(0.1 * len(done))], done[int(0.9 * len(done)) - 1]
    p10_p90 = round(0.8 * len(done) / (hi - lo), 1) if hi > lo else None
rows, retried, incomplete, seen = query('SELECT COUNT(*), COALESCE(SUM(retried), 0), '
                                        'COALESCE(SUM(completion_time IS NULL), 0), '
                                        'COUNT(DISTINCT client_id) FROM task')[0]
try:
    with open(sys.argv[2]) as stream:
        peak = json.load(stream)
except OSError:
    peak = None
record = dict(
    label=env['LABEL'], run_id=env['HSD_RUN_ID'], role='server',
    hypershell=hypershell.__version__, python=sys.version.split()[0],
    start_method=multiprocessing.get_start_method(),
    tls=int(env['HSD_TLS']), tasks=int(env['HSD_TASKS']), clients_expected=int(env['HSD_CLIENTS']),
    clients_seen=seen, threads=int(env['HSD_THREADS']), bundlesize=int(env['HSD_BUNDLESIZE']),
    client_bundlesize=int(env['HSD_CLIENT_BUNDLESIZE']), client_bundlewait=float(env['HSD_CLIENT_BUNDLEWAIT']),
    server_rc=int(env['rc']),
    submit_s=round(float(env['t_submitted']) - float(env['t_submit']), 3),
    server_wall_s=round(float(env['t_end']) - float(env['t_start']), 3),
    db_span_s=round(span, 3) if span else None,
    tasks_per_s=round(len(done) / span, 1) if span else None,
    p10_p90_tasks_per_s=p10_p90,
    rows=rows, retried=retried, incomplete=incomplete,
    exit_hist={str(status): count for status, count in query('SELECT exit_status, COUNT(*) FROM task GROUP BY exit_status')},
    peak=peak)
with open(sys.argv[3] + '.tmp', 'w') as stream:
    json.dump(record, stream, indent=1)
os.replace(sys.argv[3] + '.tmp', sys.argv[3])
EOF
    if [ "$rc" != 0 ]; then gzip -c server.log > "$run_dir/server.log.gz"; fi
    exit "$rc" ;;
client)
    : "${HSD_CLIENT_INDEX:?required for clients}"
    case "$HSD_CLIENT_INDEX" in *[!A-Za-z0-9._-]*) usage "HSD_CLIENT_INDEX must match [A-Za-z0-9._-]+" ;; esac
    site="$node/client-$HSD_CLIENT_INDEX"; mkdir -p "$site"; cd "$site"
    export HYPERSHELL_SITE="$site"
    i=0; while [ ! -e "$run_dir/server.ready" ]; do i=$((i+1)); [ "$i" -gt "$HSD_WAIT" ] && exit 71; sleep 1; done
    if [ "$HSD_TLS" = 1 ]; then
        cp "$run_dir/server.crt" "$site/server.crt"
        set -- --tls-ca "$site/server.crt"
    else
        set -- --no-tls
    fi
    t_start=$(now); set +e
    hs client -H "$HSD_HOST" -p "$HSD_PORT" -N "$HSD_THREADS" -b "$HSD_CLIENT_BUNDLESIZE" \
        -w "$HSD_CLIENT_BUNDLEWAIT" -T "$HSD_IDLE" "$@" > client.log 2>&1
    rc=$?; set -e; t_end=$(now)
    printf '{"label": "%s", "run_id": "%s", "role": "client", "index": "%s", "rc": %s, "start": %s, "end": %s}\n' \
        "$LABEL" "$HSD_RUN_ID" "$HSD_CLIENT_INDEX" "$rc" "$t_start" "$t_end" > "$site/client.json"
    publish "$site/client.json" "$run_dir/client-$HSD_CLIENT_INDEX.json"
    if [ "$rc" != 0 ]; then gzip -c client.log > "$run_dir/client-$HSD_CLIENT_INDEX.log.gz"; fi
    exit "$rc" ;;
*) usage "HSD_ROLE must be server or client" ;;
esac
