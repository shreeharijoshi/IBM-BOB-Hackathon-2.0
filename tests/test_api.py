import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_diagnose_with_compiler_output_missing_semicolon():
    payload = {
        "source_code": "int main() {\n    int x = 10\n    return 0;\n}",
        "compiler_output": "main.cpp:2:5: error: expected ';' before 'return'",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "MISSING_SEMICOLON"
    assert data["diagnosis"]["analysis_mode"] == "deterministic"


def test_diagnose_with_clang_type_mismatch():
    payload = {
        "source_code": "int main() {\n    int x = \"hello\";\n    return x;\n}",
        "compiler_output": "main.cpp:2:9: error: cannot initialize a variable of type 'int' with an lvalue of type 'const char[6]'",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "TYPE_MISMATCH"
    assert data["diagnosis"]["analysis_mode"] == "deterministic"


def test_diagnose_with_clang_undeclared_identifier():
    payload = {
        "source_code": "int main() {\n    return value;\n}",
        "compiler_output": "main.cpp:2:12: error: use of undeclared identifier 'value'",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "UNDEFINED_VARIABLE"


def test_diagnose_with_clang_missing_include():
    payload = {
        "source_code": "int main() {\n    std::cout << \"hi\";\n    return 0;\n}",
        "compiler_output": "main.cpp:2:5: error: use of undeclared identifier 'std'",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "MISSING_INCLUDE"


def test_diagnose_clean():
    payload = {
        "source_code": "int main() { return 0; }",
        "compiler_output": "",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clean"


def test_diagnose_multiple_errors_returns_all_diagnostics():
    payload = {
        "source_code": "int main() {\n    int x = 10\n    return undefined;\n}",
        "compiler_output": (
            "main.cpp:3:5: error: expected ';' before 'return'\n"
            "main.cpp:3:12: error: 'undefined' was not declared in this scope\n"
        ),
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    # Primary diagnosis is first error (backward compat)
    assert data["diagnosis"]["error_type"] == "MISSING_SEMICOLON"
    # diagnostics list contains deep analysis for every error
    assert "diagnostics" in data
    assert len(data["diagnostics"]) == 2
    # Both are fully analysed
    assert data["diagnostics"][0]["error_type"] == "MISSING_SEMICOLON"
    assert data["diagnostics"][1]["error_type"] == "UNDEFINED_VARIABLE"
    # Each has a raw field
    assert "raw" in data["diagnostics"][0]
    assert "raw" in data["diagnostics"][1]
    assert data["diagnostics"][0]["raw"]["line"] == 3


def test_diagnose_none_source_code_handled():
    """None values in the payload must not crash the endpoint."""
    payload = {"source_code": None, "compiler_output": "main.cpp:1:1: error: expected ';' before '}'"}
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "MISSING_SEMICOLON"


def test_diagnose_empty_payload_returns_clean():
    """Empty payload should return status='clean' since no errors present."""
    response = client.post("/diagnose", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clean"


def test_diagnose_unknown_error_uses_bob_fallback():
    payload = {
        "source_code": "int main() { return 0; }",
        "compiler_output": "main.cpp:1:5: error: some mysterious compiler error XYZ999",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "UNKNOWN"
    assert data["diagnosis"]["analysis_mode"] == "ai"


def test_diagnose_pipeline_field_present():
    payload = {
        "source_code": "int main() { return 0; }",
        "compiler_output": "main.cpp:1:1: error: expected ';' before '}'",
    }
    response = client.post("/diagnose", json=payload)
    data = response.json()
    assert "pipeline" in data
    assert isinstance(data["pipeline"], list)
    assert len(data["pipeline"]) > 0


# ---------------------------------------------------------------------------
# Phase 4 regression: warning handling
# ---------------------------------------------------------------------------

def test_warning_only_compiler_output_returns_clean():
    """Phase 4: compiler_output containing only warnings must return status='clean'."""
    payload = {
        "source_code": "int main() { int x = 1; return 0; }",
        "compiler_output": "main.cpp:1:14: warning: unused variable 'x' [-Wunused-variable]\n",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clean", (
        f"Warning-only output must not produce an error diagnosis; got: {data}"
    )
    assert "diagnosis" not in data, "No diagnosis key expected for clean result"


def test_multiple_warnings_only_returns_clean():
    """Phase 4: multiple warnings with no errors → status='clean'."""
    payload = {
        "source_code": "int main() { int x = 1; int y = 2; return 0; }",
        "compiler_output": (
            "main.cpp:1:14: warning: unused variable 'x' [-Wunused-variable]\n"
            "main.cpp:1:21: warning: unused variable 'y' [-Wunused-variable]\n"
        ),
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clean", (
        f"Multiple warnings must not produce an error diagnosis; got: {data}"
    )


def test_error_and_warning_diagnoses_error_not_warning():
    """Phase 4: when both a warning and an error are present, only the error
    is diagnosed — the warning must not appear as a diagnosis entry."""
    payload = {
        "source_code": "int main() {\n    int x = 10\n    return 0;\n}",
        "compiler_output": (
            "main.cpp:1:5: warning: unused variable 'y' [-Wunused-variable]\n"
            "main.cpp:2:16: error: expected ';' before 'return'\n"
        ),
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["diagnosis"]["error_type"] == "MISSING_SEMICOLON", (
        "The error (not the warning) must be the primary diagnosis"
    )
    # Only the single error should be in diagnostics, not the warning
    assert len(data["diagnostics"]) == 1
    assert data["diagnostics"][0]["error_type"] == "MISSING_SEMICOLON"


def test_note_only_compiler_output_returns_clean():
    """Phase 4: compiler 'note' lines (not errors) must also yield status='clean'."""
    payload = {
        "source_code": "int main() { return 0; }",
        "compiler_output": "main.cpp:1:5: note: declared here\n",
    }
    response = client.post("/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clean", (
        f"Note-only output must not produce an error diagnosis; got: {data}"
    )
