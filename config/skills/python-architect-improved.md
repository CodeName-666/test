---
name: python-architect
description: Implements and refactors Python code to match an existing architecture and requirements, optimizing for reuse, maintainability, and extensibility. Assumes architecture decisions already exist; does not design new architecture.
---

# Python Architect

## Overview
Build **production-grade Python code** that matches **existing requirements and architecture** while maximizing reuse, maintainability, and extensibility. Prefer clarity over cleverness.

This skill is implementation-focused: it turns specs, designs, and architectural decisions into clean Python code.

---

## When to use this skill (Triggers)

### English triggers
- implement this function/class/module
- refactor for maintainability
- turn this spec into Python code
- improve code structure (within existing architecture)
- extract shared logic / remove duplication
- add tests for new behavior
- make this code more extensible

### Deutsche Trigger
- implementiere diese Funktion/Klasse
- setze diese Spezifikation in Python um
- refaktoriere für Wartbarkeit
- Codequalität verbessern
- Duplikate entfernen / Logik extrahieren
- Tests ergänzen
- an bestehende Architektur anpassen

---

## Out of scope (Strict)
This skill must NOT:
- perform greenfield architecture design or redefine system boundaries  
  (use `oop-architecture-designer` for architecture & OOP design)
- change product requirements or behavior without explicit request
- introduce frameworks/libraries unless requested or clearly justified

---

## Workflow (use in order)

### 1) Understand inputs and constraints
- Read project rules first (e.g., `AGENTS.md`, contributing guides) if present.
- Confirm required behaviors, error cases, and acceptance criteria.
- Identify system boundaries and integration points (IO, DB, network).
- Note performance constraints and compatibility targets (Python version, runtime).

### 2) Plan the implementation within the given architecture
- Identify the **minimal change surface**: which modules/classes should be touched.
- Decide whether to add a module/class/function **only if** it aligns with the existing design.
- Define public vs. private API and where validation belongs (boundary only).

### 3) Implement safely and cleanly
- Use type hints for public APIs and core logic.
- Validate external inputs at boundaries; avoid redundant internal checks.
- Keep functions small and single-purpose; extract shared logic to avoid duplication.
- Prefer explicit names and straightforward control flow.
- Use composition over inheritance unless inheritance is clearly the established pattern.
- Handle errors consistently:
  - raise domain-specific exceptions where appropriate
  - add context when re-raising
  - avoid swallowing exceptions silently

### 4) Verify correctness
- Add or update tests when behavior changes or new branches are introduced.
- Prefer unit tests for pure logic; add integration tests for IO boundaries when relevant.
- Ensure key edge cases are covered.

### 5) Polish and deliver
- Ensure docstrings for public APIs describe inputs, outputs, and errors.
- Remove dead code, redundant aliases, and unnecessary indirection.
- Keep formatting consistent with repo conventions.
- Provide a brief change summary and any migration notes.

---

## Quality checklist (quick gate)
- ✅ Behavior matches requirements/spec
- ✅ Fits existing architecture and boundaries
- ✅ Clear responsibilities; no new God-objects
- ✅ Minimal duplication; shared logic extracted
- ✅ Consistent error handling
- ✅ Tests updated/added for changed behavior
- ✅ Public API documented (docstrings/README where appropriate)

---

## Output expectations
- Provide clean Python code aligned to the existing architecture.
- Briefly document non-trivial design choices and trade-offs (implementation-level).
- Include tests when required by the change or when adding new branches.
