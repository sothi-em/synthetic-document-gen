# Plan: Live Distress Editor in Image Preview — Sequential Task Breakdown

## Overview

When a PNG image document is generated with **trace active** (`gen_tracing=True`) and distress enabled, the pipeline currently overwrites the clean render in-place. This feature:

1. **Preserves the original**: before the distress pass, the untouched PNG is saved alongside the document and its path recorded in the trace (`gen_tracing.stages.distress.original_path`).
2. **Live editor in the preview**: `DocumentViewDialog` (the image preview) gains a distress **toolbar** — a switch per effect plus a slider per strength (`vignette_strength`, `stain_count`, `noise_strength`, `warp_strength`). Changing any control re-renders the image **live** through a new server-side preview endpoint (server-side so the browser preview is *exactly* what `distress_image` will persist — no JS re-implementation of the cv2 pipeline).
3. **Save button**: persists the current settings by re-running `distress_image` from the stored original onto the document's PNG file.
4. **Disabled state**: when the document has no stored trace (or the trace has no `original_path` — e.g. generated without tracing, or distress was off at generation time), the toolbar renders with all switches/sliders and the save button **disabled**, plus a hint explaining why.

Design decisions (fixed by this plan):

- **Server-side live preview**, not client-side canvas: one code path for preview and save; WYSIWYG guaranteed. Cost: one debounced HTTP round-trip per slider change (~0.3 s debounce).
- **Stains become seedable**: today stain positions use `SystemRandom` (deliberately unreproducible). For the editor, preview and save must match, so `distress_image` gains an optional `stain_seed`; the editor derives it deterministically from the document id (e.g. `doc_id * 1000003`) so preview and save agree, while the generation pipeline keeps the unseeded behavior.
- The original image file is named `<stem>_original.png` next to the document PNG and is **not** registered as a document in TinyDB (it is trace support data, referenced only via `gen_tracing`).

Execute tasks in order. Every task is independently checkable; do not start a task until the previous one's "done when" check passes. Tasks in the same phase build on each other; phases are ordered so the system stays consistent at every boundary.

---

## Phase 1 — Distress core: seedable stains + byte-level API

**Goal:** `document_gen/generators/png_gen.py` can distress PNG bytes in memory and place stains deterministically, with the existing in-place API unchanged.

### Task 1.1 — Extract `distress_array` — [x]

**Files:** `document_gen/generators/png_gen.py`

- Move the body of `distress_image` (after decode, before `cv2.imwrite`) into a new function:
  ```python
  def distress_array(
      clean: np.ndarray,
      options: DistressOptions,
      seed: int,
      stain_seed: int | None = None,
  ) -> np.ndarray
  ```
