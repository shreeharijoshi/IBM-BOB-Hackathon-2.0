"""Tests for Gemini AI integration and fallback behaviors.

Covers:
- Gemini disabled / no API key
- Gemini enabled with mocked responses
- Gemini invalid API key error handling
- Gemini timeout / network error handling
- Check Code with Gemini functionality
- Deterministic rules precedence (rules fire first, Gemini only for unknown/complex)
"""

import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from backend.gemini import (
    analyze_compiler_error_with_gemini,
    check_code_with_gemini,
    get_gemini_config,
    is_gemini_available,
)
from backend.pipeline import analyse_one, build_result


def test_gemini_disabled_when_no_api_key(monkeypatch):
    """When GEMINI_API_KEY is not set, Gemini is safely disabled."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = get_gemini_config(override_key="")
    assert not is_gemini_available(cfg)

    # analyze_compiler_error_with_gemini returns None
    result = analyze_compiler_error_with_gemini({"message": "unknown error"}, config=cfg)
    assert result is None

    # check_code_with_gemini returns disabled status
    review = check_code_with_gemini("int main() {}", config=cfg)
    assert review["status"] == "disabled"
    assert "not configured" in review["message"]


def test_gemini_disabled_via_flag(monkeypatch):
    """When GEMINI_ENABLED=false, Gemini is disabled even if API key exists."""
    cfg = get_gemini_config(override_key="fake-key", override_enabled=False)
    assert not is_gemini_available(cfg)

    result = analyze_compiler_error_with_gemini({"message": "unknown error"}, config=cfg)
    assert result is None


def test_gemini_analysis_mocked_success():
    """Mocked Gemini API call returning valid structured error diagnosis."""
    cfg = {
        "api_key": "test-key-123",
        "model": "gemini-2.5-flash",
        "timeout": 10.0,
        "enabled": True,
    }

    mock_gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "error_type": "TEMPLATE_DEDUCTION_FAILURE",
                                "confidence": 0.92,
                                "compiler_explanation": "Template arguments could not be deduced from function call.",
                                "source_explanation": "Type T cannot be matched to both int and double.",
                                "what_to_check": "Explicitly specify template arguments or cast parameters.",
                                "suggestion": "Call as min<double>(1, 2.5) instead of min(1, 2.5).",
                                "root_cause_line": 5,
                                "root_cause_column": 10,
                            })
                        }
                    ]
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_gemini_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        diag = {
            "message": "no matching function for call to 'min(int, double)'",
            "line": 5,
            "source_context": [{"line": 5, "code": "    return min(1, 2.5);"}],
        }
        res = analyze_compiler_error_with_gemini(diag, config=cfg)

        assert res is not None
        assert res["analysis_mode"] == "gemini"
        assert res["error_type"] == "TEMPLATE_DEDUCTION_FAILURE"
        assert res["confidence"] == 0.92
        assert "Template arguments" in res["compiler_explanation"]
        assert "min<double>" in res["suggestion"]


def test_gemini_check_code_mocked_success():
    """Mocked Gemini code review returning structured findings."""
    cfg = {
        "api_key": "test-key-123",
        "model": "gemini-2.5-flash",
        "timeout": 10.0,
        "enabled": True,
    }

    mock_gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "summary": "Found 1 critical memory leak and 1 logic issue.",
                                "findings": [
                                    {
                                        "title": "Memory leak: allocated buffer is never deleted",
                                        "category": "runtime_risk",
                                        "severity": "high",
                                        "line": 3,
                                        "column": 5,
                                        "explanation": "int* ptr = new int[100]; is not paired with delete[].",
                                        "suggestion": "Use std::vector<int> instead of manual dynamic allocation.",
                                    }
                                ]
                            })
                        }
                    ]
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_gemini_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        source = "int main() {\n    int* ptr = new int[100];\n    return 0;\n}"
        review = check_code_with_gemini(source, config=cfg)

        assert review["status"] == "ok"
        assert review["title"] == "Gemini AI Analysis"
        assert len(review["findings"]) == 1
        assert review["findings"][0]["category"] == "runtime_risk"
        assert review["findings"][0]["line"] == 3


def test_gemini_invalid_key_error_handling():
    """When Gemini returns HTTP 400/403, it must not crash the pipeline."""
    cfg = {
        "api_key": "invalid-key",
        "model": "gemini-2.5-flash",
        "timeout": 5.0,
        "enabled": True,
    }

    http_err = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com/...",
        code=400,
        msg="API_KEY_INVALID",
        hdrs={},
        fp=MagicMock(read=lambda: b'{"error":{"message":"API key not valid."}}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        # Compiler error analysis falls back gracefully to None
        res = analyze_compiler_error_with_gemini({"message": "some complex error"}, config=cfg)
        assert res is None

        # Check code returns error status without crashing
        review = check_code_with_gemini("int main() {}", config=cfg)
        assert review["status"] == "error"
        assert "API key not valid" in review["message"]


def test_gemini_timeout_error_handling():
    """When Gemini times out, it must not crash the pipeline."""
    cfg = {
        "api_key": "valid-key",
        "model": "gemini-2.5-flash",
        "timeout": 1.0,
        "enabled": True,
    }

    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        res = analyze_compiler_error_with_gemini({"message": "some complex error"}, config=cfg)
        assert res is None

        review = check_code_with_gemini("int main() {}", config=cfg)
        assert review["status"] == "error"
        assert "timed out" in review["message"]


def test_deterministic_rules_precede_gemini():
    """Deterministic rules must always take priority over Gemini."""
    cfg = {
        "api_key": "valid-key",
        "model": "gemini-2.5-flash",
        "timeout": 10.0,
        "enabled": True,
    }

    # Error is a simple MISSING_SEMICOLON reported at line 3 before 'return'
    error = {
        "file": "main.cpp",
        "line": 3,
        "column": 5,
        "severity": "error",
        "message": "expected ';' before 'return'",
    }
    source = "int main() {\n    int x = 10\n    return 0;\n}"

    # Even with Gemini configured, analyze_one should use deterministic rule
    diagnosis = analyse_one(error, source, gemini_config=cfg)
    assert diagnosis["analysis_mode"] == "deterministic"
    assert diagnosis["error_type"] == "MISSING_SEMICOLON"
    assert diagnosis["confidence"] == 1.0
    assert diagnosis["root_cause_location"]["line"] == 2
    assert diagnosis["compiler_location"]["line"] == 3
