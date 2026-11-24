#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for bounded-inputs: runs the commands a user runs, in a throwaway site, and checks what
# they refuse, take and warn of. Run from the worktree root, one mode at a time:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/bounded-inputs/drive/bounds_drive.sh" MODE
#
# temp_site.sh makes the site the working directory and removes it on exit. The configuration is the
# site's ./config.toml alone (HYPERSHELL_CONFIG_FILE). Every server binds 127.0.0.1 on a free port;
# `hs cluster` makes its own key; nothing here passes a key on a command line. Exits 0 when every check
# of the mode holds, else 1 at the first that does not (its output says which).
#
#   options  an option out of range exits 2 with one line naming it, before anything is stored or run
#            (-c 100000, -m 8388608TB, -l -5, -F nan, -R -5, the task alias); bounds inside run as today
#            (-c 32767 -W 2147483647 stored; -N 0, -P 0.5 run) (R1-R4, R6)
#   config   a file's or a variable's value out of range exits 3 naming the key and its source, before
#            any database (evict 5 under hs cluster, ratelimit 1e-300 under hs client, a variable's
#            queuesize 0); attempts = "3" runs three attempts; hs config get and set repair a bad file;
#            a file's attempts = 101 runs with one warning (R3-R6, R13)
#   set      hs config set refuses an out-of-range value (exit 2, file unchanged), an untyped 2**64,
#            and 8EB; writes 10m as given; warns once for attempts = 101 (R7, R13)
#   tags     a task file with cores:100000 is refused whole (exit 5, nothing stored); -t cores:100000
#            exits 2; a stream refuses only that line; tags past the advisory limit give one summary (R9, R13)
#   advise   options past their advisory limit run (exit 0) with one warning each; at the limit, none (R13)

set -u
mode=${1:?usage: bounds_drive.sh options|config|set|tags|advise}

fail() { echo "FAIL [$mode] $*"; exit 1; }
pass() { echo "ok   [$mode] $*"; }

HYPERSHELL_CONFIG_FILE=$PWD/config.toml
export HYPERSHELL_CONFIG_FILE
: > config.toml
printf 'echo one\necho two\n' > t.in

free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# refused STATUS NAME COMMAND...: the command exits STATUS with one refusal line naming NAME, no traceback.
refused() {
    want=$1; name=$2; shift 2
    "$@" > out.txt 2> err.txt < /dev/null; status=$?
    [ "$status" -eq "$want" ] || fail "$* exited $status, not $want: $(tail -n 3 err.txt)"
    [ "$(grep -c "$name: expected .* from .* to .*, given " err.txt)" -eq 1 ] || fail "$*: no one refusal of $name: $(tail -n 3 err.txt)"
    grep -q Traceback err.txt && fail "$*: traceback: $(tail -n 3 err.txt)"
    pass "$* exits $want, one line naming $name"
}

# count N: the site's database holds N tasks.
count() {
    seen=$(hs list --count 2> /dev/null)
    [ "$seen" = "$1" ] || fail "the database holds ${seen:-?} tasks, not $1"
}

# advised N COMMAND...: the command exits 0 with N advisory warnings.
advised() {
    want=$1; shift
    "$@" > out.txt 2> err.txt < /dev/null; status=$?
    [ "$status" -eq 0 ] || fail "$* exited $status: $(tail -n 3 err.txt)"
    seen=$(grep -c 'WARNING.* advised .*running anyway' err.txt)
    [ "$seen" -eq "$want" ] || fail "$* gave $seen advisory warnings, not $want: $(grep WARNING err.txt)"
    pass "$* runs, exit 0, with $want advisory warning(s)"
}

cluster() { hs cluster "$@" --no-tls -H 127.0.0.1 -p "$(free_port)"; }

case $mode in
options)
    refused 2 '-c/--cores' hs submit -c 100000 echo a
    refused 2 '-m/--memory' hs submit -m 8388608TB echo a
    refused 2 '-W/--timeout' hs submit -W 2147483648 echo a
    count 0
    pass 'nothing stored'
    refused 2 '-l/--limit' hs list --limit=-5
    refused 2 '-l/--limit' hs task search -l 0
    refused 2 '-g/--group' hs list -g 2147483648
    refused 2 '-F/--factor' cluster t.in --no-db -F nan
    [ -s out.txt ] && fail "hs cluster -F nan ran tasks: $(cat out.txt)"
    refused 2 '-R/--ratelimit' hs client --ratelimit=-5 --no-tls -H 127.0.0.1 -p "$(free_port)"
    refused 2 '-T/--timeout' hs client -T 0 --no-tls -H 127.0.0.1 -p "$(free_port)"
    hs submit -c 32767 -W 2147483647 echo b 2> err.txt || fail "in-range submit refused: $(tail -n 3 err.txt)"
    count 1
    [ "$(hs list cores timeout --csv 2> /dev/null | tail -n 1)" = '32767,2147483647' ] \
        || fail "stored: $(hs list cores timeout --csv)"
    pass 'hs submit -c 32767 -W 2147483647 stores both bounds'
    cluster t.in --no-db -N 0 -P 0.5 > out.txt 2> err.txt || fail "-N 0 -P 0.5 refused: $(tail -n 3 err.txt)"
    [ "$(sort out.txt | tr '\n' ' ')" = 'one two ' ] || fail "-N 0 ran: $(cat out.txt)"
    pass 'hs cluster -N 0 -P 0.5 runs both tasks'
    ;;
