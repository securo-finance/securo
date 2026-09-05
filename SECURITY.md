# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Securo, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **info@usesecuro.com** (or open a private security advisory on GitHub)

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- Acknowledgment within 48 hours
- Status update within 7 days
- Fix and disclosure coordinated with the reporter

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Best Practices for Self-Hosting

- Always change the default `SECRET_KEY` in production
- Use HTTPS in production
- Keep dependencies updated
- Restrict database access to the backend service only
- Review environment variables before deploying
