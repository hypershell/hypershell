#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Run a command against a throwaway HyperShell site so factory verify commands and
# review CLI drives never touch the developer's real database, logs, or TLS materials.
# Mirrors the env isolation of the pytest `temp_site` fixture (HYPERSHELL_SITE +
# HYPERSHELL_DATABASE_FILE); SQLite initializes the schema automatically on first use.
# The command also runs with its working directory set to the site, so relative file
# writes in a verify/cross-check drive stay contained instead of leaking into the repo.
#
# Usage:
#   .agents/factory/bin/temp_site.sh uv run hs list
#   .agents/factory/bin/temp_site.sh sh -c "seq 100 | uv run hsx -t 'echo {}' -N4 && uv run hs list"
#
# The site directory is created under $TMPDIR and removed on exit (any exit path).
set -eu

# Capture the repo root before we cd into the site below. `uv run` discovers the
# project by walking up from the working directory, so once cwd is the /tmp site it
# can no longer find pyproject.toml; UV_PROJECT pins discovery back to the repo.
# (Callers invoke this from the repo root, per the usage examples above.)
root="$(pwd)"

site="$(mktemp -d "${TMPDIR:-/tmp}/hypershell-temp-site.XXXXXX")"
trap 'rm -rf "$site"' EXIT INT TERM

HYPERSHELL_SITE="$site"
HYPERSHELL_DATABASE_FILE="$site/task.db"
UV_PROJECT="$root"
export HYPERSHELL_SITE HYPERSHELL_DATABASE_FILE UV_PROJECT

# Run inside the throwaway site so relative writes (e.g. `sh -c "… > t.in; …"`) stay
# contained instead of escaping into the working tree.
cd "$site"
"$@"
