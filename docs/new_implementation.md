# Neue Implementierung – Planner‑Gate Multi‑Agent‑System

## 1. Verzeichnisstruktur

```
.runs/{run_id}/
├─ manifest.jsonl
├─ pool.json
├─ inbox.jsonl
├─ answers.jsonl
├─ waves/
│  ├─ wave_01_compact.md
│  └─ wave_01_detailed.md
├─ artifacts/
└─ metrics.jsonl
```

Alle Writes erfolgen atomar (`.tmp → rename`).

---

## 2. Zentrale Datenstrukturen

### 2.1 Delegation
```json
{
  "delegation_id": "uuid",
  "agent_id": "code_agent",
  "task": "...",
  "acceptance_criteria": ["..."],
  "required_inputs": ["db_schema"],
  "provided_inputs": ["detail:db_schema_v2"],
  "depends_on": []
}
```

---

### 2.2 ContextPacket
```json
{
  "planner_compact": "wave_compact.md",
  "detail_index": [
    {"id":"d3","title":"DB Schema","summary":"Tables + relations","tags":["db","schema"]}
  ],
  "answered_questions": [],
  "active_assumptions": []
}
```

---

### 2.3 WorkerOutput
```json
{
  "status": "completed",
  "compact_md": "...",
  "detailed_md": "...",
  "blocking_questions": [],
  "optional_questions": [],
  "missing_info_requests": [],
  "assumptions_made": [],
  "coverage": {
    "criteria_met": [],
    "criteria_unmet": []
  }
}
```

Validator unterscheidet:
- fatal errors (JSON, Pflichtfelder)
- non‑fatal errors (Länge → truncation + note)

---

## 3. Orchestrator‑Logik

### 3.1 Vor Ausführung
- DAG‑Validierung (`depends_on`)
- Capability‑Check gegen Agent Registry
- Vollständigkeits‑Gate (`required_inputs`)

### 3.2 Nach Agent‑Antwort
- Persistenz aller Artefakte
- Dedup von Fragen (hash)
- Status‑Update der Delegation

---

## 4. Planner‑Wave

1. Sammle alle WorkerOutputs
2. Erkenne Konflikte
3. Beantworte oder eskaliere Fragen
4. Schreibe:
   - `wave_compact.md` (gültige Wahrheit)
   - `wave_detailed.md`
5. Erzeuge `planner_decision`

---

## 5. Pool‑Merge

Pool‑Eintrag:
```json
{
  "id": "fact_123",
  "content": "...",
  "origin": "delegation",
  "confidence": 0.8,
  "is_assumption": false,
  "source_refs": ["wave_01"],
  "superseded_by": null
}
```

---

## 6. Frage‑Handling

- Jede Frage erhält `question_id = hash(text + source)`
- Antworten referenzieren `question_id`
- Beantwortete Fragen erscheinen im nächsten ContextPacket

---

## 7. Retry & Resume

- Idempotente IDs
- Resume aus `manifest.jsonl`
- Doppelte Writes werden erkannt und verworfen

---

## 8. Security & Tools

- Agent darf nur registrierte Tools nutzen
- Jeder Tool‑Call erzeugt `side_effect_log`
- Secrets werden vor Persistenz entfernt

---

## 9. Metriken

`metrics.jsonl`:
- wave_time
- agent_latency
- NOT_OK Gründe

---

## 10. Garantien der Implementierung

- Kein Agent arbeitet ohne Mindestkontext
- Blockierungen sind explizit
- Jeder Schritt ist reproduzierbar
- Fehler führen zu kontrollierter Eskalation, nicht zu Silent Failure

---

**Ende der Implementierung**

