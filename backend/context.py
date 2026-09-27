"""Source context extraction for GCC diagnostics."""

from typing import TypedDict


class SourceLine(TypedDict):
    """A single source line with its 1-based line number."""
    line: int
    code: str


def get_source_context(
    diagnostic: dict,
    source_code: str,
    window: int = 2,
) -> list[SourceLine]:
    """
    Return the source lines surrounding the diagnostic location.

    Args:
        diagnostic: A Diagnostic dict (must contain a ``"line"`` key with a
                    1-based integer line number).
        source_code: The original C++ source code as a string.
        window: Number of lines to include before and after the diagnostic
                line (default: 2).

    Returns:
        A list of SourceLine dicts, each with:
            line  - 1-based line number
            code  - exact source text for that line (no trimming or normalising)

        Returns an empty list if the diagnostic line number is missing,
        non-integer, or outside the range of the source file.

    Notes:
        * Source code is never modified, trimmed, or normalised.
        * The window is clamped to the actual file boundaries so callers near
          the start or end of a file always get a valid (possibly smaller)
          result rather than an error.
        * This function only extracts context; it does not interpret, classify,
          or explain the diagnostic.
    """
    lines = source_code.splitlines()
    total = len(lines)

    # Validate the diagnostic line number.
    try:
        diag_line = int(diagnostic["line"])
    except (KeyError, TypeError, ValueError):
        return []

    # 1-based validity check.
    if diag_line < 1 or diag_line > total:
        return []

    # Convert to 0-based index for slicing, then clamp the window.
    idx = diag_line - 1
    start = max(0, idx - window)
    end = min(total - 1, idx + window)

    return [
        SourceLine(line=i + 1, code=lines[i])
        for i in range(start, end + 1)
    ]


# ---------------------------------------------------------------------------
# Legacy placeholder — kept for any existing callers; delegates to the new API.
# ---------------------------------------------------------------------------

def enrich_context(diagnostic: dict, source_code: str) -> dict:
    result = dict(diagnostic)
    result["source_context"] = get_source_context(diagnostic, source_code)
    # Store the actual source length so downstream code can detect line-number
    # mismatches (compiler line > source length) without needing source_code.
    result["source_line_count"] = len(source_code.splitlines())
    return result
