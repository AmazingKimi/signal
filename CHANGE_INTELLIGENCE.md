# 0.8.4K changes

Candidates retain one lightweight structured snapshot. `watch.make_fingerprint` owns identity; its listing URL extension keeps a stable URL across changed titles/prices. VIN identity supports confident cross-source reappearance. No fuzzy cross-source merge is inferred from similar titles.

Snapshot comparisons generate NEW_LISTING, PRICE_ADDED, PRICE_CHANGED, PRICE_DROPPED, PRICE_INCREASED, STATUS_CHANGED, RELISTED, RETURNED_TO_MARKET and SOURCE_CHANGED. Different currencies never produce a percentage decrease. Missing price evidence retains the last observed price. Missing search results never imply removal.

Run history counts new listings, price changes, status changes and relistings. Existing cards display the latest change reasons and first/last-change timestamps. High-priority changes mark notification queue intent; detection does not send mail.

Verification uses controlled provider fixtures for repeatable transitions, including six successive runs through the real run/storage/card pipeline. Browser preview acceptance data is explicitly synthetic, isolated from user databases and excluded from release archives. No claim is made that a live seller changed price during testing.
