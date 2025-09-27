# Use Python 3.12 slim as base image
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
    APP_VERSION=${APP_VERSION} \
    PATH="/usr/local/bin:$PATH" \
    UMASK=002

# Create and Set the working directory for the app
WORKDIR /app

# Install pip requirements
COPY ./requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --upgrade -r /app/requirements.txt

RUN pip install -U "yt-dlp[default]"
RUN curl -fsSL https://deno.land/install.sh | sh

# Install tzdata, gosu, curl and Deno (required for yt-dlp)
RUN apt-get update && apt-get install -y tzdata gosu curl unzip && \
    ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata && \
    # Install Deno for yt-dlp JavaScript runtime requirements
    curl -fsSL https://deno.land/install.sh | sh && \
    mv /root/.deno/bin/deno /usr/local/bin/deno && \
    chmod +x /usr/local/bin/deno && \
    rm -rf /var/lib/apt/lists/* /root/.deno

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
