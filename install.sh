#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
BINDIR="$PREFIX/bin"

mkdir -p "$BINDIR"

install -m 0755 bin/catcopy "$BINDIR/catcopy"
install -m 0755 bin/codex-handoff-render "$BINDIR/codex-handoff-render"
install -m 0755 bin/codex-shell "$BINDIR/codex-shell"

ln -sfn catcopy "$BINDIR/c"

echo
echo "Installed:"
echo "  $BINDIR/catcopy"
echo "  $BINDIR/c"
echo "  $BINDIR/codex-handoff-render"
echo "  $BINDIR/codex-shell"
echo
echo "Run:"
echo "  codex-shell"
echo
echo "Then after Codex exits:"
echo "  c thisrepo"
