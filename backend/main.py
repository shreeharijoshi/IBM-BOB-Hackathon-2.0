from fastapi import FastAPI
from pydantic import BaseModel

from backend.bob import analyze_with_bob
from backend.compiler import parse_compiler_output
from backend.context import enrich_context
from backend.diagnostics import analyze_simple_errors

app = FastAPI(title="Context Diagnostic")


class DiagnoseRequest(BaseModel):
    source_code: str = ""
    compiler_output: str = ""


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/diagnose")
def diagnose(payload: DiagnoseRequest) -> dict:
    parsed = parse_compiler_output(payload.compiler_output)
    contextual = enrich_context(parsed, payload.source_code)
    simple = analyze_simple_errors(contextual)
    return {
        "diagnosis": simple or analyze_with_bob(contextual),
        "pipeline": [
            "compiler.py",
            "context.py",
            "diagnostics.py",
            "bob.py",
        ],
    }
