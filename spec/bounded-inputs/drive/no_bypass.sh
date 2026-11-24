#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# bounded-inputs, no-bypass gate (c): no numeric option or bound is declared around the table.
# Run from the repository root:
#
#   sh spec/bounded-inputs/drive/no_bypass.sh
#
# Lists every line of src/hypershell, outside core/config_schema.py (the table), that types an option
# as a plain int or float, keeps a retired range check, or writes one of the table's bounds as a
# literal. Exits 0 when there is none, 1 otherwise. On the base build (8548053b) it lists 41 lines.

set -u
pattern='type=(int|float)\b|check_evict|timedelta\.max|2 ?\*\* ?(31|63)|1e-6|86_?400\b'
hits=$(grep -rnE "$pattern" src/hypershell --include='*.py' | grep -v '^src/hypershell/core/config_schema.py:')
if [ -n "$hits" ]; then
    printf '%s\n' "$hits"
    printf 'no_bypass: %s line(s) outside the table\n' "$(printf '%s\n' "$hits" | wc -l | tr -d ' ')"
    exit 1
fi
echo 'no_bypass: clean'
