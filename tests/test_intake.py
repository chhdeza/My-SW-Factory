"""Intake normalization tests."""

import pytest

from factory.intake import task_from_issue, task_from_text
from factory.state import StateStore


class FakeGitHub:
    def get_issue(self, number: int) -> dict:
        return {"number": number, "title": "Add health endpoint", "body": "GET /health -> 200"}


def test_task_from_text(tmp_path):
    store = StateStore(tmp_path / "db.sqlite")
    task = task_from_text(store, "add /health endpoint\nwith a test", source="cli")
    assert task.title == "add /health endpoint"
    assert task.source == "cli"
    assert store.get_task(task.id) is not None


def test_empty_text_rejected(tmp_path):
    store = StateStore(tmp_path / "db.sqlite")
    with pytest.raises(ValueError, match="empty"):
        task_from_text(store, "   ")


def test_task_from_issue(tmp_path):
    store = StateStore(tmp_path / "db.sqlite")
    task = task_from_issue(store, FakeGitHub(), 42)
    assert task.title == "Add health endpoint"
    assert task.source == "issue"
    assert "#42" in task.description
