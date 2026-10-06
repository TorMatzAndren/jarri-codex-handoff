#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
BINDIR="$PREFIX/bin"

mkdir -p "$BINDIR"

install -m 0755 bin/codex-handoff-copy "$BINDIR/codex-handoff-copy"
install -m 0755 bin/codex-handoff-render "$BINDIR/codex-handoff-render"
install -m 0755 bin/codex-handoff-notify "$BINDIR/codex-handoff-notify"
install -m 0755 bin/codex-handoff-watch "$BINDIR/codex-handoff-watch"
install -m 0755 bin/handoff_delivery.py "$BINDIR/handoff_delivery.py"
install -m 0755 bin/codex-shell "$BINDIR/codex-shell"

echo
echo "Installed:"
echo "  $BINDIR/codex-handoff-copy"
echo "  $BINDIR/codex-handoff-render"
echo "  $BINDIR/codex-handoff-notify"
echo "  $BINDIR/codex-handoff-watch"
echo "  $BINDIR/codex-shell"
echo
echo "Run:"
echo "  codex-shell"
echo
echo "Completed Codex turns are copied automatically."
echo "Manual fallback:"
echo "  codex-handoff-copy"
