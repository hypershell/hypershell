#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Bound a verify step: run COMMAND in a process group of its own and, once SECONDS have passed,
# interrupt the group (SIGINT, so pytest runs its fixture teardown and ends the relays it started),
# then kill it 15 seconds later if anything in it is left. Exits with COMMAND's status, 124 at the
# bound. macOS has no `timeout`; this needs only perl.
#
#   sh spec/relay-advertise/drive/bound.sh SECONDS COMMAND [ARGS...]
exec perl -MPOSIX=WNOHANG -e '
    my $limit = shift;
    my $pid = fork;
    defined $pid or die "bound: fork: $!\n";
    if (!$pid) { setpgrp(0, 0); exec @ARGV or die "bound: exec $ARGV[0]: $!\n"; }
    $SIG{ALRM} = sub {
        print STDERR "bound: $limit seconds passed, interrupting @ARGV\n";
        kill "INT", -$pid;
        my $done = 0;
        for (1 .. 15) { if (waitpid($pid, WNOHANG) > 0) { $done = 1; last; } sleep 1; }
        kill "KILL", -$pid;
        waitpid($pid, 0) unless $done;
        exit 124;
    };
    alarm $limit;
    waitpid($pid, 0);
    exit($? & 127 ? 128 + ($? & 127) : $? >> 8);
' "$@"
