# Contributing

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Checks

```bash
ruff check factory tests
pytest
```

Both must pass before opening a PR; CI enforces them.

## Commit convention

Conventional commits are required:

```
feat: add cursor backend
fix(selfheal): cap rerun attempts per signature
docs: document scheduler runner switch
chore|refactor|test|ci|build: ...
```

## Ground rules

- Read [CONTRACT.md](CONTRACT.md) first - its security invariants are binding. In particular:
  deploy is always human-gated, agents execute only in the sandbox, secrets never get
  committed, and every external call has a timeout.
- Keep it simple. Prefer proven patterns over clever ones; avoid new dependencies unless they
  clearly pay for themselves.
- Validate inputs with Pydantic at boundaries; catch specific exceptions; log with context.
