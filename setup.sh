#!/bin/bash

# Missing Trailer Downloader for Plex - Easy Setup Script
# This script helps you get started quickly with Docker

set -e

echo "🎬 Missing Trailer Downloader for Plex - Docker Setup"
echo "=================================================="

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

echo "✅ Docker is running"

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p config logs

# Copy config file if it doesn't exist
if [ ! -f "config/config.yml" ]; then
    echo "📋 Copying default configuration..."
    cp config.yml config/config.yml
    echo "⚠️  Please edit config/config.yml with your Plex server details"
else
    echo "✅ Configuration file already exists"
fi

# Build and start the container
echo "🔨 Building and starting container..."
docker-compose up -d --build

echo ""
echo "🎉 Setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Edit config/config.yml with your Plex server details"
echo "2. Restart the container: docker-compose restart"
echo ""
echo "📊 Useful commands:"
echo "• View logs: docker-compose logs -f"
echo "• Stop: docker-compose down"
echo "• Restart: docker-compose restart"
echo "• Interactive shell: docker-compose exec missing-trailer-downloader /bin/bash"
echo ""
echo "📖 For more help, see DOCKER_README.md"
