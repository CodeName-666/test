from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .models import Task


class TodoRepository:
    STATUS_TODO = "todo"
    STATUS_DONE = "done"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def add_task(self, text: str) -> Task:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Task text cannot be empty")
        task = Task(
            id=str(uuid4()),
            text=cleaned,
            status=self.STATUS_TODO,
            created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        )
        tasks = self._read_tasks()
        tasks.append(task)
        self._write_tasks(tasks)
        return task

    def list_tasks(self, status: str | None = None) -> list[Task]:
        tasks = self._read_tasks()
        if status is None:
            return sorted(tasks, key=lambda t: t.created_at)
        if status not in {self.STATUS_TODO, self.STATUS_DONE}:
            raise ValueError(f"Unsupported status '{status}'")
        return [task for task in tasks if task.status == status]

    def mark_done(self, task_id: str) -> Task:
        tasks = self._read_tasks()
        for task in tasks:
            if task.id == task_id:
                if task.status == self.STATUS_DONE:
                    return task
                task.status = self.STATUS_DONE
                self._write_tasks(tasks)
                return task
        raise KeyError(task_id)

    def _read_tasks(self) -> list[Task]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
        return [Task.from_dict(item) for item in payload]

    def _write_tasks(self, tasks: Iterable[Task]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump([task.to_dict() for task in tasks], handle, indent=2)
