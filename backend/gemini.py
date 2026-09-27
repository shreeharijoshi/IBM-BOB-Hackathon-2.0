"""Gemini AI integration for complex compiler error analysis and deep code reviews.

Deterministic rules remain the primary/default layer and work without any API key.
Gemini is an optional second layer for:
- Complex or unknown compiler errors that fall through deterministic rules.
- Explicit user requests to inspect/review code ("Check Code with Gemini").

Security & Robustness:
- Credentials are read only from environment variables (GEMINI_API_KEY) or secure settings.
- Never hardcode or log credentials.
- Gracefully disabled when no API key is present.
- Timeouts, rate limits, network errors, and invalid keys never crash the system.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _load_env_file() -> None:
    """Read .env from repo root if present and populate os.environ without overwriting."""
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


_load_env_file()


def get_gemini_config(
    override_key: str | None = None,
    override_model: str | None = None,
    override_timeout: float | None = None,
    override_enabled: bool | None = None,
) -> dict[str, Any]:
    """Return effective Gemini configuration."""
    api_key = (
        override_key
        if override_key is not None
        else os.environ.get("GEMINI_API_KEY", "").strip()
    )
    model = (
        override_model
        if override_model is not None
        else os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    )
    if not model:
        model = "gemini-2.5-flash"

    timeout_val = 15.0
    if override_timeout is not None:
        timeout_val = float(override_timeout)
    else:
        try:
            timeout_val = float(os.environ.get("GEMINI_TIMEOUT", "15.0"))
        except ValueError:
            timeout_val = 15.0

    enabled = True
    if override_enabled is not None:
        enabled = bool(override_enabled)
    else:
        env_enabled = os.environ.get("GEMINI_ENABLED", "true").lower()
        enabled = env_enabled not in ("false", "0", "no")

    return {
        "api_key": api_key,
        "model": model,
        "timeout": timeout_val,
        "enabled": enabled,
    }


def is_gemini_available(config: dict[str, Any] | None = None) -> bool:
    """Return True if Gemini is enabled and has an API key configured."""
    cfg = config or get_gemini_config()
    return bool(cfg["enabled"] and cfg["api_key"])


def _call_gemini_api(
    prompt: str,
    system_instruction: str = "",
    config: dict[str, Any] | None = None,
) -> str:
    """Invoke the Google Gemini REST API.

    Raises RuntimeError on API failure, timeout, or invalid response.
    """
    cfg = config or get_gemini_config()
    if not is_gemini_available(cfg):
        raise RuntimeError("Gemini API key is not configured or Gemini is disabled.")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(cfg['model'])}:generateContent?key={urllib.parse.quote(cfg['api_key'])}"
    )

    contents_payload: list[dict[str, Any]] = [
        {"role": "user", "parts": [{"text": prompt}]}
    ]

    payload: dict[str, Any] = {
        "contents": contents_payload,
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            resp_body = resp.read().decode("utf-8")
            resp_json = json.loads(resp_body)
            # Extract text candidate
            candidates = resp_json.get("candidates", [])
            if not candidates:
                raise RuntimeError("No generation candidate returned by Gemini.")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise RuntimeError("Empty response parts from Gemini.")
            return parts[0].get("text", "")
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_msg)
            message = err_json.get("error", {}).get("message", str(exc))
        except Exception:
            message = f"HTTP {exc.code}: {exc.reason}"
        raise RuntimeError(f"Gemini API error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini connection failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Gemini request timed out.") from exc


def analyze_compiler_error_with_gemini(
    contextual_diagnostic: dict,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Analyze a complex or unknown C++ compiler error with Gemini.

    Returns a unified diagnosis dict with analysis_mode='gemini', or None if
    Gemini is unavailable or fails. Never raises exceptions.
    """
    cfg = config or get_gemini_config()
    if not is_gemini_available(cfg):
        return None

    compiler_message = (
        contextual_diagnostic.get("normalized")
        or contextual_diagnostic.get("message")
        or ""
    )
    source_context = contextual_diagnostic.get("source_context", [])
    diag_line = contextual_diagnostic.get("line")

    context_str = "\n".join(
        f"Line {entry.get('line')}: {entry.get('code', '')}"
        for entry in source_context
    )

    prompt = f"""You are a C++ compiler diagnostics expert. Explain this compiler error accurately.
Compiler Message:
{compiler_message}

Surrounding Code:
{context_str}

Respond strictly with a JSON object matching this schema:
{{
  "error_type": "string (succinct upper snake-case like TEMPLATE_DEDUCTION_FAILURE, CONST_VIOLATION, etc.)",
  "confidence": 0.85,
  "compiler_explanation": "Plain language explanation of what the compiler means",
  "source_explanation": "Explanation of why this happened in the user's specific source context",
  "what_to_check": "Actionable steps for the developer to check",
  "suggestion": "Concrete fix recommendation",
  "root_cause_line": int or null (line of the actual mistake if known, otherwise null),
  "root_cause_column": int or null
}}"""

    system_instruction = (
        "You are an assistant for C++ compiler errors. Provide precise, actionable "
        "remediations in the requested JSON format."
    )

    try:
        response_text = _call_gemini_api(prompt, system_instruction, cfg)
        # Parse JSON
        cleaned = response_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        data = json.loads(cleaned.strip())

        # Construct evidence
        evidence_line = diag_line
        evidence_code = ""
        if source_context:
            for e in source_context:
                if e.get("line") == diag_line:
                    evidence_code = e.get("code", "")
                    break
            if not evidence_code:
                evidence_code = source_context[0].get("code", "")

        evidence = {"line": evidence_line, "code": evidence_code}

        return {
            "error_type": data.get("error_type", "COMPLEX_ERROR"),
            "analysis_mode": "gemini",
            "confidence": float(data.get("confidence", 0.85)),
            "compiler_message": compiler_message,
            "compiler_explanation": data.get("compiler_explanation", "Complex compiler error analyzed by Gemini AI."),
            "source_explanation": data.get("source_explanation", "Gemini analyzed the surrounding code context."),
            "explanation": data.get("source_explanation") or data.get("compiler_explanation", ""),
            "evidence": evidence,
            "what_to_check": data.get("what_to_check", "Review the flagged code and relevant type/template declarations."),
            "suggestion": data.get("suggestion", "Inspect the flagged line and adjust declarations according to compiler constraints."),
            "gemini_suggested_line": data.get("root_cause_line"),
            "gemini_suggested_column": data.get("root_cause_column"),
        }
    except Exception:
        # Never crash; return None so deterministic fallback (Bob) handles it
        return None


