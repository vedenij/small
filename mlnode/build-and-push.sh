#!/bin/bash
set -e

# Build and push MLNode image to GitHub Container Registry
# Repository: https://github.com/vedenij/small.git

VERSION=${1:-"3.0.11"}
GITHUB_USER="vedenij"
IMAGE_NAME="smallmlnode"
FULL_IMAGE="ghcr.io/${GITHUB_USER}/${IMAGE_NAME}:${VERSION}"

echo "============================================"
echo "Building MLNode image with delegation support"
echo "Image: ${FULL_IMAGE}"
echo "Platform: linux/amd64 (for NVIDIA GPU servers)"
echo "============================================"
echo ""

# Check if buildx is available
if ! docker buildx version &> /dev/null; then
    echo "ERROR: docker buildx not found"
    echo "Please enable Docker BuildKit:"
    echo "  Docker Desktop -> Settings -> Features in development -> Enable 'Use containerd for pulling and storing images'"
    exit 1
fi

# Create buildx builder if needed
if ! docker buildx inspect mlnode-builder &> /dev/null; then
    echo "Creating buildx builder..."
    docker buildx create --name mlnode-builder --use
fi

# Navigate to api package directory
cd packages/api

# Build the image for linux/amd64 (NVIDIA GPU servers)
echo "Building image for linux/amd64..."
docker buildx build --platform linux/amd64 -t ${FULL_IMAGE} -f Dockerfile --load ../..

echo ""
echo "============================================"
echo "✓ Build complete!"
echo "============================================"
echo ""
echo "Image: ${FULL_IMAGE}"
echo ""

# Ask if user wants to push
read -p "Push image to GitHub Container Registry? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Pushing ${FULL_IMAGE}..."
    docker push ${FULL_IMAGE}

    echo ""
    echo "============================================"
    echo "✓ Image pushed successfully!"
    echo "============================================"
    echo ""
    echo "Next steps:"
    echo ""
    echo "1. Update docker-compose.mlnode.yml:"
    echo "   image: ${FULL_IMAGE}"
    echo ""
    echo "2. Pull and deploy:"
    echo "   cd ../deploy/join"
    echo "   docker compose -f docker-compose.yml -f docker-compose.mlnode.yml pull"
    echo "   docker compose -f docker-compose.yml -f docker-compose.mlnode.yml up -d"
    echo ""
else
    echo ""
    echo "Skipped push."
    echo ""
    echo "To push later, run:"
    echo "  docker push ${FULL_IMAGE}"
    echo ""
fi
