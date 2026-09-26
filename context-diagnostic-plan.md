# Context Diagnostic — Person 2 Implementation Plan

## Top-Level Overview

**Goal:** Upgrade `backend/diagnostics.py` and `backend/bob.py` from minimal placeholders into working implementations that produce the unified output schema. Expand `tests/test_diagnostics.py` to cover all five deterministic rules plus the Bob fallback.

**Scope:** Only `backend/diagnostics.py`, `backend/bob.py`, and `tests/test_diagnostics.py`.

**Approach:**
- Each of the 5 deterministic error types gets a dedicated rule entry with all unified-output fields hardcoded.
- Evidence (`line` / `code`) is extracted from the input dict with `.get()` fallbacks so the code tolerates whatever keys are present.
- `bob.py` returns a stubbed unified response with `analysis_mode: "ai"` — no real API call.
- Tests are rewritten to assert the new schema and cover every rule + unknown fallback.
- Implementation is done in three sequential phases, each tested before the next begins.

---

## Sub-Tasks

---

### Phase 1 — Rewrite `backend/diagnostics.py`

**Intent:**
Replace the flat 3-rule stub with a full 5-rule engine that returns the unified output schema for every known error type.

**Expected Outcomes:**
- `analyze_simple_errors` returns `None` for unknown messages.
- For each of the 5 known error types it returns a dict with all unified-output keys:
  `error_type`, `analysis_mode`, `compiler_message`, `compiler_explanation`,
  `source_explanation`, `evidence` (with `line` and `code`), `what_to_check`, `suggestion`.
- `analysis_mode` is always `"deterministic"`.
- Evidence fields fall back gracefully when `line`/`source_context` are absent from input.

**Todo List:**
1. Define the 5 rule entries as a list of dicts, each containing:
   - `needle` — substring to match against the compiler message
   - `error_type` — one of: `MISSING_SEMICOLON`, `UNDEFINED_VARIABLE`, `TYPE_MISMATCH`, `WRONG_ARGUMENTS`, `MISSING_INCLUDE`
   - `compiler_explanation` — fixed string explaining what the compiler error means
   - `source_explanation` — fixed string describing what this looks like in code
   - `what_to_check` — fixed actionable guidance string
   - `suggestion` — fixed fix suggestion string
2. Map the following needles to error types:
   - `"expected ';'"` → `MISSING_SEMICOLON`
   - `"was not declared in this scope"` → `UNDEFINED_VARIABLE`
   - `"cannot convert"` OR `"invalid conversion"` → `TYPE_MISMATCH`
   - `"no matching function"` → `WRONG_ARGUMENTS`
   - `"was not declared"` (for include-less identifiers like `cout`) / `"file not found"` → `MISSING_INCLUDE`
     *(Note: use distinct needle from UNDEFINED_VARIABLE — e.g. detect `iostream` or `#include` context, or use `"'cout' was not declared"` pattern; confirm exact needle before coding)*
3. Implement `analyze_simple_errors(contextual_diagnostic: dict) -> dict | None`:
   - Read the compiler message text from `contextual_diagnostic.get("normalized", "") or contextual_diagnostic.get("message", "")`
   - Iterate rules, check needle substring match
   - On match, extract evidence: `line = contextual_diagnostic.get("line")`, `code = contextual_diagnostic.get("source_context", [{}])[0].get("code", "")`
   - Return the full unified dict
   - Return `None` if no rule matches
4. Remove the old `_SIMPLE_RULES` dict.

**Relevant Context:**
- Current file: [`backend/diagnostics.py`](backend/diagnostics.py)
- Unified output schema: defined in the project brief above
- Input dict keys available now: `raw`, `normalized`, `source_preview` (from `context.py`)
- Future-compatible keys: `file`, `line`, `column`, `message`, `source_context`

**Status:** `[ ] pending`

---

### Phase 2 — Rewrite `backend/bob.py`

**Intent:**
Replace the flat stub with a function that returns a unified-format dict tagged `analysis_mode: "ai"`. No real API call — stub only.

