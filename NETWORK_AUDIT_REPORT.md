# Network Audit Report — 0.8.4G Parallels SearXNG

Does SEARCH_ONLY automatically request destination URLs?  
**NO**

Does UI still imply that registered sources were individually fetched?  
**NO**

Can Windows use a configured Mac-host SearXNG without starting Mac processes?  
**YES**

Are dead target-fetch/cache code paths removed or isolated?  
**YES — removed**

## Static cleanup results

- `fetch_text`: deleted; no production definition or call remains.
- `_fetch_with_cache`: absent.
- `search_cache_get` / `search_cache_put`: deleted.
- Search raw-cache table creation: deleted.
- `page_cache_get` / `page_cache_put`: deleted.
- Page-body cache table creation: deleted.
- Target URL enrichment: absent.
- Provider failure Direct/HTML fallback: absent.

Allowed automatic application traffic is limited to localhost SIGNAL and the local SearXNG JSON search endpoint. SearXNG itself performs its configured engine searches. SIGNAL never requests search-result destinations, galleries, dealers, auction houses, or marketplace pages. Network interception tests prove fixture destination URLs cause zero outbound requests.

## Autostart controls

- JSON health check runs before PID inspection and launch.
- Only the Darwin script can use `~/searxng/.venv/bin/python` to start SearXNG.
- Windows `start.cmd` performs HTTP health checks only and never invokes Bash or Mac paths.
- Unknown port-8888 owners are never killed.
- Missing installation and READY timeout degrade search without blocking SIGNAL.
- Logs remain in `~/searxng/searxng.log` and are not surfaced as ordinary-user engine errors.

## Real Parallels acceptance — 2026-08-31

- Platform: Windows.
- Endpoint: supplied only through the process environment; no Mac IP is committed.
- Resolver: READY.
- Radar: COMPLETE, 8/8 queries.
- Results received: 40; processed: 32; deduplicated: 8.
- SearXNG JSON was disabled (403), so SIGNAL parsed the SearXNG-rendered result list only.
- Result destination requests by SIGNAL: 0.

## Compatibility fields

Historical `planned_sources`, `successfully_checked_sources`, `reviewed_eligible_sources`, and related coverage columns remain in the database model for migration compatibility. They are marked deprecated and are not referenced by normal-user JavaScript. Run status and all normal UI copy use Query and Search Result metrics.
