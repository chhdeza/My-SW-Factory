"""Factory dashboard: live runs, daily metrics, routines, traces, deploy approval.

Deliberately dependency-light: one FastAPI app, HTMX for partial refresh,
Chart.js for the daily chart, no frontend build step.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment

from factory.config import FactoryConfig, load_config
from factory.metrics.rollup import daily_rollups
from factory.metrics.store import MetricsStore
from factory.metrics.traces import TraceWriter
from factory.state import StateStore

logger = logging.getLogger(__name__)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Software Factory</title>
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #0f1117; color: #e6e6e6; }
  header { padding: 16px 24px; background: #171a23; border-bottom: 1px solid #262b38;
           display: flex; align-items: baseline; gap: 16px; }
  h1 { font-size: 18px; margin: 0; }
  main { padding: 24px; max-width: 1200px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 12px; margin-bottom: 24px; }
  .card { background: #171a23; border: 1px solid #262b38; border-radius: 8px; padding: 14px; }
  .card .v { font-size: 22px; font-weight: 600; }
  .card .l { font-size: 12px; color: #9aa3b2; margin-top: 4px; }
  section { margin-bottom: 28px; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .08em; color: #9aa3b2; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #232837; }
  th { color: #9aa3b2; font-weight: 500; }
  .ok { color: #5dd37f; } .bad { color: #ff7b72; } .warn { color: #e3b341; }
  button { background: #2d5bd7; color: #fff; border: 0; border-radius: 6px;
           padding: 6px 12px; cursor: pointer; font-size: 13px; }
  button.danger { background: #b13a3a; }
  pre { background: #10131b; padding: 12px; border-radius: 8px; overflow-x: auto;
        font-size: 12px; max-height: 400px; }
  a { color: #79a8ff; text-decoration: none; }
  canvas { background: #171a23; border: 1px solid #262b38; border-radius: 8px; padding: 8px; }
</style>
</head>
<body>
<header><h1>Software Factory</h1><span style="color:#9aa3b2">{{ today }}</span></header>
<main>
  <div class="cards" hx-get="/partials/cards" hx-trigger="every 10s" hx-swap="innerHTML">
    {{ cards | safe }}
  </div>

  <section>
    <h2>Last 14 days (tokens / est. cost)</h2>
    <canvas id="chart" height="80"></canvas>
  </section>

  <section>
    <h2>Deploy</h2>
    <div hx-get="/partials/deploy" hx-trigger="every 15s" hx-swap="innerHTML">
      {{ deploy | safe }}
    </div>
  </section>

  <section>
    <h2>Tasks</h2>
    <div hx-get="/partials/tasks" hx-trigger="every 10s" hx-swap="innerHTML">
      {{ tasks | safe }}
    </div>
  </section>

  <section>
    <h2>Agent runs</h2>
    <div hx-get="/partials/runs" hx-trigger="every 10s" hx-swap="innerHTML">
      {{ runs | safe }}
    </div>
  </section>

  <section>
    <h2>Routines</h2>
    <div hx-get="/partials/routines" hx-trigger="every 30s" hx-swap="innerHTML">
      {{ routines | safe }}
    </div>
  </section>

  <section>
    <h2>Traces</h2>
    <div hx-get="/partials/traces" hx-trigger="every 30s" hx-swap="innerHTML">
      {{ traces | safe }}
    </div>
    <div id="trace-view"></div>
  </section>
</main>
<script>
fetch('/api/daily?days=14').then(r => r.json()).then(days => {
  new Chart(document.getElementById('chart'), {
    type: 'bar',
    data: {
      labels: days.map(d => d.day.slice(5)),
      datasets: [
        { label: 'tokens', data: days.map(d => d.tokens), yAxisID: 'y' },
        { label: 'est. cost $', data: days.map(d => d.cost_usd), yAxisID: 'y1', type: 'line' },
      ],
    },
    options: { scales: { y: { position: 'left' }, y1: { position: 'right' } } },
  });
});
</script>
</body>
</html>"""

_env = Environment(autoescape=True)


