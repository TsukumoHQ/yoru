# Email Service

Robust email service for Yoru with multi-provider support (SMTP, SendGrid, Resend), Jinja2 templates, structured logging, and automatic retry.

## Features

- ✅ **Multi-Provider Support**: SMTP, SendGrid, Resend
- ✅ **Template Engine**: Jinja2-based HTML email templates with inheritance
- ✅ **Retry Logic**: Automatic retry with exponential backoff
- ✅ **Fail-Safe Design**: Email failures logged but don't block operations
- ✅ **Correlation ID Tracking**: Full request tracing through logs
- ✅ **Async/Await**: Modern async Python for performance
- ✅ **Type Safety**: Full type hints following modern Python standards

## Quick Start

### 1. Install Dependencies

Dependencies are already in `template/requirements/base.txt`:

```bash
pip install aiosmtplib Jinja2 httpx
```

### 2. Configure Environment Variables

Add to your `.env` or `.env.api`:

```bash
# Email Configuration
EMAIL_PROVIDER=smtp                    # smtp|sendgrid|resend

# SMTP Configuration (Gmail example)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-app@gmail.com
SMTP_PASSWORD=your-app-password        # Gmail: Use App Password (requires 2FA)
SMTP_USE_TLS=true
SMTP_FROM_EMAIL=noreply@yourapp.com
SMTP_FROM_NAME=YourApp

# Template Context
EMAIL_BRAND_NAME=Yoru
EMAIL_SUPPORT_EMAIL=support@yoru.sh
EMAIL_COMPANY_ADDRESS=123 Main St, City, Country

# App URLs
APP_URL=http://localhost:3000          # Frontend URL for email links

# Behavior
EMAIL_RETRY_ATTEMPTS=3
EMAIL_TIMEOUT=30
```

### 3. Gmail Setup (Recommended for Development)

1. Enable 2-Factor Authentication on your Google Account
2. Go to https://myaccount.google.com/apppasswords
3. Create a new App Password for "Mail"
4. Use this password in `SMTP_PASSWORD`

### 4. Basic Usage

```python
from libs.email import EmailManager

# Initialize (uses environment config)
email = EmailManager()

# Send templated email
await email.send_template(
    template_name="invitation.html",
    to_email="user@example.com",
    subject="You've been invited!",
    context={
        "inviter_name": "John Doe",
        "invite_url": "https://yourapp.com/accept?token=abc123",
        "expires_at": "December 31, 2024",
        "message": "Join our team!",
    },
    correlation_id="req-123",
)

# Send simple email (no template)
await email.send_simple(
    to_email="user@example.com",
    subject="Test Email",
    html_body="<h1>Hello World</h1>",
)
```

## Available Templates

All templates extend `base.html` for consistent branding.

### 1. `invitation.html`
User invitation email.

**Variables:**
- `inviter_name` (str): Name of person sending invite
- `inviter_email` (str, optional): Email of inviter
- `invite_url` (str): Acceptance link
- `expires_at` (str): Expiration date
- `message` (str, optional): Personal message

### 2. `organization_invitation.html`
Organization/team invitation.

**Variables:**
- `org_name` (str): Organization name
- `inviter_name` (str): Name of inviter
- `role` (str): Role being offered
- `invite_url` (str): Acceptance link

### 3. `signup_confirmation.html`
Welcome email for new signups.

**Variables:**
- `user_name` (str): User's first name
- `user_email` (str): User's email
- `dashboard_url` (str): Dashboard link

### Global Variables (Auto-Injected)

All templates automatically receive:
- `brand_name`: From `EMAIL_BRAND_NAME`
- `support_email`: From `EMAIL_SUPPORT_EMAIL`
- `company_address`: From `EMAIL_COMPANY_ADDRESS`

## Creating Custom Templates

1. Create new `.html` file in `template/libs/email/templates/`
2. Extend base template:

```html
{% extends "base.html" %}

{% block title %}Your Custom Title{% endblock %}

{% block content %}
<h2>Custom Email</h2>
<p>Hello {{ user_name }},</p>
<p>{{ custom_message }}</p>
<a href="{{ action_url }}" class="button">Take Action</a>
{% endblock %}
```

3. Use in code:

```python
await email.send_template(
    template_name="your_template.html",
    to_email="user@example.com",
    subject="Custom Subject",
    context={
        "user_name": "John",
        "custom_message": "Your custom content",
        "action_url": "https://...",
    },
)
```

## Provider Comparison

| Feature | SMTP | SendGrid | Resend |
|---------|------|----------|--------|
| **Best For** | Development | High volume | Modern SaaS |
| **Rate Limit (Free)** | ~100/day | 100/day | 3,000/month |
| **Setup Complexity** | Simple | Medium | Simple |
| **Analytics** | No | Yes | Yes |
| **Bounce Handling** | No | Yes | Yes |
| **Price (Pro)** | Free | $89/month | $20/month |
| **Developer Experience** | Basic | Good | Excellent |

## Provider Configuration

### SMTP (Gmail, Outlook, etc.)

```bash
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com       # or smtp.office365.com
SMTP_PORT=587                  # or 465 for SSL
SMTP_USERNAME=your@email.com
SMTP_PASSWORD=app-password
SMTP_USE_TLS=true
SMTP_FROM_EMAIL=noreply@yourapp.com
SMTP_FROM_NAME=YourApp
```

