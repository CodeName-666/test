# Agent.md – Codex CLI Regelwerk (Python, Safety-First)

## 0. Zweck (normativ)

Dieses Dokument ist die **verbindliche Quelle der Wahrheit** für **codex CLI** und andere automatische Tools (Lint/Refactor/Review).
Es beschreibt **was** zu prüfen ist, **wann** etwas als Fehler gilt und **welche Korrekturmaßnahmen** umzusetzen sind.

**Ziel:** Python-Code nach Functional-Safety-Prinzipien: **deterministisch**, **auditierbar**, **debuggbar**, **sicher**, **performant** und **leicht verständlich**.

**Durchsetzung:** Alle Regeln sind **HARD (fail-fast)**, sofern nicht explizit als Ausnahme markiert.

---

## 0.1 Proportionalitätsprinzip (Meta-Regel)

**Regel (HARD):**
- Der Aufwand einer Schutzmaßnahme (Validierung, Extraktion, Dokumentation) muss im **Verhältnis zum tatsächlichen Risiko** stehen.
- Code, der nur zur formalen Regelerfüllung existiert, aber weder Lesbarkeit, Sicherheit noch Debuggability verbessert, gilt als **Ballast** und ist zu entfernen.
- Diese Meta-Regel hat **Vorrang** vor allen folgenden Regeln: Wenn eine Einzelregel an einer konkreten Stelle keinen Mehrwert erzeugt, greift die Ausnahme dieser Meta-Regel.

**Prüffragen vor jeder Maßnahme (alle müssen mit Ja beantwortet werden):**
1. **Kann der abgesicherte Fehler in der Praxis auftreten?** (z. B. kommt der Wert von außen oder ist der Typ unkontrolliert?)
2. **Wird der Code durch die Maßnahme besser lesbar, sicherer oder debugbarer?**
3. **Würde das Weglassen der Maßnahme ein reales Risiko erzeugen?**

Wird eine Frage mit Nein beantwortet, ist die Maßnahme an dieser Stelle **nicht anzuwenden**.

**Negativbeispiele (verboten — reiner Ballast):**
```py
# Sinnlose result-Variable bei Einzeiler
def get_name(self) -> str:
    name = self.name
    result = name
    return result
# Korrekt:
def get_name(self) -> str:
    return self.name

# isinstance-Check in privater Methode für intern übergebenen Wert
def _compute(self, value: float) -> float:
    if not isinstance(value, float):   # Aufrufer übergibt immer float
        raise TypeError(...)
# Korrekt: Check entfernen, Typ steht durch Aufrufer fest.

# Explizites return None bei -> None
def stop(self) -> None:
    self.process.terminate()
    return None  # Python gibt implizit None zurück
# Korrekt: return None weglassen.
```

---

## 1. Geltungsbereich & Priorität

### 1.1 Dateien
Gilt für alle Python-Dateien (`.py`) im Repository, sofern nicht ausdrücklich ausgenommen.

### 1.2 Begriffe
- **Public API:** Funktionen/Klassen/Methoden, die außerhalb eines Moduls genutzt werden (Exports, Service-Schicht, Library-API).
- **CORE:** Domänenlogik, Business-Regeln, Validierung, Services, Utilities (alles, was Verhalten/Entscheidungen bestimmt).
- **EDGE/IO:** Adapter/Framework-Glue (HTTP-Handler, CLI-Parsing, DB/FS/Network), Serialisierung/Deserialisierung.

> Hinweis: Auch EDGE/IO ist hart geregelt. CORE hat bei Konflikten immer Vorrang.

---

## 2. Kontrollfluss & Functional-Safety

### 2.1 Single-Return-Regel (zentraler Exit Point)
**Regel (HARD):**
- Funktionen mit **Verzweigungslogik** (if/elif/else, try/except, Schleifen mit bedingtem Abbruch) müssen **genau einen** `return` am Ende besitzen.
- Das Ergebnis wird in einer `result`-Variable gesammelt und am Ende zurückgegeben.
- **Early returns sind verboten** (Ausnahmen siehe unten).

