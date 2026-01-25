from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import Task
from .repository import TodoRepository

DEFAULT_DB = Path("todo.json")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a TODO list stored in JSON.")
    parser.add_argument("--db", "-d", type=Path, default=DEFAULT_DB, help="Path to the JSON store.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new TODO.")
    add_parser.add_argument("text", help="Text describing the TODO.")

    list_parser = subparsers.add_parser("list", help="List TODOs.")
    list_parser.add_argument("--status", choices=("todo", "done"), help="Filter by status.")

    done_parser = subparsers.add_parser("done", help="Mark a TODO done.")
    done_parser.add_argument("id", help="ID of the TODO to mark done.")

    return parser


def _format_task(task: Task) -> str:
    marker = "x" if task.status == "done" else " "
    return f"[{marker}] {task.id} {task.text} ({task.created_at})"


def run(args: list[str] | None = None) -> int:
    parser = _build_parser()
    parsed = parser.parse_args(args)
    repo = TodoRepository(parsed.db)
    try:
        if parsed.command == "add":
            task = repo.add_task(parsed.text)
            print(f"Added {task.id}: {task.text}")
        elif parsed.command == "list":
            tasks = repo.list_tasks(parsed.status)
            if not tasks:
                print("No matching TODOs.")
                return 0
            for task in tasks:
                print(_format_task(task))
        elif parsed.command == "done":
            task = repo.mark_done(parsed.id)
            print(f"Marked done: {_format_task(task)}")
        return 0
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    except KeyError:
        print(f"No TODO with id {parsed.id}")
        return 1


if __name__ == "__main__":
    sys.exit(run())
