# Architektur-Analyse & Verbesserungsplan

## Architektur-Überblick

Das Projekt ist ein **Multi-Role-Orchestrierungssystem** für die Codex CLI.
Mehrere KI-Rollen (Planner, Architect, Implementer, Integrator) arbeiten in Zyklen zusammen,
um Software-Projekte zu planen und umzusetzen.

### Stärken der aktuellen Architektur

- Klare Modul-Trennung (`utils/`, `roles/`, `orchestrator/`)
- Konsequente Eingabevalidierung via `ValidationMixin`
- Frozen Dataclasses für Immutability (`PromptFlags`, `RoleBehaviors`)
- Dependency Injection in fast allen Klassen
- 3-Schichten-Konfiguration (Defaults → YAML → Environment)
- Lazy-Loading im Package-`__init__.py`
- Protocol-basierte Abstraktion der Transportschicht

### Projektstruktur

```
.
├── main.py                            # Entry Point
├── defaults.py                        # Zentrale Konstanten
├── requirements.txt                   # Abhängigkeiten
├── config/
│   ├── main.yaml                      # Orchestrator-Konfiguration
│   ├── developer_config.yaml          # Rollen-Spezifikationen & Prompts
│   ├── roles/*.yaml                   # Einzelne Rollendefinitionen
│   └── skills/                        # Codex-Skills
└── codex_multi_role/                  # Haupt-Package
    ├── __init__.py                    # Lazy-Loading
    ├── logging.py                     # TimestampLogger
    ├── prompt_builder.py              # Prompt-Konstruktion
    ├── skills_preparer.py             # Skills-Vorbereitung
    ├── timeout_resolver.py            # Timeout-Auflösung
    ├── turn_result.py                 # TurnResult Dataclass
    ├── utils/
    │   ├── env_utils.py               # Environment-/Config-Reader
    │   ├── event_utils.py             # Event-Stream-Parsing
    │   ├── json_utils.py              # JSON-Extraktion
    │   ├── system_utils.py            # System-Operationen
    │   ├── validation_utils.py        # Validierungs-Mixin
    │   └── yaml_utils.py             # YAML-Loader
    ├── roles/
    │   ├── role_spec.py               # RoleSpecCatalog (~778 Zeilen)
    │   ├── role_spec_models.py        # RoleSpec, PromptFlags, RoleBehaviors
    │   ├── role_transport.py          # AppServerTransport (IPC)
    ├── client/
    │   └── codex_role_client.py       # CodexRoleClient
    └── orchestrator/
        ├── orchestrator_config.py     # OrchestratorConfig Dataclass
        └── orchestrator.py            # CodexRunsOrchestratorV2 (~894 Zeilen)
```

---

## Verbesserungsvorschläge

### P0 — Kritisch

#### 1. Tests hinzufügen

**Problem:** Keine einzige Testdatei bei ~3.500 Zeilen Produktionscode.

**Maßnahmen:**

- [ ] `tests/` Verzeichnis mit `conftest.py` und Fixtures anlegen
- [ ] Unit-Tests für alle Utility-Klassen (`JsonPayloadFormatter`, `EventParser`, `EnvironmentReader`, `RoleYamlLoader`)
- [ ] Unit-Tests für `ValidationMixin` und alle Dataclasses
- [ ] Integrationstests für `RoleSpecCatalog` (YAML → RoleSpec Pipeline)
- [ ] Integrationstests für `PromptBuilder` (Prompt-Zusammenbau)
- [ ] Mock-basierte Tests für `CodexRoleClient` und `AppServerTransport`
- [ ] End-to-End-Tests für `CodexRunsOrchestratorV2` mit gemockten Role Clients

#### 2. Tippfehler im Entry-Point-Dateinamen

**Problem:** `main.py` — vorheriger Name `codex_mulit_role_3_gen.py` war unklar und enthielt Tippfehler.

**Maßnahmen:**

- [ ] Umbenennung zu `main.py` oder `cli.py`
- [ ] Alternativ: `codex_multi_role/__main__.py` anlegen für `python -m codex_multi_role`

---

### P1 — Hoch

#### 3. Orchestrator aufteilen (~894 Zeilen)

**Problem:** `orchestrator.py` vereint zu viele Verantwortlichkeiten: Turn-Ausführung, JSON-Parsing, Datei-Persistenz, File-Application, Pytest-Ausführung, State-Management.

**Maßnahmen:**

