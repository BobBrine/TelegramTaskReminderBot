# Security Policy

## Supported Versions
This project currently supports the latest `main` branch.

## Reporting a Vulnerability
If you discover a security issue:

1. Do **not** open a public issue with sensitive details.
2. Contact the maintainer directly (or open a private security advisory on GitHub).
3. Include reproduction steps, impact, and suggested mitigation if known.

## Secrets and Credentials
- Never commit `.env`.
- Never expose `TELEGRAM_BOT_TOKEN`.
- Rotate bot token immediately if leaked.
