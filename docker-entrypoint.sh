#!/bin/bash
set -e

# Default UID/GID if not provided
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Starting with UID: $PUID and GID: $PGID"

# Resolve or create the group for the requested GID
EXISTING_GROUP=$(getent group "$PGID" | cut -d: -f1 || true)
if [ -n "$EXISTING_GROUP" ]; then
    # GID already taken — reuse that group
    APP_GROUP="$EXISTING_GROUP"
elif getent group mtdp > /dev/null 2>&1; then
    # 'mtdp' group exists but with a different GID — update it
    groupmod -g "$PGID" mtdp
    APP_GROUP="mtdp"
else
    groupadd -g "$PGID" mtdp
    APP_GROUP="mtdp"
fi

# Resolve or create the user for the requested UID
if getent passwd mtdp > /dev/null 2>&1; then
    # 'mtdp' user exists — ensure UID and group match
    usermod -u "$PUID" -g "$APP_GROUP" mtdp 2>/dev/null || true
elif getent passwd "$PUID" > /dev/null 2>&1; then
    # UID already taken by another user — reuse that user
    EXISTING_USER=$(getent passwd "$PUID" | cut -d: -f1)
    # Create an 'mtdp' alias by updating the existing user's group
    usermod -g "$APP_GROUP" "$EXISTING_USER" 2>/dev/null || true
    # Use the existing user name for gosu
    APP_USER="$EXISTING_USER"
else
    useradd -u "$PUID" -g "$APP_GROUP" -d /app -s /bin/bash mtdp
fi
APP_USER=${APP_USER:-mtdp}

# Ensure proper ownership of directories
chown -R "$PUID:$PGID" /app /config || true
chown "$PUID:$PGID" /media || true
# Allow the app user to pip-upgrade packages at runtime (e.g. yt-dlp from the web UI).
# pip was run as root during the image build, so /usr/local is root-owned. Re-owning
# lib, bin, and share covers all locations pip writes to: site-packages, entry-point
# scripts, and data files (e.g. bash completions).
chown -R $PUID:$PGID /usr/local/lib /usr/local/bin /usr/local/share 2>/dev/null || true

# Create default config from example if none exists (never overwrites)
if [ ! -f /config/config.yml ]; then
    echo "No config.yml found in /config/ — creating from example..."
    cp /app/config.example.yml /config/config.yml
    chown "$PUID:$PGID" /config/config.yml
    echo "Default config.yml created. Please set your Plex credentials via the web UI (port 2121) or directly in /config/config.yml."
fi

# Execute the command as the app user
exec gosu "$APP_USER" "$@"