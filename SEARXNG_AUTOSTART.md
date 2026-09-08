# SIGNAL 0.8.4F — SearXNG Autostart

Double-click `start.command`. No terminal, virtualenv activation, Python command, port, or service URL is required from the user.

## Startup sequence

1. Check SIGNAL's Python environment.
2. Run `scripts/start_searxng.sh`.
3. Reuse SearXNG if its JSON search endpoint is healthy.
4. Otherwise validate `~/searxng/app`, `~/searxng/.venv/bin/python`, and `~/searxng/app/searx/settings.yml`.
5. Start the exact SearXNG venv Python in the background, save `~/searxng/searxng.pid`, and log to `~/searxng/searxng.log`.
6. Wait up to 20 seconds for READY.
7. Start SIGNAL even if local search is unavailable, then open the browser.

## Safety

- Health check precedes PID check and launch, preventing duplicate instances.
- A live saved PID is reused while readiness is checked.
- An unrelated process on port 8888 is reported and never killed.
- The script never uses system Python, activation, Docker, Homebrew, sudo, install, upgrade, or git pull.
- `stop.command` stops SIGNAL only; SearXNG remains available for faster subsequent launches.
- SIGNAL performs its own SearXNG JSON health check whenever the backend provider is initialized.
- SEARCH_ONLY consumes SearXNG `title`, `content`, `url`, and engine metadata only. It never requests result destination URLs.

## Internal defaults

```env
DEV_SEARCH_PROVIDER=searxng
SEARXNG_BASE_URL=http://127.0.0.1:8888
SEARXNG_HOME=~/searxng
```

These values are not shown in ordinary Settings. Developer status exposes environment, backend, Local Test Only mode, and `Direct Target Fetch: DISABLED`.
