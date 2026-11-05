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
#
#   Zones (server). Unset HSD_ZONES keeps the task file exactly as above (every task unzoned).
#   HSD_ZONES             task mix by zone, NAME:WEIGHT,... (e.g. default:40,cpu:30,gpu:30); each
#                         zone's tasks are submitted with --zone NAME (none for 'default'), and each
#                         checks the running client's zone mark, exiting 7 on a mismatch   (default: unset)
#   HSD_GROUPS            task groups G; task i of M is in group (i-1)*G/M                (default: 1)
#   HSD_RETRIES           server -r; with it, a failing task fails on its first attempt only (default: 0)
#   HSD_CAP               server.bundlemax (most tasks in one reply)              (default: built-in)
#                         With zones, every HSD_FAIL_EVERY-th task of each zone fails.
#   Zones (client).
#   HSD_ZONE              the zone this client serves (hs client --zone)             (default: none)
#   HSD_CLIENT_ADVERTISE  1 = pass -b HSD_CLIENT_BUNDLESIZE; 0 = omit -b (advertise nothing) (default: 1)
#   HSD_CLIENT_LOG        client log level; DEBUG records bundles received          (default: WARNING)
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
HSD_ZONES=${HSD_ZONES:-}; HSD_GROUPS=${HSD_GROUPS:-1}; HSD_RETRIES=${HSD_RETRIES:-0}; HSD_CAP=${HSD_CAP:-}
HSD_ZONE=${HSD_ZONE:-}; HSD_CLIENT_ADVERTISE=${HSD_CLIENT_ADVERTISE:-1}; HSD_CLIENT_LOG=${HSD_CLIENT_LOG:-WARNING}
export HSD_TLS HSD_TASKS HSD_CLIENTS HSD_THREADS HSD_BUNDLESIZE HSD_CLIENT_BUNDLESIZE HSD_CLIENT_BUNDLEWAIT HSD_RUN_ID
export HSD_ZONES HSD_GROUPS HSD_RETRIES HSD_CAP HSD_ZONE HSD_CLIENT_ADVERTISE HSD_FAIL_EVERY

case "$HSD_RUN_ID" in *[!A-Za-z0-9._-]*|'') usage "HSD_RUN_ID must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_TLS" in 0|1) ;; *) usage "HSD_TLS must be 0 or 1" ;; esac
case "$HSD_CLIENT_ADVERTISE" in 0|1) ;; *) usage "HSD_CLIENT_ADVERTISE must be 0 or 1" ;; esac
case "$HSD_ZONES" in *[!A-Za-z0-9._:,-]*) usage "HSD_ZONES must match NAME:WEIGHT,... with [A-Za-z0-9._-] names" ;; esac
case "$HSD_ZONE" in *[!A-Za-z0-9._-]*) usage "HSD_ZONE must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_GROUPS$HSD_RETRIES$HSD_CAP" in *[!0-9]*) usage "HSD_GROUPS, HSD_RETRIES and HSD_CAP must be whole numbers" ;; esac
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
    if [ -z "$HSD_ZONES" ]; then
        "$PY" -c 'import sys
m, k, ok, g, r = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
fail = "test \"${TASK_ATTEMPT:-1}\" -gt 1 || exit 3" if r else "exit 3"
for i in range(1, m + 1):
    line = fail if k and i % k == 0 else ok
    print(line if g <= 1 else f"{line}  # HYPERSHELL: group:{(i - 1) * g // m}")' \
            "$HSD_TASKS" "$HSD_FAIL_EVERY" "$HSD_COMMAND" "$HSD_GROUPS" "$HSD_RETRIES" > tasks.in
        t_submit=$(now); hs submit -f tasks.in -b 10000 -w 1 > submit.log 2>&1; t_submitted=$(now)
    else
        # One file per zone (tasks-<zone>.in); task i goes to the zone its position in each block
        # of sum(weights) falls in. A zoned task exits 7 unless its client's mark names its zone.
        "$PY" -c 'import sys
m, k, ok, g, r, spec = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
zones = [(name, int(weight)) for name, weight in (item.split(":") for item in spec.split(","))]
total = sum(weight for _, weight in zones)
fail = "test \"${TASK_ATTEMPT:-1}\" -gt 1 || exit 3" if r else "exit 3"
files = {name: open(f"tasks-{name}.in", "w") for name, _ in zones}
seen = dict.fromkeys(files, 0)
for i in range(1, m + 1):
    slot, edge = (i - 1) % total, 0
    for name, weight in zones:
        edge += weight
        if slot < edge:
            break
    seen[name] += 1
    check = f"[ \"$HSD_ZONE_MARK\" = {name} ] || {{ echo 1 >> \"$HSD_MISMATCH\"; exit 7; }}"
    work = fail if k and seen[name] % k == 0 else ok
    tag = "" if g <= 1 else f"  # HYPERSHELL: group:{(i - 1) * g // m}"
    print(f"{check}; {work}{tag}", file=files[name])
