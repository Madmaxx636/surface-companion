#!/usr/bin/env bash
# Install or remove Surface Companion system-wide.
#   sudo ./install.sh              install (and pull in Python dependencies)
#   sudo ./install.sh --no-deps    install without touching the package manager
#   sudo ./install.sh --uninstall  remove everything this script installed
set -euo pipefail

LIB=/usr/lib/surface-companion
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode=install; deps=1
for a in "$@"; do
  case "$a" in
    --uninstall) mode=uninstall ;;
    --no-deps) deps=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "This needs administrator rights; re-running with sudo..." >&2
  exec sudo "$0" "$@"
fi

if [[ $mode == uninstall ]]; then
  systemctl disable --now surface-companion.service 2>/dev/null || true
  rm -rf "$LIB"
  rm -f /usr/bin/surface-companion /usr/bin/surface-companiond \
        /usr/share/polkit-1/actions/org.surfacecompanion.policy \
        /usr/share/dbus-1/system.d/org.surfacecompanion.Daemon.conf \
        /usr/share/dbus-1/system-services/org.surfacecompanion.Daemon.service \
        /usr/lib/systemd/system/surface-companion.service \
        /usr/share/applications/surface-companion.desktop \
        /etc/xdg/autostart/surface-companion.desktop \
        /usr/share/icons/hicolor/scalable/apps/surface-companion.svg
  systemctl daemon-reload || true
  echo "Removed. Settings and fix records remain in /var/lib/surface-companion (delete it if you want)."
  echo "Fixes you applied are left in place; undo them from the Fixes page before uninstalling if you want them gone."
  exit 0
fi

if [[ $deps -eq 1 ]]; then
  . /etc/os-release
  case " ${ID} ${ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*) apt-get install -y python3 python3-gi python3-pyqt6 polkitd pkexec 2>/dev/null || apt-get install -y python3 python3-gi python3-pyqt6 policykit-1 ;;
    *" fedora "*|*" rhel "*)   dnf install -y python3 python3-gobject python3-pyqt6 polkit ;;
    *" arch "*)                pacman -S --needed --noconfirm python python-gobject python-pyqt6 polkit ;;
    *" suse "*)                zypper --non-interactive install python3 python3-gobject python3-qt6 polkit ;;
    *) echo "Unknown distribution: install Python 3, PyGObject, PyQt6 and polkit yourself, then use --no-deps." >&2 ;;
  esac
fi

install -d "$LIB"
rm -rf "$LIB/surface_companion"
cp -r "$here/src/surface_companion" "$LIB/"
find "$LIB" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > /usr/bin/surface-companion <<'W'
#!/bin/sh
PYTHONPATH=/usr/lib/surface-companion exec python3 -m surface_companion "$@"
W
cat > /usr/bin/surface-companiond <<'W'
#!/bin/sh
PYTHONPATH=/usr/lib/surface-companion exec python3 -m surface_companion.daemon.service "$@"
W
chmod 755 /usr/bin/surface-companion /usr/bin/surface-companiond

install -Dm644 "$here/data/org.surfacecompanion.policy" /usr/share/polkit-1/actions/org.surfacecompanion.policy
install -Dm644 "$here/data/org.surfacecompanion.Daemon.conf" /usr/share/dbus-1/system.d/org.surfacecompanion.Daemon.conf
install -Dm644 "$here/data/org.surfacecompanion.Daemon.service" /usr/share/dbus-1/system-services/org.surfacecompanion.Daemon.service
install -Dm644 "$here/data/surface-companion.service" /usr/lib/systemd/system/surface-companion.service
install -Dm644 "$here/data/surface-companion.desktop" /usr/share/applications/surface-companion.desktop
install -Dm644 "$here/data/surface-companion-autostart.desktop" /etc/xdg/autostart/surface-companion.desktop
install -Dm644 "$here/data/surface-companion.svg" /usr/share/icons/hicolor/scalable/apps/surface-companion.svg
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true

systemctl daemon-reload
systemctl reload dbus 2>/dev/null || systemctl reload dbus-broker 2>/dev/null || true
systemctl enable --now surface-companion.service
echo "Installed. Open “Surface Companion” from your application menu, or run: surface-companion"
