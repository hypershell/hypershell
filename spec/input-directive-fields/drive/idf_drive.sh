#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for input-directive-fields: runs the commands a user runs, in a throwaway site, and checks
# what task input stores and refuses. Run from the worktree root, one mode at a time:
#
#   sh spec/input-directive-fields/drive/idf_drive.sh MODE
#
# The script re-runs itself under .agents/factory/bin/temp_site.sh, which makes a fresh site the working
# directory and removes it on exit. Every server binds 127.0.0.1 on a free port with --no-tls; `hs cluster`
# makes its own key; nothing here passes a key on a command line. Exits 0 when every check of the mode
# holds, else 1 at the first that does not (its output says which).
#
#   fields  a task file with zone:gpu, zone=cpu, group=2, cores=2, memory=1G, timeout=1:30:00 (both
#           separators), grop:2 and exit_status:0 (tags) is stored with those columns, the zones in the
#           zone column and no zone tag; grop=2, exit_status=0 and `naked other:a/b` in a file are
#           refused whole (exit 5, naming line 2, nothing stored); the same in -t exit 2, as does
#           -t cores=none and `hs run -t grop=2`; a stream into hs cluster refuses that line alone and runs
#           the rest (exit 0)
#   zones   a file's zone:gpu, a tag-only zone=cpu, zone:default and -t zone=tagged store those zones,
#           registered, with no zone tag, over --zone; a --from-json record's string zone wins over
#           --zone and a record's zone 4 is refused (exit 5, record 1, nothing stored); a stream into
#           hs server stores and registers its zones; hs cluster --zone served warns once for each
#           zone of its input it does not serve and runs the rest. Each check has its own database.
#   nodb    with no database a zone in input is refused: hs submit --queue (file and arguments exit 5,
#           -t exit 2, a JSON record exit 5), hs cluster --no-db (file, JSON, and a stream refusing
#           line 2 alone), hs server --no-db (file exit 5; a stream refusing line 2 alone while a
#           client runs the rest) and hs cluster with no database configured (file exit 5).

set -u
mode=${1:?usage: idf_drive.sh fields|zones|nodb}

if [ -z "${IDF_DRIVE_SITE:-}" ]; then
    IDF_DRIVE_SITE=1
    export IDF_DRIVE_SITE
    exec sh .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/input-directive-fields/drive/idf_drive.sh" "$mode"
fi

fail() { echo "FAIL [$mode] $*"; exit 1; }
pass() { echo "ok   [$mode] $*"; }

HYPERSHELL_CONFIG_FILE=$PWD/config.toml
export HYPERSHELL_CONFIG_FILE
: > config.toml

free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# stored SQL: the rows SQL selects from the site's database, one per line, columns joined by '|'.
stored() {
    python3 - "$1" <<'EOF'
import os, sys, sqlite3
with sqlite3.connect(os.environ['HYPERSHELL_DATABASE_FILE']) as db:
    for row in db.execute(sys.argv[1]):
        print('|'.join('' if value is None else str(value) for value in row))
EOF
}

# count N: the site's database holds N tasks.
count() {
    seen=$(stored 'SELECT count(*) FROM task')
    [ "$seen" = "$1" ] || fail "the database holds ${seen:-?} tasks, not $1"
}

# zone_of NAME: the id the zone NAME is stored by.
zone_of() { python3 -c 'import sys, uuid; print(uuid.uuid5(uuid.UUID("6f1c9b4e-2a57-5d0e-9b3a-7e5f0c8d2a41"), sys.argv[1]))' "$1"; }

# refused STATUS COMMAND...: the command exits STATUS with no traceback and nothing more stored; err.txt holds its stderr.
refused() {
    want=$1; shift
    before=$(stored 'SELECT count(*) FROM task')
    "$@" > out.txt 2> err.txt < /dev/null; status=$?
    [ "$status" -eq "$want" ] || fail "$* exited $status, not $want: $(tail -n 3 err.txt)"
    grep -q Traceback err.txt && fail "$*: traceback: $(tail -n 3 err.txt)"
    count "$before"
}

