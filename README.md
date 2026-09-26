# Context Diagnostic

Context Diagnostic is a hackathon prototype that explains compiler/runtime errors with deterministic checks first and a Bob fallback for complex cases.

## Architecture / Data Flow

C++ source -> GCC/compiler -> `compiler.py` -> structured diagnostic -> `context.py` -> `diagnostics.py` deterministic analysis for known/simple errors -> `bob.py` for complex errors -> unified diagnosis -> React frontend.

## Project Structure

- `backend/` - minimal FastAPI starter endpoints and diagnostic pipeline placeholders
- `frontend/` - minimal React/Vite starter UI
- `examples/` - small sample files that intentionally contain common errors
- `tests/` - placeholder tests for backend modules and API wiring

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

See `SECURITY.MD` for security guidance and keep real credentials only in local `.env`.
