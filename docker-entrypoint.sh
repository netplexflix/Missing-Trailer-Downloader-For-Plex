#!/bin/bash
set -e

# Default UID/GID if not provided
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Starting with UID: $PUID and GID: $PGID"

# Create group and user if they don't exist
if ! getent group mtdp > /dev/null 2>&1; then
    groupadd -g "$PGID" mtdp
fi

if ! getent passwd mtdp > /dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/bash mtdp
fi

# Ensure proper ownership of directories
chown -R mtdp:mtdp /app /config || true
chown mtdp:mtdp /media || true
# Allow the app user to pip-upgrade packages at runtime (e.g. yt-dlp from the web UI).
# pip was run as root during the image build, so /usr/local is root-owned. Re-owning
# lib, bin, and share covers all locations pip writes to: site-packages, entry-point
# scripts, and data files (e.g. bash completions).
chown -R $PUID:$PGID /usr/local/lib /usr/local/bin /usr/local/share 2>/dev/null || true

# Check if config exists
if [ ! -f /config/config.yml ]; then
    echo "ERROR: config.yml not found in /config/"
    echo "Please mount your config file to /config/config.yml"
    exit 1
fi

# Execute the command as the mtdp user
exec gosu mtdp "$@"