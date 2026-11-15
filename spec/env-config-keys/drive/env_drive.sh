#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for env-config-keys: sets HYPERSHELL_* variables (and one configuration file) and checks
# what `hs` does with them. Run it inside a throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/env-config-keys/drive/env_drive.sh" MODE
#
# MODE is one of:
#   keys       underscore keys, open tables, zone tables, `_ENV`/`_EVAL`, text kept (hs config get)
#   typed      typed keys convert or are refused naming the variable; process variables are not keys
#   durations  the duration keys are refused at start naming the key and variable; `hs list` unaffected
#   serve      an all-digit key, `server.evict = "10m"` and `client.heartrate=5s` in one real run
#   export     `HYPERSHELL_EXPORT_*` reaches a task by name and value as written
#   which      `hs config which` and completion name keys and variables by the loader's rule
#
# Each check prints one line; the first failure prints FAIL with what was seen and exits 1. The
# working directory is the site (temp_site.sh); every process started here is waited on or stopped.

set -u
set -f  # Values such as a[0-3] are passed as written, never globbed.
mode=${1:?usage: env_drive.sh MODE}
site=$(pwd)
export HYPERSHELL_CONFIG_FILE=
unset HYPERSHELL_SERVER_AUTH 2>/dev/null || true

fail() { echo "FAIL $mode: $*"; exit 1; }
ok() { echo "ok   $mode: $*"; }

# get VAR=VALUE... -- KEY: print `hs config get -r KEY` under the given variables.
get() {
    vars=
    while [ "$1" != -- ]; do vars="$vars $1"; shift; done
    shift
    env $vars hs config get -r "$1" 2>&1
}

# expect_get WANT VAR=VALUE... -- KEY
expect_get() {
    want=$1; shift
    seen=$(get "$@")
    [ "$seen" = "$want" ] || fail "$* gave '$seen', not '$want'"
    ok "$* -> $want"
}

# refused STATUS PATTERN VAR=VALUE... -- COMMAND...: the command exits STATUS and stderr matches PATTERN.
refused() {
    status=$1; pattern=$2; shift 2
    vars=
    while [ "$1" != -- ]; do vars="$vars $1"; shift; done
    shift
    env $vars "$@" > "$site/out.txt" 2> "$site/err.txt" </dev/null
    rc=$?
    [ "$rc" -eq "$status" ] || fail "$vars $* exited $rc, not $status: $(head -c 400 "$site/err.txt")"
    grep -q -e "$pattern" "$site/err.txt" || fail "$vars $*: stderr lacks '$pattern': $(head -c 400 "$site/err.txt")"
    ok "$vars $* -> exit $rc"
}

# refused_timed STATUS PATTERN VAR=VALUE... -- COMMAND...: as `refused`, for a command that, without the
# fix, would keep running (hs relay); it is stopped after 60 s, which fails the check.
refused_timed() {
    status=$1; pattern=$2; shift 2
    vars=
    while [ "$1" != -- ]; do vars="$vars $1"; shift; done
    shift
    env $vars "$@" > "$site/out.txt" 2> "$site/err.txt" </dev/null &
    pid=$!
    i=0
    while kill -0 "$pid" 2>/dev/null; do
        i=$((i + 1))
        if [ "$i" -ge 600 ]; then
            kill "$pid" 2>/dev/null; wait "$pid"
            fail "$vars $* still running after 60 s: $(head -c 400 "$site/err.txt")"
        fi
        sleep 0.1
    done
    wait "$pid"; rc=$?
    [ "$rc" -eq "$status" ] || fail "$vars $* exited $rc, not $status: $(head -c 400 "$site/err.txt")"
    grep -q -e "$pattern" "$site/err.txt" || fail "$vars $*: stderr lacks '$pattern': $(head -c 400 "$site/err.txt")"
    ok "$vars $* -> exit $rc"
}

port() { python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }

