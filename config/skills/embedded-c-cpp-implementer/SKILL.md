---
name: embedded-c-cpp-implementer
description: Implements and refactors embedded C/C++ within an existing architecture (bare-metal/RTOS). Optimizes for determinism, safety, resource constraints, and diagnosability. No architecture redesign unless explicitly requested.
---

# Embedded C/C++ Implementer

## Overview
Implement **production-grade embedded C/C++** code aligned to an **existing architecture** and requirements.
Primary goals:

- deterministic behavior (bounded time)
- safe concurrency (ISR/task separation)
- predictable memory usage (prefer static allocation)
- reliability (watchdog-friendly, fault-tolerant)
- clear ownership, lifetimes, and interfaces
- testability (SIL/HIL strategies)

This skill is implementation-focused. If architecture is missing or unclear, request an architecture pass using `embedded-architecture-designer`.

---

## When to use this skill (Triggers)

### English triggers
- implement embedded driver/module
- refactor embedded C/C++ for reliability
- make ISR-safe / RTOS-safe
- reduce memory usage / avoid heap
- implement state machine
- add bounded-time behavior
- fix concurrency bug / race condition
- integrate peripheral (UART/SPI/I2C/ADC)

### Deutsche Trigger
- Embedded Modul/Treiber implementieren
- Embedded C/C++ refaktorisieren
- ISR-sicher / RTOS-sicher machen
- Speicherverbrauch reduzieren / Heap vermeiden
- Zustandsautomat implementieren
- deterministisches Verhalten / bounded time
- Concurrency Bug / Race Condition beheben
- Peripherie integrieren (UART/SPI/I2C/ADC)

---

## Out of Scope (Strict)
This skill must NOT:
- redesign system architecture or re-slice boundaries without explicit instruction  
  (use `embedded-architecture-designer`)
- assume a specific MCU/RTOS/peripheral behavior not provided
- introduce new libraries/frameworks unless requested or clearly justified
- provide unsafe shortcuts that reduce system safety without trade-off discussion

---

## Implementation Workflow (use in order)

### 1) Confirm context and constraints
- platform/toolchain (compiler, standard, warnings)
- bare-metal vs RTOS (task model, priorities)
- timing constraints and critical paths
- memory constraints (RAM/Flash) and allocation rules
- ISR constraints (what is allowed in ISR)
- coding standards (MISRA-C/CERT C, internal rules)

### 2) Plan minimal change within the existing architecture
- identify which layer/module is responsible
- define the public API and ownership/lifetime rules
- ensure dependency direction is respected
- confirm error model (return codes, error enums)

### 3) Implement with embedded guardrails
Determinism:
- avoid unbounded loops in critical paths
- bound retries/timeouts
- keep ISR minimal; defer work to task/main loop

Memory:
- prefer static allocation; avoid heap by default
- if heap is allowed: document policy and ensure bounded usage
- define buffer ownership and lifetime explicitly

Concurrency:
- avoid blocking in ISR
- minimize critical sections; keep them bounded
- use RTOS primitives appropriately (queues, semaphores, event groups)
- consider priority inversion; use mitigation if needed (priority inheritance/ceiling)

Error handling & diagnostics:
- return explicit status codes; avoid exceptions in embedded C++
- add context to faults; log via ring buffer/deferred logging
- integrate watchdog strategy (kick points, fault escalation)

### 4) Verify correctness
- add/update unit tests for pure logic (host build where possible)
- add integration tests for drivers/modules (SIL/HIL guidance)
- test boundary cases: timeouts, buffer limits, invalid states

### 5) Polish and deliver
- keep APIs small and explicit
- document assumptions, timing expectations, and ownership rules
- ensure consistent naming and formatting per repo rules
- provide a brief change summary and notes for integration

---

## Quality checklist (quick gate)
- ✅ No heavy work in ISR; ISR is bounded and non-blocking
- ✅ No hidden dynamic allocation (or policy documented)
- ✅ Bounded execution time in critical paths
- ✅ Clear buffer ownership and lifetimes
- ✅ Concurrency hazards addressed (races, deadlocks, inversion)
- ✅ Error handling consistent with project model
- ✅ Diagnostics present for field failures
- ✅ Fits existing layering and dependency rules

---

## Output expectations
- Provide embedded C/C++ code aligned to the existing architecture.
- Include brief notes on timing/memory/concurrency decisions.
- Include tests where feasible (host-based) and guidance for SIL/HIL when relevant.
