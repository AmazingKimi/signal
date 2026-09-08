# Codex execution rules

1. Read the task, tree, and relevant entry points before editing. Do not perform unnecessary full-repository rescans.
2. State intended files before editing.
3. Use symbol search, `rg`, focused diffs, and incremental reads.
4. Run targeted tests after each phase; run the full suite only at the final checkpoint.
5. Summarize successful logs instead of reinjecting full logs.
6. Checkpoint after each phase with changed files, targeted-test result, and remaining risks.

## Budgets

- `max_full_repo_scans: 2`
- `max_full_test_runs: 1`
- `max_test_log_lines: 120`
- `max_tool_iterations: 80`

If a budget must be exceeded, stop before exceeding it and report why.
