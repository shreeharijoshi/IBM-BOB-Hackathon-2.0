"""Minimal compiler output parsing placeholder."""


def parse_compiler_output(raw_output: str) -> dict:
    return {
        "raw": raw_output,
        "normalized": raw_output.strip(),
    }
