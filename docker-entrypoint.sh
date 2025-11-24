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

# Check if config exists
if [ ! -f /config/config.yml ]; then
    echo "ERROR: config.yml not found in /config/"
    echo "Please mount your config file to /config/config.yml"
    exit 1
fi

# Execute the command as the mtdp user
exec gosu mtdp "$@"