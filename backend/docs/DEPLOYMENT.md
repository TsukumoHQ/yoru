# Production Deployment Guide

Complete guide for deploying Yoru to production on Ubuntu/Debian servers with Docker Compose.

## Table of Contents

- [Prerequisites](#prerequisites)
- [1. Server Preparation](#1-server-preparation)
- [2. Security Hardening](#2-security-hardening)
- [3. DNS Configuration](#3-dns-configuration)
- [4. SSL Certificates](#4-ssl-certificates)
- [5. Application Deployment](#5-application-deployment)
- [6. Monitoring & Maintenance](#6-monitoring--maintenance)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- Ubuntu 22.04+ or Debian 11+ server
- Root/sudo access
- Domain name pointing to server IP
- Basic command line knowledge

**Minimum Server Requirements:**
- 2 CPU cores
- 4GB RAM
- 20GB disk space
- IPv4 and IPv6 connectivity (recommended)

---

## 1. Server Preparation

### 1.1 System Update

```bash
# Update package lists and upgrade system
sudo apt update && sudo apt upgrade -y

# Install essential tools
sudo apt install -y curl wget git htop vim ufw fail2ban
```

### 1.2 Install Docker

```bash
# Install Docker (official script)
curl -fsSL https://get.docker.com | sudo sh

# Add current user to docker group (no sudo needed)
sudo usermod -aG docker $USER

# Logout and login again for group changes to take effect
exit
# Reconnect via SSH
```

**Verify installation:**
```bash
docker --version
docker compose version
```

**References:**
- [Docker Installation Guide](https://docs.docker.com/engine/install/ubuntu/)

### 1.3 Create Application User (Optional but Recommended)

```bash
# Create dedicated user for the application
sudo useradd -m -s /bin/bash -G docker yoru
sudo su - yoru
```

---

## 2. Security Hardening

### 2.1 Configure Firewall (UFW)

```bash
# Deny all incoming, allow all outgoing
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (IMPORTANT: don't lock yourself out!)
sudo ufw allow 22/tcp

# Allow HTTP and HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw --force enable

# Verify status
sudo ufw status verbose
```

**⚠️ WARNING:** Make sure SSH (port 22) is allowed before enabling UFW!

### 2.2 Configure Fail2Ban

Protects against brute-force SSH attacks.

```bash
# Install fail2ban
sudo apt install -y fail2ban

# Create local configuration
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local

# Edit configuration
sudo nano /etc/fail2ban/jail.local
```

**Recommended settings:**
```ini
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = 22
```

```bash
# Start and enable fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# Check status
sudo fail2ban-client status
```

**References:**
- [Fail2Ban Documentation](https://www.fail2ban.org/wiki/index.php/Main_Page)

### 2.3 SSH Hardening

```bash
# Generate SSH key for GitHub (if deploying from private repo)
ssh-keygen -t ed25519 -C "$(whoami)@$(hostname)" -f ~/.ssh/id_ed25519 -N ""

# Add GitHub to known hosts
ssh-keyscan github.com >> ~/.ssh/known_hosts

# Display public key (add to GitHub: Settings → SSH and GPG keys)
cat ~/.ssh/id_ed25519.pub
```

**Recommended `/etc/ssh/sshd_config` settings:**
```bash
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
X11Forwarding no
```

```bash
# Restart SSH after changes
sudo systemctl restart sshd
```

### 2.4 Automatic Security Updates

```bash
# Install unattended-upgrades
sudo apt install -y unattended-upgrades

# Enable automatic security updates
sudo dpkg-reconfigure -plow unattended-upgrades
```

**References:**
- [Ubuntu Security](https://ubuntu.com/security)
- [SSH Hardening Guide](https://www.ssh.com/academy/ssh/sshd_config)

---

## 3. DNS Configuration

### 3.1 Get Server IP Addresses

```bash
# Get IPv4 address
hostname -I | awk '{print $1}'

# Get IPv6 address (if available)
ip -6 addr show | grep "inet6" | grep -v "::1" | grep -v "fe80" | awk '{print $2}' | cut -d'/' -f1 | head -1
```

### 3.2 Configure DNS Records

Add these records in your DNS provider (Cloudflare, Namecheap, etc.):

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | api.prod | `<your-ipv4>` | 300 |
| AAAA | api.prod | `<your-ipv6>` | 300 |

**Example for domain `example.com`:**
- `api.prod.example.com` → Your server IP

**Verify DNS propagation:**
```bash
# Check A record
dig +short api.prod.example.com A

# Check AAAA record (IPv6)
dig +short api.prod.example.com AAAA

# Or use online tools:
# https://dnschecker.org/
```

**Wait for DNS propagation** (usually 5-15 minutes, can take up to 48 hours)

---

## 4. SSL Certificates

### 4.1 Install Certbot

```bash
# Install Certbot
sudo apt install -y certbot

# Create webroot directory for renewal
sudo mkdir -p /var/www/certbot
```

### 4.2 Obtain SSL Certificate

**Method 1: Standalone (Recommended for initial setup)**

```bash
# Set your domain and email
export DOMAIN=api.prod.example.com
export EMAIL=admin@example.com

# Stop any service on port 80
sudo systemctl stop nginx 2>/dev/null || true

# Get certificate
sudo certbot certonly \
  --standalone \
  -d $DOMAIN \
  --non-interactive \
  --agree-tos \
  --email $EMAIL

# Copy certificates to project directory
sudo cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem \
  /home/ubuntu/your-project/docker/config/nginx/ssl/server.crt

sudo cp /etc/letsencrypt/live/$DOMAIN/privkey.pem \
  /home/ubuntu/your-project/docker/config/nginx/ssl/server.key

# Set permissions
sudo chmod 644 /home/ubuntu/your-project/docker/config/nginx/ssl/server.crt
sudo chmod 600 /home/ubuntu/your-project/docker/config/nginx/ssl/server.key
```

**Method 2: Webroot (For renewal without downtime)**

```bash
sudo certbot certonly \
  --webroot \
  -w /var/www/certbot \
  -d $DOMAIN \
  --non-interactive \
  --agree-tos \
  --email $EMAIL
```

### 4.3 Auto-Renewal

```bash
# Test renewal
sudo certbot renew --dry-run

# Setup auto-renewal cron job
sudo crontab -e
```

**Add this line:**
```cron
# Renew SSL certificates daily at 3am, restart nginx if renewed
0 3 * * * certbot renew --quiet --deploy-hook "cd /home/ubuntu/your-project/docker/compose && docker compose -f docker-compose.prod.yml restart nginx"
```

**References:**
- [Certbot Documentation](https://certbot.eff.org/)
- [Let's Encrypt](https://letsencrypt.org/)

---

## 5. Application Deployment

### 5.1 Clone Repository

```bash
# Navigate to home directory
cd ~

# Clone your project (use SSH if private repo)
git clone git@github.com:your-org/your-project.git
cd your-project
```

### 5.2 Configure Docker Secrets

```bash
# Create secrets directory from examples
cp -r docker/config/secrets.example docker/config/secrets

# Edit each secret file with your production credentials
nano docker/config/secrets/supabase_url
nano docker/config/secrets/supabase_anon_key
nano docker/config/secrets/supabase_service_key

# Secure the files
chmod 600 docker/config/secrets/*
chown $USER:$USER docker/config/secrets/*
```

**Secrets file format (plain text, no quotes):**
```
https://your-project.supabase.co
```

### 5.3 Configure Environment Variables

```bash
# Edit production environment file
nano docker/config/env/.env.production
```

**Update these values:**
```bash
# Domains (replace with your actual domain)
API_DOMAIN=api.prod.example.com
API_URL=https://api.prod.example.com

# CORS (whitelist your frontend domains)
CORS_ORIGINS=https://app.example.com,https://www.example.com

# Ensure production settings
APP_ENV=production
LOG_LEVEL=INFO
DISABLE_LOG_MASKING=false
ENABLE_RATE_LIMITING=true
```

### 5.4 Build and Start Services

```bash
cd docker/compose

# Build images
docker compose -f docker-compose.prod.yml build

# Start all services in detached mode
docker compose -f docker-compose.prod.yml up -d

# View logs
docker compose -f docker-compose.prod.yml logs -f
```

### 5.5 Verify Deployment

```bash
# Check running containers
docker ps

# Expected output: nginx, api_1, api_2, api_3, redis, worker

# Test health endpoint
curl -k https://api.prod.example.com/health

# Expected: {"status":"healthy"}

# Check SSL certificate
curl -vI https://api.prod.example.com 2>&1 | grep "SSL certificate verify ok"
```

### 5.6 Optional: API Documentation Password

Protect your API docs with basic auth:

```bash
# Install apache2-utils
sudo apt install -y apache2-utils

# Create htpasswd directory
sudo mkdir -p /etc/nginx/.htpasswd

# Create password file (replace 'api' with your username)
sudo htpasswd -c /etc/nginx/.htpasswd/api-docs api

# Add to nginx.prod.conf in /docs location:
# auth_basic "API Documentation";
# auth_basic_user_file /etc/nginx/.htpasswd/api-docs;
```

---

## 6. Monitoring & Maintenance

### 6.1 Health Checks

```bash
# Check all containers are running
docker ps

# Check API health
curl https://api.prod.example.com/health

# Check nginx status
docker logs saas_nginx

# Check API logs
docker logs saas_api_1
docker logs saas_api_2
docker logs saas_api_3
```

### 6.2 View Logs

```bash
cd docker/compose

# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker compose -f docker-compose.prod.yml logs -f api_1

# Last 100 lines
docker compose -f docker-compose.prod.yml logs --tail=100

# Nginx access logs
docker exec saas_nginx tail -f /var/log/nginx/access.log
```

### 6.3 Resource Monitoring

```bash
# System resources
htop

# Docker stats (live)
docker stats

# Disk usage
df -h
docker system df
```

### 6.4 Updates and Restarts

**Pull latest changes:**
```bash
cd ~/your-project
git pull origin main

# Rebuild and restart
cd docker/compose
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

**Restart specific service:**
```bash
docker compose -f docker-compose.prod.yml restart api_1
docker compose -f docker-compose.prod.yml restart nginx
```

**Restart all services:**
```bash
docker compose -f docker-compose.prod.yml restart
```

### 6.5 Backup Strategy

**Database (Supabase):**
- Supabase handles backups automatically
- Export SQL dumps regularly via Supabase dashboard

**Application data:**
```bash
# Backup Redis data (if using persistence)
docker exec saas_redis redis-cli BGSAVE

# Backup environment files and secrets
tar -czf backup-$(date +%Y%m%d).tar.gz \
  docker/config/env/ \
  docker/config/secrets/ \
  docker/config/nginx/ssl/
```

### 6.6 Log Rotation

Docker handles log rotation automatically via `logging` config in docker-compose files:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```

**Manual log cleanup:**
```bash
# Clean old logs
docker system prune -f

# Remove all stopped containers and unused images
docker system prune -a -f
```

---

## Troubleshooting

### SSL Certificate Issues

**Error: Certificate verify failed**
```bash
# Check certificate files exist
ls -la docker/config/nginx/ssl/

# Verify certificate
openssl x509 -in docker/config/nginx/ssl/server.crt -text -noout

# Check certificate matches key
openssl x509 -noout -modulus -in docker/config/nginx/ssl/server.crt | openssl md5
openssl rsa -noout -modulus -in docker/config/nginx/ssl/server.key | openssl md5
# MD5 hashes should match
```

### Container Won't Start

```bash
# Check logs
docker logs saas_api_1

# Check if port is already in use
sudo netstat -tulpn | grep :443
sudo netstat -tulpn | grep :80

# Check Docker secrets are readable
docker exec saas_api_1 cat /run/secrets/supabase_url
```

### High Memory Usage

```bash
# Check container stats
docker stats

# Restart container consuming too much memory
docker compose -f docker-compose.prod.yml restart api_1
```

### Connection Refused

```bash
# Check firewall
sudo ufw status

# Check nginx is running
docker ps | grep nginx

# Check nginx config is valid
docker exec saas_nginx nginx -t

# Check DNS resolution
dig +short api.prod.example.com
```

### Database Connection Issues

```bash
# Verify Supabase credentials
docker exec saas_api_1 cat /run/secrets/supabase_url

# Check API can connect to Supabase
docker logs saas_api_1 | grep -i supabase

# Test connection manually
docker exec saas_api_1 curl https://your-project.supabase.co
```

---

## Quick Reference Commands

```bash
# Start production
cd ~/your-project/docker/compose
docker compose -f docker-compose.prod.yml up -d

# Stop production
docker compose -f docker-compose.prod.yml down

# Restart
docker compose -f docker-compose.prod.yml restart

# Update from git
cd ~/your-project && git pull
cd docker/compose
docker compose -f docker-compose.prod.yml up -d --build

# View logs
docker compose -f docker-compose.prod.yml logs -f

# Check status
docker ps
curl https://api.prod.example.com/health

# Renew SSL
sudo certbot renew
docker compose -f docker-compose.prod.yml restart nginx
```

---

## Security Checklist

Before going live, verify:

- [ ] Firewall (UFW) is enabled and configured
- [ ] Fail2Ban is running
- [ ] SSH key authentication only (no passwords)
- [ ] SSL certificate is valid and auto-renewing
- [ ] Docker secrets are used (not .env files)
- [ ] Log masking is enabled (`DISABLE_LOG_MASKING=false`)
- [ ] Rate limiting is enabled
- [ ] CORS is restricted to your frontend domains
- [ ] All secrets have strong, unique values
- [ ] Supabase RLS policies are enabled
- [ ] Regular backups are configured
- [ ] Monitoring is set up

---

## Additional Resources

- [Docker Production Best Practices](https://docs.docker.com/config/containers/resource_constraints/)
- [Nginx Security Guide](https://nginx.org/en/docs/http/configuring_https_servers.html)
- [Ubuntu Security Hardening](https://ubuntu.com/security/certifications/docs/disa-stig)
- [Let's Encrypt Best Practices](https://letsencrypt.org/docs/)
- [Fail2Ban Configuration](https://www.fail2ban.org/wiki/index.php/MANUAL_0_8)

---

## Support

For issues specific to Yoru:
- GitHub Issues: [your-repo-url]
- Documentation: [your-docs-url]

For infrastructure issues:
- Docker: https://docs.docker.com/
- Ubuntu: https://help.ubuntu.com/
- DigitalOcean Tutorials: https://www.digitalocean.com/community/tutorials
