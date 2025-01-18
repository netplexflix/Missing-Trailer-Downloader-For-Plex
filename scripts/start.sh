#!/bin/sh

# THIS SCRIPT WILL BE RUN AS THE NON-ROOT USER 'appuser' IN THE CONTAINER

echo "Running application as user: $(whoami)"

# Start the script
echo "Starting MTDP application"
cd /app

# Add apscheduler to run the script every 60 minutes
# Run python script tasks.schedules 
exec python3 main.py