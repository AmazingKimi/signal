# 0.8.2 · Commercial Infrastructure

- Added SQLite-backed User Account model with email/phone verification challenges, PBKDF2 password hashing, masked targets, session revocation and rate limits.
- Added hard TEST/PAUSED plan quotas and a quota guard that runs before Radar execution.
- Added idempotency keys, per-run cost ledger, provider pricing configuration and usage endpoint.
- Added timezone-aware per-Radar schedule persistence with plan limits.
- Added automatic migration of existing Radars to the local sample account; legacy 0.8.1 APIs remain compatible.
- Added stable localized API error codes and a reproducible sample account (`demo@amazing-kimi.local` / `AmazingKimi2026`, development only).
- Added 0.8.2 security and commercial-flow tests.

This build intentionally keeps external SMS/SMTP delivery behind configuration. In development, OTP codes are returned only when `AK_DEV_OTP=1`; production delivery must be wired to a provider without exposing secrets.
