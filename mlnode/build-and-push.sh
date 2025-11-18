#!/bin/bash
set -e

# Build and push MLNode Docker image
# Usage: ./build-and-push.sh [VERSION]

VERSION=${1:-"3.0.11"}
REGISTRY="ghcr.io/product-science"
IMAGE_NAME="mlnode"
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${VERSION}"

echo "============================================"
echo "Building MLNode image: ${FULL_IMAGE}"
echo "============================================"

# Build the image
cd packages/api
docker build -t ${FULL_IMAGE} -f Dockerfile ../..

echo ""
echo "============================================"
echo "Build complete: ${FULL_IMAGE}"
echo "============================================"
echo ""
echo "To push to registry, run:"
echo "  docker push ${FULL_IMAGE}"
echo ""
echo "To test locally:"
echo "  docker run --rm --gpus all ${FULL_IMAGE}"
echo ""
echo "To update docker-compose.mlnode.yml:"
echo "  Update image version to: ${FULL_IMAGE}"
echo ""

# Ask if user wants to push
read -p "Push image to registry? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Pushing ${FULL_IMAGE}..."
    docker push ${FULL_IMAGE}
    echo "✓ Image pushed successfully!"
    echo ""
    echo "Don't forget to update docker-compose.mlnode.yml:"
    echo "  image: ${FULL_IMAGE}"
fi