# says WORD...: every WORD is on one line of err.txt.
says() {
    line=$(cat err.txt)
    for word in "$@"; do
        line=$(printf '%s\n' "$line" | grep -F -e "$word")
    done
    [ -n "$line" ] || fail "no line of stderr holds every one of: $* -- $(tail -n 3 err.txt)"
}

case $mode in
fields)
    hs initdb --yes > /dev/null 2>&1 || fail 'hs initdb failed'
    cat > fields.in <<'EOF'
echo a # HYPERSHELL: zone:gpu group:2 cores=2 memory=1G timeout=1:30:00 color:red
echo b # HYPERSHELL: zone=cpu group=3 cores:4.0 grop:2 exit_status:0
echo c
EOF
    hs submit fields.in > out.txt 2> err.txt < /dev/null || fail "hs submit fields.in exited $?: $(tail -n 3 err.txt)"
    count 3
    got=$(stored 'SELECT args, "group", cores, memory, timeout, zone, exit_status FROM task ORDER BY args')
    want=$(printf '%s\n' "echo a|2|2|1073741824|5400|$(zone_of gpu)|" "echo b|3|4|||$(zone_of cpu)|" "echo c|0|||||")
    [ "$got" = "$want" ] || fail "stored columns:
$got
not:
$want"
    pass 'zone:gpu, zone=cpu, group=, cores=, memory=, timeout= and cores:4.0 set the columns'
    tags=$(stored 'SELECT args, tag FROM task ORDER BY args')
    case $tags in *zone*) fail "a zone tag is stored: $tags" ;; esac
    printf '%s\n' "$tags" | grep -q '^echo a|{"color": *"red"}$' || fail "echo a tags: $tags"
    printf '%s\n' "$tags" | grep -q '^echo b|{"grop": *2, *"exit_status": *0}$' || fail "echo b tags: $tags"
    pass 'no zone tag; color:red, grop:2 and exit_status:0 are tags'

    printf 'echo x\necho y # HYPERSHELL: grop=2\n' > near.in
    refused 5 hs submit near.in
    says 'grop: not a task column' 'did you mean group?' 'grop:VALUE' 'line 2'
    pass 'grop=2 in a file: exit 5 naming group, grop:VALUE and line 2; nothing stored'
    printf 'echo x\necho y # HYPERSHELL: exit_status=0\n' > column.in
    refused 5 hs submit column.in
    says 'exit_status' 'not set from task input' 'exit_status:VALUE' 'line 2'
    pass 'exit_status=0 in a file: exit 5, not set from task input; nothing stored'
    printf 'echo x\necho y # HYPERSHELL: naked other:a/b\n' > naked.in
    refused 5 hs submit naked.in
    says 'Tag value must only contain' 'a/b' 'line 2'
    pass 'naked other:a/b in a file: exit 5 naming a/b and line 2; nothing stored'

    refused 2 hs submit -t grop=2 -- echo x
    says 'did you mean group?' 'grop:VALUE'
    refused 2 hs submit -t exit_status=0 -- echo x
    says 'not set from task input' 'exit_status:VALUE'
    refused 2 hs submit -t naked other:a/b -- echo x
    says 'Tag value must only contain' 'a/b'
    refused 2 hs submit -c 1 -t cores=none -- echo x
    says 'task.cores' 'none'
    pass '-t grop=2, exit_status=0, naked other:a/b and cores=none: exit 2; nothing stored'
    refused 2 hs run -t grop=2 -- echo x
    says 'did you mean group?'
    pass 'hs run -t grop=2: exit 2 before the database is opened; nothing stored'

    # The stream runs on a database of its own: the zoned tasks above wait for clients of their
    # zones, which this cluster's clients do not serve.
    HYPERSHELL_DATABASE_FILE=$PWD/stream.db
    export HYPERSHELL_DATABASE_FILE
    printf 'echo s1\necho s2 # HYPERSHELL: grop=2\necho s3 # HYPERSHELL: group=4\n' |
        hs cluster -N 1 --no-tls -H 127.0.0.1 -p "$(free_port)" > out.txt 2> err.txt
    status=$?
    [ "$status" -eq 0 ] || fail "hs cluster on a stream exited $status: $(tail -n 3 err.txt)"
    says 'Refused line 2' 'did you mean group?'
    count 2
    got=$(stored "SELECT args, \"group\", exit_status FROM task ORDER BY args")
    [ "$got" = "$(printf '%s\n' 'echo s1|0|0' 'echo s3|4|0')" ] || fail "stream tasks: $got"
    pass 'a stream into hs cluster refuses line 2 alone and runs s1 and s3 (group=4), exit 0'
    ;;
