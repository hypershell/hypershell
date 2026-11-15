#!/bin/sh
# Q2: drive the multi-key tag update through the real CLI. The caller sets the
# database env (HYPERSHELL_SITE, HYPERSHELL_DATABASE_*). For SQLite run under
# .agents/factory/bin/temp_site.sh; for postgres/timescale see 02_pg_drive.sh.
# NOTE: -t/--with-tag is nargs='+', so `hs update -f -t color:red color:blue size:big`
# parses all three as tag FILTERS (no change at all). Changes go before -t.
set -u
hs() { uv run hs "$@"; }
show() { hs list id group tag --format plain 2>&1 | cut -c1-8,37-; }
hs update -f --delete >/dev/null 2>&1
for i in 1 2 3 4; do hs submit "echo $i" >/dev/null 2>&1; done
echo '--- as written in GOAL: hs update -f -t color:red color:blue size:big'
hs update -f color:red 2>&1 | grep -v Searching
hs update -f -t color:red color:blue size:big 2>&1 | grep -v Searching
echo '--- hs update color:blue size:big -f -t color:red'
hs update color:blue size:big -f -t color:red 2>&1 | grep -v Searching
show
echo '--- reset: hs update -f color:red size:big'
hs update -f color:red size:big 2>&1 | grep -v Searching
echo '--- hs update -f --remove-tag color size -t color:red'
hs update -f --remove-tag color size -t color:red 2>&1 | grep -v Searching
show
echo '--- reset: hs update -f color:red size:big group=0'
hs update -f color:red size:big group=0 2>&1 | grep -v Searching
echo '--- hs update group=7 shape:sq -f --remove-tag size -t color:red   (field + set + remove)'
hs update group=7 shape:sq -f --remove-tag size -t color:red 2>&1 | grep -v Searching
show
echo '--- reset: hs update -f color:red size:big group=0'
hs update -f color:red size:big group=0 2>&1 | grep -v Searching
echo '--- hs update color:blue -f --remove-tag size -t color:red   (set moves rows out, then remove)'
hs update color:blue -f --remove-tag size -t color:red 2>&1 | grep -v Searching
show
echo '--- reset; hs update --limit 2 color:blue size:small -f -t color:red   (--limit path)'
hs update -f color:red size:big group=0 2>&1 | grep -v Searching
hs update --limit 2 color:blue size:small -f -t color:red 2>&1 | grep -v Searching
show
