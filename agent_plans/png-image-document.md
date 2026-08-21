# Plan: PNG Image Document Type

## Overview
A new document type **PNG image**: single-page document, same LLM pipeline shape as PDF (plan → markdown → figures → HTML+CSS), rendered to a single PNG via WeasyPrint, with an optional **distress** post-processing pass (cv2) that makes it look like a scanned, aged document. The generation dialog mirrors the PDF dialog **minus Quick Doc**, plus an **A4 aspect ratio** checkbox (default on) and the distress controls.

## 1. Dependencies (`pyproject.toml`)
- Add `opencv-python-headless` to core dependencies (numpy already comes via matplotlib).

## 2. Models — `document_gen/models/distress.py`
New Pydantic model `DistressOptions` (exported from `models/__init__.py`), one flag per effect in the reference script (`document_distressor.py`), all defaulting to reasonable values:

| Field | Type | Default | Maps to script |
|---|---|---|---|
| `enabled` | bool | `False` | master switch |
| `paper_aging` | bool | `True` | cream/beige paper tint |
| `vignette` | bool | `True` | uneven lighting / dark edges |
| `vignette_strength` | float 0–1 | `0.3` | `0.3 * (X²+Y²)` factor |
| `stains` | bool | `True` | coffee/dirt blobs |
| `stain_count` | int 0–20 | `4` | number of stain centers |
| `noise` | bool | `True` | scanner grain |
| `noise_strength` | float 0–50 | `12` | Gaussian σ |
| `ink_fade` | bool | `True` | text blended into paper (faded ink) |
| `blur` | bool | `True` | scanner focus-loss blur |
| `warp` | bool | `False` | subtle feed/lens warp (not in the reference script — implemented with a small `cv2.remap` mesh displacement, `warp_strength` float default `0.5`) |
| `seed` | int \| None | `None` | reproducibility (falls back to company seed) |

Field validators clamp ranges. Note: the script assumes grayscale black-on-white; our PNGs are colored HTML renders, so the text/ink mask is derived from **luminance as a soft alpha** (`255 - luminance`) instead of the hard `< 128` mask — same visual result, works on colored content.

## 3. Renderer — `document_gen/generators/png_gen.py`
- `html_to_png(html: str, path: Path, a4: bool) -> Path`
  - `a4=True` → force `@page { size: A4 portrait; margin: 2cm; }` (reuse `document_pdf._fix_page_rules`-style logic via a new `sanitize_image_html(html, a4)` in `document_png.py`).
  - `a4=False` → force `@page { size: auto; margin: 2cm; }` (WeasyPrint supports `size: auto` → page sized to content, so the PNG aspect ratio is content-driven).
  - Render with `weasyprint.HTML(string=html).write_png(...)` into a temp dir, keep **page 1** (single-page contract); log a warning if the render produced >1 page.
- `distress_image(path: Path, options: DistressOptions, seed: int) -> None`
  - Ports `apply_aging_effects` from the reference script into a flag-driven pipeline: paper tint → vignette → stains → noise → ink re-stamp (soft-alpha blend) → warp → blur. In-place (overwrites the PNG). Skipped entirely when `options.enabled` is `False` (perfect image).

