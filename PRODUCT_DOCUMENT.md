# C++ Diagnostic — Complete Product Document

> **IBM × BOB Hackathon 2.0**  
> A production-ready VS Code extension that explains C++ compiler errors in plain language — directly inside the editor, with zero API keys required.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Solution Overview](#2-solution-overview)
3. [How It Works — End-to-End Flow](#3-how-it-works--end-to-end-flow)
4. [System Architecture](#4-system-architecture)
5. [Repository Structure](#5-repository-structure)
6. [The VS Code Extension](#6-the-vs-code-extension)
7. [The Diagnostic Backend](#7-the-diagnostic-backend)
8. [The Deterministic Rule Engine](#8-the-deterministic-rule-engine)
9. [The Generic Fallback (Bob Integration)](#9-the-generic-fallback-bob-integration)
10. [Transport Modes](#10-transport-modes)
11. [API Reference](#11-api-reference)
12. [Configuration Reference](#12-configuration-reference)
13. [Example Files & Live Demos](#13-example-files--live-demos)
14. [Test Suite](#14-test-suite)
15. [Installation & Setup](#15-installation--setup)
16. [Build & Package Instructions](#16-build--package-instructions)
17. [Security Model](#17-security-model)
18. [Requirements & Compatibility](#18-requirements--compatibility)
19. [Key Design Decisions](#19-key-design-decisions)
20. [What Was Built vs. What Was Removed](#20-what-was-built-vs-what-was-removed)
21. [Limitations & Known Constraints](#21-limitations--known-constraints)
22. [Summary & Deliverables](#22-summary--deliverables)

---

## 1. Problem Statement

C++ compiler errors are notoriously cryptic. When a developer writes:

```cpp
int main() {
    int x = 42
    return 0;
}
```

GCC or Clang responds with:

```
main.cpp:3:5: error: expected ';' before 'return'
```

This tells you *where* the problem is, but not *why* the compiler says that, *what* to look for, or *exactly how* to fix it. For beginners and even experienced developers switching contexts, this friction slows down development significantly.

**The goal:** Turn raw, cryptic compiler output into structured, human-readable explanations — without leaving the editor, without copying errors into a browser, and without requiring any external AI service.

---

## 2. Solution Overview

**C++ Diagnostic** is a real, installable VS Code extension (`.vsix`) that:

1. Triggers on demand (keyboard shortcut, right-click menu, or save)
2. Compiles the active C++ file using the system GCC / Apple Clang
3. Parses every compiler error into a structured record
4. Runs each error through a 109-rule deterministic engine
5. Falls back to a category-aware generic explanation for unmatched errors
6. Surfaces the results in **five simultaneous VS Code layers** — squiggles, hover tooltips, amber highlights, an Output Channel log, and a rich side-by-side Webview panel
7. Works **completely offline** — no API key, no cloud service, no account needed

### What makes it different from standard linters

| Feature | Standard linter | C++ Diagnostic |
|---|---|---|
| Shows error location | ✅ | ✅ |
| Plain-language explanation | ❌ | ✅ |
| Source evidence (highlighted code) | ❌ | ✅ |
| "What to check" guidance | ❌ | ✅ |
| Actionable fix suggestion | ❌ | ✅ |
| Multi-error deep analysis | ❌ | ✅ |
| Works offline | ✅ | ✅ |
| GCC + Apple Clang both supported | Varies | ✅ |

---

## 3. How It Works — End-to-End Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     DEVELOPER IN VS CODE                    │
│                                                             │
│  Opens a .cpp file  →  writes code  →  presses Ctrl+Shift+D│
└─────────────────────────┬───────────────────────────────────┘
                          │  VS Code Extension (TypeScript)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  TRANSPORT LAYER                            │
│                                                             │
│  Option A: CLI subprocess                                   │
│    python backend/diagnose.py --file /path/to/file.cpp      │
│                                                             │
│  Option B: HTTP request                                     │
│    POST http://localhost:8000/diagnose                      │
│    { "source_code": "...", "compiler_output": "" }          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  BACKEND PIPELINE                           │
│                                                             │
│  1. compiler.py  — run GCC / Clang on source                │
│     gcc -x c++ -fsyntax-only <file.cpp>                     │
│     captures stderr with all diagnostics                    │
│                                                             │
│  2. compiler.py  — parse_compiler_output()                  │
│     regex: <file>:<line>:<col>: <severity>: <message>       │
│     returns list of structured Diagnostic records           │
│                                                             │
│  3. context.py   — enrich_context()                         │
│     extracts ±2 source lines around each error              │
│     adds source_context[] to each diagnostic                │
│                                                             │
│  4. diagnostics.py — analyze_simple_errors()                │
│     109 regex rules, priority-sorted                        │
│     matches GCC + Apple Clang message variants              │
│     returns unified diagnosis dict on match                 │
│                                                             │
│  5. bob.py       — analyze_with_bob()  [fallback only]      │
│     category-hint patterns                                  │
│     always returns a complete schema                        │
│     no API key needed                                       │
└─────────────────────────┬───────────────────────────────────┘
                          │  JSON response
                          ▼
┌─────────────────────────────────────────────────────────────┐
│               VS CODE PRESENTATION LAYERS                   │
│                                                             │
│  a) DiagnosticCollection   — red squiggly underline         │
│     one squiggle per error, appears in Problems panel       │
│                                                             │
│  b) TextEditorDecoration   — amber background highlight     │
│     on the exact evidence source line                       │
│                                                             │
│  c) HoverProvider          — full explanation on hover      │
│     shows error type, message, explanation,                 │
│     evidence code, what-to-check, suggestion                │
│                                                             │
│  d) OutputChannel          — plain-text log                 │
│     all errors printed sequentially with full details       │
│                                                             │
│  e) WebviewPanel           — rich HTML side panel           │
│     card per error, nav bar, rule-based/AI badge,           │
│     evidence code block, suggestion in green                │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. System Architecture

### Component Map

```
IBM-BOB-Hackathon-2.0/
│
├── backend/                     Python 3 — FastAPI
│   ├── main.py                  REST API server (FastAPI)
│   ├── compiler.py              GCC execution + output capture + parsing
│   ├── context.py               Source context window extraction
│   ├── diagnostics.py           109-rule deterministic engine
│   ├── bob.py                   Generic fallback (no API key)
│   └── diagnose.py              CLI adapter for extension subprocess transport
│
├── vscode-extension/            TypeScript — VS Code API
│   ├── src/extension.ts         765-line extension entry point
│   ├── out/extension.js         Compiled JavaScript (auto-generated)
│   ├── package.json             Extension manifest (commands, config, keybindings)
│   ├── tsconfig.json            TypeScript compiler config
│   ├── .vscodeignore            VSIX packaging exclusions
│   └── cpp-diagnostic-0.2.0.vsix  Installable package (14 KB)
│
├── examples/                    Sample .cpp files with intentional errors
├── tests/                       pytest test suite (204 tests)
├── requirements.txt             Python dependencies
└── README.md                    User documentation
```

### Data Flow — Unified Diagnosis Schema

Every error that passes through the pipeline results in one object with this schema:

```json
{
  "error_type":           "MISSING_SEMICOLON",
  "analysis_mode":        "deterministic",
  "compiler_message":     "expected ';' before 'return'",
  "compiler_explanation": "The compiler reached a token it did not expect because a semicolon is missing...",
  "source_explanation":   "A statement on the line before the flagged location is not terminated...",
  "evidence": {
    "line": 2,
    "code": "    int x = 42"
  },
  "what_to_check":  "Look at the line immediately before the one the compiler flagged...",
  "suggestion":     "Add a semicolon (';') at the end of the incomplete statement.",
  "raw": {
    "file":     "/path/to/file.cpp",
    "line":     3,
    "column":   5,
    "severity": "error",
    "message":  "expected ';' before 'return'"
  }
}
```

---

## 5. Repository Structure

```
backend/
  main.py          FastAPI /diagnose + /health; returns diagnostics[] array
  compiler.py      GCC/Clang subprocess execution; parse_compiler_output()
  context.py       Source context window extraction; enrich_context()
  diagnostics.py   109-rule deterministic engine; analyze_simple_errors()
  bob.py           Generic fallback with category hints; analyze_with_bob()
  diagnose.py      CLI adapter: full pipeline on ALL errors; JSON to stdout

vscode-extension/
  src/extension.ts          Full extension TypeScript source (765 lines)
  package.json              Manifest: 4 commands, keybindings, 3 settings
  tsconfig.json             TypeScript config (Node16, ES2020, outDir=out/)
  .vscodeignore             Excludes src/, node_modules/, maps, tsconfig
  out/extension.js          Compiled JS (included in VSIX)
  cpp-diagnostic-0.2.0.vsix  Installable VSIX package (14.4 KB)
  LICENSE                   MIT

examples/
  01_missing_semicolon.cpp  int x = 42  (no semicolon)
  02_undefined_variable.cpp return value; (undeclared)
  03_type_mismatch.cpp      int x = "hello";
  04_wrong_arguments.cpp    add(1) instead of add(1,2)
  05_missing_include.cpp    std::cout without #include
  06_complex_error.cpp      template with unknown_symbol

tests/
  test_diagnostics.py  150+ unit tests, all 32 error types + GCC/Clang variants
  test_api.py          API: multi-error, None coercion, bob fallback, raw field
  test_diagnose.py     CLI adapter: multi-error deep analysis
  test_compiler.py     compiler.py + context.py unit tests
  test_e2e.py          Full end-to-end with real compiler invocation
```

---

## 6. The VS Code Extension

### File: `vscode-extension/src/extension.ts`

The extension is a genuine VS Code extension using the official `vscode` API. It is written in TypeScript, compiled to JavaScript, and packaged as a `.vsix`.

### Commands

| Command | Keybinding | Description |
|---|---|---|
| `Diagnose C++: Explain Error` | `Ctrl+Shift+D` / `Cmd+Shift+D` | Diagnose active file, show squiggles + output channel |
| `Diagnose C++: Analyze File` | — (Command Palette) | Same as above + opens the rich Webview panel beside code |
| `Diagnose C++: Clear Diagnostics` | `Ctrl+Shift+K` / `Cmd+Shift+K` | Remove all markers, decorations, and stored diagnoses |
| `Diagnose C++: Show Explanation Panel` | — (Command Palette) | Re-open the last Webview panel without re-running analysis |

All four commands also appear in the **right-click context menu** on `.cpp`, `.cc`, and `.cxx` files.

### Five Presentation Layers

**1. `DiagnosticCollection` — Red squiggles + Problems panel**  
One squiggle per error, correctly mapped to line and column. Each diagnostic entry shows the error type, compiler message, explanation, and analysis mode tag (`[rule-based]` or `[AI]`). Related information shows the suggestion. Appears in VS Code's built-in Problems panel (`Ctrl+Shift+M`).

**2. `TextEditorDecoration` — Amber line highlight**  
The exact source line identified as evidence is highlighted with an amber background spanning the full line. Also shown in the editor scrollbar overview ruler.

**3. `HoverProvider` — Full explanation on hover**  
Hovering over any highlighted evidence line shows a rich Markdown tooltip with:
- Error type and count badge (e.g. `(2/3)`)
- Analysis mode tag
- Compiler message in a code block
- Plain-English explanation
- Source pattern description
- Evidence code in a C++ syntax block
- What to check
- Suggestion

**4. `OutputChannel` — Plain-text log**  
The "C++ Diagnostic" output channel shows all errors in a structured, easy-to-read format — useful for copying, screenshotting, or logging.

**5. `WebviewPanel` — Rich HTML side panel**  
Opens beside the code editor showing all errors as expandable cards. Each card contains:
- Error index number, error type, analysis-mode badge, and line location in the header
- "Compiler Message" section with the raw compiler text
- "What the Compiler Means" section
- "Source Pattern" section
- "Evidence" section with the actual source line in a code block
- "What to Check" section
- "Suggestion" section with a green left-border highlight
- Navigation bar at the top (multi-error only) linking to each error card by anchor

### Lifecycle Events

- **On save** (optional): If `cppDiagnostic.autoAnalyzeOnSave` is `true`, automatically re-runs diagnostics 500ms after saving (debounced).
- **On text change**: If the document is edited after analysis, all markers are immediately cleared so stale results are never shown.
- **On close**: All markers and stored diagnostics for the document are cleaned up.
- **On deactivate**: All disposables (channel, collection, decoration type, webview) are properly disposed.

---

## 7. The Diagnostic Backend

### `backend/compiler.py`

Responsible for one thing: running the compiler and returning raw output. It does not parse or interpret results.

- Writes source code to a temporary `.cpp` file
- Invokes `gcc -x c++ -fsyntax-only <tmpfile>` (works for both GCC and Apple Clang since macOS `gcc` is Clang under the hood)
- Captures `stdout` and `stderr`
- Cleans up the temporary file in a `finally` block
- Raises `GCCNotFoundError` if compiler is missing from PATH
- Raises `GCCTimeoutError` if compilation exceeds 10 seconds
- `parse_compiler_output()` uses a single regex to extract all structured diagnostics:  
  `<file>:<line>:<col>: <severity>: <message>`

### `backend/context.py`

Extracts a ±2-line source window around each error location. Returns a list of `SourceLine` dicts. Never modifies or normalises source text.

### `backend/main.py`

FastAPI application with two endpoints:

- `GET /health` → `{ "status": "ok" }`
- `POST /diagnose` → full pipeline, returns `diagnostics[]` + backward-compat `diagnosis` field

Handles:
- Malformed requests (`None` values coerced to empty string via Pydantic validator)
- Compiler not found / timeout
- Empty output (returns `clean` status)
- Unparsed output (treats as raw message, still runs analysis)

### `backend/diagnose.py`

The CLI adapter used by the extension subprocess transport. Accepts `--file <path>`, runs the full pipeline, and writes one JSON line to stdout. All errors go to stderr (Python tracebacks only). Always exits 0 — the extension reads the JSON status field, not the exit code.

---

## 8. The Deterministic Rule Engine

### File: `backend/diagnostics.py`

**109 rules** across **32 error categories** organised in a priority-sorted list. Each rule contains:

| Field | Purpose |
|---|---|
| `pattern` | Compiled `re` regex — matched against the normalised compiler message |
| `error_type` | Classification string (e.g. `MISSING_SEMICOLON`) |
| `priority` | Integer — higher wins when multiple rules match |
| `compiler_explanation` | What the compiler is actually saying |
| `source_explanation` | What source-code pattern caused this |
| `what_to_check` | Guided investigation steps |
| `suggestion` | Concrete fix instruction |

The matching function (`analyze_simple_errors`) sorts all rules by priority descending and returns on the first match. Needle (substring) rules and pattern (regex) rules are all in the same sorted list — regex rules are preferred by being given higher explicit priorities.

### All 32 Error Categories

| Category | Error Type | GCC Example | Clang Example |
|---|---|---|---|
| Missing semicolon | `MISSING_SEMICOLON` | `expected ';' before 'return'` | `expected ';' at end of declaration` |
| Missing brace | `MISSING_BRACE` | `expected '}' at end of input` | `unmatched '{'` |
| Missing paren | `MISSING_PAREN` | `expected ')'` | `expected '('` |
| Missing bracket | `MISSING_BRACKET` | `expected ']'` | — |
| Undefined variable | `UNDEFINED_VARIABLE` | `was not declared in this scope` | `use of undeclared identifier` |
| Undefined type | `UNDEFINED_TYPE` | `does not name a type` | `unknown type name` |
| Type mismatch | `TYPE_MISMATCH` | `cannot convert` / `invalid conversion from` | `cannot initialize a variable of type` |
| Wrong arguments | `WRONG_ARGUMENTS` | `too few arguments to function` | `requires N arguments, but M was provided` |
| No matching function | `WRONG_ARGUMENTS` | `no matching function for call to` | `no matching function for call to` |
| Undefined function | `UNDEFINED_FUNCTION` | `implicit declaration of function` | `call to undeclared function` |
| Wrong call | `WRONG_CALL` | `is not a function` | `is not a function` |
| Missing return | `MISSING_RETURN` | `control reaches end of non-void function` | `non-void function should return a value` |
| Wrong return | `WRONG_RETURN` | `void function should not return a value` | — |
| Missing include | `MISSING_INCLUDE` | `is not a member of 'std'` | `use of undeclared identifier 'std'` |
| Member not found | `MEMBER_NOT_FOUND` | `has no member named` | `no member named ... in ...` |
| Access violation | `ACCESS_VIOLATION` | `is a private member` | `private member ... of class` |
| Wrong constructor | `WRONG_CONSTRUCTOR` | `no matching constructor for initialization` | — |
| Abstract class | `ABSTRACT_CLASS` | `cannot instantiate abstract class` | `object of abstract class type is not allowed` |
| Incomplete type | `INCOMPLETE_TYPE` | `member access into incomplete type` | `forward declaration of` |
| Pointer error | `POINTER_ERROR` | `indirection requires pointer operand` | `cannot take the address of an rvalue` |
| Reference error | `REFERENCE_ERROR` | `cannot bind non-const` | — |
| Const violation | `CONST_VIOLATION` | `drops const qualifier` | — |
| Template error | `TEMPLATE_ERROR` | `template argument deduction failed` | `in instantiation of function template` |
| Operator error | `OPERATOR_ERROR` | `no match for operator` | `invalid operands to binary expression` |
| Redefinition | `REDEFINITION` | `redefinition of` | `conflicting types` |
| Linker error | `LINKER_ERROR` | `undefined reference to` | `multiple definition of` |
| Syntax error | `SYNTAX_ERROR` | `expected primary-expression` | `stray '...' in program` |
| Shadowing | `SHADOWING` | `declaration shadows a parameter` | — |
| Division by zero | `DIVISION_BY_ZERO` | `division by zero` | — |
| Deleted function | `DELETED_FUNCTION` | `use of deleted function` | — |
| Constexpr error | `CONSTEXPR_ERROR` | `is not a constexpr function` | — |
| I/O error | `IO_ERROR` | `cannot open output file` | — |
| Iterator error | `ITERATOR_ERROR` | `no type named 'iterator'` | — |

---

## 9. The Generic Fallback (Bob Integration)

### File: `backend/bob.py`

When no deterministic rule matches, `analyze_with_bob()` is called. It:

1. Scans the raw compiler message for 12 keyword patterns (`template`, `namespace`, `overload`, `virtual`, `const`, `reference`, `pointer`, `cast`, `inherit`, `static`, `decltype`, `auto`)
2. Builds a targeted category hint based on the first match
3. Returns a fully-formed unified diagnosis schema with `error_type: "UNKNOWN"` and `analysis_mode: "ai"`

This means even completely unknown error messages receive a structured, helpful response with:
- A readable explanation that includes the category hint
- Guidance to read the full compiler message carefully
- Suggestions to search for the error online or consult C++ references

**No API key. No external service. No network request. Never crashes or returns `None`.**

The `analysis_mode` field in the output tells the VS Code extension whether to badge the result as `Rule-Based` (green) or `AI Fallback` (purple).

---

## 10. Transport Modes

The extension supports two ways to communicate with the backend:

### Mode A — CLI Subprocess (Default)

The extension spawns the Python CLI adapter as a child process:

```
python backend/diagnose.py --file /absolute/path/to/file.cpp
```

- No server to start
- Works immediately after installing the extension
- Requires Python on PATH (or configured in `cppDiagnostic.pythonPath`)
- 30-second timeout with automatic process kill
- `ENOENT` error gives a user-friendly "Python not found" message
- Stdout parsed as JSON; stderr (Python tracebacks) kept separate

### Mode B — HTTP Backend

When `cppDiagnostic.backendUrl` is set (e.g. `http://localhost:8000`):

```
POST http://localhost:8000/diagnose
Content-Type: application/json

{ "source_code": "...", "compiler_output": "" }
```

- Supports HTTPS (auto-detected by URL scheme)
- 15-second timeout with automatic socket destroy
- Works with a shared backend server (team / remote deployment)
- Both HTTP/1.1 and HTTPS supported via Node.js built-in `http`/`https` modules

---

## 11. API Reference

### `POST /diagnose`

**Request:**
```json
{
  "source_code": "int main() { int x = 10\n return 0; }",
  "compiler_output": ""
}
```

- When `compiler_output` is empty, the backend compiles `source_code` with GCC and analyzes the output
- When `compiler_output` is pre-supplied, GCC is not invoked — the supplied text is parsed directly
- Both fields accept `null` (coerced to empty string)

**Response — errors found:**
```json
{
  "status": "ok",
  "diagnosis": { ... },        // first error — backward compatibility
  "diagnostics": [             // ALL errors — full multi-error deep analysis
    {
      "error_type":           "MISSING_SEMICOLON",
      "analysis_mode":        "deterministic",
      "compiler_message":     "expected ';' before 'return'",
      "compiler_explanation": "The compiler reached a token it did not expect...",
      "source_explanation":   "A statement on the line before the flagged location...",
      "evidence": { "line": 2, "code": "    int x = 10" },
      "what_to_check":        "Look at the line immediately before...",
      "suggestion":           "Add a semicolon (';') at the end of the incomplete statement.",
      "raw": {
        "file": "/tmp/abc.cpp", "line": 3, "column": 5,
        "severity": "error", "message": "expected ';' before 'return'"
      }
    }
  ],
  "pipeline": ["compiler.py", "context.py", "diagnostics.py", "bob.py"]
}
```

**Response — clean compilation:**
```json
{ "status": "clean", "message": "No compiler errors found.", "pipeline": ["compiler.py"] }
```

**Response — infrastructure error:**
```json
{ "status": "error", "message": "GCC not found. Ensure GCC is installed and available on PATH." }
```

### `GET /health`
```json
{ "status": "ok" }
```

### CLI Adapter

```bash
python backend/diagnose.py --file /absolute/path/to/file.cpp
```

Output schema is identical to the HTTP response. Always exits 0. Never writes non-JSON to stdout.

---

## 12. Configuration Reference

All settings are in the `cppDiagnostic` namespace. Accessible via VS Code Settings UI or `settings.json`.

| Setting | Type | Default | Description |
|---|---|---|---|
| `cppDiagnostic.pythonPath` | `string` | `"python"` | Path to the Python interpreter. Use an absolute path (e.g. `/usr/local/bin/python3`) if Python is not on your system PATH. On macOS with a venv: `/path/to/.venv/bin/python`. |
| `cppDiagnostic.backendUrl` | `string` | `""` | URL of a running backend server. When set, HTTP transport is used. Example: `http://localhost:8000`. Leave empty to use the CLI subprocess. |
| `cppDiagnostic.autoAnalyzeOnSave` | `boolean` | `false` | When `true`, automatically diagnoses the active C++ file 500ms after saving. Debounced — rapid saves do not cause multiple analysis runs. |

---

## 13. Example Files & Live Demos

Six example `.cpp` files are included in `examples/`. Each demonstrates a different class of error.

### `01_missing_semicolon.cpp`
```cpp
int main() {
    int x = 42
    return 0;
}
```
**Expected diagnosis:** `MISSING_SEMICOLON` — "Add a semicolon (';') at the end of the incomplete statement."

### `02_undefined_variable.cpp`
```cpp
int main() {
    return value;
}
```
**Expected diagnosis:** `UNDEFINED_VARIABLE` — "Declare the variable before using it, or fix the spelling if it is a typo."

### `03_type_mismatch.cpp`
```cpp
int main() {
    int x = "hello";
    return x;
}
```
**Expected diagnosis:** `TYPE_MISMATCH` — "Change the initializer to match the variable type, or cast the value explicitly."

### `04_wrong_arguments.cpp`
```cpp
int add(int a, int b) { return a + b; }
int main() {
    return add(1);
}
```
**Expected diagnosis:** `WRONG_ARGUMENTS` — "Adjust the call to match the function signature."

### `05_missing_include.cpp`
```cpp
int main() {
    std::cout << "hi";
    return 0;
}
```
**Expected diagnosis:** `MISSING_INCLUDE` — "Add the required #include at the top of your file (e.g. #include <iostream>)."

### `06_complex_error.cpp`
```cpp
template <typename T>
T f(T x) { return x + unknown_symbol; }
int main() {
    return f(1);
}
```
**Expected diagnosis:** `UNDEFINED_VARIABLE` or `TEMPLATE_ERROR` (depending on compiler version) — falls back to Bob generic fallback if unmatched.

---

## 14. Test Suite

### Overview

| File | Count | Covers |
|---|---|---|
| `tests/test_diagnostics.py` | ~155 | All 32 error types, GCC + Clang variants, rule priority, evidence selection, bob fallback |
| `tests/test_api.py` | 13 | HTTP endpoints, multi-error, `None` coercion, bob fallback, raw field, pipeline field |
| `tests/test_diagnose.py` | ~25 | CLI adapter, multi-error deep analysis, clean status, error status |
| `tests/test_compiler.py` | ~5 | `parse_compiler_output()`, `get_source_context()` |
| `tests/test_e2e.py` | ~15 | Full end-to-end with real compiler invocation |
| **Total** | **204** | **All passing** |

### Running Tests

```bash
# All tests
pytest -q

# Single file
pytest tests/test_diagnostics.py -v

# Specific test class
pytest tests/test_diagnostics.py::TestMissingSemicolon -v
```

### What is Tested

- Every one of the 32 error categories matches correctly
- GCC and Apple Clang message variants match the same `error_type`
- Priority ordering — high-priority rules win when multiple patterns could match
- Evidence extraction — correct source line is selected
- Multi-error analysis — all errors in a file are individually analysed
- Bob fallback — unknown errors produce a complete schema with `analysis_mode: "ai"`
- `None` coercion — `null` values in the request body do not crash the endpoint
- `raw` field — every diagnosis carries the original compiler location data
- Pipeline field — present in all successful responses
- Clean status — files with no errors return `{ "status": "clean" }`
- Infrastructure errors — GCC missing, timeout, unreadable file
- End-to-end — real `gcc` invocation on all six example files

---

## 15. Installation & Setup

### Step 1 — Backend (Python)

```bash
# Clone the repository
git clone https://github.com/shreeharijoshi/IBM-BOB-Hackathon-2.0
cd IBM-BOB-Hackathon-2.0

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

**Verify GCC / Clang is available:**
```bash
gcc --version    # macOS: shows Apple Clang; Linux: shows GCC version
```

**Run the backend server (optional — only needed for HTTP transport mode):**
```bash
uvicorn backend.main:app --reload --port 8000
```

**Run tests:**
```bash
pytest -q
# Expected: 204 passed
```

### Step 2 — VS Code Extension

**Option A — Install the pre-built VSIX (recommended):**
1. Open VS Code
2. Go to `Extensions` (`Ctrl+Shift+X`)
3. Click `...` (More Actions) → `Install from VSIX…`
4. Select `vscode-extension/cpp-diagnostic-0.2.0.vsix`
5. Reload VS Code when prompted

**Option B — Build from source:**
```bash
cd vscode-extension
npm install
npm run compile
node_modules/.bin/vsce package --no-dependencies
# Produces: cpp-diagnostic-0.2.0.vsix
```
Then install the VSIX as in Option A.

### Step 3 — Configure Python Path

In VS Code Settings, set `cppDiagnostic.pythonPath` to the Python interpreter that has the backend dependencies:
```
/Users/yourname/IBM-BOB-Hackathon-2.0/.venv/bin/python
```

Or if Python is on your system PATH, leave the default `python`.

---

## 16. Build & Package Instructions

### Compile TypeScript

```bash
cd vscode-extension
npm run compile          # one-shot build
npm run watch            # watch mode for development
```

### Package VSIX

```bash
cd vscode-extension
node_modules/.bin/vsce package --no-dependencies
# Output: cpp-diagnostic-0.2.0.vsix
```

The `.vscodeignore` file ensures the VSIX only contains:
- `out/extension.js` and `out/extension.js.map`
- `package.json`
- `LICENSE.txt`

Source files (`src/`), `node_modules/`, and TypeScript config are excluded to keep the package small (14 KB).

### Update the version

1. Edit `"version"` in `vscode-extension/package.json`
2. Run `npm run compile`
3. Run `vsce package --no-dependencies`

---

## 17. Security Model

- **No API keys required.** The entire system works offline. The Bob fallback is deterministic pattern matching — no network calls.
- **No source code sent to external services.** All analysis runs locally on the developer's machine.
- **Stack traces never exposed.** The API returns structured error messages; Python exceptions are caught and formatted.
- **Temporary files cleaned up.** The compiler temp file is always deleted in a `finally` block, even on timeout.
- **No hard-coded credentials anywhere.** The codebase contains no secrets, tokens, or credentials.
- **Extension uses no `eval`, no `innerHTML` with untrusted content.** All Webview HTML uses `escapeHtml()` before inserting any compiler message or source code.
- **Webview scripts disabled.** The Webview panel is created with `enableScripts: false` — purely static HTML.

---

## 18. Requirements & Compatibility

### Backend

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.9+ | Tested on 3.11 and 3.14 |
| FastAPI | latest | See requirements.txt |
| Uvicorn | latest | For HTTP mode |
| GCC | any modern | Also works with Apple Clang (macOS `gcc` command) |

### Extension

| Requirement | Minimum | Notes |
|---|---|---|
| VS Code | 1.85.0 | Declared in `engines.vscode` |
| Node.js | 18+ | For `npm run compile` only; not needed at runtime |
| TypeScript | 5.3+ | dev dependency only |

### Compiler Support

| Compiler | Platform | Status |
|---|---|---|
| GCC 9–14 | Linux, Windows (MinGW) | ✅ Fully supported |
| Apple Clang 14+ | macOS | ✅ Fully supported |
| Clang/LLVM | Linux | ✅ Fully supported |
| MSVC | Windows | ⚠️ Output format differs — not yet supported |

---

## 19. Key Design Decisions

### Why a real VS Code extension, not a web app?

The requirement was an installable `.vsix`. A web app forces the developer to leave the editor, copy the error, paste it, and navigate results in a browser. The VS Code extension works at the point of development — the evidence line, hover tooltip, and webview are all integrated directly into the editor where the code lives.

### Why a deterministic rule engine instead of an LLM?

1. **No API key required** — the product works out of the box for anyone
2. **Deterministic** — the same input always produces the same output; no hallucination risk
3. **Fast** — rule matching is microsecond-range; no network latency
4. **Offline** — works without internet access
5. **Testable** — every rule can be unit-tested precisely

The Bob fallback provides a safety net for the ~10% of messages that don't match a specific rule, using pattern matching to give a useful category-aware hint.

### Why CLI subprocess transport as the default?

No server to start. The developer installs the extension, configures the Python path, and it works. The HTTP mode is available for teams who want to run a shared backend or for environments where Python subprocess spawning is inconvenient.

### Why is source context extracted separately?

`context.py` is a single-responsibility module. It can be tested independently, reused by different analysis paths, and replaced with a smarter context extractor in future without touching the rule engine.

### Why does the extension clear markers on text change?

Stale diagnostics are misleading. If you fix the error and the red squiggle stays, you'll doubt whether the fix worked. Clearing on change ensures the UI state always reflects the last analysis run, not a previous state.

---

## 20. What Was Built vs. What Was Removed

### Built (new / heavily extended)

| Item | Description |
|---|---|
| `vscode-extension/src/extension.ts` | Complete rewrite — 765 lines, 4 commands, 5 presentation layers |
| `backend/diagnostics.py` | Extended from ~5 rules to 109 rules across 32 categories |
| `backend/main.py` | Added `diagnostics[]` array, `raw` field, `None` coercion |
| `backend/diagnose.py` | Extended to analyse ALL errors, not just the first |
| `backend/bob.py` | Replaced placeholder with category-hint fallback |
| `vscode-extension/package.json` | Added 4 commands, keybindings, config settings |
| `vscode-extension/.vscodeignore` | Fixed — `out/` was previously excluded, breaking VSIX |
| `vscode-extension/cpp-diagnostic-0.2.0.vsix` | First working packaged VSIX |
| `tests/test_diagnostics.py` | Extended to 155+ tests covering all 32 types |
| `tests/test_api.py` | Added 13 tests |
| `tests/test_diagnose.py` | Extended for multi-error and `raw` field |
| `tests/test_e2e.py` | Extended for full pipeline validation |
| `README.md` | Full product documentation |
| `PRODUCT_DOCUMENT.md` | This document |

### Removed (cleaned up)

| Item | Reason |
|---|---|
| `frontend/` (React web app) | Not part of the VS Code extension product |
| `context-diagnostic-plan.md` | Prototype planning document, no longer needed |

---

## 21. Limitations & Known Constraints

| Limitation | Impact | Notes |
|---|---|---|
| MSVC not supported | Windows Visual Studio users | GCC/MinGW works on Windows; MSVC output format is different |
| CLI transport requires Python on PATH | New users may need to configure `pythonPath` | Clear error message guides them to the setting |
| No automatic "apply fix" | User applies the suggested fix manually | Only unambiguous fixes could be auto-applied safely; current rule set doesn't have sufficient confidence for all cases |
| Apple Clang suppresses cascade errors | Some secondary errors not shown | Inherent compiler behaviour — independent errors on separate constructs are always reported |
| 109 rules ≠ all possible C++ errors | ~10% of messages fall through to Bob fallback | Bob fallback always returns a useful structured response |
| Extension works on `.cpp`, `.cc`, `.cxx` files only | `.h`/`.hpp` files not directly analysable | Header-only errors must be triggered from an including `.cpp` file |

---

## 22. Summary & Deliverables

### What was delivered

| Deliverable | Status | Location |
|---|---|---|
| Installable VS Code extension | ✅ | `vscode-extension/cpp-diagnostic-0.2.0.vsix` |
| TypeScript extension source | ✅ | `vscode-extension/src/extension.ts` |
| FastAPI backend | ✅ | `backend/main.py` |
| 109-rule diagnostic engine | ✅ | `backend/diagnostics.py` |
| CLI adapter | ✅ | `backend/diagnose.py` |
| Offline fallback (no API key) | ✅ | `backend/bob.py` |
| 204 passing tests | ✅ | `tests/` |
| 6 example C++ files | ✅ | `examples/` |
| Full documentation | ✅ | `README.md`, `PRODUCT_DOCUMENT.md` |

### Key numbers

- **109** deterministic diagnostic rules
- **32** error categories covered
- **2** compiler families supported (GCC + Apple Clang)
- **5** VS Code presentation layers (squiggles, amber highlight, hover, output channel, webview)
- **4** commands (Explain Error, Analyze File, Clear Diagnostics, Show Panel)
- **2** transport modes (CLI subprocess, HTTP)
- **204** automated tests, all passing
- **14 KB** VSIX package size
- **0** API keys required

### The complete real workflow

```
C++ source file
    → Developer presses Ctrl+Shift+D in VS Code
    → Extension saves file if dirty
    → Extension spawns: python backend/diagnose.py --file file.cpp
    → diagnose.py runs GCC on file.cpp
    → GCC outputs: file.cpp:2:15: error: expected ';' before 'return'
    → compiler.py parses output into structured Diagnostic
    → context.py extracts lines 1-4 around line 2
    → diagnostics.py matches "expected ';'" → MISSING_SEMICOLON rule
    → Returns: { error_type, explanation, evidence, suggestion, raw }
    → Extension receives JSON
    → Sets red squiggle on line 2, col 15 (DiagnosticCollection)
    → Highlights line 2 with amber background (TextEditorDecoration)
    → Registers hover for line 2 (HoverProvider)
    → Prints full explanation to Output Channel
    → Developer hovers line 2 → sees tooltip:
        "MISSING_SEMICOLON [rule-based]
         Compiler message: expected ';' before 'return'
         What it means: The compiler reached a token it did not expect...
         Evidence: int x = 42
         What to check: Look at the line immediately before...
         Suggestion: Add a semicolon (';') at the end of the statement."
```

**The developer understands the error, knows where to look, and knows exactly what to do — without leaving VS Code.**

---

*Repository: https://github.com/shreeharijoshi/IBM-BOB-Hackathon-2.0*  
*VSIX: `vscode-extension/cpp-diagnostic-0.2.0.vsix`*  
*License: MIT*
