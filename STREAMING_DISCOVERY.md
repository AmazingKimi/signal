# Streaming discovery

Each search query is processed immediately. Results are normalized, matched, deduplicated, scored, and persisted before the next query completes. Discoveries carry `discovered_at`, `run_id`, `query_id`, and `stream_sequence`, allowing the UI to grow while a run remains `RUNNING`.

The browser polls current runs and discoveries once per second. Run events record start, query start/completion, result receipt, candidate creation, discovery creation/update, and completion. Stage-one result domains may add deduplicated stage-two queries; planned and dynamically-added counters update at runtime.

Only search-provider result data is processed. SIGNAL never fetches a result destination page.
