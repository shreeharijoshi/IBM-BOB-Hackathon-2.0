from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from backend.bob import analyze_with_bob
from backend.compiler import (
    GCCNotFoundError,
    GCCTimeoutError,
    parse_compiler_output,
    run_compiler,
)
from backend.context import enrich_context
from backend.diagnostics import analyze_simple_errors

app = FastAPI(title="Context Diagnostic")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiagnoseRequest(BaseModel):
    source_code: str = ""
    compiler_output: str = ""

    @field_validator("source_code", "compiler_output", mode="before")
    @classmethod
    def coerce_none_to_empty(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v)


def _analyse_one(target: dict, source_code: str) -> dict:
    """Run the full context + analysis pipeline on a single diagnostic.

    Attaches a 'raw' field with the original compiler diagnostic fields.
    """
    contextual = enrich_context(target, source_code)
    diagnosis = analyze_simple_errors(contextual) or analyze_with_bob(contextual)
    diagnosis["raw"] = {
        "file": target.get("file", ""),
        "line": target.get("line"),
        "column": target.get("column"),
        "severity": target.get("severity", "error"),
        "message": target.get("message", ""),
    }
    return diagnosis


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/diagnose")
def diagnose(payload: DiagnoseRequest) -> dict:
    source_code = payload.source_code or ""
    compiler_output = payload.compiler_output or ""

    # If no pre-supplied compiler output, run GCC on the source code.
    if not compiler_output and source_code:
        try:
            result = run_compiler(source_code)
            compiler_output = result["stderr"]
        except GCCNotFoundError as exc:
            return {"status": "error", "message": str(exc)}
        except GCCTimeoutError as exc:
            return {"status": "error", "message": str(exc)}

    diagnostics = parse_compiler_output(compiler_output)
    errors = [d for d in diagnostics if d.get("severity") in ("error", "fatal error")]
    target = errors[0] if errors else (diagnostics[0] if diagnostics else None)

    if not target:
        if compiler_output.strip():
            # Treat unparsed compiler output as raw message
            raw_diag = {"normalized": compiler_output.strip(), "message": compiler_output.strip()}
            diagnosis = _analyse_one(raw_diag, source_code)
            return {
                "status": "ok",
                "diagnosis": diagnosis,
                "diagnostics": [diagnosis],
                "pipeline": ["compiler.py", "context.py", "diagnostics.py", "bob.py"],
            }
        return {
            "status": "clean",
            "message": "No compiler errors found.",
            "pipeline": ["compiler.py"],
        }

    # Deep-analyse ALL errors.
    all_diagnoses = [_analyse_one(err, source_code) for err in errors]

    return {
        "status": "ok",
        "diagnosis": all_diagnoses[0],      # first error — backward compat
        "diagnostics": all_diagnoses,       # all errors — full multi-error support
        "pipeline": [
            "compiler.py",
            "context.py",
            "diagnostics.py",
            "bob.py",
        ],
    }
