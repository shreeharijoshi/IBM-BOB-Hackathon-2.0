"""Bob fallback placeholder for complex errors."""


def analyze_with_bob(contextual_diagnostic: dict) -> dict:
    return {
        "source": "bob",
        "message": "Complex error fallback analysis placeholder.",
        "details": contextual_diagnostic.get("normalized", ""),
    }
