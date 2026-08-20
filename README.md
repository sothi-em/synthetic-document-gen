# document-gen

Generator for synthetic company profiles and data-driven documents (guides,
reports, analyses, flyers, ...), powered by LLM pipelines (Ollama) and
validated with Pydantic. The focus is on generating numbers, data tables, and
figures — grounded in a fictional company's profile.

<video src="resources/ui_navigation_edit.mp4" controls autoplay loop muted style="max-width: 100%;"></video>

## Capabilities & purposes

- **Synthetic company profiles** — invents fictional companies (name, industry,
  description, HQ, size) and the document types each would produce.
- **Data-driven documents** — generates numbers, data tables, and figures
  grounded in a company's profile, and renders them into official-looking PDFs.
- **Data labels** — produces per-industry data-reporting labels and embeds them
  into a local ChromaDB collection for semantic search.
- **Web UI + JSON API** — a browser-based front end for generating, browsing,
  and downloading companies and documents, backed by a FastAPI service.
- **Pluggable LLM backends** — any Ollama or OpenAI-compatible endpoint can
  drive both the chat and embedding models.

The primary purpose is to produce **synthetic data for testing downstream
projects**: realistic company records, document types, and data-heavy documents
that can be fed into other tools (parsers, extractors, RAG pipelines, dashboards)
without touching real, sensitive, or proprietary data.

## Disclaimer

- This project was **developed and tested with local agent models** (e.g.
  Ollama-hosted models). Results, quality, and behavior may vary with other
  models.
- A core goal is to **localize all data generation** — every piece of data is
  generated on your own infrastructure. That said, the tool **can use remote
  models** through any endpoint that speaks the **OpenAI API**.
- **All generated data is synthetic.** Nothing is scraped from, or derived
  from, real companies or real datasets — the content is produced only from
  whatever the underlying model was trained on. Treat output as fictional.

## Future plans

The following are planned but not yet implemented:

1. **Batch generation** — generate many companies/documents in a single run.
2. **Performance & efficient usage with smaller models** — optimize prompts and
   pipelines so smaller local models produce usable output at lower cost.
3. **Image generation** — generate logos and other document imagery.
4. **LLM-assisted document tweak/regenerate** — iteratively refine or
   regenerate individual documents with model assistance.
5. **Other file formats** — support additional output formats beyond PDF.

## Workflow

1. **Company generation** — queries an Ollama-hosted model to create fictional
   company profiles (name, industry, description, HQ, size) from an industry
   seed, then generates the list of document types the company would produce.
2. **Data labels** — generates per-industry data-reporting labels and embeds
   them into a local ChromaDB collection for semantic search.
