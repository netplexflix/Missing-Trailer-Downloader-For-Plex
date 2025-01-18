# Stage 1 - Python dependencies
FROM python:3.12-slim AS python-deps


# ARG APP_VERSION, will be set during build by github actions
ARG APP_VERSION=0.0.0-dev

# Set environment variables
# PYTHONDONTWRITEBYTECODE=1 -> Keeps Python from generating .pyc files in the container
# PYTHONUNBUFFERED=1 -> Turns off buffering for easier container logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ="America/New_York" \
    APP_NAME="MTDP" \
    APP_DATA_DIR="/config" \
    PUID=1000 \
    PGID=1000 \
    APP_VERSION=${APP_VERSION}

# Create and Set the working directory for the app
WORKDIR /app

# Install pip requirements
COPY ./requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --upgrade -r /app/requirements.txt

# Install tzdata, gosu and set timezone
RUN apt-get update && apt-get install -y tzdata gosu curl && \
    ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

# Copy all the files from the project’s root to the working directory
COPY . /app

# Set the python path
ENV PYTHONPATH=/app

# Make the scripts inside /app/scripts executable
RUN chmod +x /app/scripts/*.sh

# Install ffmpeg using install_ffmpeg.sh script
RUN /app/scripts/install_ffmpeg.sh

# Run entrypoint script to create directories, set permissions and timezone \
# and start the application as appuser
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