zones)
    # use_db NAME: a fresh database of its own for the next check.
    use_db() {
        HYPERSHELL_DATABASE_FILE=$PWD/$1.db
        export HYPERSHELL_DATABASE_FILE
        hs initdb --yes > /dev/null 2>&1 || fail "hs initdb ($1) failed"
    }
    # registered NAME...: every NAME is in the zone table.
    registered() {
        names=$(stored 'SELECT name FROM zone')
        for name in "$@"; do
            printf '%s\n' "$names" | grep -qx -e "$name" || fail "zone $name is not registered: $names"
        done
    }
    no_zone_tag() {
        tags=$(stored 'SELECT tag FROM task')
        case $tags in *zone*) fail "a zone tag is stored: $tags" ;; esac
    }
    # wait_for SQL WANT: wait (at most 60 s) until SQL selects WANT.
    wait_for() {
        tries=0
        until [ "$(stored "$1" 2> /dev/null)" = "$2" ]; do
            tries=$((tries + 1))
            [ "$tries" -le 240 ] || return 1
            sleep 0.25
        done
    }

    use_db file
    cat > zones.in <<'EOF'
echo a # HYPERSHELL: zone:gpu
# HYPERSHELL: zone=cpu
echo b
echo c # HYPERSHELL: zone:default
EOF
    hs submit zones.in --zone opt > out.txt 2> err.txt < /dev/null || fail "hs submit zones.in exited $?: $(tail -n 3 err.txt)"
    hs submit --zone opt -t zone=tagged -- echo d > out.txt 2> err.txt < /dev/null || fail "hs submit -t exited $?: $(tail -n 3 err.txt)"
    hs submit --zone opt -- echo e > out.txt 2> err.txt < /dev/null || fail "hs submit --zone exited $?: $(tail -n 3 err.txt)"
    count 5
    got=$(stored 'SELECT args, zone FROM task ORDER BY args')
    want=$(printf '%s\n' "echo a|$(zone_of gpu)" "echo b|$(zone_of cpu)" "echo c|" "echo d|$(zone_of tagged)" "echo e|$(zone_of opt)")
    [ "$got" = "$want" ] || fail "stored zones:
