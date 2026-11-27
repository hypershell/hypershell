#!/bin/sh
# Q1: reproduce the GOAL's two non-contention defects on SQLite, plus the
# two-name-only-tag filter check (Q4a). Run from the worktree root:
#   UV_CACHE_DIR=... .agents/factory/bin/temp_site.sh sh <abs path to this file>
set -u
hs() { uv run hs "$@"; }
fresh() {
  hs update -f --delete >/dev/null 2>&1
  for i in 1 2 3 4; do hs submit -g 0 "echo $i" >/dev/null; done
}
echo '--- A: hs update --limit 2 -f group=7 color:red'
fresh
hs update --limit 2 -f group=7 color:red 2>&1
hs list id group tag --format plain 2>&1 | cut -c1-8,37-
echo '--- B: hs update -f group=7 color:blue -w group==0'
fresh
hs update -f group=7 color:blue -w group==0 2>&1
hs list group tag --format plain 2>&1
echo '--- C: hs update -f -R --cancel color:green'
fresh
hs update -f -R --cancel color:green 2>&1
hs list exit_status tag --format plain 2>&1
echo '--- D (Q4a): two name-only tags; row1 {a,b}, row2 {a}, row3 {b}'
fresh
ids=$(hs list id --format plain)
set -- $ids
hs update "$1" tag a: ; hs update "$1" tag b:
hs update "$2" tag a:
hs update "$3" tag b:
hs list id tag --format plain | cut -c1-8,37-
echo '--- hs list -t a b --count'; hs list -t a b --count 2>&1
echo '--- hs list -t b a --count'; hs list -t b a --count 2>&1
echo '--- hs list -t a b (rows)'; hs list id tag -t a b --format plain 2>&1 | cut -c1-8,37-