case $mode in
keys)
    expect_get TLSv1.3 HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3 -- server.tls.min_version
    expect_get TLSv1.3 HYPERSHELL_server_tls_min_version=TLSv1.3 -- server.tls.min_version
    expect_get '--mem=4G' HYPERSHELL_RELAY_LAUNCHER_ARGS=--mem=4G -- relay.launcher_args
    expect_get srun HYPERSHELL_RELAY_LAUNCHER=srun HYPERSHELL_RELAY_LAUNCHER_ARGS=--mem=4G -- relay.launcher
    expect_get 5 HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=5 -- autoscale.failed_launches
    expect_get WAL HYPERSHELL_DATABASE_PRAGMAS_JOURNAL_MODE=WAL -- database.pragmas.journal_mode
    expect_get 'a[0-3]' 'HYPERSHELL_SSH_NODELIST_MY_GROUP=a[0-3]' -- ssh.nodelist.my_group
    expect_get '--gpus=1' HYPERSHELL_RELAY_ZONE_GPU_A100_LAUNCHER_ARGS=--gpus=1 -- relay.zone.gpu_a100.launcher_args
    expect_get require HYPERSHELL_DATABASE_SSL_MODE=require -- database.ssl_mode
    expect_get 1 HYPERSHELL_SERVER_PORT_X=1 -- server.port_x
    expect_get 0012345678901234 MYKEY=0012345678901234 HYPERSHELL_SERVER_AUTH_ENV=MYKEY -- server.auth
    printf '#!/bin/sh\necho 0012345678901234\n' > "$site/key.sh"; chmod +x "$site/key.sh"
    expect_get 0012345678901234 HYPERSHELL_RELAY_UPSTREAM_AUTH_EVAL="$site/key.sh" -- relay.upstream.auth
    expect_get 0012345678901234 HYPERSHELL_SERVER_AUTH=0012345678901234 -- server.auth
    refused 3 'more than one form\|More than one form' HYPERSHELL_SERVER_AUTH=0012345678901234 HYPERSHELL_SERVER_AUTH_ENV=MYKEY -- hs config get server.auth
    refused 3 HYPERSHELL_RELAY_ZONE_GPU HYPERSHELL_RELAY_ZONE_GPU=x -- hs config get relay.zone
    refused 3 HYPERSHELL_RELAY_ZONE_GPU_SIZE HYPERSHELL_RELAY_ZONE=cpu HYPERSHELL_RELAY_ZONE_GPU_SIZE=2 -- hs config get relay.zone
    ;;
typed)
    refused 3 HYPERSHELL_SERVER_PORT HYPERSHELL_SERVER_PORT=abc -- hs list --count
    refused 3 HYPERSHELL_SERVER_EAGER HYPERSHELL_SERVER_EAGER=maybe -- hs config get server.eager
    refused 3 HYPERSHELL_RELAY_MAX HYPERSHELL_RELAY_MAX=1.0 -- hs config get relay.max
    refused 3 HYPERSHELL_LOGGING_FILE HYPERSHELL_LOGGING_FILE=false -- hs list --count
    expect_get 16G HYPERSHELL_TASK_MEMORY=16G -- task.memory
    expect_get 4 HYPERSHELL_RELAY_MAX=4 -- relay.max
    expect_get '<none>' 'HYPERSHELL_RELAY_MAX=<none>' -- relay.max
    expect_get 50123 HYPERSHELL_SERVER_PORT=50123 -- server.port
    out=$(env HYPERSHELL_SERVER_EAGER=no HYPERSHELL_LOGGING_FILE=Enabled HYPERSHELL_AUTOSCALE_LAUNCHER=false \
          HYPERSHELL_LOGGING_DATEFMT= HYPERSHELL_ZONE=gpu HYPERSHELL_PYTHONPATH="$site" \
          python -c 'from hypershell.core.config import config as c; print(repr((c.server.eager, c.logging.file, c.autoscale.launcher, c.logging.datefmt, "zone" in c, "pythonpath" in c, "site" in c, "config" in c)))')
    [ "$out" = "(False, True, 'false', '', False, False, False, False)" ] || fail "typed values: $out"
    ok "typed values and process variables: $out"
    ;;
