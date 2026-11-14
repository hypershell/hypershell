#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Launcher shim for the relay-hardening drive (built from the relay-retire drive's shim, which stays
# as that cycle's record), given as a zone's launcher:
#
#   launcher = "sh hardening_shim.sh LOG LABEL WORD HOLD"
#
# It records one line per launch in LOG, then runs the site client command it was handed:
#
#   launch t=EPOCH site=LABEL pid=PID word=WORD n=N env=ENV client=ZONE
#
#   EPOCH  seconds since the epoch when the launch was made
#   LABEL  its second argument, the relay's launcher label
#   WORD   its third argument, the zone the launcher was configured for
#   N      the launch's number among this WORD's launches, from 1
#   ENV    $HYPERSHELL_ZONE as the launch set it ('-' when unset)
#   ZONE   the value of the client's --zone ('-' when the client has none)
#
# HOLD delays a client: the first HOLD launches of WORD run at once, and every later one waits until
# the file `release-WORD` exists beside LOG (at most 600 s), logging `hold t=EPOCH word=WORD n=N`
# when it starts waiting and `release t=EPOCH word=WORD n=N` when it goes on. A negative HOLD never
# holds. While it waits the client has not connected, so the relay counts no site client for it, as
# for a fleet still launching or a client between its exit and its relaunch.
#
# The relay advertises its host name, which a sandbox may not resolve, so the client's -H is
# rewritten to loopback. The shim execs the client, so PID is the site client's process id.
log=$1 label=$2 word=$3 hold=$4
shift 4
client=- prev=
for arg do
    shift
    case $prev in
        -H) arg=127.0.0.1 ;;
        --zone) client=$arg ;;
    esac
    set -- "$@" "$arg"
    prev=$arg
done
dir=$(dirname "$log")
echo "$$" >> "$dir/launches-$word.count"
n=$(wc -l < "$dir/launches-$word.count" | tr -d ' ')
printf 'launch t=%s site=%s pid=%s word=%s n=%s env=%s client=%s\n' \
    "$(date +%s)" "$label" "$$" "$word" "$n" "${HYPERSHELL_ZONE:--}" "$client" >> "$log"
if [ "$hold" -ge 0 ] && [ "$n" -gt "$hold" ]; then
    printf 'hold t=%s word=%s n=%s\n' "$(date +%s)" "$word" "$n" >> "$log"
    waited=0
    while [ ! -e "$dir/release-$word" ] && [ "$waited" -lt 600 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    printf 'release t=%s word=%s n=%s\n' "$(date +%s)" "$word" "$n" >> "$log"
fi
exec "$@"
