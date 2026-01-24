from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


@dataclass
class Task:
    id: str
    text: str
    status: Literal["todo", "done"]
    created_at: str

    @classmethod
    def from_dict(cls, data: Mapping[str, str]) -> "Task":
        return cls(
            id=data["id"],
            text=data["text"],
            status=data["status"],
            created_at=data["created_at"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "created_at": self.created_at,
        }
