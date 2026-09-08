# SIGNAL

[![CI](https://github.com/AmazingKimi/signal/actions/workflows/ci.yml/badge.svg)](https://github.com/AmazingKimi/signal/actions/workflows/ci.yml)

**Private discovery radar for overlooked art, design, and classic-car opportunities.**

SIGNAL turns a natural-language target into a structured search plan, runs public-search discovery, filters noisy results, tracks what changed, and keeps an evidence trail for every discovery.

It is built for situations where the interesting opportunity is usually buried across fragmented public information rather than sitting neatly in one marketplace.

> **Current version:** 0.8.4K — Change Intelligence

## What SIGNAL does

1. Describe what you are looking for in natural language.
2. SIGNAL parses that target into deterministic rules.
3. It plans and executes search-only discovery across a broad market-source directory (**126 sources**).
4. Results are deduplicated, scored, and filtered for actionable availability.
5. Repeated runs detect changes such as price, status, and relisting events.
6. Run history records what was planned, completed, failed, and actually observed.

Typical targets include:

- specific artists or artworks
- collectible furniture and design
- classic and specialist cars
- narrow, hard-to-find market opportunities

SIGNAL provides **discovery evidence**, not valuation, investment advice, or automated purchasing.

## Why it exists

Conventional search works well when you already know where the answer is.

SIGNAL is for a different problem:

**How do you continuously discover something specific when the useful signal is scattered across many public sources, languages, listings, auction pages, dealer pages, and search results?**

The product focuses on four things:

- **Discovery** — find plausible opportunities from fragmented public information.
- **Evidence** — preserve why a result matched instead of presenting an unexplained recommendation.
- **Change intelligence** — detect meaningful changes between runs.
- **Coverage transparency** — distinguish what was planned, checked, completed, partial, or failed.

## Product flow

Create a Radar → review parsed rules → run now or on schedule → inspect Discoveries → review change events and run history → mark feedback.

Each Radar can be edited, paused, resumed, or soft-deleted. Deletion preserves discovery, run, and feedback history.

## Discovery rules

Only current, actionable candidates such as `FOR_SALE`, `INQUIRE`, and `UPCOMING_AUCTION` can enter the main Discovery feed after passing the deterministic match gate and minimum score.

Sold lots, past auctions, museum pages, editorial references, and unresolved results remain outside the primary feed.

Search-result destination pages are not automatically fetched by the SEARCH_ONLY pipeline.

## Search architecture

SIGNAL uses a SEARCH_ONLY architecture. The source registry describes markets the system aims to discover through search; it is **not** a claim that SIGNAL directly crawls every registered site.

The registry currently contains **126 sources** with review decisions, evidence, access modes, and health states.

Access behavior is explicit:

- `SEARCH_ONLY` → discover through the configured search provider only.
- `LOGIN_REQUIRED` → excluded from automatic runs.
- `DO_NOT_AUTOMATE` → excluded from automatic runs.
- `DIRECT_OK` → requires the relevant robots/terms checks before any direct access path is enabled.

See:

- `SEARCH_ONLY_ARCHITECTURE.md`
- `SEARCH_PROVIDER_SETUP.md`
- `SOURCE_COMPLIANCE_SUMMARY.md`
- `COVERAGE_MODEL.md`

## Change Intelligence

Version 0.8.4K adds persistent candidate snapshots and deterministic change events, including price, availability/status, and relisting changes.

See `CHANGE_INTELLIGENCE.md`.

## Natural-language targets

Radar rules can represent artist/maker, model or series, aliases, keywords, year ranges, price ceilings, currencies, preferences, and exclusions.

The parser can interpret free-form requests, but the deterministic match gate remains the final authority for whether a candidate qualifies.

See `NATURAL_LANGUAGE_TARGETS.md`.

## LLM providers: BYOK only

SIGNAL does **not** ship, share, proxy, or subsidize an API key. Every user brings their own provider key locally.

Supported providers:

- DeepSeek
- OpenAI
- Anthropic / Claude
- Google Gemini
- OpenRouter
- Ollama local models
- Custom OpenAI-compatible endpoints

Set the provider in your private `.env`:

```env
LLM_PROVIDER=deepseek
LLM_API_KEY=your-own-key
LLM_MODEL=
LLM_BASE_URL=
```

**Or configure it in the product UI** (recommended for end users): open **Settings → 大模型（可选）/ LLM (optional)**, pick a provider, paste your API key, and press **保存并测试连接**. The key is stored in a local file (`data/llm_provider_config.json`) and is never displayed or returned by any API. Environment variables remain the deployment-level fallback, so both paths work together.

For Ollama, no API key is required:

```env
LLM_PROVIDER=ollama
LLM_API_KEY=
LLM_MODEL=llama3.2
```

Anthropic and Gemini use their native APIs; DeepSeek, OpenAI, OpenRouter, Ollama, and `custom` use OpenAI-compatible chat transport where applicable.

If no LLM key is configured, SIGNAL still starts and deterministic rule-based behavior remains available.

**Never commit your real `.env` file or API key.** See `.env.example` for provider defaults.

## Run locally

### Requirements

- Python 3.11+
- a configured search provider for live discovery

The application can still start without an LLM API key; LLM-dependent interpretation falls back while deterministic behavior remains available.

### macOS

```bash
cp .env.example .env
pip install -r requirements.txt
./start.command
```

Or run directly:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765`.

### Windows

```bat
copy .env.example .env
pip install -r requirements.txt
start.cmd
```

When Windows/Parallels uses a SearXNG instance running on the Mac host, set `SEARXNG_BASE_URL` in the private `.env`. Windows never guesses the host IP and never attempts to start or stop Mac processes.

Configuration belongs in `.env`; secrets and machine-specific settings should never be committed.

## Verify a fresh checkout

A contributor should be able to validate the public repository without private credentials:

```bash
python -m pip install -r requirements.txt
python -m compileall -q app
python -c "from app.main import app; assert app.title"
python -m pytest -q
```

GitHub Actions runs this validation on Linux, macOS, and Windows using Python 3.11. CI also performs a lightweight public-repository safety check for common committed secret formats and verifies that private runtime paths remain ignored.

## Language and appearance

The production Radar supports:

- `zh-CN`
- `en-US`
- Light
- Dark
- System appearance

## Search transparency

Every run stores operational evidence such as:

- planned queries
- completed queries
- failed queries
- public results received
- results processed
- deduplicated results
- provider state
- registered market-directory scope

`COMPLETE`, `PARTIAL`, and `FAILED` describe query execution status. They do not imply that every registered market website was individually visited.

## Notifications

Email and desktop notifications target the registered SIGNAL user only.

SIGNAL does not automatically contact sellers, generate seller outreach, place bids, or make purchases.

## Testing

The project includes automated regression coverage for discovery, matching, lifecycle, provider behavior, source compliance, change intelligence, and BYOK LLM transport adapters.

Historical test counts documented in development notes are preserved as project records; they should not be interpreted as a promise that every environment will reproduce the same count without the same dependencies and provider setup.

## Known limitations

- Search quality depends on the configured provider and public-web indexing.
- Registration of a source does not imply permission to automate it.
- Login-required and manual-only sources are excluded from automatic runs.
- Coverage reports what the run planned and executed, not the entire market.
- SIGNAL does not provide valuation, investment advice, purchasing decisions, or seller outreach.
- Provider model names can change over time; override `LLM_MODEL` locally when needed.

## Contributing and security

Contributions are welcome. See `CONTRIBUTING.md` for the local verification contract and evidence-preservation rules.

For credential handling and vulnerability reporting, see `SECURITY.md`.

## Project notes

This repository includes architecture and research notes from the development of SIGNAL. Some documents describe experiments, hotfixes, or historical checkpoints rather than current product behavior; the README and current code are the primary references for present behavior.

## License

MIT License. See `LICENSE`.

---

Created by **Amazing Kimi**.
