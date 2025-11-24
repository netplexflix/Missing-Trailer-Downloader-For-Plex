# Use a slim Python image as the base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IS_DOCKER=true \
    DENO_INSTALL="/usr/local" \
    DENO_DIR="/app/.deno" \
    SCHEDULE_HOURS=24

# Install system dependencies including ffmpeg for yt-dlp and unzip for Deno
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    gosu && \
    rm -rf /var/lib/apt/lists/*

# Install Deno - using direct binary download for reliability
RUN DENO_VERSION="2.5.6" && \
    ARCH="$(dpkg --print-architecture)" && \
    if [ "$ARCH" = "amd64" ]; then DENO_ARCH="x86_64"; \
    elif [ "$ARCH" = "arm64" ]; then DENO_ARCH="aarch64"; \
    else echo "Unsupported architecture: $ARCH" && exit 1; fi && \
    curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    unzip -q /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip && \
    deno --version

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY MTDP.py .
COPY Modules/ ./Modules/

# Create necessary directories
RUN mkdir -p /config /media /app/.deno /app/Logs

# Copy and prepare the entrypoint
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Start with the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "MTDP.py"]