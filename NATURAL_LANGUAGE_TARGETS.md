# Natural-language targets

SIGNAL 0.8.4I parses free-form targets into `ART`, `FURNITURE`, or `CLASSIC_CAR` before building a query tree. Parsed fields remain visible for confirmation, and unclassified terms are preserved rather than silently discarded.

Furniture targets retain designer, accent-free aliases, model, manufacturer, object type, period, and series. Art targets retain artist, work, color, feature, and price ceiling. Classic-car targets retain marque, model, variant, chassis code, and year.

An enabled Radar with an empty query plan fails explicitly with `QUERY_PLAN_EMPTY`.