$got
not:
$want"
    registered gpu cpu tagged opt
    no_zone_tag
    pass 'zone:gpu, a tag-only zone=cpu, zone:default and -t zone=tagged win over --zone, registered, no zone tag'

    use_db json
    printf '[{"args": "echo j1", "zone": "record"}, {"args": "echo j2"}]\n' > zones.json
    hs submit --from-json zones.json --zone opt > out.txt 2> err.txt < /dev/null || fail "hs submit --from-json exited $?: $(tail -n 3 err.txt)"
    got=$(stored 'SELECT args, zone FROM task ORDER BY args')
    [ "$got" = "$(printf '%s\n' "echo j1|$(zone_of record)" "echo j2|$(zone_of opt)")" ] || fail "JSON zones: $got"
    registered record opt
    no_zone_tag
    printf '[{"args": "echo k1"}, {"args": "echo k2", "zone": 4}]\n' > bad.json
    refused 5 hs submit --from-json bad.json
    says 'zone' '4' 'record 1'
    pass 'a record zone wins over --zone, registered; a record zone 4 exits 5 naming record 1; nothing stored'

    use_db server
    HYPERSHELL_SERVER_AUTH=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
    export HYPERSHELL_SERVER_AUTH
    printf 'echo s1 # HYPERSHELL: zone:gpu\necho s2 # HYPERSHELL: zone=cpu\necho s3\n' > stream.in
    hs server -b 1 -w 1 --no-tls -H 127.0.0.1 -p "$(free_port)" < stream.in > out.txt 2> err.txt &
    server=$!
    wait_for 'SELECT count(*) FROM task' 3
    found=$?
    kill "$server" 2> /dev/null
    wait "$server" 2> /dev/null
    [ "$found" -eq 0 ] || fail "hs server stored $(stored 'SELECT count(*) FROM task') of 3 tasks from its stream: $(tail -n 3 err.txt)"
    got=$(stored 'SELECT args, zone FROM task ORDER BY args')
    [ "$got" = "$(printf '%s\n' "echo s1|$(zone_of gpu)" "echo s2|$(zone_of cpu)" 'echo s3|')" ] || fail "stream zones: $got"
    registered gpu cpu
    no_zone_tag
    pass 'a stream into hs server stores and registers zone:gpu and zone=cpu'

    use_db cluster
    cat > cluster.in <<'EOF'
echo a
echo b # HYPERSHELL: zone:served
echo c # HYPERSHELL: zone:far-gpu
echo d # HYPERSHELL: zone:far-gpu
echo e # HYPERSHELL: zone=far-cpu
EOF
    hs cluster cluster.in --zone served -N 1 --no-tls -H 127.0.0.1 -p "$(free_port)" > out.txt 2> err.txt < /dev/null &
    cluster=$!
    wait_for "SELECT count(*) FROM task WHERE exit_status = 0" 2
    found=$?
    kill "$cluster" 2> /dev/null
    wait "$cluster" 2> /dev/null
    [ "$found" -eq 0 ] || fail "hs cluster did not run echo a and echo b: $(tail -n 3 err.txt)"
    got=$(stored "SELECT args FROM task WHERE exit_status = 0 ORDER BY args")
    [ "$got" = "$(printf '%s\n' 'echo a' 'echo b')" ] || fail "tasks run: $got"
    [ "$(grep WARNING err.txt | grep -c far-gpu)" -eq 1 ] || fail "not one WARNING for far-gpu: $(grep WARNING err.txt)"
    [ "$(grep WARNING err.txt | grep -c far-cpu)" -eq 1 ] || fail "not one WARNING for far-cpu: $(grep WARNING err.txt)"
    grep WARNING err.txt | grep served | grep -qv far- && fail "a WARNING for the served zone: $(grep WARNING err.txt)"
    registered served far-gpu far-cpu
    pass 'hs cluster --zone served warns once for far-gpu and once for far-cpu and runs echo a and echo b'
    ;;
