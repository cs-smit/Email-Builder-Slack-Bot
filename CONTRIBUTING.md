# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q

cd worker && npm install && npm run typecheck
```

## Guidelines

- Keep pull requests focused on one change, with a short description of why.
- Add or update tests in `tests/` for behaviour changes; `pytest` must pass.
- Never commit secrets, `.env`, `data/`, or real client designs, templates, names or addresses.
  Use fictional `example.com` data in tests and docs.
- Messages the bot posts in Slack should be short and plain: say what happened and what to do next.