**Intention:** deterministischer Kontrollfluss, zentrale Stelle für Logging/Tracing/Cleanup, bessere Debuggability.

**Ausnahmen (HARD — in diesen Fällen gilt die Regel NICHT):**
- **Triviale Methoden** (≤ 3 Statements, kein Branching): Direkter `return <ausdruck>` ohne Zwischenvariable ist Pflicht. Eine `result`-Variable bei Einzeilern ist verbotener Ballast.
- **`-> None` Methoden**: Kein explizites `return None` am Ende schreiben. Python gibt implizit `None` zurück; ein redundantes `return None` verstößt gegen das Proportionalitätsprinzip (0.1).
- **Guard-Clauses**: Ein einzelnes `if not x: raise ...` am Methodenbeginn zur Absicherung einer Vorbedingung ist erlaubt und gilt nicht als Early Return, sondern als Precondition.

**Codex-Aktion:**
- Bei Verzweigungslogik: Refactor auf `result`-Variable und Rückgabe am Ende.
- Bei trivialen Methoden: Direkten `return` verwenden, keine Zwischenvariable.
- Kein `return None` bei `-> None` Methoden.

**Negativbeispiel (verboten — Ballast):**
```py
def _build_role_sequence(self, specs: List[RoleSpec]) -> List[str]:
    role_sequence = [s.name for s in specs]
    result = role_sequence
    return result
```
**Korrektur:**
```py
def _build_role_sequence(self, specs: List[RoleSpec]) -> List[str]:
    return [s.name for s in specs]
```

---

### 2.2 Explizite Fehlerreaktion im Else-Zweig
**Regel (HARD):**
- Wenn eine Vorbedingung geprüft wird, muss der **Fehlerpfad explizit** sein.
- Muster:
  `if <valid>:` → Happy Path
  `else:` → **Error-Reaktion** (Exception oder Error-Result)

**Scope — wo explizite Else-Zweige Pflicht sind:**
- **Systemgrenzen**: User-Input, CLI-Argumente, Umgebungsvariablen, Dateiinhalte, API-Responses, Netzwerk-Daten, Datenbank-Ergebnisse
- **Public API**: `__init__`, `__post_init__`, öffentliche Methoden (ohne `_`-Prefix)
- **Deserialisierung**: YAML/JSON/Config-Parsing, wo Typen nicht durch den Compiler garantiert sind

**Scope — wo explizite Else-Zweige NICHT eingefügt werden dürfen:**
- **Private Methoden** (`_`-Prefix), deren Eingaben ausschließlich von der eigenen Klasse stammen und bereits an der Systemgrenze validiert wurden.
- **Bereits validierte Daten**: Wenn ein Wert aus einem `@dataclass` mit `__post_init__`-Validierung kommt, ist eine erneute Typprüfung redundant und verboten.

**Faustregel:** Validiere **einmal an der Grenze**, vertraue danach intern. Doppelte Validierung ist Ballast, kein Safety-Feature.

**Beispiel (erwartet — Systemgrenze):**
```py
if value is not None:
    processed = value
else:
    raise ValueError("value must not be None")
```

**Negativbeispiel (verboten — interne Methode):**
```py
def _select_timeout(self, role_spec: RoleSpec, timeout: float) -> float:
    if not isinstance(role_spec, RoleSpec):  # Aufrufer übergibt validierten RoleSpec
        raise TypeError(...)
    if not isinstance(timeout, float):       # Aufrufer übergibt float aus get_float()
        raise TypeError(...)
```

**Nicht erlaubt:**
- implizite Fehler (späterer Crash ohne klare Ursache)
- „pass" oder stille Ignorierung im else-Zweig bei Systemgrenzen-Code
- redundante isinstance-Checks für intern weitergegebene, bereits validierte Werte

**Codex-Aktion:**
- An Systemgrenzen: fehlende else-Zweige ergänzen und eine definierte Fehlerreaktion einführen.
- In privaten Methoden mit validierten Inputs: bestehende isinstance-Checks **entfernen**.
- Fehlermeldungen müssen präzise sein (Kontext + Variable).

