# Full-Web Query Planner

The planner has no product-level query-count ceiling. It builds useful dimensions from the Radar target and then normalizes whitespace, punctuation and case to remove exact duplicates. A Takis Signals plan currently produces more than 160 distinct queries, including all six P0 domains plus unrestricted and multilingual searches.

Plans execute sequentially in 30-query batches. Batch size is a stability mechanism, not a total-query cap: query 91, 120 and later queries remain in the plan. Provider errors retain the original planned count, completed count and all unfinished queries; runs are COMPLETE only when the plan finishes, PARTIAL when at least one query succeeds, and FAILED when none succeeds.

Developer diagnostics can read `/api/developer/radars/{radar_id}/query-plan` with the configured Developer key. It reports totals for P0, general-web, multilingual and historical-price queries. Ordinary users see only completion and result-level summaries.
