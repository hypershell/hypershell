#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for update-group-and-legacy-filters: runs the commands a user runs, in a throwaway site,
# and checks what `hs update` refuses and writes. Run from the worktree root, one mode at a time:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/update-group-and-legacy-filters/drive/ugl_drive.sh" MODE
#
# temp_site.sh makes the site the working directory and removes it on exit. Nothing here starts a
# server. Exits 0 when every check of the mode holds, else 1 at the first that does not (its output
# says which).
#
#   group     group=x, group=1e3 and group=none, and ID group 2147483648, exit 2 with nothing counted
#             or written; group=07 stores 7, and hs list -g 7 finds the task (R1, R2)
#   legacy    ID args 'echo changed' with -g, -w, --limit and -F exits 2 naming them and writes
#             nothing; the same with -f only writes; the filtered form with -w "id == ID" -F writes
#             nothing on a task that never ran, and with -R writes it (R3, R4)

set -u
mode=${1:?usage: ugl_drive.sh group|legacy}

fail() { echo "FAIL [$mode] $*"; exit 1; }
pass() { echo "ok   [$mode] $*"; }
count() { hs list -c "$@" 2>/dev/null; }
field() { hs list "$1" -t "$2" 2>/dev/null; }

printf 'echo 0  # HYPERSHELL: n:0\necho 1  # HYPERSHELL: n:1\necho 2  # HYPERSHELL: n:2\n' > t.in
hs submit t.in 2>/dev/null || fail 'submit'
[ "$(count -g 0)" = 3 ] || fail 'three tasks in group 0 after submit'

case $mode in
group)
    for value in x 1e3 none; do
        hs update "group=$value" -t n:1 -f 2> err.txt; status=$?
        [ "$status" -eq 2 ] || fail "group=$value exited $status, not 2: $(cat err.txt)"
        grep "group" err.txt | grep -q -- "$value" || fail "group=$value: message does not name it: $(cat err.txt)"
        grep -q 'Searching database' err.txt && fail "group=$value counted before refusing: $(cat err.txt)"
        grep -q 'Updated' err.txt && fail "group=$value wrote: $(cat err.txt)"
        [ "$(count -g 0)" = 3 ] || fail "group=$value moved a task"
    done
    pass 'group=x, group=1e3 and group=none exit 2, nothing counted or written'
    id=$(field id n:1)
    hs update "$id" group 2147483648 2> err.txt; status=$?
    [ "$status" -eq 2 ] || fail "ID group 2147483648 exited $status, not 2: $(cat err.txt)"
    grep -q '2147483648' err.txt || fail "message does not name the value: $(cat err.txt)"
    [ "$(field group n:1)" = 0 ] || fail 'ID group 2147483648 wrote the group'
    pass 'hs update ID group 2147483648 exits 2, nothing written'
    hs update group=07 -t n:1 -f 2> err.txt || fail "group=07 exited $?: $(cat err.txt)"
    [ "$(hs list id -g 7 2>/dev/null)" = "$id" ] || fail 'hs list -g 7 does not find the task given group=07'
    [ "$(count -g 0)" = 2 ] || fail 'group=07 moved more than the task asked for'
    pass 'group=07 stores 7, and hs list -g 7 finds it'
    ;;
legacy)
    id=$(field id n:0)
    hs update "$id" args 'echo changed' -g 99 -w 'exit_status == 7' --limit 1 -F 2> err.txt; status=$?
    [ "$status" -eq 2 ] || fail "single-task form with selection options exited $status, not 2: $(cat err.txt)"
    for name in -g/--group -w/--where -l/--limit -F/--failed; do
        grep -q -- "$name" err.txt || fail "message does not name $name: $(cat err.txt)"
    done
    grep -qF -- '-w "id == ID"' err.txt || fail "message does not show the filtered form: $(cat err.txt)"
    [ "$(field args n:0)" = 'echo 0' ] || fail 'single-task form with selection options wrote the task'
    pass "hs update ID args 'echo changed' -g 99 -w 'exit_status == 7' --limit 1 -F exits 2, nothing written"
    hs update "$id" args 'echo changed' -f 2> err.txt || fail "single-task form with -f exited $?: $(cat err.txt)"
    [ "$(field args n:0)" = 'echo changed' ] || fail 'single-task form with -f did not write'
    pass "hs update ID args 'echo changed' -f writes"
    id=$(field id n:1)
    hs update args='echo guarded' -w "id == $id" -F -f 2> err.txt || fail "guarded write exited $?: $(cat err.txt)"
    [ "$(field args n:1)" = 'echo 1' ] || fail 'filtered form with -F wrote a task that never ran'
    hs update args='echo guarded' -w "id == $id" -R -f 2> err.txt || fail "guarded write exited $?: $(cat err.txt)"
    [ "$(field args n:1)" = 'echo guarded' ] || fail 'filtered form with -R did not write the task'
    [ "$(field args n:2)" = 'echo 2' ] || fail 'filtered form with -w "id == ID" wrote another task'
    pass 'hs update FIELD=VALUE -w "id == ID" writes the task only when it matches'
    ;;
*)
    fail "unknown mode (group or legacy)"
    ;;
esac
