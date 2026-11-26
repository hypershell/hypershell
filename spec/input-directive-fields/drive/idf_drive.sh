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
#   zones   registration, JSON zones and the hs cluster warning (not built yet)
#   nodb    zones refused with no database (not built yet)

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
zones|nodb)
    echo "not built [$mode]"
    exit 1
    ;;
*)
    fail "unknown mode"
    ;;
esac
