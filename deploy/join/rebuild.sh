#!/bin/bash
set -e

# Quick rebuild and restart for development
# Use this after making code changes

cd "$(dirname "$0")"

echo "============================================"
echo "Rebuilding Small Node from local changes"
echo "============================================"
echo ""

# Load configuration
if [ -f config.env ]; then
    source config.env
fi

echo "Stopping services..."
docker compose \
    -f docker-compose.yml \
    -f docker-compose.mlnode.yml \
    -f docker-compose.mlnode.local.yml \
    down

echo ""
echo "Rebuilding image..."
docker compose \
    -f docker-compose.yml \
    -f docker-compose.mlnode.yml \
    -f docker-compose.mlnode.local.yml \
    build

echo ""
echo "Starting services..."
docker compose \
    -f docker-compose.yml \
    -f docker-compose.mlnode.yml \
    -f docker-compose.mlnode.local.yml \
    up -d

echo ""
echo "============================================"
echo "✓ Rebuild complete!"
echo "============================================"
echo ""
echo "View logs:"
echo "  docker logs -f mlnode-308"
echo ""