config)
    printf '[server]\nevict = 5\n' > config.toml
    refused 3 'server.evict' cluster t.in --no-db
    grep -q "(from: $PWD/config.toml)" err.txt || fail "no source: $(tail -n 1 err.txt)"
    [ -e task.db ] && fail 'the database was opened'
    printf '[client]\nratelimit = 1e-300\n' > config.toml
    refused 3 'client.ratelimit' hs client --no-tls -H 127.0.0.1 -p "$(free_port)"
    : > config.toml
    refused 3 'server.queuesize' env HYPERSHELL_SERVER_QUEUESIZE=0 hs list --count
    grep -q '(from: HYPERSHELL_SERVER_QUEUESIZE)' err.txt || fail "no source: $(tail -n 1 err.txt)"
    printf '[server]\nbundlesize = 0\n' > config.toml
    [ "$(hs config get -r server.bundlesize)" = 0 ] || fail 'hs config get cannot show the bad value'
    hs config set server.bundlesize 2 --local 2> err.txt || fail "hs config set cannot repair: $(tail -n 3 err.txt)"
    hs list --count > /dev/null 2> err.txt || fail "still refused after repair: $(tail -n 3 err.txt)"
    pass 'hs config get shows a bad file value, hs config set repairs it'
    printf '[server]\nattempts = "3"\n' > config.toml
    echo 'exit 1' > f.in
    cluster f.in > /dev/null 2> err.txt || fail "attempts = \"3\" refused: $(tail -n 3 err.txt)"
    count 3
    pass 'attempts = "3" runs a failing task three times'
    printf '[server]\nattempts = 101\n' > config.toml
    advised 1 hs list --count
    ;;
set)
    printf '[server]\nport = 54321\n' > config.toml
    cp config.toml before.toml
    refused 2 'server.bundlesize' hs config set server.bundlesize 0 --local
    refused 2 'task.memory' hs config set task.memory 8EB --local
    hs config set database.connect_args.x 18446744073709551616 --local 2> err.txt; status=$?
    [ "$status" -eq 2 ] && grep -q 'database.connect_args.x' err.txt || fail "2**64 exited $status: $(tail -n 3 err.txt)"
    pass 'an untyped 2**64 exits 2 naming the key'
    cmp -s config.toml before.toml || fail 'a refused value changed the file'
    [ -z "$(ls -a | grep '^\.config\.')" ] || fail 'a refused value made a backup'
    pass 'the file and its backups unchanged'
    hs config set server.evict 10m --local 2> err.txt || fail "10m refused: $(tail -n 3 err.txt)"
    grep -qx 'evict = "10m"' config.toml || fail "10m not written as given: $(cat config.toml)"
    pass 'server.evict 10m written as given'
    advised 1 hs config set server.attempts 101 --local
    grep -qx 'attempts = 101' config.toml || fail "101 not written: $(cat config.toml)"
    ;;
tags)
    printf 'echo a\necho b # HYPERSHELL: cores:100000\necho c\n' > g.in
    hs submit g.in > out.txt 2> err.txt; status=$?
    [ "$status" -eq 5 ] || fail "a file with cores:100000 exited $status, not 5: $(tail -n 3 err.txt)"
    grep -q 'task.cores: expected .*100000.*line 2' err.txt || fail "no refusal naming the value and line: $(tail -n 3 err.txt)"
    count 0
    pass 'a task file with cores:100000 is refused whole, exit 5, nothing stored'
    refused 2 'task.cores' hs submit echo a -t cores:100000
    cat g.in | cluster --no-db > out.txt 2> err.txt || fail "the stream's run failed: $(tail -n 3 err.txt)"
    [ "$(sort out.txt | tr '\n' ' ')" = 'a c ' ] || fail "the stream ran: $(cat out.txt)"
    grep -q 'ERROR.*line 2' err.txt || fail "no ERROR for line 2: $(grep -i error err.txt)"
    pass 'a stream refuses only line 2 and runs the rest'
    printf 'echo a # HYPERSHELL: cores:1025\necho b # HYPERSHELL: cores:2000\necho c\n' > h.in
    advised 1 hs submit h.in
    grep -q '2 tasks' err.txt && grep -q 'line 1' err.txt || fail "summary: $(grep WARNING err.txt)"
    count 3
    ;;
advise)
    advised 1 cluster t.in --no-db -F 101
    advised 1 cluster t.in --no-db -N 1025
    advised 1 cluster t.in --no-db -F 200 -F 150
    advised 0 cluster t.in --no-db -F 100 -N 1024 -r 99 -P 3600 -c 1024
    ;;
*)
    fail "no mode $mode"
    ;;
esac
