# Search Funnel Report — 0.8.5

Every Radar run persists a truthful funnel:

1. Raw public search results received.
2. Normalized results after URL/object deduplication.
3. Relevant results passing Radar rules.
4. Actionable results passing availability and score threshold.
5. Newly persisted Discoveries.

Discard counters separately record sold, past-auction, museum, article/reference and duplicate results. Query counts, provider names and backend details are not presented in the ordinary product UI; they remain available only through restricted Developer diagnostics.

Adaptive local search consumes a bounded query pool, stops after two consecutive low-novelty rounds, processes at most 250 unique result URLs per source adapter, and does not fetch result destinations.