---

### 2.3 Keine verschachtelten Funktionen
**Regel (HARD):**
- Es darf **keine Funktionsdefinition innerhalb einer Funktion** geben (`def` in `def`).
- Gleiches gilt für „lokale Klassen“ (`class` in `def`), sofern nicht zwingend (Standard: verboten).

**Codex-Aktion:**
- Innere Funktionen/Klassen auf Modulebene oder in passende Klassen verschieben.
- Abhängigkeiten über Parameter/Instanzattribute übergeben.

---

### 2.4 Monolithische Blöcke sind verboten (Step-Function Pflicht)
**Regel (HARD):**
- Funktionen mit mehr als **15 Statements** oder **3+ Verschachtelungsebenen** müssen in benannte Schritte zerlegt werden.
- Komplexe Abläufe müssen in **kleine, benannte Schritte** ausgelagert werden („Step Functions").

**Ausnahmen (Extraktion ist verboten, wenn):**
- Die zu extrahierende Logik **weniger als 3 Statements** enthält UND nur an **einer einzigen Stelle** aufgerufen wird.
- Es sich um **triviale Prüfungen** handelt (einzelne Vergleiche, `isinstance`, `is None`), die inline besser lesbar sind.
- Die Extraktion den Leser zwingt, an eine andere Stelle zu springen, um einen Einzeiler zu verstehen.

**Faustregel:** Eine extrahierte Methode muss mindestens eines erfüllen:
1. Sie enthält **≥ 3 Statements**, ODER
2. Sie wird an **≥ 2 Stellen** aufgerufen, ODER
3. Sie kapselt eine **konzeptuelle Einheit**, die einen eigenen Namen verdient (z. B. Sicherheitslogik).

**Negativbeispiel (verboten — Einzeiler-Extraktion):**
```py
def _is_turn_completed(self, message: dict) -> bool:
    method_name = message.get("method")
    is_completed = method_name == METHOD_TURN_COMPLETED
    return is_completed
```
**Korrektur:** Inline am Aufrufort: `if message.get("method") == METHOD_TURN_COMPLETED:`

**Codex-Aktion:**
- Funktionen >15 Statements in 2–N kleinere Funktionen extrahieren (klar benannt).
- Triviale Einzeiler-Logik inline lassen.
- Vorbedingungen/Fehlerpfade nur gemäß Regel 2.2 (Scope beachten).

---

## 3. Strukturierung & Wiederverwendbarkeit

### 3.1 Logisches Bündeln (Modul oder Klasse)
**Regel (HARD):**
- Zusammengehörige Funktionalität muss **gebündelt** werden.
- Erlaubt sind:
  - **Module mit puren Funktionen** (bevorzugt, wenn kein State nötig)
  - **Klassen**, wenn mindestens eines zutrifft:
    - State über mehrere Methoden
    - Abhängigkeiten (Clients/Repos) werden gehalten
    - Polymorphie/Strategien sind erforderlich
    - Ressourcen-Lifecycle (Open/Close) muss verwaltet werden

**Nicht erlaubt:**
- künstliche Klassen nur „weil Regel“
- „God Classes“ mit gemischten Verantwortlichkeiten

**Codex-Aktion:**
- Utility-Funktionen in ein thematisches Modul verschieben.
- Klassen nur erstellen, wenn ein klarer OO-Grund besteht (oben).

---

### 3.2 DRY (Duplikate eliminieren)
**Regel (HARD):**
- Duplizierte oder sehr ähnliche Logik ist verboten.
- Wenn Logik ≥ 2× vorkommt:
  - innerhalb eines Moduls → Funktion extrahieren
  - modulübergreifend → gemeinsames Modul/Service/Abstraktion schaffen

**Codex-Aktion:**
- Identische Sequenzen extrahieren.
- Gemeinsame Algorithmen in Utility-/Service-Modul verlagern.
- Keine Copy/Paste-Fixes.

---

### 3.3 Abstraktion & Erweiterbarkeit
**Regel (HARD):**
- Erweiterungen müssen bestehende Logik **nutzen**, nicht duplizieren.
- Vererbung nur, wenn sie eine echte is-a-Beziehung abbildet; sonst Komposition.

**Codex-Aktion:**
- Gemeinsame Basisfunktionalität zentralisieren.
- Flache Hierarchien, klare Schnittstellen.

---

## 4. Naming & Readability

### 4.1 Sprechende Namen (Pflicht)
**Regel (HARD):**
- Alle Bezeichner müssen Zweck und Bedeutung klar ausdrücken:
  - Funktionen/Methoden, Variablen, Parameter, Klassen, Konstanten
- Unklare Abkürzungen sind verboten (außer projektweit dokumentierte Standardbegriffe).

**Verboten (Umbenennung ohne Informationsgewinn):**
- **Alias-Variablen**: Einen Parameter in eine neue Variable kopieren, die keinen zusätzlichen Kontext liefert (z. B. `message` → `payload_message` ohne Transformation).
- **Redundante Präfixe**: `validated_x`, `normalized_x`, `resolved_x` nur verwenden, wenn tatsächlich eine Transformation stattgefunden hat. Enthält die Variable den **unveränderten** Input, den Originalnamen behalten.
- **result-Alias bei Trivial-Returns**: `result = value; return result` wenn `return value` ausreicht (siehe Regel 2.1 Ausnahmen).

**Faustregel:** Eine Umbenennung ist nur gerechtfertigt, wenn der neue Name **Information hinzufügt**, die der alte Name nicht hatte.

**Negativbeispiel (verboten):**
```py
def _send(self, message: Dict[str, Any]) -> None:
    if isinstance(message, dict):
        payload_message = message  # "message" war bereits klar
    ...
```

**Codex-Aktion:**
- Umbenennen auf klare, semantische Namen — aber nur wenn der bestehende Name unklar ist.
- Keine Ein-Buchstaben-Namen außerhalb trivialer Schleifen (und auch dort sparsam).
- Bestehende klare Namen beibehalten.

---

## 5. Typisierung (Python Type Hints)

### 5.1 Pflicht zur Typisierung
**Regel (HARD):**
- **Public APIs müssen vollständig typisiert** sein:
  - Parameter-Typen
  - Return-Typen
  - zentrale Datenstrukturen
- In CORE ist Typisierung grundsätzlich Pflicht; in EDGE/IO ebenfalls, außer wenn technisch unmöglich.

**Codex-Aktion:**
- Type hints ergänzen (`-> ReturnType`, Parameter-Annotierungen).
- Wo passend: `dataclass`, `TypedDict`, `Protocol`, `Literal`.
- Unklare Fälle: explizit `Any` verwenden und **kommentieren warum**.

---

## 6. Kommentare & Dokumentation (Input/Output/Errors Pflicht)

### 6.1 Docstrings für Public Code
**Regel (HARD):**
- Jede **Public**-Funktion/-Methode/-Klasse (ohne `_`-Prefix) muss einen Docstring haben.

**Ausnahmen (kein Docstring nötig):**
- **Private Methoden** (`_`-Prefix) mit **selbsterklärendem Namen** und ≤ 5 Statements. Der Methodenname muss die Absicht klar ausdrücken.
- **`__post_init__`**, **`__repr__`**, **`__str__`** und andere Standard-Dunder, wenn sie nur Validierung oder Standard-Formatierung durchführen.
- **Triviale Properties** und Getter/Setter, deren Name den Zweck vollständig beschreibt.

### 6.2 Inputs & Outputs müssen beschrieben sein
**Regel (HARD):**
- Für Methoden, die gemäß 6.1 einen Docstring benötigen, gilt:
- **Jeder Eingabewert (Parameter) ist zu dokumentieren:**
  - Bedeutung/Zweck
  - erwartete Formate/Wertebereiche
  - Sonderfälle/Validierung
- **Jeder Ausgabewert (Return/Output) ist zu dokumentieren:**
  - Bedeutung
  - mögliche Varianten/Fehlerzustände
  - Side-Effects (falls vorhanden; idealerweise vermeiden)

**Ausnahme:** Bei privaten Methoden ohne Docstring-Pflicht (6.1 Ausnahmen) entfällt auch die Dokumentationspflicht für Inputs/Outputs.

### 6.3 Fehlerverhalten muss dokumentiert sein
**Regel (HARD):**
- Für Methoden mit Docstring-Pflicht (6.1): Exceptions/Fehlerfälle müssen dokumentiert werden:
  - welche Fehler
  - wann sie auftreten
  - mit welchen Informationen (Message/Context)

**Codex-Aktion:**
- Docstrings ergänzen/aktualisieren für Public API.
- Private Methoden: aussagekräftiger Name statt Docstring-Boilerplate.
- Kommentare sollen das **Warum** erklären (Trade-offs, Safety-Gründe), nicht nur das Was.

---

## 7. Sicherheit & Stabilität

### 7.1 Input Validation
**Regel (HARD):**
- Eingaben, die von **außerhalb des Programms** kommen, müssen validiert werden:
  - Dateien, Umgebungsvariablen, CLI-Argumente, API-Responses, User-Input, Netzwerk-Daten
- Validierung erfolgt **einmal an der Eintrittsstelle** (Konstruktor, Parser, Handler, `__post_init__`).

**Validierungsschichten (Pflicht → Verboten):**
```
Systemgrenze (YAML, ENV, CLI, API)  → Validierung PFLICHT
  → @dataclass __post_init__        → Validierung PFLICHT
    → Public Methoden               → Validierung nur für externe Parameter
      → Private Methoden (_prefix)  → Validierung VERBOTEN (Typen stehen fest)
```

**Explizit verboten:**
- **Defensive isinstance-Checks** in privaten Methoden für Werte, die intern übergeben werden.
- **Redundante Validierung**: Wenn ein `@dataclass` mit `__post_init__` seine Felder validiert, dürfen Methoden, die dieses Objekt empfangen, den Typ nicht erneut prüfen.
- **Typ-Validierung für Rückgabewerte** eigener Methoden (z. B. Prüfen ob `self._resolve_timeouts()` wirklich ein Tuple zurückgibt).

**Faustregel:** Vertraue dem eigenen Code nach der ersten Validierung. Misstraue nur externen Daten.

### 7.2 Keine versteckten Side-Effects
**Regel (HARD):**
- Funktionen sollen deterministisch und ohne überraschende Nebenwirkungen sein.
- Wenn Side-Effects nötig sind (IO): sie müssen klar gekapselt und dokumentiert sein.

**Codex-Aktion:**
- Validierung hinzufügen.
- Side-Effects isolieren (EDGE/IO), CORE möglichst pure halten.

---

## 8. Konsistenz & Style (Tool-freundlich)

**Regel (HARD):**
- Kein „Magic“-Wissen: harte Werte in Konstanten/Konfiguration.
- Einheitliche Projektkonventionen: Imports, Namensstil, Dateistruktur.

**Codex-Aktion:**
- Konstanten extrahieren.
- Konsistenz über Module hinweg herstellen.

---

## 9. Abnahme (Definition of Done)

Code ist nur akzeptabel, wenn:
- alle HARD-Regeln erfüllt sind — einschließlich ihrer **Ausnahmen**
- das Proportionalitätsprinzip (0.1) eingehalten wurde: kein Ballast-Code, der nur formale Regelerfüllung ohne Mehrwert darstellt
- Funktionen deterministisch und debugbar sind (Single-Return bei Verzweigungslogik, explizite Fehlerpfade an Systemgrenzen)
- Duplikate entfernt und Abstraktionen sauber sind
- Type hints vollständig (mind. Public APIs) und konsistent sind
- Inputs/Outputs/Errors in Docstrings für Public API dokumentiert sind (private Methoden: Name statt Docstring)
- kein monolithischer Block (>15 Statements) bestehen bleibt
- Validierung genau einmal an der Systemgrenze stattfindet, nicht redundant in internen Methoden
- keine sinnlosen Alias-Variablen, redundanten `return None` oder Einzeiler-Extraktionen existieren

---
