"""GCC execution and raw compiler output capture."""

import re
import subprocess
import tempfile
import os
from typing import TypedDict

_GCC_TIMEOUT = 10  # seconds

# Matches a GCC diagnostic line:
#   <file>:<line>:<col>: <severity>: <message>
#
# On Windows, <file> may start with a drive letter (e.g. C:\...), so the
# first colon after a single letter is NOT a field separator.  We anchor on
# the known severity keywords and then require exactly two integer fields
# (line, column) immediately before the severity keyword.
_DIAG_RE = re.compile(
    r"^(?P<file>.+?)"           # file path (non-greedy)
    r":(?P<line>\d+)"           # :line
    r":(?P<col>\d+)"            # :column
    r":\s*(?P<severity>error|warning|note|fatal error)"  # : severity
    r":\s*(?P<message>.+)$",    # : message
    re.MULTILINE,
)


class CompilerResult(TypedDict):
    """Raw result from a GCC compilation attempt."""
    stdout: str
    stderr: str
    returncode: int


class Diagnostic(TypedDict):
    """A single structured diagnostic record parsed from GCC output."""
    file: str
    line: int
    column: int
    severity: str   # "error" | "warning" | "note" | "fatal error"
    message: str


class GCCNotFoundError(RuntimeError):
    """Raised when GCC is not installed or not on PATH."""


class GCCTimeoutError(RuntimeError):
    """Raised when GCC exceeds the compilation timeout."""


def run_compiler(source_code: str) -> CompilerResult:
    """
    Write source_code to a temporary .cpp file, invoke GCC, and return
    the captured stdout, stderr, and process return code.

    This function does NOT parse or interpret the compiler output.
    It is solely responsible for reliable GCC execution and output capture.

    Args:
        source_code: C++ source code as a string.

    Returns:
        A CompilerResult dict with keys:
            stdout    - captured standard output (usually empty for GCC)
            stderr    - captured standard error (compiler diagnostics live here)
            returncode - GCC exit code (0 = success, non-zero = compile error)

    Raises:
        GCCNotFoundError: if GCC is not installed or not on PATH.
        GCCTimeoutError: if GCC does not finish within the timeout.
        OSError: if the temporary file cannot be created or written.
    """
    tmp_path = None
    try:
        # Create a named temp file with a .cpp extension.
        # delete=False so we control cleanup ourselves.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".cpp",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(source_code)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                ["gcc", "-x", "c++", "-fsyntax-only", tmp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=_GCC_TIMEOUT,
            )
        except FileNotFoundError:
            raise GCCNotFoundError(
                "GCC not found. Ensure GCC is installed and available on PATH."
            )
        except subprocess.TimeoutExpired:
            raise GCCTimeoutError(
                f"GCC did not finish within {_GCC_TIMEOUT} seconds."
            )

        return CompilerResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def parse_compiler_output(raw_output: str) -> list[Diagnostic]:
    """
    Parse raw GCC stderr into a list of structured Diagnostic records.

    Only lines that match the standard GCC diagnostic format are included:
        <file>:<line>:<col>: <severity>: <message>

    All other lines (context lines, caret markers, blank lines, "In function"
    notes, etc.) are silently ignored rather than causing an error.

    Args:
        raw_output: The raw stderr string produced by GCC.

    Returns:
        A (possibly empty) list of Diagnostic dicts.
    """
    diagnostics: list[Diagnostic] = []
    for match in _DIAG_RE.finditer(raw_output):
        diagnostics.append(
            Diagnostic(
                file=match.group("file"),
                line=int(match.group("line")),
                column=int(match.group("col")),
                severity=match.group("severity"),
                message=match.group("message").strip(),
            )
        )
    return diagnostics
