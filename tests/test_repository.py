import pytest

from todo_cli.repository import TodoRepository


def test_add_and_list(tmp_path):
    repo = TodoRepository(tmp_path / "todo.json")
    task = repo.add_task("Document CLI")
    assert task.status == "todo"
    tasks = repo.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].text == "Document CLI"
    assert repo.list_tasks("todo")
    with pytest.raises(ValueError):
        repo.list_tasks("invalid")


def test_mark_done(tmp_path):
    repo = TodoRepository(tmp_path / "todo.json")
    task = repo.add_task("Fix bug")
    done = repo.mark_done(task.id)
    assert done.status == "done"
    assert any(t.id == task.id for t in repo.list_tasks("done"))


def test_mark_done_missing(tmp_path):
    repo = TodoRepository(tmp_path / "todo.json")
    with pytest.raises(KeyError):
        repo.mark_done("missing")


def test_reject_whitespace_text(tmp_path):
    repo = TodoRepository(tmp_path / "todo.json")
    with pytest.raises(ValueError):
        repo.add_task("   ")
