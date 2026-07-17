#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Run a command against a throwaway HyperShell site so factory verify commands and
# review CLI drives never touch the developer's real database, logs, or TLS materials.
# Mirrors the env isolation of the pytest `temp_site` fixture (HYPERSHELL_SITE +
# HYPERSHELL_DATABASE_FILE); SQLite initializes the schema automatically on first use.
#
# Usage:
#   .agents/factory/bin/temp_site.sh uv run hs list
#   .agents/factory/bin/temp_site.sh sh -c "seq 100 | uv run hsx -t 'echo {}' -N4 && uv run hs list"
#
# The site directory is created under $TMPDIR and removed on exit (any exit path).
set -eu

site="$(mktemp -d "${TMPDIR:-/tmp}/hypershell-temp-site.XXXXXX")"
trap 'rm -rf "$site"' EXIT INT TERM

HYPERSHELL_SITE="$site"
HYPERSHELL_DATABASE_FILE="$site/task.db"
export HYPERSHELL_SITE HYPERSHELL_DATABASE_FILE

"$@"
