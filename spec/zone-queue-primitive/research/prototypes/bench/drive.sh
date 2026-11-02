#!/bin/sh
# Multi-node HyperShell throughput drive (prototype). One server + N clients, any launcher.
# Everything is set by environment variables; nothing here is site- or scheduler-specific.
#
#   HSD_ROLE        server | client                                    (required)
#   HSD_ARCHIVE     path to the staged archive (python/ + site/ + LABEL) (required)
#   HSD_RESULTS     shared results directory (only results go here)     (required)
#   HSD_RUN_ID      unique id shared by the server and its clients      (required)
#   HSD_AUTH        queue auth key, >=16 chars of [A-Za-z0-9._+/=-]       (required)
#   HSD_HOST        server address clients connect to       (default: localhost)
#   HSD_BIND        server bind address                     (default: 0.0.0.0)
#   HSD_PORT        (default: 50001)       HSD_TLS      1|0  (default: 1)
#   HSD_LOCAL       node-local scratch root  (default: ${TMPDIR:-/tmp})
#   HSD_TASKS       M tasks (default: 10000)  HSD_FAIL_EVERY  every k-th task exits 3 (default: 100; 0=never)
#   HSD_COMMAND     the passing task (default: true)
#   HSD_CLIENTS     expected client count (recorded only)   HSD_THREADS  executors per client (default: 1)
#   HSD_BUNDLESIZE  server bundle size (default: 1)  HSD_CLIENT_BUNDLESIZE / HSD_CLIENT_BUNDLEWAIT (1 / 1)
#   HSD_IDLE        client idle timeout, s (default: 120)   HSD_TIMEOUT  server wall limit, s (default: 3600)
#   HSD_WAIT        client wait for server readiness, s (default: 600)
#   HSD_CLIENT_INDEX  name for this client's result file (default: host-pid)
set -eu

: "${HSD_ROLE:?}" "${HSD_ARCHIVE:?}" "${HSD_RESULTS:?}" "${HSD_RUN_ID:?}" "${HSD_AUTH:?}"
HSD_HOST=${HSD_HOST:-localhost}; HSD_BIND=${HSD_BIND:-0.0.0.0}; HSD_PORT=${HSD_PORT:-50001}
HSD_TLS=${HSD_TLS:-1}; HSD_LOCAL=${HSD_LOCAL:-${TMPDIR:-/tmp}}
HSD_TASKS=${HSD_TASKS:-10000}; HSD_FAIL_EVERY=${HSD_FAIL_EVERY:-100}; HSD_COMMAND=${HSD_COMMAND:-true}
HSD_CLIENTS=${HSD_CLIENTS:-0}; HSD_THREADS=${HSD_THREADS:-1}; HSD_BUNDLESIZE=${HSD_BUNDLESIZE:-1}
HSD_CLIENT_BUNDLESIZE=${HSD_CLIENT_BUNDLESIZE:-1}; HSD_CLIENT_BUNDLEWAIT=${HSD_CLIENT_BUNDLEWAIT:-1}
HSD_IDLE=${HSD_IDLE:-120}; HSD_TIMEOUT=${HSD_TIMEOUT:-3600}; HSD_WAIT=${HSD_WAIT:-600}

host=$(hostname)
run_dir="$HSD_RESULTS/$HSD_RUN_ID"; mkdir -p "$run_dir"
node="$HSD_LOCAL/hsd-$HSD_RUN_ID"; mkdir -p "$node"

# --- unpack the archive once per node (first process wins; others wait) -----------------------
if mkdir "$node/unpack.lock" 2>/dev/null; then
    tar -xzf "$HSD_ARCHIVE" -C "$node" && touch "$node/unpacked"
else
    i=0; while [ ! -e "$node/unpacked" ]; do i=$((i+1)); [ $i -gt 600 ] && exit 70; sleep 1; done
