#!/bin/sh

# THIS SCRIPT WILL BE RUN AS THE ROOT USER IN THE CONTAINER BEFORE APP STARTS

# Print 'M T D P' as ASCII Art 
# Generated using https://www.asciiart.eu/text-to-ascii-art
# Font: Banner3, Horizontal Layout: Wide, Vertical Layout: Wide, Width: 80
# Border: PlusBox v2, V. Padding: 1, H. Padding: 5
# Comment Style: echo commands: echo " ";
# Output Font: Standard, Output Size: 14 pt
# Whitespace break: enabled, Trim Whitespace: enabled, Replace Whitespace: disabled
echo "+====================================================================+";
echo "|                                                                    |";
echo "|           ##     ##    ########    ########     ########           |";
echo "|           ###   ###       ##       ##     ##    ##     ##          |";
echo "|           #### ####       ##       ##     ##    ##     ##          |";
echo "|           ## ### ##       ##       ##     ##    ########           |";
echo "|           ##     ##       ##       ##     ##    ##                 |";
echo "|           ##     ##       ##       ##     ##    ##                 |";
echo "|           ##     ##       ##       ########     ##                 |";
echo "|                                                                    |";
echo "+====================================================================+";
echo "Starting MTDP container with the following configuration:"
echo "APP_DATA_DIR: ${APP_DATA_DIR}"
echo "PUID: ${PUID}"
echo "PGID: ${PGID}"
echo "TZ: ${TZ}"
echo "----------------------------------------------------------------------";

# Set TimeZone based on env variable
# Print date time before 
echo "Current date time: $(date)"
echo "Setting TimeZone to ${TZ}"
echo $TZ > /etc/timezone && \
    ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata
echo "Current date time after tzdate: $(date)"

# Remove trailing slash from APP_DATA_DIR if it exists
export APP_DATA_DIR=$(echo $APP_DATA_DIR | sed 's:/*$::')


# Create appdata (default=/config) folder for storing database and other config files
echo "Creating '$APP_DATA_DIR' folder for storing config files"
mkdir -p "${APP_DATA_DIR}/logs" "${APP_DATA_DIR}/data"
# Copy the default config file to the appdata folder if it doesn't exist
if [ ! -f "${APP_DATA_DIR}/config.yml" ]; then
    echo "Copying default config file to '$APP_DATA_DIR'"
    cp /app/config.yml "${APP_DATA_DIR}/config.yml"
fi

# Set default values for PUID and PGID if not provided
PUID=${PUID:-1000}
PGID=${PGID:-1000}
APPUSER=appuser
APPGROUP=appuser

# Create the appuser group and user if they don't exist
# Check if a group with the supplied PGID already exists
if getent group "$PGID" > /dev/null 2>&1; then
    # Use the existing group name
    APPGROUP=$(getent group "$PGID" | cut -d: -f1)
    echo "Group with GID '$PGID' already exists, using group '$APPGROUP'"
else
    # Create the appuser group if it doesn't exist
    echo "Creating group '$APPGROUP' with GID '$PGID'"
    groupadd -g "$PGID" "$APPGROUP"
fi

# Check if a user with the supplied PUID already exists
if getent passwd "$PUID" > /dev/null 2>&1; then
    # Use the existing user name
    APPUSER=$(getent passwd "$PUID" | cut -d: -f1)
    echo "User with UID '$PUID' already exists, using user '$APPUSER'"
else
    # Create the appuser user if it doesn't exist
    echo "Creating user '$APPUSER' with UID '$PUID'"
    useradd -u "$PUID" -g "$PGID" -m "$APPUSER"
fi

# Set permissions for appuser on /app and /config directories
echo "Setting proper permissions for directories"
chmod -R 755 /app
chown -R "$APPUSER":"$APPGROUP" /app
chown -R "$APPUSER":"$APPGROUP" "$APP_DATA_DIR"
chmod -R 755 "$APP_DATA_DIR"
chmod 644 "$APP_DATA_DIR/config.yml" 2>/dev/null || true

# Switch to the non-root user and execute the command
echo "Switching to user '$APPUSER' and starting the application"
echo "Starting MTDP application"
cd /app
exec gosu "$APPUSER" python3 MTDP.py

# DO NOT ADD ANY OTHER COMMANDS HERE! THEY WON'T BE EXECUTED!