### SendGrid (Recommended for Production)

```bash
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.xxx...
SENDGRID_FROM_EMAIL=noreply@yourapp.com  # Must be verified domain
SENDGRID_FROM_NAME=YourApp

# Optional
EMAIL_BRAND_NAME=YourApp
EMAIL_SUPPORT_EMAIL=support@yourapp.com
APP_URL=https://yourapp.com
EMAIL_TIMEOUT=30
```

**Setup Steps:**
1. Sign up at https://sendgrid.com
2. Verify your sender domain
3. Create API Key with "Mail Send" permissions
4. Copy API key to `SENDGRID_API_KEY`

**Rate Limits:**
- Free: 100 emails/day
- Essentials: 50,000 emails/month ($19.95)
- Pro: 1.5M emails/month ($89.95)

### Resend (Modern Alternative)

```bash
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_xxx...
RESEND_FROM_EMAIL=noreply@yourapp.com  # Must be verified domain

# Optional
EMAIL_BRAND_NAME=YourApp
EMAIL_SUPPORT_EMAIL=support@yourapp.com
APP_URL=https://yourapp.com
EMAIL_TIMEOUT=30
```

**Setup Steps:**
1. Sign up at https://resend.com
2. Add and verify your domain
3. Create API Key
4. Copy API key to `RESEND_API_KEY`

**Rate Limits:**
- Free: 3,000 emails/month
- Pro: 50,000 emails/month ($20)
- Business: Custom pricing

## Error Handling

The email service follows a **fail-safe pattern**:

```python
# In services (invitation, auth, etc.)
try:
    await self.email_manager.send_template(...)
    logger.log_info("Email sent successfully")
except Exception as e:
    # FAIL-SAFE: Log warning but don't fail the operation
    logger.log_warning(
        "Failed to send email - operation succeeded but email not sent",
        {"email_error": str(e)}
    )
```

**Philosophy**: Email failures should NEVER block critical operations like signup or invitation creation.

## Logging

All email operations are logged with correlation IDs:

```json
{
  "operation": "send_templated_email",
  "component": "EmailManager",
  "correlation_id": "req-abc-123",
  "template_name": "invitation.html",
  "to_email": "user@example.com",
  "subject": "You've been invited!",
  "attempt": 1,
  "max_attempts": 3
}
```

## Retry Behavior

Failed sends are retried with exponential backoff:

- Attempt 1: Immediate
- Attempt 2: 2 second delay
- Attempt 3: 4 second delay
- Attempt 4+: 8 second delay

Configure with `EMAIL_RETRY_ATTEMPTS` (default: 3).

## Verification

Test your email configuration:

```python
from libs.email import EmailManager

email = EmailManager()

# Verify config (tries to connect)
is_valid = await email.verify_configuration()
if is_valid:
    print("✅ Email configuration is valid")
else:
    print("❌ Email configuration failed")
```

## Architecture

Follows the same pattern as `libs/redis`:

```
libs/email/
├── email_manager.py       # Main EmailManager class
├── config.py              # EmailConfig with from_env()
├── exceptions.py          # EmailError hierarchy
├── templates/
│   ├── manager.py         # TemplateManager singleton
│   └── *.html             # Jinja2 templates
└── providers/
    ├── base.py            # BaseEmailProvider abstract
    ├── smtp.py            # SMTPProvider
    ├── sendgrid.py        # (Phase 4)
    └── resend.py          # (Phase 4)
```

## Code Quality

Follows all Yoru Cursor rules:

- ✅ Modern type hints (`str | None` not `Optional[str]`)
- ✅ No `print()`, only `LoggingController`
- ✅ Absolute imports
- ✅ Timeout on all external calls
- ✅ Exception hierarchy with `correlation_id`
- ✅ `raise ... from e` for exception chaining
- ✅ No `HTTPException` in libs (only domain exceptions)

## Integration

Already integrated in:

- `InvitationService`: Sends invitation emails
- `OrganizationService`: Sends org invitation emails
- `AuthService`: Sends welcome emails on signup

## Troubleshooting

### Gmail: "Username and Password not accepted"

- Enable 2FA on Google Account
- Create App Password at https://myaccount.google.com/apppasswords
- Use App Password in `SMTP_PASSWORD` (not your regular password)

### Emails not sending but no errors

Check logs for warnings:
```bash
docker logs -f saas_api | grep "email"
```

Look for "Failed to send email" warnings.

### Template not found

Ensure template file exists in `template/libs/email/templates/` and has `.html` extension.

### Timeout errors

Increase timeout:
```bash
EMAIL_TIMEOUT=60  # seconds
```

## Production Recommendations

1. **Use SendGrid or Resend** for production (Phase 4)
2. **Set up SPF/DKIM/DMARC** for your domain
3. **Monitor email delivery rates** in provider dashboard
4. **Keep templates in version control**
5. **Test with real emails** before deploying
6. **Set up email bounce handling** (Phase 5)

## What's Next (Phase 4)

- [ ] SendGrid provider implementation
- [ ] Resend provider implementation
- [ ] Email tracking (opens, clicks)
- [ ] Bounce handling
- [ ] Unsubscribe management
- [ ] Email queue for rate limiting
