#!/usr/bin/env bash
# deploy.sh — Pull latest code and rebuild the backend service.
# Run from the GitHub Actions self-hosted runner or manually via Git Bash.
# Usage: bash scripts/deploy.sh

set -euo pipefail

PROJECT_DIR="/d/Claude/pa"

echo "==> Changing to project directory: $PROJECT_DIR"
cd "$PROJECT_DIR"

echo "==> Fetching latest changes from origin/main"
git fetch origin main

echo "==> Resetting to origin/main (hard)"
git reset --hard origin/main

echo "==> Rebuilding and restarting backend container"
docker compose up --build -d backend

echo "==> Tailing backend logs for 10s to confirm startup"
sleep 3
docker logs pa-backend --tail 30

echo ""
echo "==> Deploy complete. Container status:"
docker compose ps backend
