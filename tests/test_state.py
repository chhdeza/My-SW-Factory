"""State store and crash-resume tests."""

from pathlib import Path

from factory.state import StateStore, TaskStatus, UnitStatus


def make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / ".factory" / "factory.db")


def test_task_lifecycle(tmp_path):
    store = make_store(tmp_path)
    task = store.create_task("add endpoint", "add /health", source="cli")
    assert store.get_task(task.id).status is TaskStatus.PENDING

    store.update_task(task.id, status=TaskStatus.CODING, branch="factory/x")
    loaded = store.get_task(task.id)
    assert loaded.status is TaskStatus.CODING
    assert loaded.branch == "factory/x"


def test_contract_persisted(tmp_path):
    store = make_store(tmp_path)
    task = store.create_task("t", "d")
    store.update_task(task.id, contract="interface: foo()")
    assert store.get_task_contract(task.id) == "interface: foo()"


def test_units_and_ownership(tmp_path):
    store = make_store(tmp_path)
    task = store.create_task("t", "d")
    unit = store.create_unit(task.id, "u1", "impl", ["a.py", "tests/test_a.py"])
    units = store.units_for_task(task.id)
    assert len(units) == 1
    assert units[0].owned_files == ["a.py", "tests/test_a.py"]
    store.update_unit(unit.id, status=UnitStatus.CODED, worktree_path="/wt/u1")
    assert store.units_for_task(task.id)[0].status is UnitStatus.CODED


def test_reconcile_requeues_running_units_and_returns_active_tasks(tmp_path):
    store = make_store(tmp_path)
    active = store.create_task("active", "d")
    done = store.create_task("done", "d")
    store.update_task(active.id, status=TaskStatus.CODING)
    store.update_task(done.id, status=TaskStatus.DONE)
    unit = store.create_unit(active.id, "u", "d", ["f.py"])
    store.update_unit(unit.id, status=UnitStatus.RUNNING)

    resumable = store.reconcile()

    assert [t.id for t in resumable] == [active.id]
    assert store.units_for_task(active.id)[0].status is UnitStatus.PENDING


def test_registered_worktrees(tmp_path):
    store = make_store(tmp_path)
    task = store.create_task("t", "d")
    unit = store.create_unit(task.id, "u", "d", ["f.py"])
    store.update_unit(unit.id, worktree_path="/wt/u1")
    assert store.registered_worktrees() == {"/wt/u1"}
