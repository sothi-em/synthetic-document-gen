# Plan: PNG Image Document Type — Sequential Steps

## Overview
A new document type **PNG image**: single-page document, same LLM pipeline shape as PDF (plan → markdown → figures → HTML+CSS), rendered to a single PNG via WeasyPrint, with an optional **distress** post-processing pass (cv2) that makes it look like a scanned, aged document. The generation dialog mirrors the PDF dialog **minus Quick Doc**, plus an **A4 aspect ratio** checkbox (default on) and the distress controls.

Execute the steps below in order. Each step is self-contained and independently testable; do not start a step until the previous one's "done when" check passes.

---

## Step 1: Add the OpenCV dependency

**Files:** `pyproject.toml`

- Add `opencv-python-headless` to core dependencies (numpy already comes via matplotlib).

**Done when:** `uv sync` succeeds and `uv run python -c "import cv2"` works.

---

## Step 2: `DistressOptions` model

**Files:** `document_gen/models/distress.py` (new), `document_gen/models/__init__.py`, `tests/test_models.py`

New Pydantic model `DistressOptions` (exported from `models/__init__.py`), one flag per effect in the distress pipeline (Step 3), all defaulting to reasonable values:

| Field | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | `False` | master switch |
| `paper_aging` | bool | `True` | cream/beige paper tint |
| `vignette` | bool | `True` | uneven lighting / dark edges |
| `vignette_strength` | float 0–1 | `0.3` | vignette falloff factor |
| `stains` | bool | `True` | coffee/dirt blobs |
| `stain_count` | int 0–20 | `4` | number of stain centers |
| `noise` | bool | `True` | scanner grain |
| `noise_strength` | float 0–50 | `12` | Gaussian σ |
| `ink_fade` | bool | `True` | text blended into paper (faded ink) |
| `blur` | bool | `True` | scanner focus-loss blur |
| `warp` | bool | `False` | subtle feed/lens warp (implemented with a small `cv2.remap` mesh displacement, `warp_strength` float default `0.5`) |
| `seed` | int \| None | `None` | reproducibility (falls back to company seed) |

Field validators clamp ranges.

**Tests:** `tests/test_models.py` — `DistressOptions` defaults + range validation.

**Done when:** `uv run pytest tests/test_models.py` passes.

---

## Step 3: Distress renderer (`distress_image`)

**Files:** `document_gen/generators/png_gen.py` (new), `tests/test_png_gen.py` (new)

- `distress_image(path: Path, options: DistressOptions, seed: int) -> None`
  - Flag-driven pipeline (each stage gated by its `DistressOptions` flag, in this order): paper tint → vignette → stains → noise → ink re-stamp (soft-alpha blend) → warp → blur. In-place (overwrites the PNG). Skipped entirely when `options.enabled` is `False` (perfect image).
  - Stage algorithms (based on an uncommitted reference script, inlined here so the plan is self-contained):
    1. **paper tint** — build an RGB paper layer filled with soft cream `[245, 235, 215]` (BGR `[215, 235, 245]`).
    2. **vignette** — `X, Y = np.meshgrid(linspace(-1, 1, w), linspace(-1, 1, h))`; multiply paper by `clip(1 - vignette_strength * (X² + Y²), 0, 1)` per channel.
    3. **stains** — draw `stain_count` filled `cv2.circle` blobs (radius 40–120, random centers, seeded RNG) into a uint8 mask; `cv2.GaussianBlur(mask, (151, 151), 0)`; darken paper per channel by factor `1 - 0.35 * mask_norm * f` with per-channel `f` in `[0.75, 0.82, 0.88]` (differential darkening → brown tint).
    4. **noise** — `cv2.randn(noise, 0, noise_strength)` on an int16 buffer; add to paper, clip 0–255.
    5. **ink re-stamp** — derive the text/ink mask from the original clean render as **luminance-based soft alpha** (`alpha = (255 - luminance) / 255`); blend ink back over the dirty paper as `ink_px = 30 * 0.85 + paper * 0.15`, i.e. `out = alpha * ink_px + (1 - alpha) * paper` per channel (faded-ink look; no hard `< 128` threshold, so it works on colored HTML renders, not just grayscale).
    6. **warp** — small `cv2.remap` mesh displacement (random low-frequency offsets scaled by `warp_strength`), seeded.
    7. **blur** — `cv2.GaussianBlur(out, (3, 3), 0)` to mimic scanner focus loss.
  - All randomness (stain centers, noise, warp offsets) driven by `seed` for reproducibility.

**Tests:** `tests/test_png_gen.py` (no LLM) — distress on a synthetic numpy image: each flag toggles the corresponding change (e.g. noise raises pixel variance, stains darken, disabled = byte-identical), warp/blur shapes, seed determinism.

**Done when:** `uv run pytest tests/test_png_gen.py` passes.

---

## Step 4: PNG renderer (`html_to_png` + `sanitize_image_html`)

**Files:** `document_gen/generators/png_gen.py`, `document_gen/document_png.py` (new, module skeleton), `tests/test_png_gen.py`

