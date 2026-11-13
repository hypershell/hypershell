#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Launcher shim for the relay-topology drive, given as a relay's launcher:
#
#   launcher = "sh topology_shim.sh LOG SITE {zone}"
#
# It records one line per launch in LOG, then runs the site client command it was handed:
#
#   launch t=EPOCH site=SITE pid=PID word=WORD env=ENV client=ZONE
#
#   EPOCH  seconds since the epoch when the launch was made
#   SITE   its second argument, the relay's site
#   WORD   its third argument, the launcher word that carries {zone}
#   ENV    $HYPERSHELL_ZONE as the launch set it ('-' when unset)
#   ZONE   the value of the client's --zone ('-' when the client has none)
#
# The relay advertises its host name, which a sandbox may not resolve, so the client's -H is
# rewritten to loopback. The shim execs the client, so PID is the site client's process id.
log=$1 site=$2 word=$3
shift 3
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
printf 'launch t=%s site=%s pid=%s word=%s env=%s client=%s\n' \
    "$(date +%s)" "$site" "$$" "$word" "${HYPERSHELL_ZONE:--}" "$client" >> "$log"
exec "$@"
