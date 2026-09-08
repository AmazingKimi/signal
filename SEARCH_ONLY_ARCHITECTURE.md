# SEARCH_ONLY Architecture

## Production flow

```text
User Radar
→ explicitly configured Tavily or Brave official Search API
→ title / snippet / url / provider / minimal metadata
→ local deterministic extraction, filtering, matching and deduplication
→ Discovery with evidence_level=SEARCH_RESULT
→ user manually opens the original URL
```

The SearchAdapter never calls `fetch_text`, page cache, a DirectAdapter, an HTML search fallback, or a destination-page enrichment function. Provider failures stop that search path and produce `SEARCH_PROVIDER_UNAVAILABLE`; missing configuration produces `SEARCH_PROVIDER_NOT_CONFIGURED`.

## Provider boundary

Raw Tavily and Brave responses live only for the current API call and Run. They are mapped immediately to minimal Search Result Documents. The legacy 24-hour search cache is not called. Persistent Discovery records contain only product-required fields, not the full Provider payload.

## Evidence boundary

Search snippets may provide a title, year, price clue, currency, and relevance evidence. Missing information remains unknown. A Search Result Discovery never claims that SIGNAL read the full listing or verified the seller's description.

## Source Registry

The 126 records are a target-market/source directory and future integration index. They do not mean SIGNAL directly visits, crawls, or has an adapter for 126 websites.

## Search transparency metrics

Normal users see Search Queries planned/completed/failed, public results received/processed/deduplicated, Provider connection state, and 126 registered target-market sources as strategy scope. Historical per-source coverage columns remain database compatibility fields only and are deprecated; the normal UI does not render them.
