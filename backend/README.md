# Yoru Project

A production-ready SaaS application built with FastAPI, featuring multi-tenancy, RBAC, rate limiting, and comprehensive observability.

## Table of Contents

- [Environment Configuration](#environment-configuration)
- [Quick Start](#quick-start)
- [Development Environment](#development-environment)
- [Production Environment](#production-environment)
- [Production Deployment](#production-deployment)
- [Project Structure](#project-structure)
- [Features](#features)
- [Architecture](#architecture)

## Environment Configuration

Yoru supports separate configurations for development and production environments, each optimized for its specific use case.

### Environment Comparison

| Feature | Development | Production |
|---------|-------------|------------|
| **API Instances** | 1 (single) | 3 (replicated for HA) |
| **Reverse Proxy** | None (direct access) | Nginx with SSL |
| **Port Access** | 8002 (exposed) | 443 (via nginx) |
| **Log Level** | DEBUG | INFO |
| **Log Masking** | Disabled | Enabled (BLOC-008) |
| **Rate Limiting** | Relaxed | Strict |
| **Secrets Backend** | .env files | git-crypt or Docker secrets |
| **SSL/TLS** | Optional | Required |
| **Domain Pattern** | *.dev.*.local | *.prod.*.com |
| **CORS** | Permissive (localhost) | Restrictive (whitelist) |

### Configuration Files

Environment-specific settings are located in:

- **Development**: `docker/config/env/.env.development`
- **Production**: `docker/config/env/.env.production`
- **Shared**: `docker/config/env/.env.shared`
- **Example**: `.env.example` (shows all available options)

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Supabase account with project credentials
- GitHub CLI (`gh`) for repository setup (optional)

### Create a New Project

```bash
# Install forge CLI
pip install -e .

# Create a new project (copies the complete template)
forge smith my-saas-project

# The forge copies everything - you configure the environment after
```

## Development Environment

The development environment is optimized for local development with relaxed security constraints and verbose logging.

### Setup

1. **Copy environment file**:
   ```bash
   cp docker/config/env/.env.development .env
   ```

2. **Configure Supabase credentials** in `.env`:
   ```bash
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_ANON_KEY=your-anon-key
   SUPABASE_SERVICE_KEY=your-service-key
   ```

3. **Add local DNS entries** (optional, for domain-based access):

   Add to `/etc/hosts` (macOS/Linux) or `C:\Windows\System32\drivers\etc\hosts` (Windows):
   ```
   127.0.0.1 api.dev.myproject.local
   127.0.0.1 worker.dev.myproject.local
   ```

4. **Start services**:
   ```bash
   docker compose -f docker/compose/docker-compose.dev.yml up

   # Or use the default compose file (aliases to dev)
   docker compose up
   ```

### Access Points

- **API**: http://localhost:8002
- **API (domain)**: http://api.dev.myproject.local:8002 (after hosts setup)
- **API Docs**: http://localhost:8002/docs
- **Jaeger UI** (tracing): http://localhost:16686

### Development Features

- Single API instance for simple debugging
- Debug logging enabled (`LOG_LEVEL=DEBUG`)
- Log masking disabled for easier troubleshooting
- No rate limiting (set to very high values)
- Hot reload enabled (code changes reflect immediately)
- Direct port access without reverse proxy
- Relaxed CORS policy (allows localhost origins)

### Common Development Commands

```bash
# Start in detached mode
docker compose -f docker/compose/docker-compose.dev.yml up -d

# View logs
docker compose -f docker/compose/docker-compose.dev.yml logs -f api

# Stop services
docker compose -f docker/compose/docker-compose.dev.yml down

# Restart API only
docker compose -f docker/compose/docker-compose.dev.yml restart api

# Access API container shell
docker exec -it saas_api bash

# Run tests inside container
docker exec saas_api pytest
```

## Production Environment

The production environment is hardened for deployment with SSL, load balancing, strict rate limiting, and production logging.

### Setup

**IMPORTANT**: Complete these steps before starting production services.

#### 1. Configure SSL Certificates

Generate SSL certificates using Let's Encrypt:

```bash
# Install certbot
sudo apt-get install certbot

# Generate certificate (replace with your domain)
sudo certbot certonly --standalone -d api.prod.yourdomain.com

# Copy certificates to nginx
sudo cp /etc/letsencrypt/live/api.prod.yourdomain.com/fullchain.pem \
  docker/config/nginx/ssl/server.crt
sudo cp /etc/letsencrypt/live/api.prod.yourdomain.com/privkey.pem \
  docker/config/nginx/ssl/server.key

# Set permissions
chmod 644 docker/config/nginx/ssl/server.crt
chmod 600 docker/config/nginx/ssl/server.key
```

See `docker/config/nginx/ssl/README.md` for detailed SSL setup instructions.

#### 2. Configure DNS

Set up DNS A records pointing to your server:

```
api.prod.yourdomain.com     → <your-server-ip>
worker.prod.yourdomain.com  → <your-server-ip>
```

#### 3. Update Production Configuration

Edit `docker/config/env/.env.production`:

```bash
# Update domains (replace example.com with your actual domain)
API_DOMAIN=api.prod.yourdomain.com
WORKER_DOMAIN=worker.prod.yourdomain.com
API_URL=https://api.prod.yourdomain.com

# Update CORS origins (whitelist your frontend domains)
CORS_ORIGINS=https://app.yourdomain.com,https://www.yourdomain.com

# Configure Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-key

# For secrets, see Step 4 below (git-crypt, Docker secrets, or platform vars)
```

#### 4. Setup Docker Secrets (Recommended)

**Docker Compose secrets** are the simplest and most secure way to handle production credentials:

```bash
# 1. Copy example secrets to create your actual secret files
cp -r docker/config/secrets.example docker/config/secrets

# 2. Edit each file with your real Supabase credentials
nano docker/config/secrets/supabase_url
nano docker/config/secrets/supabase_anon_key
nano docker/config/secrets/supabase_service_key

# 3. Secure the files
chmod 600 docker/config/secrets/*
```

**That's it!** The secrets are automatically loaded by Docker Compose.

See: https://docs.docker.com/compose/how-tos/use-secrets/

**Alternative Approaches:**

<details>
<summary><strong>Option B: Git-crypt (encrypted in repository)</strong></summary>

Encrypt your `.env.production` file in the repository:

```bash
# Install git-crypt
brew install git-crypt  # macOS
apt-get install git-crypt  # Ubuntu

# Initialize and encrypt
git-crypt init
echo "docker/config/env/.env.production filter=git-crypt diff=git-crypt" >> .gitattributes
git add .gitattributes docker/config/env/.env.production
git commit -m "Encrypt production secrets"

# Export key for deployment servers
git-crypt export-key /path/to/keyfile

# On servers, unlock with:
git-crypt unlock /path/to/keyfile
```
</details>

<details>
<summary><strong>Option C: Platform Environment Variables (PaaS)</strong></summary>

If deploying to Heroku, Fly.io, Railway, or Render:
- Set environment variables via platform UI/CLI
- Variables are injected at runtime
- No files needed
</details>

#### 5. Start Production Services

```bash
cd docker/compose
docker compose -f docker-compose.prod.yml up -d
```

### Production Features

- **High Availability**: 3 API replicas with nginx load balancing
- **SSL/TLS**: Automatic HTTPS with TLS 1.2+ (BLOC-002, BLOC-003)
- **Security Headers**: HSTS, CSP, X-Frame-Options, etc. (BLOC-005)
- **Rate Limiting**: Strict per-plan limits (BLOC-007)
- **Log Masking**: Enabled for PII protection (BLOC-008)
- **Secure Secrets**: git-crypt, Docker secrets, or platform injection
- **Graceful Shutdown**: 30-second timeout for clean connection termination
- **Health Checks**: Automatic container restart on failure
- **Request Timeouts**: 30s default, 300s for LLM endpoints (BLOC-004)

### Production Commands

```bash
# Start services
docker compose -f docker/compose/docker-compose.prod.yml up -d

# View logs
docker compose -f docker/compose/docker-compose.prod.yml logs -f

# Check service health
docker compose -f docker/compose/docker-compose.prod.yml ps

# View nginx logs
docker logs saas_nginx

# Restart nginx (e.g., after SSL cert renewal)
docker compose -f docker/compose/docker-compose.prod.yml restart nginx

# Scale API instances (if needed)
# Note: By default, 3 replicas are pre-configured

# Stop all services
docker compose -f docker/compose/docker-compose.prod.yml down
```

### SSL Certificate Renewal

Let's Encrypt certificates expire every 90 days. Set up automatic renewal:

```bash
# Test renewal
sudo certbot renew --dry-run

# Add to crontab for auto-renewal
sudo crontab -e

# Add this line (runs daily at midnight)
0 0 * * * certbot renew --quiet --deploy-hook "cd /path/to/project && docker compose -f docker/compose/docker-compose.prod.yml restart nginx"
```

### Monitoring Production

1. **Health Check Endpoint**: `https://api.prod.yourdomain.com/health`
2. **Metrics Endpoint**: `https://api.prod.yourdomain.com/metrics` (internal)
3. **Nginx Access Logs**: JSON formatted in `nginx_logs` volume
4. **API Logs**: JSON formatted via Docker logging driver
5. **Distributed Tracing**: Jaeger UI (if enabled)

## Production Deployment

For detailed production deployment instructions, see:

📚 **[Complete Deployment Guide](docs/DEPLOYMENT.md)**

The deployment guide covers:
- Server preparation (Ubuntu/Debian)
- Security hardening (firewall, fail2ban, SSH)
- DNS configuration
- SSL certificate setup (Let's Encrypt)
- Docker secrets configuration
- Application deployment
- Monitoring and maintenance
- Troubleshooting

### Quick Deployment Scripts

Helper scripts are available in `scripts/`:

```bash
# 1. Harden server security (run on fresh server)
sudo ./scripts/setup-server-security.sh

# 2. Deploy application
./scripts/deploy-production.sh
```

⚠️ **Important:** Review scripts before running. These are reference implementations.

**Recommended:** Follow the manual deployment guide in `docs/DEPLOYMENT.md` to understand each step.

## Project Structure

```
.
├── apps/
│   ├── api/              # FastAPI application
│   │   ├── api/
│   │   │   ├── config/   # Centralized settings
│   │   │   ├── middleware/
│   │   │   ├── routers/
│   │   │   ├── models/
│   │   │   └── services/
│   │   └── main.py       # Application entry point
│   ├── worker/           # Background worker (webhook processing)
│   └── autoscaler/       # Horizontal pod autoscaler (k8s)
├── docker/
│   ├── compose/
│   │   ├── services/     # Service-specific compose files
│   │   ├── docker-compose.dev.yml
│   │   ├── docker-compose.prod.yml
│   │   └── docker-compose.yml  # Alias for dev
│   ├── config/
│   │   ├── env/          # Environment-specific .env files
│   │   └── nginx/        # Nginx configuration + SSL
│   └── image/            # Dockerfiles
├── libs/                 # Shared libraries
│   ├── redis/
│   ├── supabase/
│   ├── email/
│   ├── tracing/
│   └── resilience/
└── migrations/           # Database migrations

```

## Features

### Authentication & Authorization

- Supabase Auth integration
- JWT token-based authentication
- Row Level Security (RLS) policies
- Multi-tenancy with organization isolation
- Role-Based Access Control (RBAC)
- Group-based permissions

### API Features

- RESTful API with OpenAPI documentation
- Automatic request/response validation
- Rate limiting per subscription plan
- Request size limits (10MB default, 50MB for uploads)
- Request timeout enforcement (30s default, 300s for LLM operations)
- CORS protection with configurable origins
- Security headers (HSTS, CSP, X-Frame-Options)

### Multi-Tenancy

- Organization-based isolation
- Invitation system for team members
- Plan-based feature access
- Subscription management
- Usage tracking and limits

### Observability

- Structured JSON logging
- Distributed tracing (OpenTelemetry + Jaeger)
- Request correlation IDs
- Health check endpoints
- Metrics endpoint (Prometheus-compatible)

### Resilience

- Circuit breakers for external services
- Graceful shutdown handling
- Automatic retries with exponential backoff
- Connection pooling
- Rate limiting and throttling

### Developer Experience

- Hot reload in development
- Comprehensive error messages
- API documentation (Swagger UI)
- Type hints throughout
- Cursor AI rules for code quality

## Architecture

### Development Architecture

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ HTTP :8002
       │
┌──────▼──────┐
│  API (1x)   │
└──────┬──────┘
       │
   ┌───┴───┐
   │       │
┌──▼──┐ ┌─▼─────┐
│Redis│ │Supabase│
└─────┘ └────────┘
```

### Production Architecture

```
                      ┌─────────────┐
                      │   Client    │
                      └──────┬──────┘
                             │ HTTPS :443
                             │
                      ┌──────▼──────┐
                      │    Nginx    │
                      │ (SSL + LB)  │
                      └──────┬──────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐┌─────▼──────┐┌─────▼──────┐
       │  API (1/3)  ││  API (2/3) ││  API (3/3) │
       └──────┬──────┘└─────┬──────┘└─────┬──────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                         ┌───┴───┐
                         │       │
                      ┌──▼──┐ ┌─▼─────┐
                      │Redis│ │Supabase│
                      └─────┘ └────────┘
```

### Middleware Stack (Execution Order)

1. **CORS** - Cross-origin request handling
2. **Security** - Security headers
3. **FeatureFlags** - Kill switches and maintenance mode
4. **RequestSizeLimit** - Reject oversized payloads
5. **Tracing** - Generate correlation IDs and spans
6. **Timeout** - Enforce request timeouts
7. **Logging** - Request/response logging
8. **OrganizationContext** - Extract user/org from JWT
9. **RBAC** - Permission checking
10. **RateLimit** - Per-user/plan rate limiting

## Compliance

This project follows strict cursor rules for:

- **Security**: BLOC-008 (log masking), BLOC-002/003 (TLS), BLOC-005 (security headers)
- **Scalability**: Horizontal scaling, stateless API design
- **Observability**: Structured logging, distributed tracing
- **Architecture**: BLOC-ARCH-005 (centralized configuration)
- **Infrastructure**: Separate .env files, encrypted secrets (git-crypt/Docker/platform)

## License

[Your License Here]

## Support

For issues and questions:
- GitHub Issues: [your-repo-url]
- Documentation: [your-docs-url]
