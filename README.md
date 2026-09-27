# C++ Diagnostic — VS Code Extension

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/shreeharijoshi/IBM-BOB-Hackathon-2.0)

> **🚀 Live Web Testing:** Click the badge above or visit [https://codespaces.new/shreeharijoshi/IBM-BOB-Hackathon-2.0](https://codespaces.new/shreeharijoshi/IBM-BOB-Hackathon-2.0) to launch an instant in-browser VS Code environment with this extension pre-installed and ready to test!

A production-ready VS Code extension that analyzes C++ compiler errors (GCC and Apple Clang) and provides plain-language, context-aware explanations directly inside the editor.

## Features

- **Explain Error** (`Ctrl+Shift+D` / `Cmd+Shift+D`) — Diagnose the active C++ file and show the first error with a full explanation
- **Analyze File** — Same as Explain Error but also opens a rich HTML explanation panel beside your code
- **Clear Diagnostics** (`Ctrl+Shift+K` / `Cmd+Shift+K`) — Remove all diagnostic markers and decorations
- **Show Explanation Panel** — Re-open the last explanation in a side-by-side webview
- **Problems panel integration** — Squiggly underlines + multi-error markers via VS Code's native DiagnosticCollection
- **Hover explanations** — Hover over the flagged line to see the full diagnosis in a tooltip
- **Evidence highlighting** — The flagged source line is highlighted with an amber background
- **Auto-analyze on save** — Optional setting to automatically diagnose on save

## Architecture

```
C++ source
    → GCC / Apple Clang (compiler.py)
    → parse_compiler_output() — structured diagnostic
    → enrich_context() — source window extraction
    → analyze_simple_errors() — 107 deterministic rules
    → analyze_with_bob() — generic fallback (no API key needed)
    → unified diagnosis JSON
    → VS Code extension (DiagnosticCollection + HoverProvider + WebviewPanel)
```

## Project Structure

```
backend/
  main.py          — FastAPI /diagnose and /health endpoints
  compiler.py      — GCC execution, output capture, output parsing
  context.py       — Source context window extraction
  diagnostics.py   — Deterministic rule engine (107+ rules)
  bob.py           — Generic AI-style fallback (no API key required)
  diagnose.py      — CLI adapter (used by extension subprocess transport)

vscode-extension/
  src/extension.ts — Full VS Code extension TypeScript source
  out/             — Compiled JavaScript
  cpp-diagnostic-0.2.0.vsix — Installable VSIX package

examples/          — Sample .cpp files with intentional errors
tests/             — Comprehensive pytest test suite (204 tests)
```

## Quick Start — Backend

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Run tests:
```bash
pytest -q
```

## Quick Start — VS Code Extension

### Option 1: Install the VSIX directly

1. In VS Code: `Extensions` → `...` (More Actions) → `Install from VSIX…`
2. Select `vscode-extension/cpp-diagnostic-0.2.0.vsix`
3. Reload VS Code

### Option 2: Build from source

```bash
cd vscode-extension
npm install
npm run compile
# Then install the resulting VSIX as above
```

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `cppDiagnostic.pythonPath` | `python` | Path to the Python interpreter for the CLI adapter |
| `cppDiagnostic.backendUrl` | *(empty)* | URL of a running backend server (e.g. `http://localhost:8000`). When set, HTTP transport is used instead of the CLI subprocess. |
| `cppDiagnostic.autoAnalyzeOnSave` | `false` | Automatically diagnose C++ files when saved |

### Transport Modes

**CLI subprocess (default):** The extension spawns `python backend/diagnose.py --file <path>`. No server needed — just Python in the `cppDiagnostic.pythonPath`.

**HTTP backend:** Set `cppDiagnostic.backendUrl` to a running uvicorn server URL. The extension posts source code to `/diagnose` and receives the diagnosis JSON. This mode supports multi-error analysis via the `diagnostics` array.

## Workflow

1. Open a C++ project in VS Code
2. Write code that contains a compiler error (or open one of the `examples/` files)
3. Press `Ctrl+Shift+D` (or right-click → "Diagnose C++: Explain Error")
4. The extension:
   - Saves the file if dirty
   - Runs the diagnostic backend (CLI or HTTP)
   - Shows a squiggly underline on the flagged line
   - Highlights the evidence line with an amber background
   - Displays the full explanation in the Output Channel
   - Provides a hover tooltip on the evidence line
5. Use **Analyze File** (`Ctrl+Shift+D` then the command palette) to open the rich webview panel

## Diagnostic Rule Coverage (107+ rules)

| Category | Examples |
|----------|---------|
| Missing semicolons | GCC: `expected ';' before`, Clang: `expected ';' at end of declaration` |
| Missing braces/parens | `expected '}' at end of input`, `expected ')'`, `unmatched '{'` |
| Undefined variables | GCC: `was not declared in this scope`, Clang: `use of undeclared identifier` |
| Undefined types | `unknown type name`, `does not name a type` |
| Type mismatches | `cannot convert`, `cannot initialize a variable of type`, `invalid conversion from` |
| Wrong arguments | `no matching function for call to`, `too few/many arguments`, `requires N arguments, but M was provided` |
| Missing includes | `is not a member of 'std'`, `use of undeclared identifier 'std'`, `file not found` |
| Member not found | `has no member named`, `no member named ... in '...'` |
| Undefined functions | `call to undeclared function`, `implicit declaration of function` |
| Missing return | `function does not return a value`, `control reaches end of non-void function` |
| Access violations | `is a private member`, `private member ... of class` |
| Pointer errors | `indirection requires pointer operand`, `cannot take the address of an rvalue` |
| Reference errors | `cannot bind non-const`, `drops const qualifier` |
| Template errors | `template argument deduction failed`, `in instantiation of function template` |
| Operator errors | `no match for operator`, `invalid operands to binary expression`, `expression is not assignable` |
| Redefinition | `redefinition of`, `conflicting types` |
| Linker errors | `undefined reference to`, `multiple definition of` |
| Syntax errors | `expected primary-expression`, `stray '...' in program`, `unexpected token` |
| C++ modern | `use of deleted function`, `cannot instantiate abstract class`, `narrowing conversion` |
| Other | Division by zero, constexpr, shadowing, incomplete types, and more |

## API Reference

### `POST /diagnose`

**Request body:**
```json
{
  "source_code": "int main() { int x = 10\n return 0; }",
  "compiler_output": ""
}
```
When `compiler_output` is empty and `source_code` is provided, the backend runs GCC and analyzes the output. When `compiler_output` is pre-supplied, it is used directly (no GCC invocation).

**Response (error found):**
```json
{
  "status": "ok",
  "diagnosis": {
    "error_type": "MISSING_SEMICOLON",
    "analysis_mode": "deterministic",
    "compiler_message": "...",
    "compiler_explanation": "...",
    "source_explanation": "...",
    "evidence": { "line": 2, "code": "    int x = 10" },
    "what_to_check": "...",
    "suggestion": "..."
  },
  "diagnostics": [
    {
      "error_type": "MISSING_SEMICOLON",
      "analysis_mode": "deterministic",
      "compiler_message": "...",
      "compiler_explanation": "...",
      "source_explanation": "...",
      "evidence": { "line": 2, "code": "    int x = 10" },
      "what_to_check": "...",
      "suggestion": "...",
      "raw": { "file": "...", "line": 2, "column": 5, "severity": "error", "message": "..." }
    }
  ],
  "pipeline": ["compiler.py", "context.py", "diagnostics.py", "bob.py"]
}
```

**Response (clean compilation):**
```json
{ "status": "clean", "message": "No compiler errors found.", "pipeline": ["compiler.py"] }
```

**Response (infrastructure error):**
```json
{ "status": "error", "message": "GCC not found. Ensure GCC is installed and available on PATH." }
```

### `GET /health`

```json
{ "status": "ok" }
```

## Requirements

- **Backend:** Python 3.9+, GCC or Apple Clang on PATH, packages in `requirements.txt`
- **Extension:** VS Code 1.85+, Python on PATH (or configure `cppDiagnostic.pythonPath`)

## Security

- API keys are never required. The fallback works fully offline.
- No source code is sent to external services.
- Stack traces are never exposed in API responses.
- See `SECURITY.MD` for full security guidance.

## VSIX Location

```
vscode-extension/cpp-diagnostic-0.2.0.vsix
```
