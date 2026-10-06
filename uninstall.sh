#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
BINDIR="$PREFIX/bin"

rm -f \
    "$BINDIR/codex-handoff-copy" \
    "$BINDIR/codex-handoff-render" \
    "$BINDIR/codex-handoff-notify" \
    "$BINDIR/codex-handoff-watch" \
    "$BINDIR/handoff_delivery.py" \
    "$BINDIR/codex-shell"

echo "Jarri Codex Handoff removed from $BINDIR"
