#!/bin/bash
set -e

# Deploy Small Node with locally built image
# This rebuilds the image from local files without pushing to GitHub

cd "$(dirname "$0")"

echo "============================================"
echo "Small Node - Local Development Deployment"
echo "============================================"
echo ""

# Check if config.env exists
if [ ! -f config.env ]; then
    echo "⚠️  config.env not found. Creating from template..."
    cp config.env.template config.env
    echo ""
    echo "⚠️  IMPORTANT: Edit config.env and set required parameters!"
    echo ""
    read -p "Press Enter to continue after editing config.env..."
fi

# Load configuration
source config.env

echo "Building image from local files..."
echo ""

# Build and start services
docker compose \
    -f docker-compose.yml \
    -f docker-compose.mlnode.yml \
    -f docker-compose.mlnode.local.yml \
    build --no-cache

echo ""
echo "Starting services..."
echo ""

docker compose \
    -f docker-compose.yml \
    -f docker-compose.mlnode.yml \
    -f docker-compose.mlnode.local.yml \
    up -d

echo ""
echo "============================================"
echo "✓ Deployment complete!"
echo "============================================"
echo ""
echo "Services running:"
docker compose -f docker-compose.yml -f docker-compose.mlnode.yml -f docker-compose.mlnode.local.yml ps
echo ""
echo "View logs:"
echo "  docker logs -f mlnode-308"
echo ""
echo "Delegation mode: ${DELEGATION_ENABLED:-0}"
if [ "${DELEGATION_ENABLED:-0}" = "1" ]; then
    echo "Delegation URL: ${DELEGATION_URL}"
fi
echo ""
