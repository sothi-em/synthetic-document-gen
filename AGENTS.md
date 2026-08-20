# Repository Guidelines

## Project Structure

```
document-gen/
├── document_gen/           # main package
│   ├── cli.py              # CLI entry point (document-gen command)
│   ├── server.py           # FastAPI JSON API; serves built frontend from web/dist
│   ├── pipeline.py         # company generation pipeline (threaded)
│   ├── document_query.py   # TinyDB store: companies, document_types, documents, user_settings (in-memory default, TINYDB_PATH for file)
│   ├── llm.py              # LLM backends (Ollama / OpenAI-compatible), settings, structured completion
│   ├── prompts.py          # LLM prompt templates
│   ├── document_pdf.py     # PDF document pipeline (markdown -> HTML -> WeasyPrint PDF)
│   ├── labels.py           # data-label generation + ChromaDB embedding
│   └── models/             # Pydantic models (company.py, document.py, figures.py, excel.py, llm.py)
├── web/                    # frontend: Vite + React + TS, Tailwind v4, shadcn/ui (builds to web/dist)
├── tests/                  # pytest tests; JSON fixtures in tests/fixtures/
├── code_plans/             # planning documents
├── data/                   # generated output (gitignored)
├── pyproject.toml          # dependencies + build config (uv-managed)
└── README.md               # project overview (single source of truth)
```

See `README.md` for setup, configuration, and usage details.

## Build, Test, and Development Commands

```powershell
# Install dependencies (creates .venv); add --extra embed for chromadb
uv sync --group dev
uv sync --group dev --extra web   # web UI (fastapi, uvicorn)

# Run the CLI
uv run document-gen migrate --from ./data/companies.json
uv run document-gen --help

# Web UI (requires `web` extra + built frontend; frontend uses pnpm)
cd web && pnpm install && pnpm build && cd ..
uv run document-gen serve --port 8000

# Frontend dev / quality
cd web
pnpm dev                     # dev server, proxies /api to :8000
pnpm build                   # tsc -b && vite build
pnpm lint                    # oxlint

# Tests and formatting
uv run pytest
uv run pytest --cov
uv run black .
```

## Coding Style & Naming Conventions

- 4-space indentation, no trailing whitespace, UTF-8 files.
- `black` is the code formatter (run with `uv run black .`).
- Pydantic models live in `document_gen/models/` and are exported via `__init__.py`.
- Class names use `PascalCase`; functions, modules, and constants use `snake_case`.
- Public functions/classes have docstrings (Args/Returns sections).
- No import-time side effects (e.g. no module-level DB clients or network calls).
- Optional heavy dependencies (chromadb) are imported lazily inside functions.

## Testing Guidelines

- `pytest` is the test runner; test files are `tests/test_*.py`.
- Run all tests with `uv run pytest`.
- Aim for 80%+ coverage on the `document_gen/models/` package
  (see `[tool.coverage]` in `pyproject.toml`).
- Pipeline/llm/labels modules require a live LLM server and are not
  unit-tested; keep them thin and test the models/prompts/CLI parsing instead.
- `server.py` is unit-tested with the Ollama client mocked out
  (`tests/test_server.py`); skipped automatically when the `web` extra is absent.
- `document_query.py` is fully unit-tested (`tests/test_document_query.py`) by
  pointing `TINYDB_PATH` at a temp file or unsetting it for in-memory mode;
  always call `document_query.reset_db()` when switching between the two.
- JSON fixtures live in `tests/fixtures/`; do not commit generated output.

## Commit & Pull Request Guidelines

- Commit messages follow Conventional Commits: `type(scope): subject`.
- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.
- PRs should reference an issue (`#123`) and include a clear description.
- Ensure `uv run pytest` passes and `uv run black .` is clean before merging.

## Configuration

Runtime config comes from `.env` (never commit it; see `.env.example`):
`OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_EMBED_MODEL`, `CHROMA_DB_PATH`,
`TINYDB_PATH` (optional; unset = in-memory company store),
`DOCUMENTS_DIR` (optional; default PDF output directory, legacy `REPORTS_DIR` still honored).
