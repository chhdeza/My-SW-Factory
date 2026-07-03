# Software Factory

A self-building, self-healing software factory you can export to any GitHub repository.
Specialized LLM agents plan, code in parallel, review, error-correct, and propose
improvements autonomously - deployment is the only mandatory human gate.

## What it does

- **Multi-agent pipeline**: an orchestrator decomposes tasks, runs a contract phase to agree
  on shared interfaces, and dispatches parallel coder agents into sandboxed git worktrees. An
  integrator serializes merges and dispatches a conflict-resolver agent when needed.
- **On-demand gates**: quality (ruff, pytest, coverage, actionlint, LLM review) and security
  (bandit, semgrep/opengrep, gitleaks, pip-audit, LLM review) with explicit thresholds. The
  SAST engine is auto-selected: semgrep where it runs natively, opengrep (its LGPL fork with
  native Windows binaries) elsewhere. Python and Node profiles are built-in; other languages
  are added via `factory.yaml`.
- **Self-healing**: failures are classified and routed to a fix agent that opens a PR. Failed
  GitHub Actions runs are pulled, classified, auto-rerun when transient, and fixed otherwise.
  A daily log review files improvement issues. All healing is bounded (signature dedupe,
  attempt caps, backoff).
- **Risk-based merge**: low-risk PRs auto-merge after gates and CI; workflow edits,
  dependency upgrades, security-flagged changes, migrations, and large diffs are held for a
  human.
- **Human-gated deploy**: always. A protected GitHub Environment with required reviewers in
  CI, plus an approval action in the local dashboard. Deploy targets are pluggable OpenTofu
  modules (GCP Cloud Run or a free-tier VM with docker compose; optional k3s + Argo CD
  profile).
- **Scheduler**: cron-like routines (log review, dependency audits, reports, maintenance)
  running locally or via generated GitHub Actions cron - never both.
- **Dashboard and metrics**: FastAPI + HTMX dashboard with daily rollups (fixes, commits,
  PRs, agents, tokens, estimated cost, gate pass rate, self-heal MTTR) and redacted per-run
  traces.

See [CONTRACT.md](CONTRACT.md) for the binding scope and security invariants, and
[AGENTS.md](AGENTS.md) for the agent roster and operating rules.

## Quick start

```bash
# 1. Use this repo as a GitHub template (or clone it into your project).
#    Maintainers: enable Settings -> General -> "Template repository" once,
#    then every new project starts from "Use this template".
# 2. Install
pip install -e ".[dev]"          # add [cursor] and/or [claude] extras for your provider

# 3. Bootstrap: writes factory.yaml and .env, and offers to install the
#    open-source gate toolchain (ruff, pytest, bandit, pip-audit, semgrep or
#    opengrep + rules, gitleaks, actionlint) so gate checks don't skip
factory init

# (or manage the toolchain separately at any time)
factory tools check
factory tools install

# 4. Run a task through the full pipeline
factory run "add a /health endpoint with a test"

# 5. Watch it work
factory dashboard --with-scheduler   # http://localhost:8700
```

Requirements: Python 3.11+, git. Docker is recommended for sandboxing (falls back to a
confined subprocess without it). An API key for Cursor and/or Anthropic.

## CLI

| Command | Purpose |
|---|---|
| `factory init` | Interactive bootstrap (provider, topology, models, budgets, sandbox, mcps) + optional gate-tool install. |
| `factory tools check` / `install` | Show or install the open-source gate toolchain (pip tools + gitleaks/actionlint release binaries into `~/.factory/bin`). |
| `factory run "<task>"` | Run the full pipeline for a task. `--issue <n>` pulls a GitHub issue. |
| `factory heal` | Trigger self-heal / CI review manually. |
| `factory dashboard` | Serve the dashboard. `--with-scheduler` embeds the routine daemon. |
| `factory scheduler` | Run the cron routine daemon standalone. |
| `factory routine run <name>` | Run a configured routine now. |

## Configuration

Everything lives in [factory.yaml](factory.yaml) (validated by `factory/config.py`):
provider and per-role models, budgets, sandbox mode, gate thresholds, merge risk rules,
self-heal caps, scheduler routines, MCP servers, tracing, and the deploy hook. Secrets go in
`.env` (see [.env.example](.env.example)) locally and Actions secrets in CI.

## GitHub Actions

Shipped workflows (least-privilege permissions each):

- `ci.yml` - lint + test the factory itself.
- `factory.yml` - run the pipeline on issue label or manual dispatch.
- `routines.yml` - cron routines when `scheduler.runner: ci`.
- `self-heal.yml` - triggered by failed workflow runs; reruns transient failures, otherwise
  opens a fix PR.
- `deploy.yml` - bound to a protected Environment with required reviewers.

## Infrastructure

OpenTofu modules under [infra/](infra/) for target apps (the factory itself runs locally or
on Actions runners):

- `infra/cloudrun` - GCP Cloud Run service (free tier).
- `infra/vm-compose` - single free-tier VM (e2-micro / t3.micro) running docker compose.
- `infra/k3s-argocd` - optional documented profile for when you have a real cluster.

## License

Apache-2.0. See [LICENSE](LICENSE).
