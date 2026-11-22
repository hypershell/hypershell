#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Drive the submitted-input checks through the real CLI in a throwaway site, on SQLite. Run it from the
# repository root inside a temp site, one mode per call:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh" hash
#
# Modes: hash (a `#` stays in the command), group (a non-integer group refuses a file whole; `-t` exits 2),
# stream (a stream into `hs cluster` refuses only the bad line, and `group:nan` does not hang),
# reached (`hs submit` refuses a group below the reached group; a plain submission takes the reached group).
# Every command is bounded by bounded.py; the drive prints PASS or FAIL per check and exits 0 only when
# every check passes. It needs HYPERSHELL_SITE and HYPERSHELL_DATABASE_FILE (temp_site.sh sets both).

set -u

here=$(cd "$(dirname "$0")" && pwd)
bound="python3 $here/bounded.py"
mode=${1:-}
failed=0

check() {  # check NAME CONDITION-STATUS
    if [ "$2" -eq 0 ]; then echo "PASS  $mode: $1"; else echo "FAIL  $mode: $1"; failed=1; fi
}

sql() {  # print the rows of one query on the site's SQLite database, one value per line
    python3 -c 'import sqlite3, sys; [print(*row) for row in sqlite3.connect(sys.argv[1]).execute(sys.argv[2])]' \
        "$HYPERSHELL_DATABASE_FILE" "$1"
}

args() {  # stored commands, one per line, in submit order
    sql 'SELECT args FROM task ORDER BY submit_time'
}

case $mode in
hash)
    cat > hash.in <<'EOF'
# a comment line
echo 'issue #42'
curl 'http://h/p#frag'
echo $#
echo a#b
awk '{print $1}' # trailing
echo marked # HYPERSHELL: group:1
EOF
    $bound 30 hs submit hash.in > hash.log 2>&1
    check "file submitted (exit 0)" $?
    args > hash.out
    printf '%s\n' "echo 'issue #42'" "curl 'http://h/p#frag'" 'echo $#' 'echo a#b' \
        "awk '{print \$1}' # trailing" 'echo marked' > hash.want
    cmp -s hash.out hash.want
    check "every command stored as written; the marker still cut" $?
    $bound 30 hs submit -- echo 'direct #7' > direct.log 2>&1
    check "arguments submitted (exit 0)" $?
    args | tail -1 | grep -qx 'echo "direct #7"'
    check "argument command keeps its #" $?
    ;;
group)
    printf 'echo a\necho b # HYPERSHELL: group:x\necho c\n' > group.in
    $bound 30 hs submit -b 1 group.in > group.log 2>&1
    status=$?
    test "$status" -eq 5
    check "file with group:x exits 5 (got $status)" $?
    grep -q 'line 2' group.log && grep -q '"x"' group.log
    check "error names the value and line 2" $?
    test "$(sql 'SELECT count(*) FROM task')" = 0
    check "nothing inserted" $?
    printf 'echo a\necho b # HYPERSHELL: group:nan\n' > nan.in
    $bound 30 hs submit -b 1 nan.in > nan.log 2>&1
    status=$?
    test "$status" -eq 5
    check "file with group:nan exits 5, no hang (got $status)" $?
    $bound 30 hs submit -t group:x -- echo tagged > tag.log 2>&1
    status=$?
    test "$status" -eq 2
    check "-t group:x exits 2 (got $status)" $?
    $bound 30 hs submit -t group:07 -- echo ok > ok.log 2>&1
    check "-t group:07 accepted" $?
    ;;
stream)
    for value in x nan; do
        printf 'echo a\necho b # HYPERSHELL: group:%s\necho c\n' "$value" \
            | $bound 60 hs cluster -N1 --no-tls -H 127.0.0.1 > "stream-$value.out" 2> "stream-$value.log"
        status=$?
        test "$status" -eq 0
        check "stream with group:$value exits 0 (got $status)" $?
        grep -q 'ERROR' "stream-$value.log" && grep -q 'line 2' "stream-$value.log"
        check "ERROR names line 2 for group:$value" $?
        test "$(sort "stream-$value.out" | tr '\n' ' ')" = 'a c '
        check "only lines 1 and 3 ran for group:$value" $?
    done
    ;;
reached)
    for spec in '0 echo a' '5 echo b' '5 echo c'; do
        set -- $spec
        g=$1; shift
        $bound 30 hs submit -g "$g" -- "$@" > /dev/null 2>&1
    done
    # Group 0 done, group 5 started (one in flight, one waiting): the reached group is 5.
    python3 - "$HYPERSHELL_DATABASE_FILE" <<'EOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE task SET schedule_time = submit_time, completion_time = submit_time, exit_status = 0 WHERE args = 'echo a'")
db.execute("UPDATE task SET schedule_time = submit_time WHERE args = 'echo b'")
db.commit()
EOF
    $bound 30 hs submit -g 0 -- echo late > late.log 2>&1
    status=$?
    test "$status" -eq 2
    check "group 0 below reached group 5 exits 2 (got $status)" $?
    grep -q '0' late.log && grep -q '5' late.log && ! args | grep -qx 'echo late'
    check "refusal names both groups; nothing inserted" $?
    $bound 30 hs submit -g 5 -- echo at > at.log 2>&1
    check "group 5 (the reached group) accepted" $?
    # Make that row a late lower group now running: group 1, the most recently scheduled.
    python3 - "$HYPERSHELL_DATABASE_FILE" <<'EOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE task SET schedule_time = datetime('now', '+1 hour') WHERE args = 'echo at'")
db.execute("UPDATE task SET \"group\" = 1 WHERE args = 'echo at'")
db.commit()
EOF
    $bound 30 hs submit -- echo plain > plain.log 2>&1
    check "a submission naming no group is accepted" $?
    test "$(sql "SELECT \"group\" FROM task WHERE args = 'echo plain'")" = 5
    check "it takes the reached group 5, not the late group 1" $?
    ;;
*)
    echo "usage: input_drive.sh hash|group|stream|reached" >&2
    exit 64
    ;;
esac
exit $failed