3. **PDF document generation** — drafts one of a company's document types as
   markdown, converts it to a mock official HTML document, and renders it to
   an A4 portrait PDF with WeasyPrint (see
   [PDF document generation](#pdf-document-generation)).

## Setup

Requires Python >= 3.13 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --group dev          # install deps + dev tools (pytest, black)
uv sync --group dev --extra embed   # also install chromadb for label embedding
uv sync --group dev --extra web     # also install the web UI (fastapi, uvicorn)
```

Configure the LLM backends via `.env` (copy from `.env.example`). The chat
(LLM) and embedding endpoints are independent — each can be an Ollama server
or any OpenAI-compatible endpoint (llama.cpp, LM Studio, vLLM, OpenAI, ...):

```ini
# Chat (LLM) endpoint
LLM_BACKEND=ollama                      # or: openai
LLM_HOST=http://localhost:11434         # ollama host, or base URL for openai
LLM_MODEL=llama3.2-128k:latest
# LLM_OPENAI_BASE_URL=http://localhost:8080/v1   # openai backends
# LLM_API_KEY=

# Embedding endpoint
EMBED_BACKEND=ollama
EMBED_HOST=http://localhost:11434
EMBED_MODEL=nomic-embed-text:latest

# Legacy Ollama variables still work as fallbacks
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2-128k:latest
OLLAMA_EMBED_MODEL=nomic-embed-text:latest
CHROMA_DB_PATH=./data/chromadb

# Optional: persist company records to a TinyDB file (in-memory by default)
# TINYDB_PATH=./data/companies.db
```

The web UI's **Settings** tab can override these at runtime; saved settings
persist in the TinyDB `user_settings` collection under the `"llm"` key
(see [Company storage](#company-storage-tinydb)). A pre-existing legacy
settings file (`data/llm-settings.json`, override with `LLM_SETTINGS_PATH`)
is imported into the collection automatically on first load. Note that
settings are stored in plaintext (including any API keys), so treat the
`TINYDB_PATH` file like a secrets file. The web API always returns keys
masked (`****`).

### Company storage (TinyDB)

Generated company profiles are stored through the
`document_gen/document_query.py` helper in a [TinyDB](https://tinydb.readthedocs.io/)
database. By default the store is **in-memory** — records are lost when the
process exits. Set `TINYDB_PATH` in `.env` to a file path to persist records
across restarts (the file is plain JSON, so it is easy to inspect and back up).

The database holds four collections:

- **companies** (default collection) — one document per company: profile,
  seed, timestamps. Document types are *not* embedded here.
- **`document_types`** — one document per document type, linked back to its
  company via a `company_id` foreign key.
- **`documents`** — one document per generated file (PDF, ...), linked back
  to its document type via a `document_type_id` foreign key.
- **`user_settings`** — one document per named settings group (the LLM
  chat/embedding configuration under `"llm"`, the document output directory
  under `"documents"`, future dashboard preferences).

Legacy databases are migrated automatically the first time they are opened:
company documents that still embed their document types are moved into
`document_types`, and the old `report_types` / `report_documents`
collections (plus the `"reports"` settings key) are renamed to the current
layout.

To import a legacy `companies.json` flatfile into a persistent store, set
`TINYDB_PATH` first, then run:

```powershell
uv run document-gen migrate --from ./data/companies.json
```

## Usage

The CLI is available as `document-gen` (or `uv run document-gen`):

```powershell
uv run document-gen --help
```

Company profiles are generated from the web UI and stored in the TinyDB
company store (see [Company storage](#company-storage-tinydb)).

### PDF document generation

Renders one of a company's document types into an official-looking PDF. The
pipeline runs in three stages: the chat LLM drafts the document content as
markdown (table of contents, sections, sample data tables), the chat LLM
converts that markdown into a standalone HTML+CSS document styled as a mock
official company document (letterhead, borders, page-number footer), and
WeasyPrint renders the HTML to PDF. The layout rules (A4 portrait via the
CSS `@page` rule, no fixed pixel widths, proportional scaling) are enforced
both in the LLM system prompt and unconditionally on the model output.

```powershell
# Generate a PDF for a stored company's document type
uv run document-gen document --company-id 1 --document "Onboarding Guide"

# With free-text guidance, a model override, and the markdown/HTML kept
uv run document-gen document --company-id 1 --document "Operations Report" \
    --input "Focus on Q3 throughput by region" --model llama3.2:latest \
    --keep-intermediates
```

The output directory is resolved in order: the `--output-dir` flag, the
directory saved from the web UI Settings tab, then the `DOCUMENTS_DIR` env
var (legacy `REPORTS_DIR` is still honored). When none is set, generation is
disabled (frontend), rejected with a 400 (API), and exits with an error
(CLI).

In the web UI, expand a company's document-type row and click **Generate PDF**
(optional guidance + model override, live progress, download link). The
output directory is configured on the **Settings** tab via a text field or
an interactive directory browser that navigates the server's filesystem.

### Web UI

The frontend lives in `web/` (Vite + React + TypeScript, Tailwind CSS v4,
shadcn/ui, managed with pnpm) and is served as static files by the FastAPI
app. Requires the `web` extra plus a one-time frontend build:

```powershell
uv sync --group dev --extra web

cd web
pnpm install
pnpm build           # outputs web/dist, served by the API
```

Run the server (serves the UI at the site root plus a JSON API under `/api`):

```powershell
uv run document-gen serve                 # http://127.0.0.1:8000
uv run document-gen serve --port 9000 --host 0.0.0.0
```

For frontend development, run the Vite dev server (proxies `/api` to port 8000):

```powershell
cd web
pnpm dev                     # http://localhost:5173 (uv run document-gen serve in another shell)
```

- **Generate** tab — start a background job (count, optional instructions, industry, model) with live progress; review the generated companies and pick which ones to keep (work is split across a fixed pool of 4 threads).
- **Companies** tab — browse/search the company store with a detail view.
- **Document types** tab — browse a company's document types and generate PDFs.
- **Documents** tab — browse every generated file.
- **Labels** tab — placeholder for the ChromaDB label tools.

Key API endpoints: `GET /api/health`, `GET /api/models`, `GET /api/industries`,
`POST /api/companies/generate`, `GET /api/companies/jobs/{id}` (+ `/events` SSE stream),
`GET /api/companies`, `GET /api/companies/{id}`, `GET /api/storage`,
`GET|PUT|DELETE /api/companies/{id}/document-types`, `POST /api/companies/{id}/document-types` (append),
`POST /api/companies/{id}/document-types/generate`, `GET|PUT|DELETE /api/settings`,
`POST /api/settings/test`, `GET|PUT|DELETE /api/settings/documents`,
`GET /api/fs/browse`, `POST /api/companies/{id}/pdf`,
`GET /api/companies/{id}/pdf/{filename}`. Interactive docs at `/docs`.

### Data labels (ChromaDB)

Requires the `embed` extra (`chromadb`). Run from the repo root:

```powershell
uv run python -m document_gen.labels generate   # write labels.json
uv run python -m document_gen.labels embed      # embed into ChromaDB
uv run python -m document_gen.labels query "units sold per region"
uv run python -m document_gen.labels tags       # print unique tags
```

## Project layout

```
document-gen/
├── document_gen/
│   ├── cli.py            # CLI entry point (document-gen command)
│   ├── server.py         # FastAPI web UI + JSON API (requires 'web' extra)
│   ├── pipeline.py       # Company generation pipeline (threaded)
│   ├── document_query.py # TinyDB store (companies, document_types, documents, user_settings)
│   ├── llm.py            # LLM backends (Ollama / OpenAI-compatible) + settings
│   ├── prompts.py        # LLM prompt templates
│   ├── document_pdf.py   # PDF document pipeline (markdown -> HTML -> WeasyPrint PDF)
│   ├── labels.py         # Data-label generation + ChromaDB embedding
│   ├── generators/       # File-format renderers (pdf_gen.html_to_pdf, ...)
│   └── models/
│       ├── company.py    # SyntheticCompany, CompanyProfile, DocumentType, ...
│       ├── document.py   # DocumentPlan (per-document layout/design plan)
│       ├── figures.py    # Figure spec models (kinds, data, styling)
│       ├── excel.py      # ExcelDoc, Sheet, Table, Column, Cell
│       └── llm.py        # LLM settings models (chat/embedding backends)
├── web/                  # Frontend: Vite + React + TS, Tailwind v4, shadcn/ui (builds to web/dist)
├── tests/                # pytest tests + JSON fixtures in tests/fixtures/
├── code_plans/           # Planning documents
├── data/                 # Generated output (gitignored)
└── pyproject.toml        # Project metadata + dependencies
```

## Development

```powershell
uv run pytest                          # run tests
uv run pytest --cov                    # run tests with coverage (models pkg)
uv run black .                         # format
```

Commit messages follow Conventional Commits (`feat`, `fix`, `docs`, ...).
See `AGENTS.md` for agent-specific contribution guidelines.
