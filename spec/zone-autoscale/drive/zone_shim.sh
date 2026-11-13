#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Launcher shim for the zone-autoscale drive, given as --launcher="sh zone_shim.sh LOG {zone}".
# It records one line per launch in LOG, then runs the client command it was handed:
#
#   launch pid=PID word=WORD env=ENV client=ZONE g0=COUNT
#
#   WORD   its second argument, the launcher word that carries {zone}
#   ENV    $HYPERSHELL_ZONE as the launch set it ('-' when unset)
#   ZONE   the value of the client's --zone ('-' when the client has none)
#   COUNT  tasks of group 0 not yet complete when the launch was made
#
# The cluster advertises its host name, which a sandbox may not resolve, so the client's -H is
# rewritten to loopback. The shim execs the client, so PID is the client's process id.
log=$1 word=$2
shift 2
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
g0=$(hs list -g 0 --remaining --count 2>/dev/null || echo '?')
printf 'launch pid=%s word=%s env=%s client=%s g0=%s\n' "$$" "$word" "${HYPERSHELL_ZONE:--}" "$client" "$g0" >> "$log"
exec "$@"
