from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from backend.compiler import (
    GCCNotFoundError,
    GCCTimeoutError,
    parse_compiler_output,
    run_compiler,
)
from backend.pipeline import analyse_one, build_result

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

    parsed = parse_compiler_output(compiler_output)
    errors = [d for d in parsed if d.get("severity") in ("error", "fatal error")]

    # Only errors are actionable.  Warnings and notes must never be treated as
    # errors — a warning-only compiler_output should produce status='clean'.
    if not errors:
        if compiler_output.strip() and not parsed:
            # Entirely unparsed compiler output (no structured diagnostics at all)
            # — treat the raw text as a message to analyse.
            raw_diag = {"normalized": compiler_output.strip(), "message": compiler_output.strip()}
            diagnosis = analyse_one(raw_diag, source_code)
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

    # Deep-analyse ALL errors, then attach the pipeline field HTTP callers expect.
    result = build_result(errors, source_code)
    result["pipeline"] = ["compiler.py", "context.py", "diagnostics.py", "bob.py"]
    return result
