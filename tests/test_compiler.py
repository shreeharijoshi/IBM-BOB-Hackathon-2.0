import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compiler import parse_compiler_output


def test_parse_compiler_output_returns_structure():
    parsed = parse_compiler_output(" error: expected ';' ")
    assert parsed["normalized"] == "error: expected ';'"
