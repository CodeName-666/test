# Konfiguration developer_config.yaml (Uebersicht)

Diese Datei beschreibt die Struktur und Parameter der
`config/developer_config.yaml`.

## Hauptstruktur

```yaml
version: 1
defaults:
  reasoning_effort: high
  prompt_flags:
    allow_tools: true
    allow_read: true
    allow_write: false
    allow_file_suggestions: false
  behaviors:
    timeout_policy: default
    apply_files: false
    can_finish: false
general_prompts:
  role_header: "Rolle: {role_name}\n"
  goal_section: "Ziel:\n{goal}\n"
  input_section: "\nInput (reduziertes JSON, klein halten):\n{input}\n"
  rules_header: "\nREGELN:\n"
  analysis_rules: |
    - Tiefe Analyse NUR im Feld analysis_md (Markdown String im JSON).
    - Ausgabe JSON klein halten (analysis_md darf lang sein, wird ausgelagert).
  json_contract: |
    FORMAT-VERTRAG (streng):
    - Antworte mit GENAU EINEM gueltigen JSON-Objekt.
    - KEIN Text ausserhalb des JSON. KEIN Markdown-Codefence.
    - Wenn unklar: gib JSON mit Feld "error" zurueck.
schema_hints:
  planner: |
    SCHEMA-HINWEIS (planner, PSEUDO):
    summary: <string>
    tasks: [ { id: <string>, title: <string>, owner: architect|implementer|integrator, priority: <int> } ]
    next_owner: architect|implementer|integrator
    notes: <string>
  implementer: |
    SCHEMA-HINWEIS (implementer, PSEUDO):
    summary: <string>
    files: [ { path: <string>, content: <string> } ]
    analysis_md: <markdown>
    analysis_md_path: <string>  # setzt Controller
    next_owner_suggestion: planner
  default: |
    SCHEMA-HINWEIS ({role_name}, PSEUDO):
    summary: <string>
    key_points: [<string>]
    requests: { need_more_context: <bool>, files: [<string>], why: <string> }
    analysis_md: <markdown>
    analysis_md_path: <string>  # setzt Controller
    status: <DONE|CONTINUE?>
    next_owner_suggestion: planner
roles:
  - name: planner
    role_file: roles/planner.yaml
```

## Parameter-Referenz

### `version` (Pflicht)
- **Typ:** Integer
- **Bedeutung:** Versionskennung der Konfiguration.
- **Werte:** Derzeit `1`.

### `defaults`
Globale Defaults fuer alle Rollen. Diese Werte werden pro Rolle gemerged und
koennen in der Rolle ueberschrieben werden.

#### `defaults.reasoning_effort`
- **Typ:** String
- **Werte:** Beliebiger nicht-leerer String.

#### `defaults.prompt_flags`
- **Typ:** Mapping
- **Werte:** Siehe `prompt_flags` in der Rollen-Referenz.

#### `defaults.behaviors`
- **Typ:** Mapping
- **Werte:** Siehe `behaviors` in der Rollen-Referenz.

### `general_prompts`
Globale Prompt-Templates (Strings oder Block-Strings).

Moegliche Keys (aktuelle Implementierung):
- `role_header`
- `goal_section`
- `input_section`
- `rules_header`
- `analysis_rules`
- `json_contract`

### `schema_hints`
Globale Schema-Hints. Keys sind Rollen-Namen oder `default`.

Hinweise zur Redundanz-Reduktion:
- Definiere wiederverwendbare Templates als eigene Keys (z.B. `worker_agent`).
- Verwende in Templates Platzhalter wie `{role_name}` oder `{files_block}`.
- Rolle-spezifische Werte kommen aus `roles[].schema_hint_template`
  und `roles[].schema_hint_params` (siehe Rollen-Referenz).

### `roles` (Pflicht)
Liste der Rollen-Konfigurationen (Mapping je Rolle).
Details dazu stehen in `config/roles/ROLE_PARAMETERS.md`.
