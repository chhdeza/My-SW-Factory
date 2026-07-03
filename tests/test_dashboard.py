"""Dashboard endpoint tests."""

import pytest
from fastapi.testclient import TestClient

from factory.config import FactoryConfig, Routine, RoutineAction
from factory.dashboard.app import create_app
from factory.deploy.gate import DeployGate
from factory.metrics.store import MetricsStore
from factory.metrics.traces import TraceWriter


@pytest.fixture
def client(tmp_path):
    cfg = FactoryConfig()
    cfg.schedules = {
        "daily_report": Routine(cron="0 7 * * *", action=RoutineAction(type="report"))
    }
    app = create_app(repo_root=tmp_path, config=cfg)
    return TestClient(app), tmp_path, cfg


def test_index_renders(client):
    http, tmp_path, _ = client
    MetricsStore(tmp_path / ".factory" / "factory.db").record_agent_run(
        role="coder", status="finished", prompt_tokens=10, completion_tokens=5
    )
    response = http.get("/")
    assert response.status_code == 200
    assert "Software Factory" in response.text
    assert "daily_report" in response.text


def test_api_daily(client):
    http, _, _ = client
    days = http.get("/api/daily?days=5").json()
    assert len(days) == 5
    assert "tokens" in days[0]


def test_trace_partial_and_404(client):
    http, tmp_path, cfg = client
    writer = TraceWriter(tmp_path / cfg.tracing.dir, cfg.tracing)
    trace_id = writer.write(role="coder", prompt="p", output="o", status="finished")

    ok = http.get(f"/partials/trace/{trace_id}")
    assert ok.status_code == 200
    missing = http.get("/partials/trace/trace-000000000000")
    assert missing.status_code == 404


def test_routine_run_now_unknown_404(client):
    http, _, _ = client
    assert http.post("/routines/nope/run").status_code == 404


def test_routine_run_now_starts(client):
    http, _, _ = client
    response = http.post("/routines/daily_report/run")
    assert response.json()["status"] == "started"


def test_deploy_approval_flow(client):
    http, tmp_path, cfg = client
    gate = DeployGate(tmp_path, cfg)
    gate.request("v9")

    body = http.get("/partials/deploy").text
    assert "v9" in body and "Approve deploy" in body

    approve = http.post("/deploy/approve")
    assert "approved" in approve.json()["status"]
    assert gate.status().approved
