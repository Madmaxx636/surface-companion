#!/usr/bin/env bash
# Build dist/surface-companion_<version>_all.deb (pure Python, so architecture "all").
# Needs only dpkg-deb. Run from anywhere: ./packaging/build-deb.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/surface_companion/__init__.py)"
[[ -n $version ]] || { echo "could not read version" >&2; exit 1; }
pkg=surface-companion
maint="$(git config user.name) <$(git config user.email)>"
out="dist/${pkg}_${version}_all.deb"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/root"

d() { install -d -m 755 "$root/$1"; }
f() { install -D -m "${3:-644}" "$here/$1" "$root/$2"; }

d usr/lib/surface-companion
cp -r src/surface_companion "$root/usr/lib/surface-companion/"
find "$root" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$root/usr/lib/surface-companion" -type f -exec chmod 644 {} +

install -D -m 755 /dev/stdin "$root/usr/bin/surface-companion" <<'W'
#!/bin/sh
PYTHONPATH=/usr/lib/surface-companion exec python3 -m surface_companion "$@"
W
install -D -m 755 /dev/stdin "$root/usr/bin/surface-companiond" <<'W'
#!/bin/sh
PYTHONPATH=/usr/lib/surface-companion exec python3 -m surface_companion.daemon.service "$@"
W

f data/org.surfacecompanion.policy usr/share/polkit-1/actions/org.surfacecompanion.policy
f data/org.surfacecompanion.Daemon.conf usr/share/dbus-1/system.d/org.surfacecompanion.Daemon.conf
f data/org.surfacecompanion.Daemon.service usr/share/dbus-1/system-services/org.surfacecompanion.Daemon.service
f data/surface-companion.service usr/lib/systemd/system/surface-companion.service
f data/surface-companion.desktop usr/share/applications/surface-companion.desktop
f data/surface-companion-autostart.desktop etc/xdg/autostart/surface-companion.desktop
f data/surface-companion.svg usr/share/icons/hicolor/scalable/apps/surface-companion.svg
f README.md usr/share/doc/$pkg/README.md

# Debian changelog, taken from CHANGELOG.md's newest section
install -d -m 755 "$root/usr/share/doc/$pkg"
{
  echo "$pkg ($version) unstable; urgency=low"
  echo
  awk '/^## /{n++; next} n==1 && NF {print}' CHANGELOG.md | sed 's/^- /* /' | fold -s -w 74 | sed 's/^/  /; s/ *$//'
  echo
  echo " -- $maint  $(date -R)"
} | gzip -9n > "$root/usr/share/doc/$pkg/changelog.gz"
chmod 644 "$root/usr/share/doc/$pkg/changelog.gz"

cat > "$root/usr/share/doc/$pkg/copyright" <<C
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Surface Companion

Files: *
Copyright: 2026 Surface Companion contributors
License: Expat
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
C
chmod 644 "$root/usr/share/doc/$pkg/copyright"

find "$root" -type d -exec chmod 755 {} +      # install -D leaves parents group-writable under some umasks
find "$root/usr/lib/surface-companion" -type f -exec chmod 644 {} +

# ---- control files
ctl="$root/DEBIAN"
install -d -m 755 "$ctl"
size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
cat > "$ctl/control" <<C
Package: $pkg
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: $maint
Installed-Size: $size
Depends: python3 (>= 3.10), python3-gi, gir1.2-glib-2.0, python3-pyqt6, dbus, polkitd | policykit-1, systemd
Recommends: thermald
Suggests: iptsd, libcamera-tools
Description: Control and fix Microsoft Surface hardware on Linux
 One window for brightness, keyboard backlight, performance profile, sleep
 and wake, battery, temperatures and fans, touch and pen, cameras and the
 Surface Book detach latch. Also lists the linux-surface project's documented
 workarounds for your model, and installs and updates the linux-surface
 kernel and drivers.
 .
 A small root service applies changes and asks polkit before each one. The
 window looks the same on every desktop environment.
C
echo "/etc/xdg/autostart/surface-companion.desktop" > "$ctl/conffiles"

cat > "$ctl/postinst" <<'S'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    python3 -m compileall -q /usr/lib/surface-companion >/dev/null 2>&1 || true
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
    if [ -d /run/systemd/system ]; then
        systemctl daemon-reload || true
        systemctl reload dbus 2>/dev/null || systemctl reload dbus-broker 2>/dev/null || true
        systemctl enable surface-companion.service || true
        systemctl restart surface-companion.service || true
    fi
fi
exit 0
S
cat > "$ctl/prerm" <<'S'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = deconfigure ]; then
    if [ -d /run/systemd/system ]; then
        systemctl disable --now surface-companion.service 2>/dev/null || true
    fi
fi
exit 0
S
cat > "$ctl/postrm" <<'S'
#!/bin/sh
set -e
case "$1" in
    remove|purge)
        rm -rf /usr/lib/surface-companion          # byte-code written by postinst
        [ -d /run/systemd/system ] && systemctl daemon-reload 2>/dev/null || true
        ;;
esac
if [ "$1" = purge ]; then
    rm -rf /var/lib/surface-companion
fi
exit 0
S
chmod 755 "$ctl/postinst" "$ctl/prerm" "$ctl/postrm"

( cd "$root" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | sort | xargs md5sum > DEBIAN/md5sums )
chmod 644 "$ctl/md5sums"

mkdir -p dist
dpkg-deb --root-owner-group -Zxz --build "$root" "$out" >/dev/null
( cd dist && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )
echo "Built $out"
cat "$out.sha256"
