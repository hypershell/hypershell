#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Docs check for queue-wire-codec (R11). Run from anywhere in the repository:
#
#   sh spec/queue-wire-codec/drive/docs_check.sh
#
# Fails when docs/, AGENTS.md or .agents/factory/invariants.md still says the queue's RPC is
# pickle-framed or that its wire is the stock manager's, when docs/security.rst lacks its
# `versionchanged:: 2.9.0` note on the new envelope, or when a line this branch changed under docs/
# shows a key option on a command line (the client's short or long key option) or an `hs task`
# command. Prints each finding; exits 0
# when there is none.

set -u

here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || exit 2
cd "$root" || exit 2
base=${DOCS_BASE:-develop}
status=0

finding() { echo "docs_check: $*"; status=1; }

stale=$(grep -rniE 'pickle-framed|framing (uses|is) (python )?.{0,2}pickle|rpc \*?framing\*? uses|pickle-serialized rpc|every byte on the wire are those of a stock|wire, auth and TLS behaviour are those of the stock manager|those of a stock .?BaseManager' \
    docs AGENTS.md .agents/factory/invariants.md 2>/dev/null | grep -v '^docs/_build')
[ -z "$stale" ] || finding "still describes the old envelope:
$stale"

grep -n 'versionchanged:: 2.9.0' docs/security.rst >/dev/null || finding 'docs/security.rst has no versionchanged:: 2.9.0'
grep -niE 'closed (rpc )?vocabulary' docs/security.rst >/dev/null || finding 'docs/security.rst does not describe the closed vocabulary'

changed=$(git diff -U0 "$base"...HEAD -- docs | grep '^+' | grep -v '^+++')
dash=-  # The key options are built here, so this script never shows one itself
short="${dash}k"
long="${dash}${dash}auth"
shown=$(printf '%s\n' "$changed" | grep -E "(^|[[:space:]\`])($short|$long)([[:space:]=\`]|\$)|hs task ")
[ -z "$shown" ] || finding "a changed docs line shows a key option or an hs task command:
$shown"

[ "$status" -eq 0 ] && echo 'docs_check: no finding'
exit $status
