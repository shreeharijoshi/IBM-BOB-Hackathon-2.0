"""Root-cause location resolution for compiler diagnostics.

GCC often points to the NEXT token/line after the actual mistake (e.g. for a
missing semicolon GCC reports the line *after* the missing `;`, or for a missing
closing brace it reports the end of input). This module resolves the GCC-reported
compiler location to the actual root-cause location of the source mistake with
precise line, column, and range information wherever reliable.
"""

from __future__ import annotations

import re
from typing import TypedDict


class LocationDict(TypedDict, total=False):
    file: str
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None
    source: str


def _find_symbol_range(line_text: str, symbol: str) -> tuple[int, int] | None:
    """Find 1-based start and end column for symbol in line_text."""
    if not line_text or not symbol:
        return None
    # Try word boundary first
    pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
    m = pattern.search(line_text)
    if m:
        return (m.start() + 1, m.end() + 1)
    # Fallback to substring
    idx = line_text.find(symbol)
    if idx != -1:
        return (idx + 1, idx + 1 + len(symbol))
    return None


def _find_unclosed_brace(source_code: str) -> tuple[int, int] | None:
    """Scan source code to locate the line and 1-based column of the last unclosed '{'."""
    lines = source_code.splitlines()
    brace_stack: list[tuple[int, int]] = []  # (1-based line, 1-based col)

    in_block_comment = False
    for line_idx, line in enumerate(lines):
        line_num = line_idx + 1
        i = 0
        in_string = False
        string_char = ""
        while i < len(line):
            if in_block_comment:
                if line[i : i + 2] == "*/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if not in_string:
                if line[i : i + 2] == "/*":
                    in_block_comment = True
                    i += 2
                    continue
                if line[i : i + 2] == "//":
                    break
                if line[i] in ('"', "'"):
                    in_string = True
                    string_char = line[i]
                    i += 1
                    continue
                if line[i] == "{":
                    brace_stack.append((line_num, i + 1))
                elif line[i] == "}":
                    if brace_stack:
                        brace_stack.pop()
            else:
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == string_char:
                    in_string = False
            i += 1

    if brace_stack:
        return brace_stack[-1]
    return None


