"""Minimal context enrichment placeholder."""


def enrich_context(diagnostic: dict, source_code: str) -> dict:
    result = dict(diagnostic)
    result["source_preview"] = source_code.splitlines()[:5]
    return result