- [ ] `ArtifactPersister` extrahieren — Schreiben von Turn-Artefakten, Handoff-JSON, Controller-State
- [ ] `FileApplicator` extrahieren — Validierung und Anwendung generierter Dateien
- [ ] `TestRunner` extrahieren — Pytest-Ausführung und Ergebnis-Parsing
- [ ] `TurnExecutor` extrahieren — Turn-Ausführung mit JSON-Repair-Logik
- [ ] `OrchestratorState` als eigene Dataclass statt `Dict[str, Any]`

#### 4. RoleSpecCatalog aufteilen (~778 Zeilen)

**Problem:** `role_spec.py` mischt Konfigurationsloading, Prompt-Formatierung und Capability-Regeln.

**Maßnahmen:**

- [ ] `PromptFormatter` extrahieren — `format_general_prompt`, `json_contract_instruction`, `schema_hint_non_json`, `capability_rules`
- [ ] `RoleBuilder` extrahieren — `_build_role`, `_merge_prompt_flags`, `_merge_behaviors`
- [ ] `RoleSpecCatalog` bleibt als Fassade, delegiert an die neuen Klassen

#### 5. Package-Struktur mit pyproject.toml

**Problem:** Kein `pyproject.toml` oder `setup.py`. Projekt ist nicht als Package installierbar.

**Maßnahmen:**

- [ ] `pyproject.toml` mit Build-System-Konfiguration anlegen
- [ ] Entry-Point-Definition für CLI unter `[project.scripts]`
- [ ] `__version__` in `__init__.py` definieren
- [ ] Dev-Dependencies hinzufügen (`pytest`, `pytest-cov`, `mypy`)

#### 6. Logging verbessern

**Problem:** `TimestampLogger` ist ein Print-Wrapper ohne Log-Levels, File-Logging oder strukturierte Ausgabe.

**Maßnahmen:**

- [ ] Migration zu Pythons `logging`-Modul mit konfigurierbaren Levels
- [ ] Strukturiertes Logging (JSON-Format) für maschinelle Auswertung
- [ ] Separate Log-Datei pro Run in `.runs/{run_id}/`
- [ ] Log-Rotation für langlebige Instanzen

---

### P2 — Mittel

#### 7. Typisierter Orchestrator-State

**Problem:** State ist `Dict[str, Any]` — keine Typensicherheit, keine Autovervollständigung.

**Maßnahmen:**

- [ ] `OrchestratorState` Dataclass erstellen:
  ```python
  @dataclass
  class OrchestratorState:
      run_id: str
      goal: str
      cycles: int
      latest_json_by_role: Dict[str, Dict]
      history: List[TurnRecord]
  ```
- [ ] Zugriff über Properties statt Dict-Keys

#### 8. CLI-Interface

**Problem:** Kein standardisierter Einstiegspunkt. Konfiguration nur über `.env`/Environment-Variablen.

**Maßnahmen:**

- [ ] `codex_multi_role/__main__.py` anlegen
- [ ] `argparse` oder `click` für CLI-Parameter integrieren
- [ ] Ziel: `python -m codex_multi_role --goal "..." --cycles 3 --model gpt-5.1`

#### 9. Subprocess-Crash-Handling

**Problem:** Codex-Subprocess-Crashes werden nicht konsistent erkannt. Queue-Kommunikation kann bei Prozess-Tod hängenbleiben.

**Maßnahmen:**

- [ ] Health-Check via `process.poll()` in der Event-Loop
- [ ] Retry-Logik mit exponentiellem Backoff bei Prozess-Crashes
- [ ] Structured Logging der Fehlerzustände
- [ ] Timeout-Absicherung gegen tote Prozesse

#### 10. Mypy-Integration

**Problem:** Type-Hints vorhanden, aber keine statische Analyse konfiguriert.

**Maßnahmen:**

- [ ] `[tool.mypy]` in `pyproject.toml` konfigurieren
- [ ] `--strict` Mode aktivieren
- [ ] CI-Step für Type-Checking

#### 11. Magic Strings eliminieren

**Problem:** Keys wie `"analysis_md"`, `"files"`, `"status"` und Pfade als String-Literale verstreut.

**Maßnahmen:**

- [ ] Payload-Keys als `Enum` definieren:
  ```python
  class PayloadKeys(str, Enum):
      ANALYSIS = "analysis_md"
      FILES = "files"
      STATUS = "status"
  ```
