# SIGNAL Search Transparency Model

SIGNAL uses configured official Search APIs. It does not calculate normal-user coverage from websites visited because SEARCH_ONLY never automatically visits destination pages.

## Query metrics

- `queries_planned`: Search Query operations scheduled for the Run.
- `queries_completed`: Queries whose official Provider call completed, including zero-result responses.
- `queries_failed`: Planned Queries that did not complete.

## Result metrics

- `search_results_received`: Public Search Results returned by the Provider.
- `search_results_processed`: Unique results processed locally after deduplication.
- `search_results_deduplicated`: Duplicate result count removed during local processing.

## Provider and scope

- `provider_status`: connected, not configured, or temporarily unavailable.
- `provider_name`: explicitly selected Tavily or Brave; technical details are Developer-only.
- `target_market_sources_registered`: target-market directory size used to shape search strategy. It never means those sites were individually fetched.

## Run outcomes

- `COMPLETE`: every planned Query completed.
- `PARTIAL`: at least one Query completed and at least one did not.
- `FAILED`: no Query completed or the Provider was unavailable/not configured.

Historical source-check columns remain deprecated database compatibility fields only. They are not rendered in the normal UI and do not determine Run outcome.
