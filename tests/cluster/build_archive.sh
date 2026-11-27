#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Build one self-contained, relocatable HyperShell archive for the multi-node drive (drive.sh).
#
# The archive holds a standalone CPython (python/), HyperShell and its locked dependencies
# installed as a plain --target tree (site/, byte-compiled), and a LABEL file. It contains no
# absolute paths and no console scripts, so it unpacks and runs anywhere on a node of the same
# platform with nothing installed:  PYTHONPATH=site python/bin/python3 -c 'from hypershell import main; ...'
#
# Environment:
#   HSA_REF       git ref to build: a tag (2.8.3), a commit (b819de6), or HEAD      (required)
#   HSA_LABEL     label recorded with every result (default: HSA_REF, e.g. 'feature' for HEAD)
#   HSA_PYTHON    uv Python request: a version (3.13) or a full key for another platform,
#                 e.g. cpython-3.13-linux-x86_64-gnu                                (default: 3.13)
#   HSA_PLATFORM  target platform for dependency wheels when cross-building, e.g.
#                 x86_64-manylinux_2_28 (uv --python-platform); skips byte-compilation
#   HSA_OUT       output file (default: ./hypershell-<label>.tar.gz)
#
# Requires git and uv, and HTTPS access to the Python package index and to the standalone
# Python downloads (uv's release mirror or GitHub). Run it from inside the repository.
set -eu

: "${HSA_REF:?set HSA_REF to a git ref}"
HSA_LABEL=${HSA_LABEL:-$HSA_REF}
HSA_PYTHON=${HSA_PYTHON:-3.13}
HSA_PLATFORM=${HSA_PLATFORM:-}
HSA_OUT=${HSA_OUT:-$(pwd)/hypershell-$HSA_LABEL.tar.gz}

case "$HSA_LABEL" in
    *[!A-Za-z0-9._-]*|'') echo "build_archive: HSA_LABEL must match [A-Za-z0-9._-]+" >&2; exit 64 ;;
esac

repo=$(git rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "$HSA_REF^{commit}")
work=$(mktemp -d "${TMPDIR:-/tmp}/hsa-build.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
stage="$work/stage"
mkdir -p "$work/src" "$work/py" "$stage"

echo "build_archive: $HSA_LABEL = $revision (python $HSA_PYTHON${HSA_PLATFORM:+, platform $HSA_PLATFORM})" >&2
git -C "$repo" archive --format=tar "$revision" | tar -xf - -C "$work/src"

# A python-build-standalone interpreter is relocatable by design.
uv python install --quiet --install-dir "$work/py" --no-bin "$HSA_PYTHON"
interpreter=$(find "$work/py" -mindepth 1 -maxdepth 1 -type d -name 'cpython-*' | head -n 1)
[ -n "$interpreter" ] || { echo "build_archive: no interpreter installed" >&2; exit 1; }
mv "$interpreter" "$stage/python"
version=$(basename "$interpreter" | cut -d- -f2)
minor=$(echo "$version" | cut -d. -f1-2)

# The locked dependency set of that ref, then the project itself, as a plain target tree.
(cd "$work/src" && uv export --quiet --frozen --no-dev --no-group docs --no-emit-project --no-hashes \
    --output-file "$work/requirements.txt")
if [ -n "$HSA_PLATFORM" ]; then
    set -- --python-platform "$HSA_PLATFORM" --python-version "$minor"
else
    set -- --python "$stage/python/bin/python3"
fi
uv pip install --quiet "$@" --target "$stage/site" -r "$work/requirements.txt"
uv pip install --quiet "$@" --target "$stage/site" --no-deps "$work/src"

# No console scripts (their shebangs are absolute) and no install-location records.
rm -rf "$stage/site/bin"
find "$stage/site" -name direct_url.json -path '*.dist-info/*' -exec rm -f {} +
if [ -z "$HSA_PLATFORM" ]; then
    # Source paths are recorded relative to the archive root.
    "$stage/python/bin/python3" -m compileall -q -s "$stage" "$stage/site" > /dev/null
fi
if grep -rIl "$work" "$stage/site" > /dev/null 2>&1; then
    echo "build_archive: build paths leaked into site/:" >&2
    grep -rIl "$work" "$stage/site" >&2
    exit 1
fi

printf '%s\n' "$HSA_LABEL" > "$stage/LABEL"
printf '%s\n' "$revision" > "$stage/REVISION"
mkdir -p "$(dirname "$HSA_OUT")"
tar -czf "$HSA_OUT.tmp" -C "$stage" python site LABEL REVISION
mv "$HSA_OUT.tmp" "$HSA_OUT"
echo "build_archive: wrote $HSA_OUT ($(du -h "$HSA_OUT" | cut -f1), python $version)" >&2
