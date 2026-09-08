# 0.8.4K Hotfix snapshot — 2026-09-08

This archive includes source URL validation and test-data isolation changes.
The full pytest subprocess completed successfully (exit code 0).
The database isolation audit found identical real database record counts and row hashes before and after testing.

One confirmed synthetic Discovery and its candidate snapshot and associated change events were removed from the running database after a recoverable backup; real Discovery rows were preserved.

Remaining acceptance: complete the real-search-to-source-page browser click verification. This snapshot does not claim that final browser acceptance is complete.

No databases, test-generated records, private environment files, or caches are included in this archive.