durations)
    refused 3 'HYPERSHELL_SERVER_EVICT' HYPERSHELL_SERVER_EVICT=10x HYPERSHELL_SERVER_AUTH=abcdefghijklmnop1234 -- hs server --no-db
    refused 3 'HYPERSHELL_CLIENT_HEARTRATE' HYPERSHELL_CLIENT_HEARTRATE=10x HYPERSHELL_SERVER_AUTH=abcdefghijklmnop1234 -- hs client
    refused_timed 3 'HYPERSHELL_RELAY_UPSTREAM_DEADLINE' HYPERSHELL_RELAY_UPSTREAM_DEADLINE=10x -- hs relay
    refused_timed 3 'HYPERSHELL_RELAY_POLL' HYPERSHELL_RELAY_POLL=5MB -- hs relay
    printf '[relay]\ntimeout = "5MB"\n' > "$site/bad.toml"
    refused_timed 3 'relay.timeout' HYPERSHELL_CONFIG_FILE="$site/bad.toml" -- hs relay
    env HYPERSHELL_SERVER_EVICT=10x hs list --count > /dev/null 2>&1 || fail "hs list --count refused a duration key it does not read"
    ok "hs list --count with a bad server.evict -> exit 0"
    ;;
serve)
    key=0012345678901234
    p=$(port)
    printf '[server]\nevict = "10m"\n' > "$site/serve.toml"
    echo 'echo served' > "$site/tasks.in"
    env HYPERSHELL_CONFIG_FILE="$site/serve.toml" HYPERSHELL_SERVER_AUTH=$key HYPERSHELL_SERVER_PORT="$p" \
        hs server --no-db "$site/tasks.in" > "$site/server.log" 2>&1 &
    server=$!
    i=0
    until grep -q 'Server listening' "$site/server.log"; do
        kill -0 "$server" 2>/dev/null || { wait "$server"; fail "server exited $?: $(head -c 400 "$site/server.log")"; }
        i=$((i + 1)); [ "$i" -lt 300 ] || { kill "$server"; wait "$server"; fail "server not listening"; }
        sleep 0.1
    done
    env HYPERSHELL_SERVER_AUTH=$key HYPERSHELL_SERVER_PORT="$p" HYPERSHELL_CLIENT_HEARTRATE=5s \
        hs client -H localhost > "$site/client.out" 2> "$site/client.log"
    crc=$?
    wait "$server"; src=$?
    [ "$crc" -eq 0 ] || fail "client exited $crc: $(head -c 400 "$site/client.log")"
    [ "$src" -eq 0 ] || fail "server exited $src: $(head -c 400 "$site/server.log")"
    grep -q served "$site/client.out" || fail "task output missing: $(cat "$site/client.out")"
    ok "all-digit key, evict = \"10m\", heartrate 5s: server and client exit 0"
    ;;
export)
    echo 'printenv RUN_ID MyVar; printenv EMPTY > /dev/null && echo empty-set' > "$site/tasks.in"
    out=$(env HYPERSHELL_SERVER_PORT="$(port)" HYPERSHELL_EXPORT_RUN_ID=007 HYPERSHELL_EXPORT_MyVar=x HYPERSHELL_EXPORT_EMPTY= \
          hs cluster --no-db -N1 "$site/tasks.in" 2> "$site/cluster.log")
    want=$(printf '007\nx\nempty-set\n')
    [ "$out" = "$want" ] || fail "task saw '$out': $(head -c 400 "$site/cluster.log")"
    ok "task env RUN_ID=007, MyVar=x, EMPTY= (as written)"
    ;;
which)
    out=$(env HYPERSHELL_server_tls_min_version=TLSv1.3 hs config which server.tls.min_version 2>&1)
    case $out in *'env: HYPERSHELL_server_tls_min_version'*) ok "which -> $out" ;; *) fail "which gave: $out" ;; esac
    out=$(env HYPERSHELL_EXPORT_MyVar=x hs config which export.MyVar 2>&1)
    case $out in *'env: HYPERSHELL_EXPORT_MyVar'*) ok "which -> $out" ;; *) fail "which gave: $out" ;; esac
    list=$(env HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3 HYPERSHELL_SITE="$site" hs config get --list-available)
    for want in server.tls.min_version autoscale.failed_launches relay.launcher_args; do
        printf '%s\n' "$list" | grep -qx "$want" || fail "completion lacks $want (one per line)"
    done
    for bad in server.tls.min.version autoscale.failed.launches site config.file; do
        printf '%s\n' "$list" | grep -qx "$bad" && fail "completion offers $bad"
    done
    ok "completion offers key paths, one per line, no process variables"
    ;;
*)
    echo "env_drive: unknown mode $mode" >&2
    exit 2
    ;;
esac