fi
PY="$node/python/bin/python3"; LABEL=$(cat "$node/LABEL")
# Only node-local paths on sys.path; isolate from user/site config and the shared FS.
export PYTHONPATH="$node/site" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
HS_MAIN='import sys; from hypershell import main; sys.exit(main())'   # same entry point as `hs`
hs() { "$PY" -c "$HS_MAIN" "$@"; }
for v in $(env | sed -n 's/^\(HYPERSHELL_[A-Z0-9_]*\)=.*/\1/p'); do unset "$v"; done
export HYPERSHELL_CONFIG_FILE='' HYPERSHELL_LOGGING_LEVEL=WARNING HYPERSHELL_SERVER_AUTH="$HSD_AUTH"
now() { "$PY" -c 'import time; print(f"{time.time():.3f}")'; }

case "$HSD_ROLE" in
server)
    site="$node/server"; mkdir -p "$site"; cd "$site"
    export HYPERSHELL_SITE="$site" HYPERSHELL_DATABASE_FILE="$site/task.db"
    "$PY" -c 'import sys; m, k, ok = map(str, sys.argv[1:4]); m, k = int(m), int(k)
for i in range(1, m + 1): print("exit 3" if k and i % k == 0 else ok)' "$HSD_TASKS" "$HSD_FAIL_EVERY" "$HSD_COMMAND" > tasks.in
    t_sub=$(now); hs submit -f tasks.in -b 10000 -w 1; t_sub_end=$(now)
    tls_opt=; [ "$HSD_TLS" = 1 ] || tls_opt=--no-tls
    t0=$(now)
    # Launched directly (not via the hs() function) so $! is the server's own pid.
    "$PY" -c "$HS_MAIN" server --restart -H "$HSD_BIND" -p "$HSD_PORT" -b "$HSD_BUNDLESIZE" $tls_opt > server.log 2>&1 &
    spid=$!
    ( i=0; while kill -0 "$spid" 2>/dev/null; do i=$((i+5)); sleep 5
        if [ $i -ge "$HSD_TIMEOUT" ]; then kill -TERM "$spid"; sleep 30; kill -KILL "$spid"; fi; done ) 2>/dev/null &
    wpid=$!
    # Sampler: waits for LISTEN on the port (no probe connection), publishes readiness, then
    # samples the server's process tree until it exits and writes the peaks.
    "$PY" - "$spid" "$HSD_PORT" "$site" "$run_dir" "$HSD_HOST" "$HSD_TLS" <<'EOF' &
import json, os, shutil, sys, time, psutil
pid, port, site, run_dir, host, tls = int(sys.argv[1]), int(sys.argv[2]), *sys.argv[3:]
proc = psutil.Process(pid); peak = dict(rss_mb=0, threads=0, fds=0, children=0, tree_rss_mb=0, tree_threads=0, tree_fds=0)
def tree():
    try: return [proc] + proc.children(recursive=True)
    except psutil.Error: return [proc]
ready = False
while proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
    ps = tree()
    try:
        if not ready and any(c.status == psutil.CONN_LISTEN and c.laddr.port == port
                             for p in ps for c in p.net_connections(kind='tcp')):
            if tls == '1':
                crt = next(os.path.join(d, 'server.crt') for d, _, f in os.walk(site) if 'server.crt' in f)
                shutil.copy(crt, os.path.join(run_dir, 'server.crt.tmp'))
                os.replace(os.path.join(run_dir, 'server.crt.tmp'), os.path.join(run_dir, 'server.crt'))
            with open(os.path.join(run_dir, 'server.ready.tmp'), 'w') as f: f.write(f'{host} {port}\n')
            os.replace(os.path.join(run_dir, 'server.ready.tmp'), os.path.join(run_dir, 'server.ready'))
            ready = True
        own = dict(rss_mb=proc.memory_info().rss / 2**20, threads=proc.num_threads(), fds=proc.num_fds(), children=len(ps) - 1)
        tr = dict(tree_rss_mb=0, tree_threads=0, tree_fds=0)
        for p in ps:
            try: tr['tree_rss_mb'] += p.memory_info().rss / 2**20; tr['tree_threads'] += p.num_threads(); tr['tree_fds'] += p.num_fds()
            except psutil.Error: pass
        for k, v in {**own, **tr}.items(): peak[k] = max(peak[k], v)
    except (psutil.Error, StopIteration): pass
    time.sleep(0.2)