- `html_to_png(html: str, path: Path, a4: bool) -> Path`
  - `a4=True` → force `@page { size: A4 portrait; margin: 2cm; }` (reuse `document_pdf._fix_page_rules`-style logic via a new `sanitize_image_html(html, a4)` in `document_png.py`).
  - `a4=False` → force `@page { size: auto; margin: 2cm; }` (WeasyPrint supports `size: auto` → page sized to content, so the PNG aspect ratio is content-driven).
  - Render with `weasyprint.HTML(string=html).write_png(...)` into a temp dir, keep **page 1** (single-page contract); log a warning if the render produced >1 page.

**Tests:** extend `tests/test_png_gen.py` — `html_to_png` A4 vs auto (WeasyPrint is a hard dep, so renderable).

**Done when:** `uv run pytest tests/test_png_gen.py` passes.

---

## Step 5: Prompts

**Files:** `document_gen/prompts.py`, `tests/test_prompts.py`

Two new templates (single-page contract, no TOC ever):
- `image_content_prompt` — slots: `<company_profile>`, `<document_type>`, `<user_input>`, `<figures>`. Instructs: concise single-page document (title + 3–5 short sections), at most 1 data table, at most 1 figure (reuses the existing fenced-```chart block format and `_content_figures_instruction`).
- `image_html_prompt` + `image_html_system_prompt` — slots: `<company_profile>`, `<design_brief>`, `<markdown>`, `<figures>`, `<page_size>`. Instructs: everything on **one page**, no page breaks/headers/footers/page numbers, compact type scale; `<page_size>` says "A4 portrait" or "content-sized (auto)".

**Tests:** `tests/test_prompts.py` — new templates contain all slots.

**Done when:** `uv run pytest tests/test_prompts.py` passes.

---

## Step 6: Pipeline (`generate_document_image`)

**Files:** `document_gen/document_png.py`, `tests/test_document_png.py` (new)

`generate_document_image(company_id, report, user_input, model_name, output_dir, figure_kinds, a4_aspect=True, distress: DistressOptions | None, gen_tracing=False) -> ImageArtifact`

Stages (reusing existing private helpers exactly as `document_excel` does):
1. **plan** — reuse `document_pdf._plan_document` (design brief; TOC decision ignored/forced off).
2. **markdown** — `image_content_prompt`, full token cap (no quick doc), thinking on.
3. **figures** — reuse `document_excel._extract_figure_specs` (heuristic + LLM fallback).
4. **html** — `image_html_prompt` → `sanitize_image_html(raw, a4)` → embed figures.
5. **png** — `html_to_png`.
6. **distress** — `distress_image` when enabled (seed = `options.seed` or company seed).
7. Record via `document_pdf._record_document`, unique `.png` path from markdown title slug, full per-stage `gen_tracing` (distress stage stores the options + seed so the PNG is reproducible from the trace).

`ImageArtifact` dataclass: `company_id, report_name, markdown, html, png_path, figures, gen_tracing`.

**Tests:** `tests/test_document_png.py` — follow the `tests/test_document_excel.py` `FakeBackend` pattern: end-to-end with a temp output dir, trace persistence flag, `a4_aspect` page rule in sanitized HTML, distress stage in trace, error cases, filename fallback/collision.

**Done when:** `uv run pytest tests/test_document_png.py` passes.

---

## Step 7: Server endpoints

**Files:** `document_gen/server.py`, `tests/test_server.py`

- `DocumentImageRequest`: `report`, `user_input`, `model`, `figure_kinds` (validated as before), `a4_aspect: bool = True`, `distress: DistressOptions = DistressOptions()`, `gen_tracing: bool = False`.
- `POST /api/companies/{id}/image` → 202 background job (same `_Job` pattern; 400 when no output dir), result `{"png": filename, "report": name}`.
- `GET /api/companies/{id}/image/{filename}` → `FileResponse` with `image/png`, same path-traversal guards.

**Tests:** `tests/test_server.py` — image endpoints with the pipeline mocked (400 no-output-dir, 202 job, download guards).

**Done when:** `uv run pytest tests/test_server.py` passes.

---

## Step 8: CLI subcommand

**Files:** `document_gen/cli.py`, `tests/test_cli.py`

New `image` subcommand mirroring `document`: `--company-id`, `--document`, `--input`, `--output-dir`, `--model`, `--figure-kind` (repeatable), `--no-a4` (default A4), `--distress` + `--no-stains/--no-vignette/--no-noise/--no-ink-fade/--no-blur`, `--warp`, `--stain-count`, `--seed`, `--keep-intermediates`.

**Tests:** `tests/test_cli.py` — `image` subcommand parsing.

**Done when:** `uv run pytest tests/test_cli.py` passes and `uv run document-gen image --help` shows the flags.

---

## Step 9: Frontend

**Files:** `web/src/lib/api.ts`, `web/src/components/generate-image-dialog.tsx` (new), `web/src/components/document-types-panel.tsx`, `web/src/components/documents-panel.tsx`, `web/src/components/document-view-dialog.tsx`

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

**Done when:** `pnpm build` and `pnpm lint` pass, and the flow works end-to-end against a running server (generate → poll job → preview PNG → download).

---

## Step 10: Docs + final gate

**Files:** `README.md`

- Mention the PNG image document type + `opencv-python-headless` dependency.

**Done when:** full `uv run pytest` passes and `uv run black .` is clean.

---

## Out of scope / notes
- Multi-page PNGs (contract is one page; overflow logs a warning and keeps page 1).
- The distress pass is deterministic per seed but is PNG-only (not part of PDF trace replay).
