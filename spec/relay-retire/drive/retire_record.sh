#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Recorder for one site client of the multi-node relay-retire drive (copied from the relay-topology
# drive's, which stays as that cycle's record). retire_launch.sh starts it, as a job step on the node
# the client lands on:
#
#   sh retire_record.sh T_LAUNCH WORD CLIENT_ARGV...
#
# It writes one file per client, HSD_RUN/launches/<site>-<node>-<pid>.rec:
#
#   start t_launch=T t_start=T site=SITE node=NODE pid=PID word=WORD env=ENV client=ZONE key=0|1
#   kill t=T                    (only when the recorder ended the client, see below)
#   exit t_end=T status=N killed=0|1 signal=NAME|-
#
#   SITE      the launching relay's label, HSD_SITE (A or A2, though both advertise site A)
#   T_LAUNCH  epoch seconds when the relay's launcher ran (on the relay's node)
#   NODE      the node the client landed on (SLURMD_NODENAME, else the host name)
#   WORD      the launcher word that carries {zone}; ENV is $HYPERSHELL_ZONE and ZONE the client's
#             --zone ('-' when unset)
#   key       1 if the upstream key (HSD_RUN/key) appears anywhere in the client's environment or
#             command line
#   killed    1 if this recorder ended the client on the drive's kill flag for its site
#             (HSD_RUN/kill-SITE), or on the run's abort flag or deadline
#   signal    a signal the recorder was sent and passed on to the client (TERM, INT, HUP), else '-'
#
# The client runs as a child, so its exit status is recorded and passed back to the relay. Nothing
# here is deleted. Settings come from the relay's environment: HSD_RUN, HSD_SITE, HSD_DEADLINE
# (epoch seconds), HSD_PY (for a sub-second clock where `date` has none).
set -u

: "${HSD_RUN:?}" "${HSD_SITE:?}"
now() {
    t=$(date +%s.%N 2>/dev/null)
    case $t in *N*|'') "${HSD_PY:-python3}" -c 'import time; print(f"{time.time():.3f}")' ;; *) echo "$t" ;; esac
}
t_start=$(now)
t_launch=$1 word=$2
shift 2

node=${SLURMD_NODENAME:-$(hostname)}
node=${node%%.*}
client=- prev=
for arg; do
    [ "$prev" = --zone ] && client=$arg
    prev=$arg
done
key=0
if [ -r "$HSD_RUN/key" ]; then
    secret=$(cat "$HSD_RUN/key")
    if [ -n "$secret" ]; then
        if env | grep -qF -e "$secret"; then key=1; fi
        case " $* " in *"$secret"*) key=1 ;; esac
    fi
fi
mkdir -p "$HSD_RUN/launches"
rec="$HSD_RUN/launches/$HSD_SITE-$node-$$.rec"
printf 'start t_launch=%s t_start=%s site=%s node=%s pid=%s word=%s env=%s client=%s key=%s\n' \
    "$t_launch" "$t_start" "$HSD_SITE" "$node" "$$" "$word" "${HYPERSHELL_ZONE:--}" "$client" "$key" > "$rec"

"$@" &
child=$!
signal=-
pass() { signal=$1; kill -"$1" "$child" 2>/dev/null || true; }
trap 'pass TERM' TERM
trap 'pass INT' INT
trap 'pass HUP' HUP
# A watcher ends the client on the drive's flags or the run's deadline, and notes it in the record.
deadline=${HSD_DEADLINE:-0}
(
    while kill -0 "$child" 2>/dev/null; do
        if [ -e "$HSD_RUN/kill-$HSD_SITE" ] || [ -e "$HSD_RUN/abort" ] || \
                { [ "$deadline" -gt 0 ] && [ "$(date +%s)" -ge "$deadline" ]; }; then
            printf 'kill t=%s\n' "$(now)" >> "$rec"
            kill -KILL "$child" 2>/dev/null || true
            exit 0
        fi
        sleep 1
    done
) &
watcher=$!
status=0
wait "$child" || status=$?
while kill -0 "$child" 2>/dev/null; do  # a trapped signal interrupts wait; wait again
    status=0
    wait "$child" || status=$?
done
t_end=$(now)
kill "$watcher" 2>/dev/null || true
killed=0
grep -q '^kill ' "$rec" && killed=1
printf 'exit t_end=%s status=%s killed=%s signal=%s\n' "$t_end" "$status" "$killed" "$signal" >> "$rec"
exit "$status"
