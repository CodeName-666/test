# Agent.md – Codex CLI Regelwerk (Python, Safety-First)

## 0. Zweck (normativ)

Dieses Dokument ist die **verbindliche Quelle der Wahrheit** für **codex CLI** und andere automatische Tools (Lint/Refactor/Review).
Es beschreibt **was** zu prüfen ist, **wann** etwas als Fehler gilt und **welche Korrekturmaßnahmen** umzusetzen sind.

**Ziel:** Python-Code nach Functional-Safety-Prinzipien: **deterministisch**, **auditierbar**, **debuggbar**, **sicher**, **performant** und **leicht verständlich**.

**Durchsetzung:** Alle Regeln sind **HARD (fail-fast)**, sofern nicht explizit als Ausnahme markiert.

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
- Jede Funktion/Methode muss **genau einen** `return` besitzen.
- Dieser `return` muss **am Ende** der Funktion stehen.
- **Early returns sind verboten.**

**Intention:** deterministischer Kontrollfluss, zentrale Stelle für Logging/Tracing/Cleanup, bessere Debuggability.

**Codex-Aktion:**
- Refactor auf `result`-/`output`-Variable(n) und Rückgabe am Ende.
- Kein „hidden exit“ über mehrere returns.

---

### 2.2 Explizite Fehlerreaktion im Else-Zweig
**Regel (HARD):**
- Wenn eine Vorbedingung geprüft wird, muss der **Fehlerpfad explizit** sein.
- Muster:  
  `if <valid>:` → Happy Path  
  `else:` → **Error-Reaktion** (Exception oder Error-Result)

**Beispiele (erwartet):**
```py
if value is not None:
    processed = value
else:
    raise ValueError("value must not be None")
```

**Nicht erlaubt:**
- implizite Fehler (späterer Crash ohne klare Ursache)
- „pass“ oder stille Ignorierung im else-Zweig

**Codex-Aktion:**
- Fehlende else-Zweige ergänzen und eine definierte Fehlerreaktion einführen.
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
- Funktionen dürfen nicht zu „Monolith-Blöcken“ werden.
- Komplexe Abläufe müssen in **kleine, benannte Schritte** ausgelagert werden („Step Functions“).

**Codex-Aktion:**
- Große Funktionen in 2–N kleinere Funktionen extrahieren (klar benannt).
- Jede extrahierte Funktion muss eigene Vorbedingungen/Fehlerpfade besitzen.

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

**Codex-Aktion:**
- Umbenennen auf klare, semantische Namen.
- Keine Ein-Buchstaben-Namen außerhalb trivialer Schleifen (und auch dort sparsam).

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
- Jede Public-Funktion/-Methode/-Klasse muss einen Docstring haben.

### 6.2 Inputs & Outputs müssen beschrieben sein
**Regel (HARD):**
- **Jeder Eingabewert (Parameter) ist detailliert zu dokumentieren:**
  - Bedeutung/Zweck
  - erwartete Formate/Wertebereiche
  - Sonderfälle/Validierung
- **Jeder Ausgabewert (Return/Output) ist detailliert zu dokumentieren:**
  - Bedeutung
  - mögliche Varianten/Fehlerzustände
  - Side-Effects (falls vorhanden; idealerweise vermeiden)

### 6.3 Fehlerverhalten muss dokumentiert sein
**Regel (HARD):**
- Exceptions/Fehlerfälle müssen dokumentiert werden:
  - welche Fehler
  - wann sie auftreten
  - mit welchen Informationen (Message/Context)

**Codex-Aktion:**
- Docstrings ergänzen/aktualisieren.
- Kommentare sollen das **Warum** erklären (Trade-offs, Safety-Gründe), nicht nur das Was.

---

## 7. Sicherheit & Stabilität

### 7.1 Input Validation
**Regel (HARD):**
- Externe Eingaben (User, File, Network, DB) müssen validiert werden.
- Keine „trust input“-Annahmen.

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
- alle HARD-Regeln erfüllt sind
- Funktionen deterministisch und debugbar sind (Single-Return, explizite Fehlerpfade)
- Duplikate entfernt und Abstraktionen sauber sind
- Type hints vollständig (mind. Public APIs) und konsistent sind
- Inputs/Outputs/Errors in Docstrings dokumentiert sind
- kein monolithischer Block bestehen bleibt (Step Functions)

---
