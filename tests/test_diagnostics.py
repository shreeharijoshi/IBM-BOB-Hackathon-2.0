import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.diagnostics import analyze_simple_errors


def test_analyze_simple_errors_known_case():
    result = analyze_simple_errors({"normalized": "error: expected ';' before '}'"})
    assert result is not None
    assert result["source"] == "deterministic"
