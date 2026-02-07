# Rollen-Parameter Referenz (config/roles)

Diese Datei beschreibt **alle konfigurierbaren Rollen-Parameter** fuer:

- Rollen-Eintraege in `config/developer_config.yaml` unter `roles`
- Rollen-Dateien in `config/roles/*.yaml` (via `role_file`)

## Merge-Logik (wichtig)

Die effektive Rollen-Konfiguration entsteht in dieser Reihenfolge:

1. `defaults` aus `config/developer_config.yaml`
2. `role_file` (falls gesetzt) wird geladen und gemerged
3. Inline-Eintrag in `roles` ueberschreibt `role_file`

Zusatzregeln:
- `prompt_text` hat Vorrang vor `prompt_file`, wenn beide gesetzt sind.
- `name` muss nicht in beiden stehen, **aber** wenn in beiden vorhanden, muessen
  sie identisch sein.

## Beispiel (YAML)

```yaml
# config/developer_config.yaml (Auszug)
roles:
  - name: planner
    role_file: roles/planner.yaml
    model: "gpt-5.1-codex-mini"
    model_env: "PLANNER_MODEL"
    prompt_text: "Du bist PLANNER. Nur JSON."
    prompt_file: "roles/planner_prompt.txt"
    skills:
      - "python-architect"
    reasoning_effort: medium
    prompt_flags:
      allow_tools: true
      allow_read: true
      allow_write: false
      allow_file_suggestions: false
    behaviors:
      timeout_policy: planner
      apply_files: false
      can_finish: false

# config/roles/planner.yaml (Auszug)
name: planner
model: "gpt-5.1-codex-mini"
model_env: "PLANNER_MODEL"
prompt_text: |
  Du bist PLANNER. Plane und delegiere. Gib next_owner zurueck. Nur JSON.
prompt_file: "roles/planner_prompt.txt"
skills:
  - "python-architect"
reasoning_effort: high
prompt_flags:
  allow_tools: true
  allow_read: true
  allow_write: false
  allow_file_suggestions: false
behaviors:
  timeout_policy: planner
  apply_files: false
  can_finish: false
```

## Parameter (Rollen-Konfiguration)

### `name` (Pflicht)
- **Typ:** String (nicht leer)
- **Bedeutung:** Eindeutiger Rollenname (z.B. `planner`, `architect`).
- **Werte:** Beliebiger nicht-leerer String.
- **Hinweis:** Muss zwischen `roles[].name` und `role_file.name` uebereinstimmen,
  wenn beide gesetzt sind.

### `model`
- **Typ:** String (optional)
- **Bedeutung:** Konkreter Modellname fuer diese Rolle.
- **Werte:** Beliebiger nicht-leerer String, z.B. `gpt-5.1-codex-mini`.
- **Fallback:** Wenn nicht gesetzt, siehe `model_env` bzw. Default-Model.

### `model_env`
- **Typ:** String (optional)
- **Bedeutung:** Name einer Umgebungsvariable, die den Modellnamen liefert.
- **Werte:** Beliebiger nicht-leerer String (z.B. `MY_ROLE_MODEL`).
- **Fallback:** Wenn Env-Var leer/nicht gesetzt ist, wird das Default-Model genutzt.

### `prompt_text`
- **Typ:** String (optional, aber erforderlich falls `prompt_file` fehlt)
- **Bedeutung:** System-Prompt der Rolle als Inline-Text.
- **Werte:** Beliebiger nicht-leerer String.
- **Prioritaet:** Hat Vorrang vor `prompt_file`, wenn beide gesetzt sind.

### `prompt_file`
- **Typ:** String (optional, aber erforderlich falls `prompt_text` fehlt)
- **Bedeutung:** Pfad zu einer Datei mit dem System-Prompt.
- **Werte:** Absoluter Pfad oder relativer Pfad relativ zu `config/`.
- **Validierung:** Datei muss existieren und ein lesbarer Text sein.