def check_code_with_gemini(
    source_code: str,
    selection_range: dict[str, int] | None = None,
    file_path: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform a deep code review with Gemini AI for selected code or active file.

    Identifies:
    - possible bugs
    - logic issues
    - suspicious code
    - runtime risks
    - maintainability issues
    - improvements
    - explanations
    - suggested fixes

    Returns a structured dictionary labeled as Gemini AI Analysis.
    Never crashes.
    """
    cfg = config or get_gemini_config()
    file_name = Path(file_path).name if file_path else "active file"

    if not is_gemini_available(cfg):
        return {
            "status": "disabled",
            "title": "Gemini AI Analysis",
            "message": (
                "Gemini AI code review is not configured or is currently disabled.\n"
                "To enable it, set your GEMINI_API_KEY environment variable or "
                "configure 'cppDiagnostic.geminiApiKey' in VS Code settings."
            ),
            "file": file_name,
            "findings": [],
        }

    # Number the source lines for precise references
    lines = source_code.splitlines()
    start_line = selection_range.get("start_line", 1) if selection_range else 1
    end_line = selection_range.get("end_line", len(lines)) if selection_range else len(lines)

    numbered_code = "\n".join(
        f"{idx + 1:4d} | {line}"
        for idx, line in enumerate(lines)
        if start_line <= (idx + 1) <= end_line
    )

    scope_desc = (
        f"selected lines {start_line}-{end_line}"
        if selection_range
        else f"entire file ({len(lines)} lines)"
    )

    prompt = f"""Review the following C++ code snippet ({scope_desc} from {file_name}).
Identify any:
1. Possible bugs
2. Logic issues
3. Suspicious code / anti-patterns
4. Runtime risks (undefined behavior, memory leaks, null dereferences, buffer overflows)
5. Maintainability issues
6. Concrete improvements and suggested fixes

C++ Code:
{numbered_code}

Respond strictly with a JSON object following this exact schema:
{{
  "summary": "Overall summary of code health and findings",
  "findings": [
    {{
      "title": "Brief title of finding",
      "category": "bug" | "logic" | "runtime_risk" | "maintainability" | "improvement",
      "severity": "high" | "medium" | "low",
      "line": int or null (1-based line number if applicable),
      "column": int or null,
      "explanation": "Detailed explanation of the issue or risk",
      "suggestion": "Concrete suggested fix or code snippet"
    }}
  ]
}}"""

    system_instruction = (
        "You are an expert C++ static analyzer and code reviewer. Analyze the code carefully. "
        "Do not hallucinate errors where code is valid. Clearly explain logic or runtime risks."
    )

    try:
        response_text = _call_gemini_api(prompt, system_instruction, cfg)
        cleaned = response_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        data = json.loads(cleaned.strip())

        return {
            "status": "ok",
            "title": "Gemini AI Analysis",
            "scope": scope_desc,
            "file": file_name,
            "summary": data.get("summary", "Gemini analysis completed."),
            "findings": data.get("findings", []),
        }
    except Exception as exc:
        return {
            "status": "error",
            "title": "Gemini AI Analysis",
            "file": file_name,
            "message": f"Gemini code analysis failed: {str(exc)}",
            "findings": [],
        }