- [ ] Alle Pfade vollständig in `defaults.py` zentralisieren

---

### P3 — Niedrig / Langfristig

#### 12. Async Migration

**Problem:** Alle I/O-Operationen laufen synchron. Background-Thread in `AppServerTransport` ist ein Workaround.

**Maßnahmen:**

- [ ] Migration zu `asyncio` für die Event-Loop
- [ ] `asyncio.subprocess` statt `subprocess.Popen` + Thread
- [ ] Optional: `aiofiles` für asynchrones Datei-I/O
- [ ] Ermöglicht parallele Role-Ausführung

#### 13. ValidationMixin vereinfachen

**Problem:** 217 Zeilen Boilerplate-Validierung, manueller Aufruf in jedem `__post_init__`.

**Maßnahmen:**

- [ ] Deklarativer Ansatz mit `pydantic` v2 oder `attrs` mit Validators evaluieren
- [ ] Alternativ: Custom Decorator `@validated_dataclass`
- [ ] Ziel: ~50% weniger Code pro Dataclass

#### 14. Parallele Role-Ausführung

**Problem:** Rollen werden strikt sequentiell ausgeführt. `depends_on` in der YAML-Konfiguration existiert, wird aber nicht genutzt.

**Maßnahmen:**

- [ ] DAG-basierte Ausführungsplanung implementieren
- [ ] `depends_on` auswerten für Abhängigkeitsgraph
- [ ] Unabhängige Rollen parallel ausführen via `concurrent.futures` oder `asyncio.gather`

#### 15. Graceful Degradation

**Problem:** Fehlgeschlagene Rollen brechen den gesamten Zyklus ab.

**Maßnahmen:**

- [ ] `optional: true` Flag in der Rollenkonfiguration
- [ ] Skip-Logik für fehlgeschlagene optionale Rollen
- [ ] Retry-Logik pro Rolle (nicht nur JSON-Repair)
- [ ] Ergebnis-Aggregation für partielle Ergebnisse

#### 16. API-Dokumentation

**Problem:** Docstrings vorhanden, aber keine zusammenhängende Dokumentation.

**Maßnahmen:**

- [ ] Sphinx oder MkDocs Setup
- [ ] Getting-Started Guide
- [ ] Konfigurationsreferenz (alle Environment-Variablen und YAML-Keys)
- [ ] Architektur-Diagramm erweitern

#### 17. Security Hardening

**Problem:** `_is_safe_relative_path()` prüft nur Path-Traversal. Weitere Risiken nicht abgedeckt.

**Maßnahmen:**

- [ ] Symlink-Prüfung via `Path.resolve()` gegen Workspace-Root
- [ ] Whitelist für erlaubte Dateiendungen
- [ ] Warnung bei `.env`-Commit-Risiko
- [ ] Sandbox-Modus für Dateioperationen

#### 18. Caching

**Problem:** YAML-Dateien und Prompts werden bei jedem Zyklus neu geladen/gebaut.

**Maßnahmen:**

- [ ] `@functools.lru_cache` für YAML-Loading und Prompt-Templates
- [ ] Lazy-Evaluation für selten gebrauchte Konfigurationswerte

---

## Priorisierungsübersicht

| Prio | # | Verbesserung | Aufwand |
|------|---|---|---|
| P0 | 1 | Tests hinzufügen | Hoch |
| P0 | 2 | Tippfehler im Dateinamen | Niedrig |
| P1 | 3 | Orchestrator aufteilen | Mittel |
| P1 | 4 | RoleSpecCatalog aufteilen | Mittel |
| P1 | 5 | pyproject.toml + Package-Struktur | Niedrig |
| P1 | 6 | Logging verbessern | Mittel |
| P2 | 7 | Typisierter State | Niedrig |
| P2 | 8 | CLI-Interface | Niedrig |
| P2 | 9 | Subprocess-Crash-Handling | Mittel |
| P2 | 10 | Mypy-Integration | Niedrig |
| P2 | 11 | Magic Strings eliminieren | Niedrig |
| P3 | 12 | Async Migration | Hoch |
| P3 | 13 | ValidationMixin vereinfachen | Mittel |
| P3 | 14 | Parallele Role-Ausführung | Hoch |
| P3 | 15 | Graceful Degradation | Mittel |
| P3 | 16 | API-Dokumentation | Mittel |
| P3 | 17 | Security Hardening | Mittel |
| P3 | 18 | Caching | Niedrig |