## 4. Prompts — `document_gen/prompts.py`
Two new templates (single-page contract, no TOC ever):
- `image_content_prompt` — slots: `<company_profile>`, `<document_type>`, `<user_input>`, `<figures>`. Instructs: concise single-page document (title + 3–5 short sections), at most 1 data table, at most 1 figure (reuses the existing fenced-```chart block format and `_content_figures_instruction`).
- `image_html_prompt` + `image_html_system_prompt` — slots: `<company_profile>`, `<design_brief>`, `<markdown>`, `<figures>`, `<page_size>`. Instructs: everything on **one page**, no page breaks/headers/footers/page numbers, compact type scale; `<page_size>` says "A4 portrait" or "content-sized (auto)".

## 5. Pipeline — `document_gen/document_png.py`
`generate_document_image(company_id, report, user_input, model_name, output_dir, figure_kinds, a4_aspect=True, distress: DistressOptions | None, gen_tracing=False) -> ImageArtifact`

Stages (reusing existing private helpers exactly as `document_excel` does):
1. **plan** — reuse `document_pdf._plan_document` (design brief; TOC decision ignored/forced off).
2. **markdown** — `image_content_prompt`, full token cap (no quick doc), thinking on.
3. **figures** — reuse `document_excel._extract_figure_specs` (heuristic + LLM fallback).
4. **html** — `image_html_prompt` → `sanitize_image_html(raw, a4)` → embed figures.
5. **png** — `html_to_png`.
6. **distress** — `distress_image` when enabled (seed = options.seed or company seed).
7. Record via `document_pdf._record_document`, unique `.png` path from markdown title slug, full per-stage `gen_tracing` (distress stage stores the options + seed so the PNG is reproducible from the trace).

`ImageArtifact` dataclass: `company_id, report_name, markdown, html, png_path, figures, gen_tracing`.

## 6. Server — `document_gen/server.py`
- `DocumentImageRequest`: `report`, `user_input`, `model`, `figure_kinds` (validated as before), `a4_aspect: bool = True`, `distress: DistressOptions = DistressOptions()`, `gen_tracing: bool = False`.
- `POST /api/companies/{id}/image` → 202 background job (same `_Job` pattern; 400 when no output dir), result `{"png": filename, "report": name}`.
- `GET /api/companies/{id}/image/{filename}` → `FileResponse` with `image/png`, same path-traversal guards.

## 7. CLI — `document_gen/cli.py`
New `image` subcommand mirroring `document`: `--company-id`, `--document`, `--input`, `--output-dir`, `--model`, `--figure-kind` (repeatable), `--no-a4` (default A4), `--distress` + `--no-stains/--no-vignette/--no-noise/--no-ink-fade/--no-blur`, `--warp`, `--stain-count`, `--seed`, `--keep-intermediates`.

## 8. Frontend (`web/`)
- **`lib/api.ts`**: `DistressOptions`, `DocumentImageRequest`, `ImageJobResult` types; `startDocumentImage`, `documentImageUrl`.
- **`generate-image-dialog.tsx`** (new, mirrors `generate-pdf-dialog.tsx` minus Quick Doc):
  - Additional instructions, Model, Generate Trace, Figures checkboxes (same 6 kinds).
  - **A4 aspect ratio** checkbox — default **checked** (tooltip: "Lock the page to A4 portrait; unchecked lets the page size itself to the content").
  - **Distress document** checkbox — default off (tooltip: "Make it look like a scanned, aged document: paper tint, stains, noise, warp").
  - When distress is checked, an indented sub-panel with the per-effect checkboxes (paper aging, vignette, stains, noise, ink fade, blur, warp) + small numeric inputs (vignette strength, stain count, noise strength, warp strength).
  - Download link when done.
- **`document-types-panel.tsx`**: `imageTarget` state + **"Generate Image"** button (lucide `Image` icon) next to PDF/Excel, same `pdfDir === null` gating.
- **`documents-panel.tsx`**: add `png` entry to `FILETYPE_STYLES`.
- **`document-view-dialog.tsx`**: new `"image"` preview kind — render the PNG via `<img>` from the file URL (falls back to download).

## 9. Tests
- `tests/test_models.py`: `DistressOptions` defaults + range validation.
- `tests/test_prompts.py`: new templates contain all slots.
- `tests/test_png_gen.py` (no LLM): distress on a synthetic numpy image — each flag toggles the corresponding change (e.g. noise raises pixel variance, stains darken, disabled = byte-identical), warp/blur shapes, seed determinism; `html_to_png` A4 vs auto (WeasyPrint is a hard dep, so renderable).
- `tests/test_document_png.py`: follow the `tests/test_document_excel.py` `FakeBackend` pattern — end-to-end with a temp output dir, trace persistence flag, `a4_aspect` page rule in sanitized HTML, distress stage in trace, error cases, filename fallback/collision.
- `tests/test_server.py`: image endpoints with the pipeline mocked (400 no-output-dir, 202 job, download guards).
- `tests/test_cli.py`: `image` subcommand parsing.

## 10. Docs
- `README.md`: mention the PNG image document type + `opencv-python-headless` dependency.

## Out of scope / notes
- Multi-page PNGs (contract is one page; overflow logs a warning and keeps page 1).
- The distress pass is deterministic per seed but is PNG-only (not part of PDF trace replay).
