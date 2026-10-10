#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.3.0}"
PACKAGE="jarri-codex-handoff"
ROOT="$(git rev-parse --show-toplevel)"
OUT="$ROOT/dist"

cd "$ROOT"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid version: $VERSION" >&2
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Refusing to build from a dirty worktree." >&2
    exit 1
fi

for tool in git tar gzip dpkg-deb sha256sum; do
    command -v "$tool" >/dev/null || {
        echo "Missing build tool: $tool" >&2
        exit 1
    }
done

BUILD_COMMIT="$(git rev-parse HEAD)"
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SOURCE="$WORK/$PACKAGE-$VERSION"
mkdir -p "$SOURCE" "$OUT"

echo "===== BUILD RELEASE $VERSION ====="
echo "Commit: $BUILD_COMMIT"

echo
echo "--- EXPORT COMMITTED SOURCE ---"
git archive --format=tar HEAD | tar -xf - -C "$SOURCE"

# Catcopy owns c; exclude the legacy copy from release payloads.
rm -f "$SOURCE/bin/c"
test ! -e "$SOURCE/bin/c"

echo
echo "--- BUILD PORTABLE ARCHIVE ---"
tar -C "$WORK" \
    --sort=name \
    --mtime="@$SOURCE_DATE_EPOCH" \
    --owner=0 --group=0 --numeric-owner \
    -cf - "$PACKAGE-$VERSION" |
    gzip -n -9 > "$OUT/$PACKAGE-$VERSION.tar.gz"

echo
echo "--- BUILD DEBIAN PACKAGE ---"
DEB="$WORK/deb"
mkdir -p "$DEB/DEBIAN"
mkdir -p "$DEB/usr/bin"
mkdir -p "$DEB/usr/share/doc/$PACKAGE"

for name in \
    codex-shell \
    codex-handoff-copy \
    codex-handoff-notify \
    codex-handoff-render \
    codex-handoff-watch \
    handoff_delivery.py
do
    install -m 0755 "$SOURCE/bin/$name" "$DEB/usr/bin/$name"
done

install -m 0644 "$SOURCE/README.md" \
    "$DEB/usr/share/doc/$PACKAGE/README.md"

install -m 0644 "$SOURCE/LICENSE" \
    "$DEB/usr/share/doc/$PACKAGE/LICENSE"

cat > "$DEB/DEBIAN/control" <<EOF
Package: $PACKAGE
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: Jarri Systems
Depends: bash, python3, git
Description: Exact-turn Codex CLI clipboard handoff
 Automatically captures completed Codex CLI turns for
 repository-scoped handoff and recovery.
 Requires separately installed Codex CLI and Catcopy.
EOF

dpkg-deb --root-owner-group --build \
    "$DEB" "$OUT/${PACKAGE}_${VERSION}_all.deb"

echo
echo "--- CHECKSUMS ---"
(
    cd "$OUT"
    sha256sum \
        "${PACKAGE}_${VERSION}_all.deb" \
        "${PACKAGE}-${VERSION}.tar.gz" \
        > SHA256SUMS
)

cat "$OUT/SHA256SUMS"

echo
echo "--- DEBIAN CONTENTS ---"
dpkg-deb --contents "$OUT/${PACKAGE}_${VERSION}_all.deb"

echo
echo "--- ARCHIVE CONTENTS ---"
tar -tzf "$OUT/${PACKAGE}-${VERSION}.tar.gz"

echo
echo "===== RELEASE BUILD COMPLETE ====="
