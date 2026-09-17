#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
BINDIR="$PREFIX/bin"

rm -f \
    "$BINDIR/c" \
    "$BINDIR/catcopy" \
    "$BINDIR/codex-handoff-render" \
    "$BINDIR/codex-shell"

echo "Jarri Codex Handoff removed from $BINDIR"
