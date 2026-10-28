#!/bin/sh
# Runs after the YourDoc .deb/.rpm is installed. Refresh the desktop database so
# the launcher icon appears without a logout. Never fails the install.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
exit 0
