# Security Policy

SIGNAL is designed to run locally and may use private credentials for optional search, LLM, and notification providers.

## Keep credentials private

- Never commit `.env`.
- Never commit API keys, access tokens, SMTP passwords, private keys, or provider credentials.
- Use `.env.example` only as a blank configuration template.
- Treat files under `data/` as private runtime state and do not publish them.

The repository CI includes a lightweight check for common committed secret formats, but that check is not a substitute for careful review.

## Reporting a vulnerability

Please do not publish working credentials, personal data, or an exploitable proof-of-concept in a public issue.

For a suspected vulnerability, contact the repository owner through the GitHub profile first and provide the minimum information needed to reproduce the issue safely.

## Scope

Security reports are especially useful for issues involving:

- credential exposure
- unintended network access
- unsafe source automation
- authentication or authorization bypass
- leakage of local runtime data
- seller/contact actions occurring without explicit user intent