nodb)
    hs initdb --yes > /dev/null 2>&1 || fail 'hs initdb failed'
    HYPERSHELL_SERVER_AUTH=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
    export HYPERSHELL_SERVER_AUTH
    printf 'echo a\necho b # HYPERSHELL: zone:gpu\necho c\n' > zoned.in
    printf '[{"args": "echo a"}, {"args": "echo b", "zone": "gpu"}]\n' > zoned.json
    port=$(free_port)

    refused 5 hs submit --queue --no-tls -H 127.0.0.1 -p "$port" zoned.in
    says 'gpu' 'line 2'
    refused 5 hs submit --queue --no-tls -H 127.0.0.1 -p "$port" -- echo b '#' HYPERSHELL: zone=gpu
    says 'gpu' 'arguments'
    refused 2 hs submit --queue --no-tls -H 127.0.0.1 -p "$port" -t zone:gpu -- echo b
    says 'gpu' 'database'
    refused 5 hs submit --queue --no-tls -H 127.0.0.1 -p "$port" --from-json zoned.json
    says 'gpu' 'record 1'
    grep -q Connection err.txt && fail "hs submit --queue connected before refusing: $(tail -n 3 err.txt)"
    pass 'hs submit --queue: a zone in a file, the arguments or a record exits 5, in -t exits 2'

    refused 5 hs cluster zoned.in --no-db -N 1 --no-tls -H 127.0.0.1 -p "$(free_port)"
    says 'gpu' 'line 2'
    [ -s out.txt ] && fail "hs cluster --no-db ran tasks: $(cat out.txt)"
    refused 5 hs cluster --from-json zoned.json --no-db -N 1 --no-tls -H 127.0.0.1 -p "$(free_port)"
    says 'gpu' 'record 1'
    [ -s out.txt ] && fail "hs cluster --no-db --from-json ran tasks: $(cat out.txt)"
    hs cluster --no-db -N 1 --no-tls -H 127.0.0.1 -p "$(free_port)" < zoned.in > out.txt 2> err.txt
    status=$?
    [ "$status" -eq 0 ] || fail "hs cluster --no-db on a stream exited $status: $(tail -n 3 err.txt)"
    says 'Refused line 2' 'gpu'
    [ "$(sort out.txt | tr '\n' ' ')" = 'a c ' ] || fail "hs cluster --no-db stream ran: $(cat out.txt)"
    pass 'hs cluster --no-db: a file or record naming a zone exits 5; a stream refuses line 2 and runs a and c'

    refused 5 hs server zoned.in --no-db --no-tls -H 127.0.0.1 -p "$(free_port)"
    says 'gpu' 'line 2'
    port=$(free_port)
    hs server --no-db --no-tls -H 127.0.0.1 -p "$port" < zoned.in > server.txt 2>&1 &
    server=$!
    tries=0
    until grep -q 'Server listening' server.txt; do
        tries=$((tries + 1))
        [ "$tries" -le 120 ] || { kill "$server" 2> /dev/null; wait "$server" 2> /dev/null; fail "hs server --no-db never listened: $(tail -n 3 server.txt)"; }
        sleep 0.25
    done
    hs client -N 1 -T 5 --no-tls -H 127.0.0.1 -p "$port" > out.txt 2> err.txt < /dev/null
    status=$?
    kill "$server" 2> /dev/null
    wait "$server" 2> /dev/null
    [ "$status" -eq 0 ] || fail "hs client exited $status: $(tail -n 3 err.txt)"
    [ "$(grep -c 'Refused line 2' server.txt)" -eq 1 ] || fail "hs server --no-db did not refuse line 2 once: $(tail -n 3 server.txt)"
    grep 'Refused line 2' server.txt | grep -q gpu || fail "the refusal does not name gpu: $(grep 'Refused line' server.txt)"
    [ "$(sort out.txt | tr '\n' ' ')" = 'a c ' ] || fail "the client ran: $(cat out.txt)"
    pass 'hs server --no-db: a file naming a zone exits 5; a stream refuses line 2 and serves a and c'

    count 0
    HYPERSHELL_DATABASE_FILE=:memory: hs cluster zoned.in -N 1 --no-tls -H 127.0.0.1 -p "$(free_port)" > out.txt 2> err.txt < /dev/null
    status=$?
    [ "$status" -eq 5 ] || fail "hs cluster with no database configured exited $status, not 5: $(tail -n 3 err.txt)"
    says 'gpu' 'line 2'
    [ -s out.txt ] && fail "hs cluster with no database ran tasks: $(cat out.txt)"
    pass 'hs cluster with no database configured: a file naming a zone exits 5'
    ;;
*)
    fail "unknown mode"
    ;;
esac
