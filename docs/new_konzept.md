# Neues Kommunikationskonzept für ein Planner‑zentriertes Multi‑Agent‑System

## 1. Ziel des Konzepts
Dieses Konzept beschreibt eine robuste, skalierbare und auditierbare Kommunikation zwischen mehreren spezialisierten Agents, die **ausschließlich über einen Planner** koordiniert werden. Ziel ist:
- deterministische Abläufe
- klare Verantwortlichkeiten
- vollständige Informationsversorgung der Agents
- kontrollierte Fehler‑ und Fragebehandlung

Der Planner ist die **Single Source of Truth** für Entscheidungen, während der Pool als Arbeits‑ und Erinnerungsstruktur dient.

---

## 2. Grundprinzipien

### 2.1 Planner‑Gate‑Prinzip
- Agents kommunizieren **niemals direkt miteinander**
- Jeder Informationsfluss läuft über: `Agent → Orchestrator → Planner → Orchestrator → Agent`
- Der Planner entscheidet über:
  - Konsolidierung
  - Weiterdelegation
  - Konfliktauflösung
  - User‑Interaktion

### 2.2 Trennung von Rollen
- **Agent**: Löst klar abgegrenzte Aufgaben
- **Planner**: Denkt global, entscheidet, konsolidiert
- **Orchestrator**: Erzwingt Regeln, validiert, persistiert

---

## 3. Delegation Lifecycle

### 3.1 Delegationserstellung (Planner)
Jede Delegation enthält:
- `delegation_id`
- `agent_id`
- `task_description`
- `acceptance_criteria[]`
- `required_inputs[]`
- `provided_inputs[]`
- `depends_on[]`

**Gate:** Wenn `required_inputs − provided_inputs ≠ ∅` → Delegation darf nicht starten.

---

## 4. ContextPacket

Jeder Agent erhält ein **ContextPacket**, bestehend aus:

- `planner_compact_input.md` (kuratierte, kurze Wahrheit)
- `detail_index[]`:
  - `detail_id`
  - `title`
  - `summary`
  - `tags[]`
- `answered_questions[]`
- `active_assumptions[]`

Der Agent darf **gezielt Details nachladen**, nicht alles auf einmal.

---

## 5. Agent Output

Jeder Agent liefert:

### 5.1 Pflichtartefakte
- `compact_md` (max. strukturiert, truncation erlaubt)
- `detailed_md` (vollständig, referenzierbar)

### 5.2 Strukturierte Metadaten
- `status`: `completed | blocked | failed`
- `blocking_questions[]`
- `optional_questions[]`
- `missing_info_requests[]`
- `assumptions_made[]`
- `coverage`:
  - `criteria_met[]`
  - `criteria_unmet[]`

---

## 6. Fragen‑ und Blockierungsmodell

### 6.1 Blocking Questions
- verhindern Abschluss
- erzwingen Planner‑Aktion

### 6.2 Optionale Fragen
- beeinflussen Qualität, nicht Abschluss

**Regel:**
- `blocked` ⇒ mindestens eine `blocking_question`
- `completed` ⇒ nur optionale Fragen erlaubt

---

## 7. Planner‑Entscheidung (Wave‑Entscheidung)

Der Planner erzeugt pro Wave:

- `wave_compact.md` → **Source of Truth**
- `wave_detailed.md` → Begründung & Nachvollziehbarkeit

Zusätzlich:
- `planner_decision`:
  - `io_status: OK | NOT_OK`
  - `not_ok_reasons[]`
  - `conflicts_resolved[]`
  - `next_actions[]`

---

## 8. Konfliktmanagement

- Widersprüche werden explizit erkannt
- Konflikte müssen vom Planner aufgelöst werden
- Pool‑Einträge bekommen:
  - `superseded_by`
  - `origin`

---

## 9. Pool‑Regeln

- append‑only
- Facts enthalten:
  - `source_refs[]`
  - `confidence`
  - `is_assumption`

**Planner‑Dokumente schlagen Pool‑Inhalte**

---

## 10. User‑Interaktion

- Nur der Planner spricht mit dem User
- `needs_user_input=true` nur bei **kritischen offenen Fragen**
- User‑Fragen enthalten:
  - `priority`
  - `expected_answer_format`

---

## 11. Sicherheit & Governance

- Agent Registry mit:
  - capabilities
  - allowed_tools
  - risk_level
- Tool‑Side‑Effects werden geloggt
- Secrets werden vor Persistenz redacted

---

## 12. Observability & Stabilität

- jede Operation ist idempotent
- atomare Writes
- Metriken:
  - wave_duration
  - failure_reasons
  - agent_success_rate

---

## 13. Garantien des Konzepts

Dieses Konzept garantiert:
- keine Agent‑Blindflüge
- keine stillen Informationsverluste
- nachvollziehbare Entscheidungen
- kontrollierte Eskalation bei Unklarheiten

Nicht garantiert (bewusst):
- inhaltliche Korrektheit ohne menschliche Entscheidungen

---

**Ende des Konzepts**

