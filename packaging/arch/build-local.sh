#!/bin/bash
# Build the Arch package from this working tree (no git commit or release
# needed). The version gets a .dYYYYMMDDHHMM suffix, so each build upgrades
# the previous one.
#
#   packaging/arch/build-local.sh [makepkg options, e.g. --nocheck]
#   sudo pacman -U build/arch/homemesh-*.pkg.tar.zst
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
out=$root/build/arch

release=$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")
code=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/homemesh/__init__.py")
if [[ $release != "$code" ]]; then
    echo "PKGBUILD says $release but homemesh/__init__.py says $code." >&2
    exit 1
fi
pkgver=$release.d$(date +%Y%m%d%H%M)

rm -rf "$out"
mkdir -p "$out"
tarball=$out/homemesh-$pkgver.tar.gz
tar -C "$root" --exclude=./build --exclude=./.git --exclude='__pycache__' \
    --transform "s|^\./|homemesh-$pkgver/|" -czf "$tarball" .
cp "$here/PKGBUILD" "$here/homemesh.install" "$out/"
sum=$(sha256sum "$tarball" | cut -d' ' -f1)
sed -i -e "s/^pkgver=.*/pkgver=$pkgver/" -e "s/^sha256sums=.*/sha256sums=('$sum')/" "$out/PKGBUILD"

cd "$out"
PKGDEST=$out SRCDEST=$out BUILDDIR=$out makepkg --force --cleanbuild "$@"
echo "Package: $(ls "$out"/*.pkg.tar.*)"
