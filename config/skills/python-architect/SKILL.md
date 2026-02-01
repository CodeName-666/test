---
name: python-architect
description: Expert Python implementation aligned to requirements and architecture with emphasis on reuse, maintainability, and extensibility. Use when asked to implement or refactor functions/classes/methods, apply architecture decisions, or turn specs into clean Python code without sacrificing functionality.
---

# Python Architect

## Overview

Build Python code that matches the stated requirements and architecture while maximizing reuse, maintainability, and extensibility. Keep functionality correct and prioritize clarity over cleverness.

## Workflow (use in order)

### 1) Understand inputs and constraints

- Read AGENTS.md or project-specific rules first if present.
- Identify system boundaries, data validation points, and public vs private API.
- Confirm required behaviors, edge cases, and performance constraints.

### 2) Design the structure

- Choose modules vs classes based on state, dependencies, and lifecycle.
- Keep functions small and single-purpose; extract shared logic to avoid duplication.
- Prefer explicit types and clear naming over abbreviations.

### 3) Implement safely

- Add type hints for public APIs and core logic.
- Validate external inputs once at the boundary; avoid redundant internal checks.
- Keep control flow deterministic; centralize returns in branching functions if required by project rules.
- Avoid nested functions; split monoliths into step functions with clear names.

### 4) Verify and polish

- Add or update tests when behavior changes or new branches are introduced.
- Ensure docstrings for public APIs describe inputs, outputs, and errors.
- Remove dead code, redundant aliases, and unnecessary indirection.

## Quick triggers and examples

Use this skill for prompts like:
- "Implementiere diese Funktion" (implement a function from specs)
- "Baue mir eine Klasse" (design and implement a class)
- "Setzte diese Methoden oder Funktionen um" (implement methods/functions)
- "Refaktoriere diesen Code fuer Wartbarkeit und Erweiterbarkeit"

## Output expectations

- Provide clean Python code aligned to the architecture.
- Prefer readability and predictability; avoid over-engineering.
- Document reasoning briefly when architecture or design choices matter.
