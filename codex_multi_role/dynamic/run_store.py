"""Atomic persistence helpers for dynamic planner-gated runs."""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class RunStore:
    """Persist dynamic run artifacts with atomic writes and idempotent appends."""

    def __init__(self, run_directory: Path) -> None:
        """Initialize run storage.

        Args:
            run_directory: Base directory for the current run.

        Raises:
            TypeError: If run_directory is not a Path.
        """
        if not isinstance(run_directory, Path):
            raise TypeError("run_directory must be a pathlib.Path")
        self.run_directory = run_directory
        self.manifest_path = run_directory / "manifest.jsonl"
        self.pool_path = run_directory / "pool.json"
        self.inbox_path = run_directory / "inbox.jsonl"
        self.answers_path = run_directory / "answers.jsonl"
        self.metrics_path = run_directory / "metrics.jsonl"
        self.waves_directory = run_directory / "waves"
        self.artifacts_directory = run_directory / "artifacts"
        self._lock = threading.Lock()
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.waves_directory.mkdir(parents=True, exist_ok=True)
        self.artifacts_directory.mkdir(parents=True, exist_ok=True)
        if not self.pool_path.exists():
            self._atomic_write_json(self.pool_path, {"facts": []})
        for path in (
            self.manifest_path,
            self.inbox_path,
            self.answers_path,
            self.metrics_path,
        ):
            if not path.exists():
                self._atomic_write_text(path, "")

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, path)

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        text_content = json.dumps(payload, ensure_ascii=False, indent=2)
        self._atomic_write_text(path, text_content + "\n")

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped:
                    try:
                        payload = json.loads(stripped)
                        if isinstance(payload, dict):
                            records.append(payload)
                    except Exception:
                        # Invalid lines are ignored to keep runs resumable.
                        continue
        return records

    def _write_jsonl(self, path: Path, records: List[Dict[str, Any]]) -> None:
        serialized = ""
        if records:
            serialized = "\n".join(
                json.dumps(record, ensure_ascii=False) for record in records
            ) + "\n"
        self._atomic_write_text(path, serialized)

    def _append_jsonl(
        self,
        path: Path,
        record: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        if not isinstance(record, dict):
            raise TypeError("record must be a dictionary")
        if idempotency_key is not None and not isinstance(idempotency_key, str):
            raise TypeError("idempotency_key must be a string or None")

        with self._lock:
            records = self._read_jsonl(path)
            key_to_use = idempotency_key or record.get("idempotency_key")
            already_present = False
            if isinstance(key_to_use, str) and key_to_use.strip():
                for existing in records:
                    existing_key = existing.get("idempotency_key")
                    if existing_key == key_to_use:
                        already_present = True
                        break
            if already_present:
                result = False
            else:
                records.append(record)
                self._write_jsonl(path, records)
                result = True
        return result

    def append_manifest(
        self,
        event_type: str,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Append a manifest event.

        Args:
            event_type: Event type label.
            payload: Event payload.
            idempotency_key: Optional key used to suppress duplicates.

        Returns:
            True when event was appended, False if duplicate key was detected.
        """
        if not isinstance(event_type, str):
            raise TypeError("event_type must be a string")
        if not event_type.strip():
            raise ValueError("event_type must not be empty")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        manifest_event = {
            "event_type": event_type,
            "payload": payload,
            "idempotency_key": idempotency_key,
        }
        result = self._append_jsonl(
            self.manifest_path,
            manifest_event,
            idempotency_key=idempotency_key,
        )
        return result

    def append_inbox(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Append an inbox record from worker outputs."""
        result = self._append_jsonl(
            self.inbox_path,
            payload,
            idempotency_key=idempotency_key,
        )
        return result

    def append_answer(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Append a user answer record."""
        result = self._append_jsonl(
            self.answers_path,
            payload,
            idempotency_key=idempotency_key,
        )
        return result

    def append_metric(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Append a metric record."""
        result = self._append_jsonl(
            self.metrics_path,
            payload,
            idempotency_key=idempotency_key,
        )
        return result

    def load_manifest(self) -> List[Dict[str, Any]]:
        """Load manifest events."""
        result = self._read_jsonl(self.manifest_path)
        return result

    def load_answers(self) -> List[Dict[str, Any]]:
        """Load persisted answer records."""
        result = self._read_jsonl(self.answers_path)
        return result

    def load_pool(self) -> Dict[str, Any]:
        """Load current pool document."""
        if self.pool_path.exists():
            raw_text = self.pool_path.read_text(encoding="utf-8")
            try:
                payload = json.loads(raw_text)
                if isinstance(payload, dict):
                    if isinstance(payload.get("facts"), list):
                        result = payload
                    else:
                        result = {"facts": []}
                else:
                    result = {"facts": []}
            except Exception:
                result = {"facts": []}
        else:
            result = {"facts": []}
        return result

    def merge_pool_entries(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge pool entries into `pool.json`.

        Args:
            entries: New fact entries to merge.

        Returns:
            Updated pool document.
        """
        if not isinstance(entries, list):
            raise TypeError("entries must be a list")
        with self._lock:
            pool_document = self.load_pool()
            existing_facts = pool_document.get("facts", [])
            if not isinstance(existing_facts, list):
                existing_facts = []
            facts_by_id: Dict[str, Dict[str, Any]] = {}
            for fact in existing_facts:
                if isinstance(fact, dict):
                    fact_id = fact.get("id")
                    if isinstance(fact_id, str) and fact_id.strip():
                        facts_by_id[fact_id] = fact
            for entry in entries:
                normalized_entry = self._normalize_pool_entry(entry)
                existing_entry = facts_by_id.get(normalized_entry["id"])
                if existing_entry is None:
                    self._mark_superseded_fact(existing_facts, normalized_entry)
                    existing_facts.append(normalized_entry)
                    facts_by_id[normalized_entry["id"]] = normalized_entry
            updated_pool = {"facts": existing_facts}
            self._atomic_write_json(self.pool_path, updated_pool)
        return updated_pool

    def _normalize_pool_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            raise TypeError("pool entry must be a dictionary")
        normalized_id = self._normalize_pool_id(entry)
        content_value = entry.get("content", "")
        if isinstance(content_value, str):
            normalized_content = content_value
        else:
            normalized_content = str(content_value)
        origin_value = entry.get("origin", "delegation")
        if isinstance(origin_value, str) and origin_value.strip():
            normalized_origin = origin_value
        else:
            normalized_origin = "delegation"
        normalized_confidence = self._normalize_pool_confidence(entry.get("confidence", 0.5))
        normalized_source_refs = self._normalize_pool_source_refs(entry.get("source_refs", []))
        is_assumption = bool(entry.get("is_assumption", False))
        superseded_by_value = entry.get("superseded_by")
        if isinstance(superseded_by_value, str) and superseded_by_value.strip():
            superseded_by = superseded_by_value
        else:
            superseded_by = None
        normalized = {
            "id": normalized_id,
            "content": normalized_content,
            "origin": normalized_origin,
            "confidence": normalized_confidence,
            "is_assumption": is_assumption,
            "source_refs": normalized_source_refs,
            "superseded_by": superseded_by,
        }
        return normalized

    def _normalize_pool_id(self, entry: Dict[str, Any]) -> str:
        entry_id = entry.get("id")
        normalized_id: str
        if isinstance(entry_id, str) and entry_id.strip():
            normalized_id = entry_id
        else:
            synthetic = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            normalized_id = f"fact_{uuid.uuid5(uuid.NAMESPACE_OID, synthetic).hex[:16]}"
        return normalized_id

    def _normalize_pool_confidence(self, value: Any) -> float:
        normalized_confidence = 0.5
        if isinstance(value, (int, float)):
            numeric = float(value)
            if numeric < 0:
                normalized_confidence = 0.0
            elif numeric > 1:
                normalized_confidence = 1.0
            else:
                normalized_confidence = numeric
        return normalized_confidence

    def _normalize_pool_source_refs(self, value: Any) -> List[str]:
        normalized_source_refs: List[str] = []
        if isinstance(value, list):
            for source in value:
                if isinstance(source, str):
                    normalized_source_refs.append(source)
                else:
                    normalized_source_refs.append(str(source))
        return normalized_source_refs

    def _mark_superseded_fact(
        self,
        existing_facts: List[Dict[str, Any]],
        new_fact: Dict[str, Any],
    ) -> None:
        for existing in existing_facts:
            same_content = existing.get("content") == new_fact.get("content")
            same_origin = existing.get("origin") == new_fact.get("origin")
            not_superseded = existing.get("superseded_by") is None
            if same_content and same_origin and not_superseded:
                existing["superseded_by"] = new_fact["id"]

    def write_wave_documents(
        self,
        wave_index: int,
        compact_md: str,
        detailed_md: str,
    ) -> Tuple[Path, Path]:
        """Write compact and detailed planner wave documents.

        Args:
            wave_index: One-based wave index.
            compact_md: Compact planner wave markdown.
            detailed_md: Detailed planner wave markdown.

        Returns:
            Tuple of (compact_path, detailed_path).
        """
        if wave_index < 1:
            raise ValueError("wave_index must be >= 1")
        compact_path = self.waves_directory / f"wave_{wave_index:02d}_compact.md"
        detailed_path = self.waves_directory / f"wave_{wave_index:02d}_detailed.md"
        self._atomic_write_text(compact_path, compact_md)
        self._atomic_write_text(detailed_path, detailed_md)
        result = (compact_path, detailed_path)
        return result

    def write_artifact(self, relative_path: str, content: str) -> Path:
        """Write an artifact under the run's artifacts directory.

        Args:
            relative_path: Relative path under `artifacts/`.
            content: Artifact content.

        Returns:
            Absolute artifact path.
        """
        if not isinstance(relative_path, str):
            raise TypeError("relative_path must be a string")
        if not relative_path.strip():
            raise ValueError("relative_path must not be empty")
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        artifact_path = self.artifacts_directory / relative_path.strip()
        self._atomic_write_text(artifact_path, content)
        return artifact_path
