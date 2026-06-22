"""Secrets management guide for Yoru.

Yoru uses a simple approach to secrets management:

## Development
- Use .env files in docker/config/env/ directory
- Files are loaded automatically by Pydantic settings
- Keep .env files out of git (already in .gitignore)

## Production
Several options depending on your deployment platform:

1. **Git-crypt** (Recommended for simple setups):
   - Encrypt .env.production file in the repository
   - Git-crypt automatically decrypts on authorized machines
   - Simple, works with any deployment platform

   Setup:
   ```bash
   # Install git-crypt
   brew install git-crypt  # macOS
   apt-get install git-crypt  # Ubuntu

   # Initialize in your project
   cd your-project
   git-crypt init

   # Create .gitattributes
   echo "docker/config/env/.env.production filter=git-crypt diff=git-crypt" >> .gitattributes
   git add .gitattributes
   git commit -m "Enable git-crypt for production secrets"

   # Lock the repository
   git-crypt lock
   ```

2. **Docker Secrets** (For Docker Swarm):
   - Use Docker's built-in secrets management
   - Secrets are encrypted and only available to containers

   ```bash
   # Create secret
   docker secret create supabase_key /path/to/key/file

   # Reference in docker-compose:
   # secrets:
   #   - supabase_key
   ```

3. **Kubernetes Secrets** (For Kubernetes deployments):
   - Use Kubernetes native secret management
   - Secrets are base64 encoded and can be encrypted at rest

   ```bash
   # Create secret
   kubectl create secret generic supabase-creds \
     --from-literal=url='https://...' \
     --from-literal=key='...'
   ```

4. **Environment Variables** (For PaaS platforms):
   - Set environment variables via platform UI/CLI
   - Works with Heroku, Railway, Render, Fly.io, etc.
   - Variables are injected at runtime

5. **CI/CD Secrets** (For GitHub Actions, GitLab CI, etc.):
   - Store secrets in your CI/CD platform
   - Inject as environment variables during deployment

## Best Practices

1. **Never commit unencrypted secrets** to git
2. **Use different secrets** for dev, staging, and production
3. **Rotate secrets regularly** (at least every 90 days)
4. **Limit secret access** to only services that need them
5. **Monitor secret usage** and audit access logs

## No External Dependencies Required

Unlike some frameworks, Yoru doesn't force you to use:
- AWS Secrets Manager (costly, AWS-specific)
- HashiCorp Vault (complex setup, overkill for most projects)
- Cloud-specific key management services

Choose the approach that fits your deployment platform and team size.
"""

# This file is intentionally minimal - secrets are managed via:
# 1. Environment variables (loaded by Pydantic settings)
# 2. .env files (development)
# 3. Encrypted .env files (git-crypt for production)
# 4. Platform-specific secret injection (Docker, K8s, PaaS)

# No code needed - Pydantic handles everything automatically!
