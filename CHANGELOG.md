# Changelog

## 0.8.7 — 2026-09-08

- LLM provider configuration is now available in the **Settings screen** (BYOK for end users): pick DeepSeek / OpenAI / Anthropic / Gemini / OpenRouter / Ollama / Custom, paste your own API key, and test the connection before saving.
- Keys are persisted locally (`data/llm_provider_config.json`) and are never serialized back to any API response; environment variables remain the deployment-level fallback.
- New endpoints: `GET/POST /api/settings/llm`, `POST /api/settings/llm/test`.

## 0.8.4I — 2026-09-01

- Added structured natural-language target parsing for art, furniture, and classic cars.
- Added furniture-specific multilingual planning, accent-insensitive aliases, and design-market coverage.
- Added immediate per-query normalization, matching, deduplication, scoring, and discovery persistence.
- Added live run counters, ordered events, stream metadata, polling, and adaptive second-stage expansion.
- Preserved strict SEARCH_ONLY isolation: destination pages are never fetched.

## 0.8.4G FULL WEB SEARCH revision — 2026-09-01

- Removed search-backend, unknown-price, legacy-module and source-registry blocks from ordinary Settings.
- Promoted Sotheby's, Christie's, Artsy, Phillips, LiveAuctioneers and Wright to the six art P0 sources.
- Replaced fixed query budgets with a deduplicated, value-driven planner capable of 90, 120 or more queries.
- Added P0, general-web, current-sale, auction, historical-price, year, feature and multilingual query dimensions.
- Added 30-query execution batches without truncation and truthful unfinished counts for partial runs.
- Added canonical-URL-first result deduplication and P0 ranking weight while preserving availability safeguards.
- Kept strict SEARCH_ONLY isolation; result destination URLs are never fetched.

## 0.8.5 — 2026-09-01

- Added deterministic availability classification, freshness and 0–100 actionability scoring with a 60-point discovery gate.
- Excluded sold, past-auction, museum, article and reference results from the current discovery feed.
- Added same-object clustering, adaptive bounded query expansion and diminishing-return stopping.
- Added one best current discovery per Radar on the home view, compact price presentation and thumbnail fallback.
- Replaced ordinary query/provider statistics with the raw → deduplicated → relevant → actionable → new funnel.
- Preserved strict SEARCH_ONLY isolation: result destination pages are never fetched.

## 0.8.4G — 2026-08-31

- Added a platform-aware SearXNG endpoint resolver with explicit URL, Darwin localhost, and optional fallback priority.
- Added Windows `start.cmd`/`stop.cmd`; Windows checks only configured remote SearXNG and never runs Mac scripts or processes.
- Kept Mac-only SearXNG autostart behind a Darwin guard.
- Exposed platform, resolved endpoint, and READY/UNAVAILABLE status only through Developer settings.
- Added compatibility for SearXNG installations that permit HTML results but deny JSON, without fetching result destinations.
- Real Parallels acceptance: READY, 8/8 queries, 40 results received, 32 processed, 8 deduplicated.

## 0.8.4F — 2026-08-31

- Added `scripts/start_searxng.sh` with health-first reuse, PID protection, installation checks, safe port-collision handling, background launch, and a 20-second READY wait.
- Integrated local-search bootstrap into the one-click `start.command` flow without blocking SIGNAL on failure.
- Added `SearXNGProvider` with backend health validation, JSON result mapping, and graceful unavailable/invalid-response states.
- Kept SEARCH_ONLY destination isolation and removed the temporary DDG dependency from the runtime.
- Added Developer environment/backend/mode/direct-fetch status.

## 0.8.4D — 2026-08-31

- Added DuckDuckGo/ddgs as a development-only, key-free local test backend.
- Hid provider and API-key controls from ordinary local-mode Settings.
- Added an eight-query global Radar budget, sequential execution, and one-second DDG throttling.
- Kept SEARCH_ONLY result mapping and the hard prohibition on destination-page fetching.
- Added an explicit production guard that rejects the DDG development backend.
- Verified a real run: 8/8 queries, 40 results received, 34 processed, 6 deduplicated.

## 0.8.4C — 2026-08-31

- Added a complete Settings → Search Service flow for Tavily and Brave Search.
- Persisted provider-specific keys separately and reloaded the selected provider immediately.
- Added a real one-query official API connection test with safe error categories and no key disclosure.
- Added a Provider Gate before query generation, commercial job creation, and Radar History insertion.
- Migrated legacy DuckDuckGo selections to an unconfigured state while preserving unrelated `.env` values.
- Updated the launch script, example environment, bilingual UI, tests, and provider setup guide.

## 0.8.4B — 2026-08-31

- Replaced provider-not-configured error Toast with a persistent neutral Blue Liquid Glass inline state.
- Deleted dormant destination-page fetch code, raw Search Result cache functions/tables, and page-body cache functions/tables.
- Rebased Run outcomes on Search Query completion rather than per-source checks.
- Added persisted query and public-result metrics plus target-market directory scope.
- Replaced normal UI source-check ratios with query counts, processed-result counts, and registered target-market scope.
- Updated empty states, History, settings copy, email, desktop notifications, architecture docs, and network audit.

## 0.8.4A — 2026-08-31

- Removed the production DuckDuckGo/ddgs provider and dependency.
- Restricted production discovery providers to explicitly configured Tavily or Brave official Search APIs.
- Added structured NOT_CONFIGURED and UNAVAILABLE provider states with user-safe bilingual UI text.
- Removed all search-result destination-page fetching, page-cache reads/writes, and raw Search Result caching from SEARCH_ONLY.
- Converted Research input to minimal Search Result Documents and marked Discoveries as `SEARCH_RESULT` evidence.
- Restricted Provider technical errors and source-level details to Developer APIs.
- Added outbound network interception tests proving target URLs are never requested.

## 0.8.4 — 2026-08-31

- Added immutable four-layer coverage snapshots to every Radar Run.
- Separated Eligible Coverage from per-Run completion coverage.
- Added COMPLETE, PARTIAL, FAILED, and DATA_INTEGRITY_ERROR outcomes.
- Added aggregate skip/failure reason counts without exposing source identities to normal users.
- Added lightweight coverage summaries to Radar cards, Coverage details, empty states, History, email, and desktop notifications.
- Added normal and Developer coverage APIs plus bilingual coverage text.

## 0.8.2 — 2026-08-31

- Rebranded production to SIGNAL / Private Discovery Radar / 私人发现雷达.
- Restricted normal Source Network views to aggregate categories and counts.
- Added compliance metadata and hard runtime enforcement for all 126 sources.
- Added Radar edit, pause, resume, soft delete, preserved history, and lifecycle UI.
- Added similar-Radar detection with View existing and Create anyway paths.
- Removed obsolete third-party communication configuration/tests while retaining user notifications.
- Rewrote current documentation and added Codex execution budgets.

Historical 0.4–0.7 notes are in `docs/history/CHANGELOG-0.4-0.7.md`.
