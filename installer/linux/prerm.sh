#!/bin/sh
# Runs before the YourDoc .deb/.rpm is removed. Refresh the desktop database so
# the stale launcher entry disappears. Never fails the uninstall.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
exit 0
