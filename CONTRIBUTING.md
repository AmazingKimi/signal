# Contributing to SIGNAL

Thanks for taking an interest in SIGNAL.

SIGNAL is an evidence-first discovery system. Contributions should preserve that principle: do not turn uncertain search results into stronger claims than the underlying evidence supports.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env       # Windows: copy .env.example .env
python -m pytest -q
```

Run the app with:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## Contribution rules

- Keep API keys, tokens, provider credentials, runtime data, and `.env` out of commits.
- Prefer focused changes over broad refactors.
- Add or update tests when behavior changes.
- Preserve explicit distinctions between COMPLETE, PARTIAL, and FAILED search execution.
- Do not imply that a registered source was directly visited when it was only discoverable through SEARCH_ONLY.
- Do not add automatic seller outreach, bidding, purchasing, or other third-party actions without an explicit product-level decision and safety review.
- Synthetic fixtures must be clearly identifiable as test data and must not be presented as observed market evidence.

## Before opening a pull request

Run:

```bash
python -m compileall -q app
python -m pytest -q
```

The GitHub Actions workflow also verifies the application import and test suite on Linux, macOS, and Windows.
