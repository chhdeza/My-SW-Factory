# V1 Contract

The safety and scope boundary this scaffold is built and tested against. Changes to this
contract require human review.

## In scope (v1)

- Local-first execution end-to-end (the primary tested path); GitHub Actions workflows are
  shipped and runnable but the smoke test runs locally.
- Default target = the repo the factory is installed in (in-repo mode), which may be a target
  app or the factory itself; control-plane (remote target repos) ships as a config switch,
  fully wired as a follow-up.
- One orchestrated task -> contract phase -> >=2 sandboxed parallel coding agents -> gates ->
  self-heal -> risk-based merge -> approval-gated deploy.
- Polyglot-by-config gates with Python + Node built-in profiles.
- Self-healing for runtime/CI failures including GitHub Actions log review.
- Metrics + traces dashboard; cron scheduler and routines.
- Deploy targets: OpenTofu + Docker on genuinely free infrastructure (GCP Cloud Run or a
  single free-tier VM with docker compose). Only target apps are cloud-deployable; the
  factory itself runs locally and on GitHub Actions runners.

## Non-goals (v1)

- Managed Kubernetes (GKE/EKS) and Argo CD as the default deploy stack (they exceed AWS/GCP
  free tiers). k3s + Argo CD ships only as an optional, documented profile.
- Micro-VM isolation (Firecracker/gVisor), multi-tenant hosting, or a hosted control plane.
- Fully autonomous deploys (deploy is always human-gated) and auto-merge of high-risk changes.
- External tracing SaaS (LangSmith/Phoenix) by default - traces stay local.
- Built-in toolchains beyond Python/Node (others via `factory.yaml`).

## Human approval points

- Deploy: always (GitHub Environment required reviewer + local dashboard approval).
- Merge: risk-based - high-risk changes (workflow edits, dependency upgrades, security-flagged
  findings, DB migrations, large diffs) require human approval; low-risk auto-merges after
  gates + CI pass.

## Required GitHub permissions (least privilege, per workflow)

- `factory.yml`: `contents: write`, `pull-requests: write`, `issues: write`, `actions: read`.
- `routines.yml`: `contents: write`, `pull-requests: write`, `issues: write`, `actions: read`.
- `self-heal.yml`: `actions: write` (read logs + rerun), `contents: write`,
  `pull-requests: write`.
- `deploy.yml`: `contents: read`, `deployments: write`, `id-token: write` (only if OIDC);
  bound to a protected Environment with required reviewers.

## Security invariants

- Deploy never happens without human approval; high-risk merges never auto-merge.
- Agents execute only inside the sandbox (Docker or confined subprocess), scoped to the
  worktree; routine `command` actions are allowlisted (exact argv, no shell interpolation).
- Secrets only via `.env`/Actions secrets, never committed; traces and logs are redacted.
- Every external call has a timeout; inputs are validated with Pydantic models.
- Self-heal is bounded (signature dedupe, attempt cap, backoff); budgets and concurrency caps
  are enforced.

## Gate thresholds (defaults, configurable)

- ruff: zero errors; pytest: all selected tests pass; coverage: >= 80% (or no-regression vs
  baseline).
- bandit: no High (Medium warns); semgrep: no findings at/above `error`; gitleaks: zero
  secrets; pip-audit/npm-audit: no High/Critical.
- actionlint: zero errors on changed workflows.

## Acceptance criteria

- `factory init` writes a valid `factory.yaml` + `.env` (provider, topology, models, mcps,
  sandbox).
- `factory run "<task>"` runs >=2 parallel sandboxed agents in worktrees, integrates with no
  manual git, passes gates, and opens a PR.
- Breaking a test triggers self-heal -> fix PR; a failed Actions run triggers `ci_review`
  (rerun transient, else fix PR).
- Low-risk PR auto-merges after gates + CI; a high-risk PR is held for human approval.
- Deploy stays blocked until approved (Environment + dashboard); the no-op deploy hook runs
  post-approval.
- Dashboard shows runs, daily metrics, routines, and redacted traces.
- Killing the orchestrator mid-run and restarting reconciles worktrees and resumes or
  fails-forward (no orphans).

## First smoke-test scenario

In-repo mode on a small sample app; task: "add an endpoint + test". Observe contract ->
parallel agents -> gates -> PR -> risk-based merge -> approval-gated no-op deploy, with
metrics and traces in the dashboard; then break a test to exercise self-heal.

## Failure modes -> expected behavior

- Agent fails to start (auth/config): retry per backend, surface in dashboard, no partial
  merge.
- Gate fails: self-heal up to cap; if still failing, leave PR open + `needs-human-review`.
- Merge conflict: conflict-resolver agent; if unresolved after cap, escalate to human.
- Budget exceeded: pause task, mark blocked, notify in dashboard.
- No Docker: fall back to confined subprocess; if neither is safe, refuse to execute.
- Deploy approval timeout: stays pending; nothing deploys.
