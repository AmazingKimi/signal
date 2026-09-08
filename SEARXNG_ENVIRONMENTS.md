# SIGNAL 0.8.4G — SearXNG Environments

Environment addresses are configuration, not application code.

## macOS

`start.command` may use the explicit `SEARXNG_BASE_URL`, then local `http://127.0.0.1:8888`, then `SEARXNG_FALLBACK_URL`. If local SearXNG is unavailable, the Darwin-only bootstrap can start `~/searxng/.venv/bin/python -m searx.webapp` and wait for READY.

## Windows / Parallels

Create a private `.env` and set `SEARXNG_BASE_URL` to the Mac host address that Windows can reach. Do not commit that address. Run `start.cmd`.

Windows performs only an HTTP health check and starts SIGNAL. It never executes `~/searxng`, Bash, Mac Python, or a Mac kill command. It never guesses the Parallels host IP. If the address changes, edit only `.env` and restart SIGNAL.

## Resolver priority

1. Explicit `SEARXNG_BASE_URL`.
2. Darwin localhost default.
3. Optional `SEARXNG_FALLBACK_URL`.
4. Otherwise `UNAVAILABLE`.

Linux uses configured URLs only. Ordinary UI shows connected/unavailable without endpoint details. Developer settings may show backend, resolved endpoint, status, and platform.

SIGNAL supports SearXNG JSON results and the SearXNG-rendered HTML result list when JSON format is disabled. Both paths consume only title, snippet/content, URL, and engine metadata. Neither path requests result destinations.
