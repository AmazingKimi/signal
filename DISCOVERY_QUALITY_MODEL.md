# Discovery Quality Model — 0.8.5

Each normalized search result is classified as `FOR_SALE`, `INQUIRE`, `UPCOMING_AUCTION`, `SOLD`, `PAST_AUCTION`, `MUSEUM`, `ARTICLE`, `REFERENCE`, or `UNKNOWN` using only returned title, snippet, URL and optional thumbnail. Explicit historical or editorial signals take precedence over positive words.

The deterministic actionability score combines availability evidence (up to 45), Radar-rule match (up to 25), freshness (up to 15), price or inquiry evidence (up to 10), and source evidence. A result becomes a Discovery only when its Radar rules match, its availability is currently actionable, and its score is at least 60.

Sold and past-auction results are marked historical context; museums, articles and references are non-actionable. They are counted in the run funnel but never enter the main Discovery feed. Object clusters normalize price and marketplace boilerplate so the same work is not shown repeatedly.

Images come only from the search response thumbnail. Missing or broken images render the built-in SIGNAL placeholder. No destination page is requested to obtain an image or to enrich a result.
