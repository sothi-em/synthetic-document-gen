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
  grounded in a company's profile, and renders them into official-looking PDFs
  or single-page PNG images (with an optional "distress" pass that makes them
  look like scanned, aged documents).
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
5. **Other file formats** — support additional output formats beyond PDF and
   PNG.

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
4. **PNG image document generation** — same pipeline shape as the PDF
   pipeline, but rendered to a single PNG page (A4 portrait or content-sized),
   optionally post-processed with OpenCV to look like a scanned, aged
   document (see [PNG image document generation](#png-image-document-generation)).

## Setup

Requires Python >= 3.13 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --group dev          # install deps + dev tools (pytest, black)
uv sync --group dev --extra embed   # also install chromadb for label embedding
```

Core dependencies include `weasyprint` (PDF/PNG rendering),
`opencv-python-headless`, and `augraphy` (the optional "distress"
post-processing pass for PNG image documents).

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

### PNG image document generation

Renders one of a company's document types into a **single-page PNG image**.
The pipeline mirrors the PDF pipeline (plan → markdown → figures →
HTML+CSS), but the HTML is constrained to one page and rendered to PNG with
WeasyPrint. The page is A4 portrait by default; unchecking the A4 aspect
ratio lets the page size itself to the content.

Optionally, the PNG is post-processed (**distress**) to look like a scanned,
aged document. The default **augraphy** backend builds an
[Augraphy](https://github.com/anchal-agrawel/Augraphy) pipeline from the
`DistressOptions` effects: the classic effects (paper aging, vignette,
stains, noise, ink fade) map to native augmentations, and ~30 further
effects are available per phase (ink: bleed, bleed-through, letterpress,
mottling, dithering, dot matrix, low-ink lines, …; paper: watermark,
noise/brightness texturize, tessellations, paper factory; post: bad photo
copy, faxify, dirty drum/rollers/screen, shadow cast, moire, JPEG
artifacts, folding, bindings, markup, scribbles, … — the full list with
defaults and ranges is in `document_gen/models/distress.py`). Each effect
without a dedicated numeric parameter carries a 0-1 intensity that scales
its augmentation parameters (0 = off). A subtle warp
and a focus-loss blur have no augraphy equivalent and remain custom OpenCV
tail stages. The seed drives the whole augraphy pipeline (explicit `--seed`
or the company seed), so stain positions are reproducible per seed.

A `--distress-preset` bundles a curated set of effects on top of the
classic defaults (explicit `--distress*` flags win over preset values):

- `scanned` — dirty screen, moire, JPEG artifacts, color shift
- `office` — paper aging, stains, folding, bindings, markup, scribbles, shadow cast
- `fax` — faxify, dithering, low-ink random lines, noise, brightness
- `archival` — paper aging, vignette, stains, ink bleed, letterpress, ink mottling, bleed-through, watermark

The pre-augraphy hand-rolled stage sequence is preserved verbatim as the
**legacy** backend (`--distress-backend legacy`): it reproduces old renders
exactly (stain positions random every run; augraphy-only effects are
no-ops there). The augraphy backend is **native-only** — augmentations
are used as-is, so `paper_aging` (mottled tint) and `vignette`
(light-strip gradient) look different from the legacy stages, and
`ink_fade` currently has no visible effect on augraphy 8.2.6; use the
legacy backend for the old look.
Augraphy also writes a small LRU cache to `augraphy_cache/` in the working
directory at runtime (gitignored).

```powershell
# Generate a PNG for a stored company's document type (A4, clean render)
uv run document-gen image --company-id 1 --document "Onboarding Guide"

# Content-sized page, distressed to look like a scanned document
uv run document-gen image --company-id 1 --document "Operations Report" \
    --no-a4 --distress --distress-preset scanned --seed 42

# Reproduce the pre-augraphy (legacy) distressed look
uv run document-gen image --company-id 1 --document "Operations Report" \
    --no-a4 --distress --distress-backend legacy --stain-count 6 --seed 42
```

The same output-directory resolution rules as the PDF command apply.

In the web UI, expand a company's document-type row and click **Generate
Image** — the dialog mirrors the PDF dialog (no Quick Doc), plus an **A4
aspect ratio** checkbox (default on) and a **Distress document** checkbox
that reveals per-effect sliders. Generated PNGs preview
inline in the document view dialog.

**Live distress editing.** When a PNG is generated with tracing on, the
untouched render is preserved as `<stem>_original.png` next to the document
(referenced from the trace at
`gen_tracing.stages.distress.original_path`) — even when distress was
disabled at generation time. The document view dialog then
shows a distress toolbar — a slider per effect (grouped into Ink / Paper /
Post sections; 0 = off, and for JPEG quality 95 = off) that always renders
with the augraphy backend — that re-renders the stored original
server-side on every (debounced) change, so the preview is exactly what
gets persisted. **Save** writes the current render over the document
file; the original stays untouched, so the image remains re-editable
(sliding all effects to 0 and saving restores the clean render). When no original exists (generated without tracing, or before
this feature), the toolbar renders fully disabled with a hint explaining
why.

### Web UI

The frontend lives in `web/` (Vite + React + TypeScript, Tailwind CSS v4,
shadcn/ui, managed with pnpm) and is served as static files by the FastAPI
app. Requires a one-time frontend build:

```powershell
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
- **Document types** tab — browse a company's document types and generate PDFs and PNG images.
- **Documents** tab — browse every generated file.
- **Labels** tab — placeholder for the ChromaDB label tools.

Key API endpoints: `GET /api/health`, `GET /api/models`, `GET /api/industries`,
`POST /api/companies/generate`, `GET /api/companies/jobs/{id}` (+ `/events` SSE stream),
`GET /api/companies`, `GET /api/companies/{id}`, `GET /api/storage`,
`GET|PUT|DELETE /api/companies/{id}/document-types`, `POST /api/companies/{id}/document-types` (append),
`POST /api/companies/{id}/document-types/generate`, `GET|PUT|DELETE /api/settings`,
`POST /api/settings/test`, `GET|PUT|DELETE /api/settings/documents`,
`GET /api/fs/browse`, `POST /api/companies/{id}/pdf`,
`GET /api/companies/{id}/pdf/{filename}`, `POST /api/companies/{id}/image`,
`GET /api/companies/{id}/image/{filename}`. Interactive docs at `/docs`.

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
│   ├── server.py         # FastAPI web UI + JSON API
│   ├── pipeline.py       # Company generation pipeline (threaded)
│   ├── document_query.py # TinyDB store (companies, document_types, documents, user_settings)
│   ├── llm.py            # LLM backends (Ollama / OpenAI-compatible) + settings
│   ├── prompts.py        # LLM prompt templates
│   ├── document_pdf.py   # PDF document pipeline (markdown -> HTML -> WeasyPrint PDF)
│   ├── document_png.py   # PNG image pipeline (single page + optional distress pass)
│   ├── labels.py         # Data-label generation + ChromaDB embedding
│   ├── generators/       # File-format renderers (pdf_gen.html_to_pdf, ...)
│   └── models/
│       ├── company.py    # SyntheticCompany, CompanyProfile, DocumentType, ...
│       ├── document.py   # DocumentPlan (per-document layout/design plan)
│       ├── figures.py    # Figure spec models (kinds, data, styling)
│       ├── excel.py      # ExcelDoc, Sheet, Table, Column, Cell
│       ├── distress.py   # DistressOptions (scanned/aged look for PNG documents)
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