**Expected Outcomes:**
- `analyze_with_bob` returns a dict with all unified-output keys.
- `analysis_mode` is `"ai"`.
- `error_type` is `"UNKNOWN"`.
- All string fields contain meaningful placeholder text (not empty strings).
- `evidence` contains `line` and `code` extracted from the input with `.get()` fallbacks.
- `compiler_message` reflects the actual normalized compiler text from the input.

**Todo List:**
1. Rewrite `analyze_with_bob(contextual_diagnostic: dict) -> dict`:
   - Extract `compiler_message` from `contextual_diagnostic.get("normalized", "") or contextual_diagnostic.get("message", "")`
   - Extract evidence with fallbacks (same pattern as Phase 1)
   - Return unified dict with:
     - `error_type: "UNKNOWN"`
     - `analysis_mode: "ai"`
     - `compiler_message`: actual compiler text from input
     - `compiler_explanation`: `"This error requires deeper analysis."`
     - `source_explanation`: `"Bob AI analysis is pending for this error type."`
     - `evidence`: `{"line": <extracted or None>, "code": <extracted or "">}`
     - `what_to_check`: `"Review the flagged line and surrounding context carefully."`
     - `suggestion`: `"Consult documentation or ask Bob for further guidance."`

**Relevant Context:**
- Current file: [`backend/bob.py`](backend/bob.py)
- Phase 1 evidence-extraction pattern should be reused/shared

**Status:** `[ ] pending`

---

### Phase 3 — Rewrite `tests/test_diagnostics.py`

**Intent:**
Replace the single minimal test with a full test suite that covers every deterministic rule, the unknown fallback, and the Bob stub output shape.

**Expected Outcomes:**
- All existing test names are updated or replaced to match the new schema.
- One test per deterministic error type asserting `error_type` and `analysis_mode == "deterministic"`.
- One test for an unknown error message asserting `analyze_simple_errors` returns `None`.
- One test for `analyze_with_bob` asserting `analysis_mode == "ai"` and `error_type == "UNKNOWN"`.
- All tests pass with `pytest`.

**Todo List:**
1. Update import to also import `analyze_with_bob` from `backend.bob`.
2. Write `test_missing_semicolon()` — input with `"expected ';'"` needle, assert `error_type == "MISSING_SEMICOLON"` and `analysis_mode == "deterministic"`.
3. Write `test_undefined_variable()` — input with `"was not declared in this scope"`, assert `error_type == "UNDEFINED_VARIABLE"`.
4. Write `test_type_mismatch()` — input with `"cannot convert"`, assert `error_type == "TYPE_MISMATCH"`.
5. Write `test_wrong_arguments()` — input with `"no matching function"`, assert `error_type == "WRONG_ARGUMENTS"`.
6. Write `test_missing_include()` — input matching the MISSING_INCLUDE needle, assert `error_type == "MISSING_INCLUDE"`.
7. Write `test_unknown_error_returns_none()` — input with an unrecognized message, assert result is `None`.
8. Write `test_bob_fallback_shape()` — call `analyze_with_bob({})`, assert `analysis_mode == "ai"` and `error_type == "UNKNOWN"` and all required keys are present.
9. Replace the old `test_analyze_simple_errors_known_case` with the updated tests above (assert `analysis_mode` not `source`).

**Relevant Context:**
- Current file: [`tests/test_diagnostics.py`](tests/test_diagnostics.py)
- Required unified output keys: `error_type`, `analysis_mode`, `compiler_message`, `compiler_explanation`, `source_explanation`, `evidence`, `what_to_check`, `suggestion`

**Status:** `[ ] pending`

---

## Notes for Implementation

- Work phases in order: Phase 1 → run tests → Phase 2 → run tests → Phase 3 → run tests.
- After each phase: run `pytest tests/test_diagnostics.py` and report results before starting the next.
- Do NOT modify `backend/main.py`, `backend/compiler.py`, `backend/context.py`, or any frontend files.
- The `MISSING_INCLUDE` needle needs to be chosen carefully to avoid false-positive overlap with `UNDEFINED_VARIABLE` — confirm the exact needle pattern when implementing Phase 1, Step 2.