def resolve_root_cause(
    diagnostic: dict,
    error_type: str = "",
    source_code: str = "",
) -> dict:
    """Return a detailed root_cause_location dict for the compiler diagnostic.

    Args:
        diagnostic: A parsed compiler diagnostic dict with keys:
            line (int|None), column (int|None), file (str), message / normalized (str),
            source_context (list), source_line_count (int|None).
        error_type: Classified error type (e.g. "MISSING_SEMICOLON").
        source_code: Full original C++ source code if available.

    Returns:
        dict with:
            file       — file path string
            line       — 1-based int or None
            column     — 1-based int or None
            end_line   — 1-based int or None
            end_column — 1-based int or None
            source     — source text of the root cause line
    """
    file_path: str = diagnostic.get("file", "")
    gcc_line: int | None = diagnostic.get("line")
    gcc_col: int | None = diagnostic.get("column")
    msg: str = diagnostic.get("message") or diagnostic.get("normalized") or ""
    source_context: list = diagnostic.get("source_context", [])
    source_line_count: int | None = diagnostic.get("source_line_count")

    # If source_code wasn't passed directly, reconstruct lines from context if possible
    source_lines: list[str] = source_code.splitlines() if source_code else []
    if not source_lines and source_context:
        max_ctx_line = max((e.get("line", 0) for e in source_context), default=0)
        source_lines = [""] * max(max_ctx_line, (source_line_count or 0))
        for e in source_context:
            idx = e.get("line", 0) - 1
            if 0 <= idx < len(source_lines):
                source_lines[idx] = e.get("code", "")

    def get_line_text(l: int | None) -> str:
        if l is None or l < 1:
            return ""
        if source_lines and l <= len(source_lines):
            return source_lines[l - 1]
        for e in source_context:
            if e.get("line") == l:
                return e.get("code", "")
        return ""

    root_line: int | None = gcc_line
    root_col: int | None = gcc_col
    end_line: int | None = gcc_line
    end_col: int | None = gcc_col

    # -------------------------------------------------------------------------
    # 1. MISSING_SEMICOLON
    # -------------------------------------------------------------------------
    if error_type == "MISSING_SEMICOLON":
        # Check if error is "expected ';' at end of declaration" or "after ..." (reported ON the line)
        is_at_end = bool(
            re.search(r"at end of declaration", msg, re.I)
            or re.search(r"expected ';' after", msg, re.I)
        )

        if is_at_end and gcc_line is not None:
            root_line = gcc_line
            line_str = get_line_text(gcc_line)
            trimmed_len = len(line_str.rstrip())
            root_col = trimmed_len + 1 if trimmed_len > 0 else (gcc_col or 1)
            end_col = root_col + 1
        elif gcc_line is not None:
            # GCC message typically says "expected ';' before '<token>'", pointing to the line of <token>
            curr_line_text = get_line_text(gcc_line)

            # Extract token if message says "before '<token>'"
            token_match = re.search(r"before\s+['\"]?([^'\"\s]+)", msg)
            token = token_match.group(1) if token_match else ""

            code_before_token = ""
            token_idx = -1
            if token and gcc_col and gcc_col > 0:
                search_start = max(0, gcc_col - len(token) - 2)
                found = curr_line_text.find(token, search_start)
                if found != -1 and found <= gcc_col + 2:
                    token_idx = found
            elif token:
                token_idx = curr_line_text.find(token)

            if token_idx != -1:
                code_before_token = curr_line_text[:token_idx].strip()
            elif gcc_col and gcc_col > 1:
                code_before_token = curr_line_text[: gcc_col - 1].strip()

            if code_before_token and not curr_line_text.strip().startswith("}"):
                # Missing semicolon is earlier on the SAME line
                root_line = gcc_line
                if token_idx != -1:
                    root_col = len(curr_line_text[:token_idx].rstrip()) + 1
                else:
                    root_col = len(curr_line_text[: gcc_col - 1].rstrip()) + 1
                end_col = root_col + 1
            else:
                # Missing semicolon is on the previous non-empty line
                prev_line = gcc_line - 1
                while prev_line >= 1:
                    prev_text = get_line_text(prev_line)
                    stripped = prev_text.strip()
                    if stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
                        break
                    prev_line -= 1

                if prev_line >= 1:
                    root_line = prev_line
                    prev_text = get_line_text(prev_line)
                    trimmed_len = len(prev_text.rstrip())
                    root_col = trimmed_len + 1 if trimmed_len > 0 else 1
                    end_col = root_col + 1
                else:
                    root_line = gcc_line
                    root_col = gcc_col
                    end_col = (gcc_col + 1) if gcc_col else None

        end_line = root_line

    # -------------------------------------------------------------------------
    # 2. MISSING_BRACE
    # -------------------------------------------------------------------------
    elif error_type == "MISSING_BRACE":
        if "at end of input" in msg.lower() or "expected '}'" in msg.lower():
            # Missing closing brace: find the unclosed opening brace in source
            if source_code:
                unclosed = _find_unclosed_brace(source_code)
                if unclosed:
                    root_line, root_col = unclosed
                    end_line = root_line
                    end_col = root_col + 1
            elif gcc_line is not None and gcc_line > 1:
                # If source_code not available, point to the last block start in context
                root_line = gcc_line
                root_col = gcc_col
                end_line = gcc_line
                end_col = gcc_col

    # -------------------------------------------------------------------------
    # 3. MISSING_PAREN
    # -------------------------------------------------------------------------
    elif error_type == "MISSING_PAREN":
        if "before ';'" in msg.lower() and gcc_line is not None and gcc_col is not None:
            # Missing ')' right before semicolon
            root_line = gcc_line
            end_line = gcc_line
            root_col = max(1, gcc_col - 1)
            end_col = gcc_col
        else:
            root_line = gcc_line
            end_line = gcc_line
            root_col = gcc_col
            end_col = (gcc_col + 1) if gcc_col else None

    # -------------------------------------------------------------------------
    # 4. UNDEFINED_VARIABLE / UNDEFINED_FUNCTION
    # -------------------------------------------------------------------------
    elif error_type in ("UNDEFINED_VARIABLE", "UNDEFINED_FUNCTION"):
        sym_match = re.search(r"'([^']+)'", msg)
        if sym_match and gcc_line is not None:
            symbol = sym_match.group(1).split("::")[-1]  # get unqualified identifier
            line_text = get_line_text(gcc_line)
            rng = _find_symbol_range(line_text, symbol)
            if rng:
                root_line = gcc_line
                end_line = gcc_line
                root_col, end_col = rng

    # -------------------------------------------------------------------------
    # 5. WRONG_ARGUMENTS
    # -------------------------------------------------------------------------
    elif error_type == "WRONG_ARGUMENTS":
        sym_match = re.search(r"(?:call to|function)\s+'([^'(]+)", msg)
        if sym_match and gcc_line is not None:
            fn_name = sym_match.group(1).strip().split("::")[-1]
            line_text = get_line_text(gcc_line)
            rng = _find_symbol_range(line_text, fn_name)
            if rng:
                root_line = gcc_line
                end_line = gcc_line
                # Try to expand range to closing ')'
                call_start = rng[0]
                after_fn = line_text[call_start - 1 :]
                close_paren_idx = after_fn.find(")")
                if close_paren_idx != -1:
                    root_col = call_start
                    end_col = call_start + close_paren_idx + 1
                else:
                    root_col, end_col = rng

    # -------------------------------------------------------------------------
    # 6. TYPE_MISMATCH
    # -------------------------------------------------------------------------
    elif error_type == "TYPE_MISMATCH":
        if gcc_line is not None and gcc_col is not None:
            root_line = gcc_line
            end_line = gcc_line
            line_text = get_line_text(gcc_line)
            # If gcc_col points to an expression on that line
            if line_text and gcc_col <= len(line_text):
                # Try to highlight the expression token
                col_idx = gcc_col - 1
                rest = line_text[col_idx:]
                end_match = re.search(r"[;,)}\s]", rest)
                token_len = end_match.start() if end_match else len(rest)
                root_col = gcc_col
                end_col = gcc_col + max(1, token_len)

    # -------------------------------------------------------------------------
    # 7. MISSING_INCLUDE
    # -------------------------------------------------------------------------
    elif error_type == "MISSING_INCLUDE":
        # Extract the symbol e.g. 'cout' from 'cout' is not a member of 'std'
        sym_match = re.search(r"'([^']+)'", msg)
        if sym_match and gcc_line is not None:
            symbol = sym_match.group(1)
            line_text = get_line_text(gcc_line)
            rng = _find_symbol_range(line_text, symbol)
            if rng:
                root_line = gcc_line
                end_line = gcc_line
                root_col, end_col = rng

    # -------------------------------------------------------------------------
    # 8. MEMBER_NOT_FOUND / ACCESS_VIOLATION
    # -------------------------------------------------------------------------
    elif error_type in ("MEMBER_NOT_FOUND", "ACCESS_VIOLATION"):
        sym_match = re.search(r"(?:member named|member)\s+'([^']+)'", msg)
        if sym_match and gcc_line is not None:
            member_name = sym_match.group(1)
            line_text = get_line_text(gcc_line)
            rng = _find_symbol_range(line_text, member_name)
            if rng:
                root_line = gcc_line
                end_line = gcc_line
                root_col, end_col = rng

    # -------------------------------------------------------------------------
    # Boundary validation & fallback
    # -------------------------------------------------------------------------
    if root_line is not None:
        if source_line_count is not None and root_line > source_line_count:
            root_line = gcc_line
            root_col = gcc_col
            end_line = gcc_line
            end_col = gcc_col
        elif root_line < 1:
            root_line = 1
            root_col = 1
            end_line = 1
            end_col = 1

    source_text = get_line_text(root_line)

    return {
        "file": file_path,
        "line": root_line,
        "column": root_col,
        "end_line": end_line,
        "end_column": end_col,
        "source": source_text,
    }
