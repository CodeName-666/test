from todo_cli.cli import run


def test_cli_add_list_done(tmp_path, capsys):
    db = tmp_path / "todo.json"
    assert run(["add", "write docs", "--db", str(db)]) == 0
    add_output = capsys.readouterr().out
    assert "Added" in add_output
    task_id = add_output.split()[1].rstrip(":")
    assert run(["list", "--db", str(db)]) == 0
    list_output = capsys.readouterr().out
    assert "[ ]" in list_output
    assert task_id in list_output
    assert run(["done", task_id, "--db", str(db)]) == 0
    done_output = capsys.readouterr().out
    assert "Marked done" in done_output
    assert run(["list", "--status", "done", "--db", str(db)]) == 0
    done_list = capsys.readouterr().out
    assert "[x]" in done_list
    assert task_id in done_list
    assert run(["done", "bogus", "--db", str(db)]) == 1
    missing_output = capsys.readouterr().out
    assert "No TODO with id bogus" in missing_output


def test_cli_rejects_empty_text(tmp_path, capsys):
    db = tmp_path / "todo.json"
    assert run(["add", "   ", "--db", str(db)]) == 1
    error_output = capsys.readouterr().out
    assert "Task text cannot be empty" in error_output