- The 7 stages (paper tint → vignette → stains → noise → ink re-stamp → warp → blur) move over **verbatim**; the only change is the stain RNG:
  - `stain_seed=None` → `random.SystemRandom()` (today's behavior).
  - `stain_seed=<int>` → `random.Random(stain_seed)`.
- cv2/numpy remain lazy imports inside the function.
- `distress_image(path, options, seed)` becomes a thin wrapper: decode (existing RGBA-over-white handling) → `distress_array(..., stain_seed=None)` → `cv2.imwrite`. Public signature and behavior unchanged.

**Done when:** `uv run python -c "from document_gen.generators.png_gen import distress_array, distress_image"` succeeds; existing `tests/test_png_gen.py` still passes (run it now even though new tests come in 1.3).

### Task 1.2 — Add `distress_image_to_bytes` — [x]

**Files:** `document_gen/generators/png_gen.py`

- New function:
  ```python
  def distress_image_to_bytes(
      data: bytes,
      options: DistressOptions,
      seed: int,
      stain_seed: int | None = None,
  ) -> bytes
  ```
- Decode with `cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)`; same gray→BGR / RGBA-over-white normalization as `distress_image` (factor the normalization into a small private `_normalize_bgr(arr)` helper shared by both entry points).
- Run `distress_array`, encode with `cv2.imencode(".png", out)[1].tobytes()`.
- Raises: `ValueError` on undecodable input; skips entirely (returns `data` unchanged) when `options.enabled` is `False`, mirroring `distress_image`.

**Done when:** import succeeds; manual smoke test: distress a synthetic 400×300 PNG bytes with default options and get back decodable PNG bytes of the same size.

### Task 1.3 — Tests for Phase 1 — [x]

**Files:** `tests/test_png_gen.py`

- `stain_seed` determinism: same `(image, options, seed, stain_seed)` → byte-identical output; different `stain_seed` → different output (use `stains=True`, all other effects off for a clean signal).
- `stain_seed=None` still varies between two calls (SystemRandom path preserved).
- `distress_image_to_bytes(...)` output equals `distress_image` in-place output for the same inputs (`stain_seed` set).
- `options.enabled=False` → input bytes returned unchanged.
- Undecodable input → `ValueError`.

**Done when:** `uv run pytest tests/test_png_gen.py` passes.

---

## Phase 2 — Preserve the original PNG at generation time

**Goal:** traced + distressed image documents keep an untouched copy on disk, referenced from the trace.

### Task 2.1 — `save_original_png` helper — [x]

**Files:** `document_gen/document_png.py`, `tests/test_document_png.py` (new file)

- New helper:
  ```python
  def save_original_png(path: Path) -> Path
  ```
  copies *path* to `<stem>_original.png` in the same directory, using `_unique_path` for collision safety (so `foo.png` → `foo_original.png`, `foo_original.png` → `foo_original_2.png`), and returns the copy's path.
- Tests (no LLM needed): copy is byte-identical; naming; numeric suffix on collision.

**Done when:** `uv run pytest tests/test_document_png.py` passes.

### Task 2.2 — Hook into the pipeline — [x]

**Files:** `document_gen/document_png.py`

- In `generate_document_image`, stage 5 (distress pass): when `gen_tracing=True` **and** `distress_options.enabled`:
  ```python
  original = save_original_png(path)
  trace["stages"]["distress"]["original_path"] = str(original)
  ```
  before calling `distress_image(path, ...)`.
- When tracing is off or distress is disabled: nothing saved, field absent (this is what disables the toolbar later).
- Docstrings: module docstring stage 6 and the `gen_tracing` arg of `generate_document_image` mention the preserved original.

**Done when:** `uv run pytest` passes; code review confirms the field is only written in the traced+distressed branch.

---

## Phase 3 — Server endpoints: preview and save

**Goal:** HTTP surface for live preview and persistence; document record size stays accurate.

### Task 3.1 — `document_query.update_document_size` — [x]

**Files:** `document_gen/document_query.py`, `tests/test_document_query.py`

- New function `update_document_size(doc_id: int) -> dict[str, Any] | None`:
  - Looks up the document; recomputes `size_kb` from the on-disk file via the existing `_document_fields` logic; updates the TinyDB record; returns the updated record dict or `None` if missing.
  - Raises `FileNotFoundError` if the file is gone (caller maps to 404/409).
- Tests: size updates after rewriting the file; `None` for unknown id.

**Done when:** `uv run pytest tests/test_document_query.py` passes.

### Task 3.2 — Request model + original-path helper — [x]

**Files:** `document_gen/server.py`

- New request model:
  ```python
  class DistressEditRequest(BaseModel):
      distress: DistressOptions
      seed: int          # noise/warp seed (from the trace)
      stain_seed: int    # editor-derived stain seed
  ```
- Private helper:
  ```python
  def _original_image_path(record: dict) -> Path | None
  ```
  reads `record["gen_tracing"]["stages"]["distress"]["original_path"]`; returns `None` when the trace is missing, distress was disabled at generation, the field is absent, or the file no longer exists on disk.

**Done when:** `uv run pytest` passes (no behavior change yet).

### Task 3.3 — Preview endpoint — [x]

**Files:** `document_gen/server.py`

- `POST /api/documents/{doc_id}/image/distress-preview` → `Response(content=png_bytes, media_type="image/png")` with `Content-Disposition: inline`:
  - 404 unknown document; 400 `filetype != "png"`; 409 when `_original_image_path` is `None` (detail: "No stored original image for this document").
  - Reads original bytes → `distress_image_to_bytes(data, req.distress, req.seed, req.stain_seed)` → return.
- Plain `def` (sync) so FastAPI runs it in the thread pool (cv2 work must not block the event loop).

**Done when:** import check + `uv run pytest` passes.

### Task 3.4 — Save endpoint — [x]

**Files:** `document_gen/server.py`

- `POST /api/documents/{doc_id}/image/distress-save` → updated document record dict:
  - Same 404/400/409 checks as preview.
  - Writes the distressed bytes **over `record["filepath"]`**; the original file is left untouched (document stays re-editable).
  - Calls `document_query.update_document_size(doc_id)` and returns the refreshed record; maps `FileNotFoundError` to 404.

**Done when:** `uv run pytest` passes.

### Task 3.5 — Server tests — [x]

**Files:** `tests/test_server.py`

- Fixture setup (Ollama mocked as today): temp output dir, synthetic PNG written as both the document file and `<stem>_original.png`, document record saved with a `gen_tracing` payload containing `stages.distress.original_path`.
- Preview: 200 + valid PNG bytes for a valid body; 409 no trace; 409 trace present but original file deleted; 400 non-PNG filetype; 404 unknown id.
- Save: document file overwritten (bytes match a manual `distress_image_to_bytes` call), original file byte-identical before/after, returned record's `size_kb` reflects the new file size; same 409/400/404 matrix.

**Done when:** `uv run pytest tests/test_server.py` passes.

---

## Phase 4 — Frontend API client

**Goal:** TypeScript surface for the new endpoints + trace helpers.

### Task 4.1 — Types and helpers — [x]

**Files:** `web/src/lib/api.ts`

- `originalImagePath(doc: DocumentRecord): string | null` — defensive navigation of `doc.gen_tracing?.stages?.distress?.original_path` (it is `Record<string, unknown>`); returns the string only if it is a non-empty string.
- `stainSeedFor(docId: number): number` — pure, deterministic (e.g. `docId * 1000003`); exported so preview and save always agree.
- `DistressEditBody` type: `{ distress: DistressOptions; seed: number; stain_seed: number }`.

**Done when:** `cd web && pnpm build` passes.

### Task 4.2 — Endpoint methods — [x]

**Files:** `web/src/lib/api.ts`

- `distressPreview: (docId: number, body: DistressEditBody) => Promise<Blob>` — POST JSON; throws `Error(status: text)` on `!ok`; returns `response.blob()`.
- `distressSave: (docId: number, body: DistressEditBody) => Promise<DocumentRecord>` — POST JSON via the existing `request` helper.

**Done when:** `cd web && pnpm build && pnpm lint` pass.

---

## Phase 5 — UI primitives

**Goal:** shadcn `Slider` and `Switch` available in `web/src/components/ui/`.

### Task 5.1 — Install + add components — [ ]

**Files:** `web/package.json`, `web/src/components/ui/slider.tsx` (new), `web/src/components/ui/switch.tsx` (new)

- `cd web && pnpm add @radix-ui/react-slider @radix-ui/react-switch`.
- Add the standard shadcn/ui `Slider` and `Switch` components, styled consistently with the existing `ui/` components (same CVA/`cn` patterns, same token usage).

**Done when:** `cd web && pnpm build && pnpm lint` pass.

---

## Phase 6 — Distress toolbar in the image preview

**Goal:** the live editor UX, including the disabled state.

### Task 6.1 — `DistressToolbar` component (controls + state) — [ ]

**Files:** `web/src/components/distress-toolbar.tsx` (new)

- Props:
  ```ts
  interface DistressToolbarProps {
    doc: DocumentRecord
    /** Called after a successful save with the refreshed record. */
    onSaved?: (doc: DocumentRecord) => void
  }
  ```
- Local state: `options: DistressOptions`, `busy: boolean`, `error: string | null`, `justSaved: boolean`.
- Initial values: `doc.gen_tracing.stages.distress.options` when present (cast/validate defensively against the `DistressOptions` shape), else `DistressOptions` defaults. `seed` from `stages.distress.seed ?? 0`; `stain_seed` from `stainSeedFor(doc.id)`.
- `editable = originalImagePath(doc) !== null`.
- Controls:
  - Switches: `paper_aging`, `vignette`, `stains`, `noise`, `ink_fade`, `blur`, `warp`.
  - Sliders: `vignette_strength` (0–1, step 0.05), `stain_count` (0–20, step 1), `noise_strength` (0–50, step 1), `warp_strength` (0–1, step 0.05); each slider `disabled` when its parent switch is off.
  - All controls `disabled={!editable}`.
  - When `!editable`: muted hint line "No generation trace stored for this image — distress editing is unavailable."
- Label each control with its effect name and current value.

**Done when:** `pnpm build && pnpm lint` pass; component renders (not yet wired to the image).

### Task 6.2 — Live preview loop — [ ]

**Files:** `web/src/components/distress-toolbar.tsx`, `web/src/components/document-view-dialog.tsx`

- Lift the preview image source into shared state: `DocumentViewDialog` owns `previewSrc: string` (initially `api.documentPreviewUrl(doc.id)`) and passes it + a setter (or a `DistressToolbar`-managed blob) to the toolbar; simplest shape: the toolbar receives `onPreview(blob: Blob) => void` and the dialog converts to a blob URL.
- Toolbar live-edit loop:
  - On any control change (and only when `editable`): debounce ~300 ms → `api.distressPreview(doc.id, body)` → `onPreview(blob)`.
  - Keep a ref to the current blob URL; revoke it when replaced and on unmount (`useEffect` cleanup).
  - `busy` while a request is in flight: show a small non-blocking "Rendering…" spinner badge (dialog overlays it on the image); keep showing the previous image (no flicker).
  - On request failure: set `error`, keep the last good image; the error note is dismissible.
- Dialog wiring: in the `kind === "image"` branch, render `<DistressToolbar>` above the image area and use `previewSrc` as the `<img src>`.

**Done when:** `pnpm build && pnpm lint` pass; manual: traced+distressed image → sliders/toggles re-render the image live with a rendering indicator; blob URLs are revoked (check DevTools for no unbounded object-URL growth).

### Task 6.3 — Save button + list refresh — [ ]

**Files:** `web/src/components/distress-toolbar.tsx`, `web/src/components/document-view-dialog.tsx`, `web/src/components/documents-panel.tsx`

- Save button (disabled when `!editable` or `busy`): `api.distressSave(doc.id, body)` → on success:
  - Keep the last preview blob as the displayed image (byte-identical to what was saved — no refetch).
  - Flash a brief "Saved" state on the button.
  - Call `onSaved(updatedRecord)`.
- `DocumentViewDialog` gains optional `onDocumentSaved?: (doc: DocumentRecord) => void` and forwards it; `documents-panel.tsx` passes a callback that reuses the existing document-list refresh path (the one used after generation).

**Done when:** `pnpm build && pnpm lint` pass; manual: Save persists — re-download shows the new image; document list size updates; the image is still editable afterwards (original untouched); non-traced image shows the fully disabled toolbar + hint.

---

## Phase 7 — Docs and final verification

### Task 7.1 — README — [ ]

**Files:** `README.md`

- In the image-document section: tracing preserves `<stem>_original.png` next to the document; the preview dialog offers live distress editing with a save button; editing is unavailable (toolbar disabled) when no trace/original exists.

**Done when:** README reflects the feature.

### Task 7.2 — Full gate — [ ]

- `uv run pytest` — all green.
- `uv run black .` — no changes.
- `cd web && pnpm lint && pnpm build` — clean.
- Manual pass over the Task 6.2/6.3 checks end to end.

**Done when:** all gates pass.

---

## Out of scope

- Client-side (canvas) distress rendering — deliberately avoided to keep one source of truth.
- Editing non-distress aspects of the image (crop, rotate, brightness) — the toolbar covers the `DistressOptions` surface only.
- Restoring the original from the UI (the save flow always re-derives from the original, so "restore" is just toggling all effects off and saving — no extra endpoint needed).
- Registering `<stem>_original.png` files as documents in TinyDB or cleaning them up on document delete (existing `delete_document` behavior for the main file is unchanged; originals are orphaned the same way any trace support data would be).
