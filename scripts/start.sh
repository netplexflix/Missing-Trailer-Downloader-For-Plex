#!/bin/sh

# THIS SCRIPT WILL BE RUN AS THE NON-ROOT USER 'appuser' IN THE CONTAINER

echo "Running application as user: $(whoami)"

# Start the script
echo "Starting MTDP application"
cd /app
# exec uvicorn backend.main:trailarr_api --host 0.0.0.0 --port ${APP_PORT:-7889}

# Add apscheduler to run the script every 60 minutes
# Run python script tasks.schedules 
exec python3 main.py