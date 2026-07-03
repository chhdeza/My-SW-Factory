# Software Factory - Agent Operating Guide

This repository is a self-building software factory: a harness of specialized LLM agents that
plan, code, review, error-correct, and propose improvements autonomously. Deployment is the
only mandatory human gate. Read [CONTRACT.md](CONTRACT.md) for the binding scope, security
invariants, and acceptance criteria.

## How the factory works

```
intake (CLI / webhook / issue / self-heal signal)
  -> orchestrator (decompose task, contract phase, file-ownership map)
  -> parallel coder agents (each in a sandboxed git worktree)
  -> integrator (serialized rebase/merge; conflict-resolver agent on conflict)
  -> quality gate + security gate (on demand)
  -> self-heal loop on failure (bounded)
  -> risk-based merge (low-risk auto; high-risk needs human)
  -> CI -> human approval (always) -> pluggable deploy hook
```

Every agent run is logged to SQLite metrics and a redacted trace in `.factory/traces/`.
The dashboard (`factory dashboard`) renders runs, daily metrics, routines, traces, and the
deploy approval action.

## Agent roster

| Role | Purpose | When it runs |
|---|---|---|
| `orchestrator` | Decompose a task into work units, produce the shared interface contract and a file-ownership map that prevents conflicts. | Start of every task. |
| `coder` | Implement one work unit inside its own git worktree, honoring the shared contract. | In parallel, >=2 per task. |
| `conflict_resolver` | Resolve rebase/merge conflicts the integrator cannot fast-forward. | On integration conflict. |
| `quality` | LLM diff review complementing ruff/pytest/coverage/actionlint. | Quality gate. |
| `security` | LLM security review complementing bandit/semgrep/gitleaks/pip-audit. | Security gate; always on workflow-file edits. |
| `reviewer` | Final PR-level review and risk assessment feeding the merge policy. | Before merge decision. |
| `fixer` | Produce a minimal fix for a classified failure and open a fix PR. | Self-heal loop. |
| `log_analyzer` | Review recent logs, file improvement issues/PRs. | Daily routine. |
| `ci_analyzer` | Pull failed GitHub Actions logs, classify (transient/build/test/lint/dependency/workflow/infra), rerun or propose a fix. | On `workflow_run` failure or polling routine. |

Role prompts live in `factory/agents/`. Each role can be pinned to a provider and model in
`factory.yaml` under `models:`.

## Operating rules (binding for all agents)

1. Never deploy. Deploy requires human approval via the protected GitHub Environment or the
   dashboard - no exceptions, no workarounds.
2. Execute commands only through the sandbox executor (Docker or confined subprocess),
   scoped to your worktree. Never touch files outside your assigned ownership map.
3. Never commit secrets. Use env vars; `.env` is gitignored. Traces are redacted.
4. All changes flow through gates -> PR. Never push directly to the default branch.
5. Workflow-file edits (`.github/workflows/*`) always get the security gate plus a
   `needs-human-review` label.
6. Respect budgets: token/cost caps and concurrency limits from `factory.yaml` are hard
   limits. If exceeded, stop and mark the task blocked.
7. Self-heal is bounded: dedupe by error signature, cap attempts per signature, back off
   exponentially. Never loop on the same failure.
8. Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `ci:`).
9. Prefer simple, proven patterns. Parameterized queries only, explicit timeouts on every
   external call, Pydantic validation at boundaries, cursor-based pagination (max page 100).

## Key entry points

- `factory init` - interactive bootstrap (provider, topology, models, budgets, sandbox, mcps).
- `factory run "<task>"` / `factory run --issue <n>` - run the full pipeline for a task.
- `factory heal` - trigger the self-heal loop / CI review manually.
- `factory dashboard [--with-scheduler]` - serve the dashboard (optionally embed scheduler).
- `factory scheduler` - run the cron routine daemon.
- `factory routine run <name>` - run a configured routine now.

## Configuration

`factory.yaml` is the single source of configuration (provider, topology, models, gates and
thresholds, budgets, sandbox, scheduler and routines, self-heal, merge policy, mcps, tracing,
deploy). Secrets go in `.env` (see `.env.example`). `factory/config.py` loads and validates
everything with Pydantic; invalid config fails fast.