json.dump({k: round(v, 1) for k, v in peak.items()}, open(os.path.join(site, 'peak.json'), 'w'))
EOF
    sampler=$!
    set +e; wait "$spid"; rc=$?; set -e
    t1=$(now); wait "$wpid" "$sampler" || true
    "$PY" - "$site/task.db" "$site/peak.json" "$run_dir/server.json" <<EOF
import json, sqlite3, sys
from datetime import datetime
db = sqlite3.connect(sys.argv[1]); q = lambda s: db.execute(s).fetchall()
ts = lambda s: datetime.fromisoformat(s).timestamp()
done = sorted(ts(r[0]) for r in q('SELECT completion_time FROM task WHERE completion_time IS NOT NULL'))
first = ts(q('SELECT MIN(schedule_time) FROM task')[0][0])
m = $HSD_TASKS; span = done[-1] - first if done else None
lo, hi = done[int(0.1 * len(done))], done[int(0.9 * len(done)) - 1]
rows, retried, incomplete, n_clients = q('SELECT COUNT(*), SUM(retried), SUM(completion_time IS NULL), COUNT(DISTINCT client_id) FROM task')[0]
json.dump(dict(label='$LABEL', run_id='$HSD_RUN_ID', role='server', host='$host', tls=$HSD_TLS, tasks=m,
               clients_expected=$HSD_CLIENTS, clients_seen=n_clients, threads=$HSD_THREADS,
               bundlesize=$HSD_BUNDLESIZE, server_rc=$rc, submit_s=round($t_sub_end - $t_sub, 3),
               server_wall_s=round($t1 - $t0, 3), db_span_s=round(span, 3), tasks_per_s=round(m / span, 1),
               p10_p90_tasks_per_s=round(0.8 * len(done) / (hi - lo), 1),
               rows=rows, retried=retried, incomplete=incomplete,
               exit_hist={str(k): v for k, v in q('SELECT exit_status, COUNT(*) FROM task GROUP BY exit_status')},
               peak=json.load(open(sys.argv[2]))), open(sys.argv[3], 'w'), indent=1)
EOF
    gzip -c server.log > "$run_dir/server.log.gz"
    exit "$rc" ;;
client)
    name=${HSD_CLIENT_INDEX:-$host-$$}
    site="$node/client-$name"; mkdir -p "$site"; cd "$site"; export HYPERSHELL_SITE="$site"
    i=0; while [ ! -e "$run_dir/server.ready" ]; do i=$((i+1)); [ $i -gt "$HSD_WAIT" ] && exit 71; sleep 1; done
    if [ "$HSD_TLS" = 1 ]; then cp "$run_dir/server.crt" "$site/server.crt"; set -- --tls-ca "$site/server.crt"; else set -- --no-tls; fi
    t0=$(now); set +e
    hs client -H "$HSD_HOST" -p "$HSD_PORT" -N "$HSD_THREADS" -b "$HSD_CLIENT_BUNDLESIZE" \
        -w "$HSD_CLIENT_BUNDLEWAIT" -T "$HSD_IDLE" "$@" > client.log 2>&1
    rc=$?; set -e; t1=$(now)
    printf '{"label": "%s", "run_id": "%s", "role": "client", "name": "%s", "rc": %s, "start": %s, "end": %s}\n' \
        "$LABEL" "$HSD_RUN_ID" "$name" "$rc" "$t0" "$t1" > "$run_dir/client-$name.json"
    [ "$rc" = 0 ] || gzip -c client.log > "$run_dir/client-$name.log.gz"
    exit "$rc" ;;
*) echo "HSD_ROLE must be server or client" >&2; exit 64 ;;
esac