for stream in files.values():
    stream.close()' "$HSD_TASKS" "$HSD_FAIL_EVERY" "$HSD_COMMAND" "$HSD_GROUPS" "$HSD_RETRIES" "$HSD_ZONES"
        t_submit=$(now)
        for spec in $(echo "$HSD_ZONES" | tr ',' ' '); do
            zone=${spec%%:*}; zone_opt=; [ "$zone" = default ] || zone_opt="--zone $zone"
            hs submit -f "tasks-$zone.in" -b 10000 -w 1 $zone_opt >> submit.log 2>&1
        done
        t_submitted=$(now)
    fi
    tls_opt=; [ "$HSD_TLS" = 1 ] || tls_opt=--no-tls
    retry_opt=; [ "$HSD_RETRIES" = 0 ] || retry_opt="-r $HSD_RETRIES"
    [ -z "$HSD_CAP" ] || export HYPERSHELL_SERVER_BUNDLEMAX="$HSD_CAP"
    t_start=$(now)
    # Started directly (not through hs) so that $! is the server's own process.
    "$PY" -c "$HS_MAIN" server --restart -H "$HSD_BIND" -p "$HSD_PORT" -b "$HSD_BUNDLESIZE" $retry_opt $tls_opt > server.log 2>&1 &
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
columns = [row[1] for row in query('PRAGMA table_info("task")')]
zoned = bool(env['HSD_ZONES']) and 'zone' in columns
names = dict(query('SELECT id, name FROM zone')) if zoned else {}
zone_tasks = ({names.get(zone, 'default' if zone is None else 'unknown'): count
               for zone, count in query('SELECT zone, COUNT(*) FROM task GROUP BY zone')} if zoned else None)
zone_mismatched_retries = (query('SELECT COUNT(*) FROM task t JOIN task p ON t.previous_id = p.id '
                                 'WHERE t.zone IS NOT p.zone')[0][0] if zoned else None)
evictions = query('SELECT COALESCE(SUM(evicted), 0) FROM client')[0][0]
groups = sorted(row[0] for row in query('SELECT DISTINCT "group" FROM task'))
group_order = True
for earlier, later in zip(groups, groups[1:]):
    last_done = query(f'SELECT MAX(completion_time) FROM task WHERE "group" = {earlier}')[0][0]
    first_out = query(f'SELECT MIN(schedule_time) FROM task WHERE "group" = {later}')[0][0]
    if last_done is None or first_out is None or stamp(last_done) > stamp(first_out):
        group_order = False
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
    peak=peak,
    zones=env['HSD_ZONES'] or None, groups=int(env['HSD_GROUPS']), retries=int(env['HSD_RETRIES']),
    cap=int(env['HSD_CAP']) if env['HSD_CAP'] else None, fail_every=int(env['HSD_FAIL_EVERY']),
    zone_tasks=zone_tasks, zone_mismatched_retries=zone_mismatched_retries, evictions=evictions,
    group_order=group_order)
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
    [ "$HSD_CLIENT_ADVERTISE" = 0 ] || set -- -b "$HSD_CLIENT_BUNDLESIZE" "$@"
    [ -z "$HSD_ZONE" ] || set -- --zone "$HSD_ZONE" "$@"
    export HSD_ZONE_MARK="${HSD_ZONE:-default}" HSD_MISMATCH="$site/mismatch" HYPERSHELL_LOGGING_LEVEL="$HSD_CLIENT_LOG"
    : > "$HSD_MISMATCH"
    t_start=$(now); set +e
    hs client -H "$HSD_HOST" -p "$HSD_PORT" -N "$HSD_THREADS" \
        -w "$HSD_CLIENT_BUNDLEWAIT" -T "$HSD_IDLE" "$@" > client.log 2>&1
    rc=$?; set -e; t_end=$(now)
    export rc t_start t_end
    "$PY" - "$site/client.log" "$HSD_MISMATCH" "$site/client.json" <<'EOF'
import json, os, re, sys
env = os.environ
with open(sys.argv[1], errors='replace') as stream:
    sizes = [int(size) for size in re.findall(r'Received (\d+) tasks', stream.read())]
with open(sys.argv[2]) as stream:
    mismatched = sum(1 for line in stream if line.strip())
debug = env['HYPERSHELL_LOGGING_LEVEL'].upper() in ('DEBUG', 'TRACE', 'DEVEL')
record = dict(label=env['LABEL'], run_id=env['HSD_RUN_ID'], role='client', index=env['HSD_CLIENT_INDEX'],
              rc=int(env['rc']), start=float(env['t_start']), end=float(env['t_end']),
              zone=env['HSD_ZONE'] or None,
              advertised=int(env['HSD_CLIENT_BUNDLESIZE']) if env['HSD_CLIENT_ADVERTISE'] == '1' else None,
              received=sum(sizes) if debug else None, largest=max(sizes, default=0) if debug else None,
              exit7=mismatched)
with open(sys.argv[3], 'w') as stream:
    json.dump(record, stream)
EOF
    publish "$site/client.json" "$run_dir/client-$HSD_CLIENT_INDEX.json"
    if [ "$rc" != 0 ]; then gzip -c client.log > "$run_dir/client-$HSD_CLIENT_INDEX.log.gz"; fi
    exit "$rc" ;;
*) usage "HSD_ROLE must be server or client" ;;
esac