### `skills`
- **Typ:** Liste von Strings (optional)
- **Bedeutung:** Skill-Namen, die als `$skill-name` in den Prompt eingefuegt
  werden, damit Codex CLI die Skills aus `.codex/skills` laedt.
- **Werte:** Skill-Namen (mussen mit `name` in `SKILL.md` uebereinstimmen).
- **Validierung:** Eintraege muessen nicht leer sein; Aufloesung erfolgt durch
  Codex CLI beim Prompt-Handling.

### `reasoning_effort`
- **Typ:** String (optional)
- **Bedeutung:** Reasoning-Label fuer das Modell.
- **Werte:** Beliebiger nicht-leerer String.
- **Hinweis:** Hauefige Werte sind `low`, `medium`, `high`, die exakten Labels
  haengen jedoch vom Modell/Backend ab.
- **Fallback:** Leerer String oder nicht gesetzt -> Default aus `defaults`.

### `prompt_flags`
Steuert, welche Regeln der Orchestrator in die Rolle schreibt.

#### `prompt_flags.allow_tools`
- **Typ:** Bool
- **Werte:** `true` | `false`
- **Effekt:** Erlaubt/verbietet Tools/Commands in der Rolle.

#### `prompt_flags.allow_read`
- **Typ:** Bool
- **Werte:** `true` | `false`
- **Effekt:** Erlaubt/verbietet Dateilesen in der Rolle.

#### `prompt_flags.allow_write`
- **Typ:** Bool
- **Werte:** `true` | `false`
- **Effekt:** Erlaubt/verbietet Dateischreiben in der Rolle.

#### `prompt_flags.allow_file_suggestions`
- **Typ:** Bool
- **Werte:** `true` | `false`
- **Effekt:** Wenn `true`, soll die Rolle Datei-Aenderungen als Vorschlag liefern
  (z.B. `files=[{path,content}]`).

### `behaviors`
Steuert Orchestrator-Verhalten fuer die Rolle.

#### `behaviors.timeout_policy`
- **Typ:** String (nicht leer)
- **Werte:** Beliebiger String.
- **Spezialwert:** `planner` -> verwendet `PLANNER_TIMEOUT_S`.
- **Sonst:** Jeder andere Wert -> verwendet `ROLE_TIMEOUT_S`.

#### `behaviors.apply_files`
- **Typ:** Bool
- **Werte:** `true` | `false`
- **Effekt:** Wenn `true`, werden Datei-Vorschlaege automatisch angewendet.

#### `behaviors.can_finish`
- **Typ:** Bool
- **Werte:** `true` | `false`
- **Effekt:** Wenn `true`, kann die Rolle den Lauf beenden (Status `DONE`).

## Parameter nur im `roles`-Eintrag

### `role_file`
- **Typ:** String (optional)
- **Bedeutung:** Verweis auf eine Rollen-Datei (YAML), die geladen wird.
- **Werte:** Absoluter Pfad oder relativer Pfad relativ zu `config/`.
- **Validierung:** Muss existieren und eine Mapping-Struktur enthalten.
- **Merge:** `role_file` wird geladen, dann ueberschreibt der Inline-Eintrag.

### `schema_hint_template`
- **Typ:** String (optional)
- **Bedeutung:** Key im globalen `schema_hints`-Block, der fuer diese Rolle
  verwendet wird.
- **Fallback:** Wenn nicht gesetzt, wird der Rollenname als Key verwendet.
- **Validierung:** Der referenzierte Key muss in `schema_hints` existieren.

### `schema_hint_params`
- **Typ:** Mapping (optional)
- **Bedeutung:** Platzhalterwerte fuer das ausgewaehlte Schema-Template.
- **Validierung:** Alle Keys muessen nicht-leere Strings sein.
- **Typischer Einsatz:** Rolle-spezifische Unterschiede wie optionaler
  `files`-Block ohne Duplikation des gesamten Schemas.

Beispiel:
```yaml
roles:
  - name: implementer
    role_file: roles/implementer.yaml
    schema_hint_template: worker_agent
    schema_hint_params:
      files_block: |
        files:
          - path: <string>
            content: <string>
```

