"""Deterministic diagnostic placeholder for simple known errors."""


_SIMPLE_RULES = {
    "expected ';'": "Possible missing semicolon.",
    "was not declared in this scope": "Possible undefined variable.",
    "no matching function": "Possible wrong arguments for function call.",
}


def analyze_simple_errors(contextual_diagnostic: dict) -> dict | None:
    text = str(contextual_diagnostic.get("normalized", ""))
    for needle, message in _SIMPLE_RULES.items():
        if needle in text:
            return {"source": "deterministic", "message": message}
    return None