def _fmt_cards(agg: dict) -> str:
    pass_rate = (
        f"{agg['gate_pass_rate'] * 100:.0f}%" if agg["gate_pass_rate"] is not None else "-"
    )
    mttr = (
        f"{agg['selfheal_mttr_seconds']:.0f}s"
        if agg["selfheal_mttr_seconds"] is not None else "-"
    )
    cards = [
        (agg["agent_runs"], "agent runs today"),
        (agg["agents_used"], "roles used"),
        (f"{agg['tokens']:,}", "tokens"),
        (f"${agg['cost_usd']:.2f}", "est. cost"),
        (agg["fixes"], "fixes"),
        (agg["commits"], "commits"),
        (agg["prs_opened"], "PRs opened"),
        (agg["prs_merged"], "PRs merged"),
        (agg["conflicts_resolved"], "conflicts resolved"),
        (pass_rate, "gate pass rate"),
        (mttr, "self-heal MTTR"),
    ]
    return "".join(
        f'<div class="card"><div class="v">{value}</div><div class="l">{label}</div></div>'
        for value, label in cards
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def create_app(
    repo_root: Path | None = None,
    config: FactoryConfig | None = None,
    with_scheduler: bool = False,
) -> FastAPI:
    root = (repo_root or Path.cwd()).resolve()
    cfg = config or load_config(root)
    db_path = root / ".factory" / "factory.db"
    metrics = MetricsStore(db_path)
    state = StateStore(db_path)
    tracer = TraceWriter(root / cfg.tracing.dir, cfg.tracing)

    app = FastAPI(title="software-factory-dashboard")

    if with_scheduler:
        from factory.scheduler.runner import start_embedded

        start_embedded(root, cfg)

    # -- partials -----------------------------------------------------------

    def cards_html() -> str:
        today = datetime.now(UTC).date().isoformat()
        return _fmt_cards(metrics.day_aggregates(today))

    def tasks_html() -> str:
        rows = [
            [t.id, t.title[:60], f'<span class="{_status_class(t.status.value)}">'
             f"{t.status.value}</span>", t.updated_at]
            for t in state.list_tasks(limit=20)
        ]
        return _table(["task", "title", "status", "updated"], rows) if rows else "<p>none</p>"

    def runs_html() -> str:
        rows = [
            [r["ts"], r["role"], r["provider"], r["model"],
             f'<span class="{_status_class(r["status"])}">{r["status"]}</span>',
             f"{r['prompt_tokens'] + r['completion_tokens']:,}",
             f"${r['cost_usd']:.3f}", f"{r['duration_seconds']:.0f}s"]
            for r in metrics.recent_agent_runs(limit=25)
        ]
        return (_table(["time", "role", "provider", "model", "status", "tokens",
                        "est. cost", "duration"], rows) if rows else "<p>none yet</p>")

    def routines_html() -> str:
        history = {r["name"]: r for r in metrics.recent_routine_runs(limit=100)}
        rows = []
        for name, routine in cfg.schedules.items():
            last = history.get(name)
            last_txt = (
                f"{last['ts']} ({'ok' if last['ok'] else 'failed'})" if last else "never"
            )
            state_txt = "enabled" if routine.enabled else "disabled"
            rows.append([
                name, routine.cron, routine.action.type + ":" + routine.action.ref,
                state_txt, last_txt,
                f'<button hx-post="/routines/{name}/run" hx-swap="none">run now</button>',
            ])
        return (_table(["routine", "cron", "action", "state", "last run", ""], rows)
                if rows else "<p>no routines configured</p>")

    def traces_html() -> str:
        rows = [
            [t["day"],
             f'<a hx-get="/partials/trace/{t["trace_id"]}" hx-target="#trace-view" '
             f'href="#trace-view">{t["trace_id"]}</a>']
            for t in tracer.list_traces(limit=20)
        ]
        return _table(["day", "trace"], rows) if rows else "<p>none yet</p>"

    def deploy_html() -> str:
        from factory.deploy.gate import DeployGate

        gate = DeployGate(root, cfg)
        status = gate.status()
        if status.pending is None:
            return "<p>no deploy pending</p>"
        return (
            f"<p>pending deploy: <b>{status.pending}</b> "
            f'<button hx-post="/deploy/approve" hx-swap="none">Approve deploy</button> '
            f'<button class="danger" hx-post="/deploy/reject" hx-swap="none">Reject</button>'
            f"</p>"
        )

    # -- routes ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index():
        template = _env.from_string(_PAGE)
        return template.render(
            today=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            cards=cards_html(), tasks=tasks_html(), runs=runs_html(),
            routines=routines_html(), traces=traces_html(), deploy=deploy_html(),
        )

    @app.get("/partials/cards", response_class=HTMLResponse)
    def partial_cards():
        return cards_html()

    @app.get("/partials/tasks", response_class=HTMLResponse)
    def partial_tasks():
        return tasks_html()

    @app.get("/partials/runs", response_class=HTMLResponse)
    def partial_runs():
        return runs_html()

    @app.get("/partials/routines", response_class=HTMLResponse)
    def partial_routines():
        return routines_html()

    @app.get("/partials/traces", response_class=HTMLResponse)
    def partial_traces():
        return traces_html()

    @app.get("/partials/deploy", response_class=HTMLResponse)
    def partial_deploy():
        return deploy_html()

    @app.get("/partials/trace/{trace_id}", response_class=HTMLResponse)
    def partial_trace(trace_id: str):
        record = tracer.read(trace_id)
        if record is None:
            raise HTTPException(status_code=404, detail="trace not found")
        import html
        import json

        return f"<pre>{html.escape(json.dumps(record, indent=2))}</pre>"

    @app.get("/api/daily")
    def api_daily(days: int = 14):
        return JSONResponse(daily_rollups(db_path, days=days))

    @app.get("/api/runs")
    def api_runs(limit: int = 50):
        return JSONResponse(metrics.recent_agent_runs(limit=limit))

    @app.post("/routines/{name}/run")
    def routine_run_now(name: str):
        if name not in cfg.schedules:
            raise HTTPException(status_code=404, detail=f"routine {name!r} not configured")
        from factory.scheduler.routines import RoutineExecutor

        executor = RoutineExecutor(root, cfg)
        thread = threading.Thread(target=executor.run, args=(name,), daemon=True)
        thread.start()
        return {"status": "started", "routine": name}

    @app.post("/deploy/approve")
    def deploy_approve():
        from factory.deploy.gate import DeployGate

        gate = DeployGate(root, cfg)
        result = gate.approve(approver="dashboard")
        metrics.record_event("deploy_approved", detail=result)
        return {"status": result}

    @app.post("/deploy/reject")
    def deploy_reject():
        from factory.deploy.gate import DeployGate

        gate = DeployGate(root, cfg)
        return {"status": gate.reject(approver="dashboard")}

    return app


def _status_class(status: str) -> str:
    if status in ("finished", "done", "merged", "ok"):
        return "ok"
    if status in ("error", "failed", "startup_failed", "timeout", "blocked"):
        return "bad"
    return "warn"
