#!/bin/bash
# Production Deployment Script for SaaSForge
# This is a REFERENCE script - review and customize before running!
#
# Usage:
#   ./scripts/deploy-production.sh
#
# This script is meant to be run ONCE for initial setup.
# For updates, use: git pull && docker compose -f docker/compose/docker-compose.prod.yml up -d --build

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}SaaSForge Production Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}ERROR: Do not run this script as root${NC}"
   echo "Run as a normal user with sudo privileges"
   exit 1
fi

# Function to prompt for confirmation
confirm() {
    read -p "$1 (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}Aborted by user${NC}"
        exit 1
    fi
}

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}ERROR: Docker is not installed${NC}"
    echo "Install Docker first: curl -fsSL https://get.docker.com | sudo sh"
    exit 1
fi

# Check if Docker Compose is available
if ! docker compose version &> /dev/null; then
    echo -e "${RED}ERROR: Docker Compose is not available${NC}"
    exit 1
fi

echo -e "${YELLOW}This script will:${NC}"
echo "  1. Configure Docker secrets"
echo "  2. Update production environment variables"
echo "  3. Build and start production services"
echo ""
confirm "Continue with deployment?"

# Step 1: Configure Docker Secrets
echo ""
echo -e "${GREEN}[1/3] Configuring Docker Secrets${NC}"
echo "------------------------------------"

SECRETS_DIR="docker/config/secrets"
SECRETS_EXAMPLE_DIR="docker/config/secrets.example"

if [ ! -d "$SECRETS_EXAMPLE_DIR" ]; then
    echo -e "${RED}ERROR: secrets.example directory not found${NC}"
    echo "Are you in the project root directory?"
    exit 1
fi

if [ -d "$SECRETS_DIR" ]; then
    echo -e "${YELLOW}Warning: Secrets directory already exists${NC}"
    confirm "Overwrite existing secrets?"
    rm -rf "$SECRETS_DIR"
fi

cp -r "$SECRETS_EXAMPLE_DIR" "$SECRETS_DIR"
chmod 600 "$SECRETS_DIR"/*

echo ""
echo -e "${YELLOW}Please edit the following secret files with your actual credentials:${NC}"
echo "  - $SECRETS_DIR/supabase_url"
echo "  - $SECRETS_DIR/supabase_anon_key"
echo "  - $SECRETS_DIR/supabase_service_key"
echo ""
echo -e "${YELLOW}Each file should contain ONLY the value (no quotes, no variable names)${NC}"
echo ""

# Ask user to edit secrets
read -p "Press Enter when you've edited all secret files..."

# Validate secrets are not empty
for secret_file in "$SECRETS_DIR"/*; do
    if [ -f "$secret_file" ] && [ ! -s "$secret_file" ]; then
        echo -e "${RED}ERROR: Secret file $secret_file is empty${NC}"
        exit 1
    fi
done

echo -e "${GREEN}✓ Secrets configured${NC}"

# Step 2: Configure Environment Variables
echo ""
echo -e "${GREEN}[2/3] Checking Production Environment${NC}"
echo "------------------------------------"

ENV_FILE="docker/config/env/.env.production"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}ERROR: $ENV_FILE not found${NC}"
    exit 1
fi

echo -e "${YELLOW}Important: Verify these settings in $ENV_FILE:${NC}"
echo "  - API_DOMAIN (your actual domain)"
echo "  - CORS_ALLOWED_ORIGINS (your frontend URLs)"
echo "  - APP_ENV=production"
echo "  - DISABLE_LOG_MASKING=false"
echo ""

read -p "Have you verified .env.production settings? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please edit $ENV_FILE before continuing"
    exit 1
fi

echo -e "${GREEN}✓ Environment checked${NC}"

# Step 3: Build and Start Services
echo ""
echo -e "${GREEN}[3/3] Building and Starting Services${NC}"
echo "------------------------------------"

cd docker/compose

echo "Building Docker images..."
docker compose -f docker-compose.prod.yml build

echo ""
echo "Starting production services..."
docker compose -f docker-compose.prod.yml up -d

echo ""
echo -e "${GREEN}Waiting for services to start...${NC}"
sleep 10

# Check if containers are running
RUNNING=$(docker ps --filter "name=saas_" --format "{{.Names}}" | wc -l)
EXPECTED=5  # nginx, api_1, api_2, api_3, redis

if [ "$RUNNING" -ge "$EXPECTED" ]; then
    echo -e "${GREEN}✓ All services started successfully${NC}"
else
    echo -e "${YELLOW}Warning: Expected $EXPECTED containers, found $RUNNING${NC}"
    echo "Check logs: docker compose -f docker-compose.prod.yml logs"
fi

# Display running containers
echo ""
echo "Running containers:"
docker ps --filter "name=saas_" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Final instructions
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "1. Verify health endpoint:"
echo "   curl https://your-domain.com/health"
echo ""
echo "2. Check logs:"
echo "   docker compose -f docker/compose/docker-compose.prod.yml logs -f"
echo ""
echo "3. Monitor containers:"
echo "   docker ps"
echo "   docker stats"
echo ""
echo "4. Setup SSL certificate renewal (if not done):"
echo "   See docs/DEPLOYMENT.md section 4.3"
echo ""
echo -e "${YELLOW}Important:${NC}"
echo "  - Configure firewall (UFW) if not already done"
echo "  - Setup SSL certificates with certbot"
echo "  - Configure auto-renewal cron job"
echo "  - Setup monitoring and backups"
echo ""
echo "Full documentation: docs/DEPLOYMENT.md"
echo ""
