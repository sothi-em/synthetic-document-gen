# Plan: Migrate the Distress Pass to Augraphy

> **Status (post Phase 1+2):** Phases 1 and 2 are implemented and committed.
> During implementation the augraphy-only principle below (D9) was fixed:
> the augraphy backend uses **native augraphy augmentations only** — no
> legacy stage is re-implemented in cv2 to reproduce the old look. Three
> 8.2.6 behaviors diverged from the original assumptions (see findings 8–10)
> and were accepted as-is rather than papered over with custom code. The
> Phase 3 test expectations for `vignette` and `ink_fade` below are updated
> accordingly.

## Overview

The distress (scanned/aged look) pass for PNG image documents is implemented
hand-rolled in `document_gen/generators/png_gen.py` (`distress_array`): seven
fixed cv2 stages — paper tint → vignette → stains → noise → ink re-stamp →
warp → blur — driven by `DistressOptions` (12 fields) in
`document_gen/models/distress.py`.

This plan rebuilds the pass on **augraphy's `AugraphyPipeline`**
(ink / paper / post phases), replacing the hand-rolled stages with
augraphy augmentations where equivalents exist, keeping the two stages that
have no augraphy equivalent (warp, global blur) as custom cv2 tail stages,
and exposing the rest of augraphy's catalog as new per-effect toggles on
`DistressOptions`, the JSON API, the live distress editor, and the CLI.

The **old hand-rolled pipeline is kept in place as a legacy reference
implementation** (not deleted): it is preserved verbatim as
`distress_array_legacy` and remains selectable via a new
`DistressOptions.backend` field (`"augraphy"` default, `"legacy"` for the
old path), so old renders can be reproduced exactly, the two looks can be
compared side by side, and the legacy code stays available as a fallback
while the augraphy path is matured.

**The augraphy backend is native-only (D9):** it composes augraphy
augmentations and pipeline parameters exclusively. A legacy stage is never
re-implemented in custom cv2 code to make the augraphy output look like the
old render — where an augraphy native behaves differently (vignette shape,
paper tint texture, ink fade), the native behavior is accepted. The only
custom code on the augraphy path is the warp/blur tail (no augraphy
equivalent) and defensive output re-normalization.

Public contracts that must not change:

- `distress_image(path, options, seed)`, `distress_image_to_bytes(data, options, seed, stain_seed)`,
  `distress_array(clean, options, seed, stain_seed)` signatures.
- `DistressOptions.enabled=False` → input returned unchanged.
- Live editor: preview and save must produce identical output for the same
  `(options, seed, stain_seed)` (server-side re-distress of the stored
  original).
- Existing traces in TinyDB (`gen_tracing.stages.distress.options`) must
  still validate — all current `DistressOptions` fields keep their names,
  types, and default values.

---

## Investigation findings (verified against augraphy 8.2.6 in `.venv`)

1. **Version / API mismatch with the reference example.** The latest
   released augraphy on PyPI is **8.2.6** — exactly what
   `pyproject.toml` already pins. The example call sites in the migration
   brief use parameter names from an unreleased 9.x API and would raise
   `TypeError` on 8.2.6. Examples of mismatches:
   `ColorPaper(color_range=)` → actual `hue_range`/`saturation_range`;
   `Jpeg(jpeg_quality_range=)` → `quality_range`; `Faxify(scale_dexterity=)`
   → `scale_range`; `Scribbles(scribbles_count=)` → `scribbles_count_range`;
   `WaterMark` is fine; `Dithering(dither_type=)` → `dither`;
   `InkColorSwap(active_color=)` → `ink_swap_color`;
   `LowInkPeriodicLines(periodicity_range=)` → `period_range`;
   `BindingsAndFasteners(fastener_type=)` → `overlay_types`;
   `SubtleNoise(noise_type=)` → `subtle_range`; `Hollow(hole_size_range=)`
   → `hollow_median_kernel_value_range`; `PaperFactory(tile_texture_shape=)`
   → no such param (texture path/generation flags instead).
   **The plan uses the real 8.2.6 signatures** (catalog below).
2. **Determinism.** `AugraphyPipeline(..., random_seed=N)` seeds the global
   `random` module at construction. Verified: two *fresh* pipeline instances
   with the same seed produce byte-identical output; reusing one instance
   across calls does not. ⇒ Build a fresh pipeline per `distress_array`
   call.
3. **Pipeline architecture matches ours.** `augment_single_image` runs:
   ink phase on the input image → paper phase on a **flat white canvas**
   (`paper_color=255` default) → composites ink onto paper via
   `OverlayBuilder(overlay_type='ink_to_paper', alpha=overlay_alpha)` →
   post phase. This replaces our custom "paper base + luminance ink
   re-stamp" stages directly. `augment(img, return_dict=0)` returns a plain
   `np.ndarray`.
4. **Constraints.** Input must be ≥30×30 px and 3-channel uint8 (our
   `_normalize_bgr` already guarantees the channel/dtype part; add a size
   guard). Works with numpy 2.4.6.
5. **Side effect.** Every `augment` call writes a cache PNG into
   `./augraphy_cache/` under the process CWD (LRU of 30 files). Add to
   `.gitignore`.
6. **Thread safety.** `random_seed` seeds the *process-global* `random`
   module; the FastAPI server runs sync endpoints in a threadpool, so
   concurrent distress previews could interleave and break determinism.
   Guard the pipeline build + augment with a module-level
   `threading.Lock` (the current code has the same latent issue via
   `cv2.setRNGSeed`).
7. **Performance.** A small 3-augmentation pipeline on an A4@96dpi image
   runs in ~0.2 s. Heavy augmentations (PaperFactory with texture
   generation, Voronoi/Delaunay tessellation, ShadowCast with large blur
   kernels) may take seconds — see Risks.
8. **`overlay_alpha` is ignored by the `ink_to_paper` blend.** Contrary to
   finding 3's implication, `OverlayBuilder.ink_to_paper_blend` composites
   via `make_white_transparent` (alpha = 255 − luminance of the ink image)
   and never reads `overlay_alpha`. The pipeline-level `overlay_alpha`
   kwarg is still passed (it is the native knob and may matter for other
   overlay types / future versions), but on 8.2.6 `ink_fade` has **no
   visible effect** on the default overlay type. No custom fade stage is
   added (D9).
9. **`LightingGradient` is a light-*strip* band, not a radial vignette.**
   It decays along one axis from a (optionally rotated, random direction
   when `direction=None`) light strip; edges can come out *brighter* than
   the center. It is used natively anyway — `vignette` on the augraphy
   backend means "uneven lighting gradient", not the old radial dark-edge
   look.
10. **`ColorPaper` assigns per-pixel *random* hue/saturation** (OpenCV
    0–180 hue scale) over the paper canvas, preserving value. The result
    is a subtly mottled tint, not a uniform cream base; with the catalog's
    `hue_range=(28, 45)` the tint reads yellow-green, not beige. Accepted
    as the native paper-aging texture (D9) — ranges are not re-tuned to
    chase the old cream look.
11. **Seed overflow.** `zlib.crc32` returns unsigned 32-bit values that
    overflow `cv2.setRNGSeed` (signed C int) inside `AugraphyPipeline.__init__`
    → `ValueError`. The combined seed is masked to signed 32-bit
    (`& 0x7FFFFFFF`) before being passed as `random_seed`.
12. **numba 0.67 parfor compile crash.** In one process, if `SubtleNoise`'s
    prange kernel is JIT-compiled before `BadPhotoCopy`'s worley-noise
    parfor kernel (with `Stains` also present), numba's compiler raises a
    bare `AssertionError`. Deterministic minimal repro:
    `Stains + SubtleNoise + BadPhotoCopy` in that compile order. Workaround:
    append `BadPhotoCopy` **first** in the post phase so its kernel compiles
    first. (`numba_jit=0` is not a viable workaround: augraphy toggles the
    process-global `numba.config.DISABLE_JIT` in `__init__`, which breaks
    other compiled kernels.)
13. **`Faxify` breaks the output contract.** It resamples by its drawn
    scale (no resize back) and returns a **2D grayscale** array. The
    post-augment re-normalization in `distress_array` converts 2D→BGR and
    resizes to the input shape (`INTER_AREA`).

---

## Design decisions (fixed by this plan)

- **D1 — Augraphy default, legacy kept.** The augraphy pipeline replaces
  the paper tint, vignette, stains, noise, and ink re-stamp stages on the
  default path. `warp` (low-frequency remap) and `blur` (global 3×3
  Gaussian) have no augraphy equivalent and remain as custom cv2 tail
  stages, applied after `augment()`, unchanged. The entire old stage
  sequence is preserved **verbatim** as `distress_array_legacy(clean,
  options, seed, stain_seed)` (same body as today's `distress_array`,
  including `_PAPER_BGR` / `_STAIN_FACTORS` and the unseeded-stains
  behavior) and is documented as the legacy reference implementation. It
  is *not* deleted and *not* refactored during this migration.
- **D1b — Backend switch.** New field
  `backend: Literal["augraphy", "legacy"] = "augraphy"` on
  `DistressOptions`. `distress_array` dispatches: `"legacy"` →
  `distress_array_legacy` (old behavior, old seed semantics: stains
  unseeded unless `stain_seed` given); `"augraphy"` → new pipeline. New
  augraphy-only toggles are ignored (no-op) on the legacy backend — the
  legacy path only understands the original 12 fields. Traces written
  before this migration contain no `backend` key and validate to the
  `"augraphy"` default.
- **D2 — Legacy fields keep names and semantics; implementation changes on
  the default backend.**
  `paper_aging`, `vignette`(+`vignette_strength`), `stains`(+`stain_count`),
  `noise`(+`noise_strength`), `ink_fade`, `warp`(+`warp_strength`), `blur`,
  `seed` all keep their current field names, types, ranges, and defaults so
  stored traces and the existing frontend keep validating. On the default
  augraphy backend their *implementation* moves to augraphy (except
  warp/blur) and visual output differs from the old hand-rolled look —
  that is the point of the migration. Selecting `backend="legacy"`
  reproduces the old look exactly (accepted, noted in the PR).
- **D3 — New toggles default to `False`.** Enabling a new augraphy effect
  never changes an existing render's output.
- **D4 — One seed drives everything.** augraphy has no per-augmentation
  seed. `random_seed` = `seed` when `stain_seed is None`, else a stable
  combination `zlib.crc32(f"{seed}:{stain_seed}".encode())`. Consequences:
  stain positions become reproducible under the seed (previously
  intentionally unseeded) — a behavior change, and arguably a fix for the
  live editor. The `stain_seed` parameter stays in the signatures for API
  compatibility.
- **D5 — `p=1.0` on every enabled augmentation.** We gate effects with our
  own booleans; augraphy's per-augmentation probability is always 1.
- **D6 — Conservative parameter ranges.** Where an augmentation takes a
  `(min, max)` range, we pass a fixed, tasteful range (not the library
  default, which is often extreme) so defaults look like "mildly degraded
  office document", not "destroyed document". Ranges are listed in the
  catalog below and are the *only* user-tunable values exposed as sliders
  where a sensible single scalar exists.
- **D7 — Small-image guard.** If the normalized image is <30×30 on either
  side, skip the augraphy pipeline (return the input, or apply only the
  custom warp/blur tail) and log a warning.
- **D8 — CLI stays flag-based, plus presets.** The existing `--distress`
  flags keep working (they set the same `DistressOptions` fields). Add
  `--distress-preset {scanned,office,fax,archival}` that enables a
  curated bundle of the new toggles (see Phase 5). No 40 new CLI flags.
- **D9 — Native augraphy only; never port legacy stages.** The augraphy
  backend composes augraphy augmentations and native pipeline parameters
  exclusively. A legacy hand-rolled stage is **not** re-implemented in
  custom cv2 code on the augraphy path to reproduce the old look (no
  custom vignette, no custom ink-fade blend, no re-tuned paper tint):
  where a native augmentation behaves differently from the legacy stage
  (findings 8–10), the native behavior is the new behavior. Custom code on
  the augraphy path is limited to (a) the warp/blur tail stages (no
  augraphy equivalent) and (b) defensive re-normalization of `augment()`
  output (uint8 / 3-channel / input size, finding 13). The legacy backend
  (D1b) is the only way to get the old look.

---

## Augmentation catalog (real 8.2.6 signatures)

### Legacy → augraphy mapping

| `DistressOptions` field | augraphy implementation |
|---|---|
| `paper_aging` | `ColorPaper(hue_range=(28, 45), saturation_range=(10, 40), p=1.0)` — per-pixel random warm-tint mottle (finding 10), not the old uniform cream base |
| `vignette` / `vignette_strength` | `LightingGradient(light_position=(w//2, h//2), max_brightness=255, min_brightness=round(255 * (1 - vignette_strength)), mode="gaussian", p=1.0)` — native light-strip gradient (finding 9), not the old radial dark-edge vignette |
| `stains` / `stain_count` | `Stains(stains_type="random", stains_blend_method="darken", stains_blend_alpha=min(0.2 + 0.04 * stain_count, 0.9), p=1.0)` — count becomes blend intensity (augraphy 8.2.6 exposes no count); slider label in the UI changes to "Stain intensity" |
| `noise` / `noise_strength` | `SubtleNoise(subtle_range=max(1, int(noise_strength)), p=1.0)` in the post phase |
| `ink_fade` | pipeline-level `overlay_alpha`: `0.85` when `ink_fade` is on, `1.0` when off. **Note (finding 8):** the 8.2.6 `ink_to_paper` blend ignores `overlay_alpha`, so on the default overlay type this toggle currently has no visible effect; it is kept as the native knob, not replaced by a custom fade stage (D9) |
| `warp` / `warp_strength` | **custom cv2 remap tail stage (unchanged)** |
| `blur` | **custom 3×3 GaussianBlur tail stage (unchanged)** |

### New toggles — ink phase

| `DistressOptions` field (default) | augraphy call (p=1.0) |
|---|---|
| `ink_bleed: bool` (False) | `InkBleed(intensity_range=(0.1, 0.4), kernel_size=(5, 5))` |
| `bleed_through: bool` (False) | `BleedThrough(intensity_range=(0.1, 0.4), alpha=0.3)` |
| `letterpress: bool` (False) | `Letterpress(n_samples=(20, 60), n_clusters=(20, 60), std_range=(1500, 5000), value_range=(200, 255))` |
| `ink_mottling: bool` (False) | `InkMottling(ink_mottling_alpha_range=(0.1, 0.3))` |
| `ink_color_swap: bool` (False) | `InkColorSwap(ink_swap_color="random")` |
| `hollow: bool` (False) | `Hollow(hollow_median_kernel_value_range=(71, 101))` |
| `dithering: bool` (False) | `Dithering(dither="floyd-steinberg", order=(2, 4))` |
| `dot_matrix: bool` (False) | `DotMatrix(dot_matrix_shape="random", dot_matrix_dot_width_range=(3, 8), dot_matrix_dot_height_range=(3, 8))` |
| `low_ink_periodic_lines: bool` (False) | `LowInkPeriodicLines(count_range=(2, 5), period_range=(10, 30))` |
| `low_ink_random_lines: bool` (False) | `LowInkRandomLines(count_range=(5, 10))` |
| `lines_degradation: bool` (False) | `LinesDegradation(line_split_probability=(0.2, 0.4))` |

### New toggles — paper phase

| `DistressOptions` field (default) | augraphy call (p=1.0) |
|---|---|
| `noise_texturize: bool` (False) | `NoiseTexturize(sigma_range=(3, 10), turbulence_range=(2, 5))` |
| `brightness_texturize: bool` (False) | `BrightnessTexturize(texturize_range=(0.85, 0.99), deviation=0.08)` |
| `watermark: bool` (False) + `watermark_word: str` ("CONFIDENTIAL") | `WaterMark(watermark_word=watermark_word, watermark_font_size=(10, 15), watermark_rotation=(0, 360), watermark_method="darken")` |
| `pattern_generator: bool` (False) | `PatternGenerator(color="random", alpha_range=(0.25, 0.4))` |
| `voronoi_tessellation: bool` (False) | `VoronoiTessellation(mult_range=(50, 80), num_cells_range=(500, 1000))` |
| `delaunay_tessellation: bool` (False) | `DelaunayTessellation(n_points_range=(500, 800))` |
| `paper_factory: bool` (False) | `PaperFactory(generate_texture=1)` — off by default (texture generation is the slowest augmentation; see Risks) |

### New toggles — post phase

| `DistressOptions` field (default) | augraphy call (p=1.0) |
|---|---|
| `bad_photo_copy: bool` (False) | `BadPhotoCopy(noise_side="random", noise_sparsity=(0.1, 0.4), noise_concentration=(0.1, 0.4))` |
| `faxify: bool` (False) | `Faxify(scale_range=(1.0, 1.25))` |
| `dirty_drum: bool` (False) | `DirtyDrum(line_concentration=0.1, line_width_range=(1, 4))` |
| `dirty_rollers: bool` (False) | `DirtyRollers(line_width_range=(8, 12))` |
| `dirty_screen: bool` (False) | `DirtyScreen(n_clusters=(50, 100), n_samples=(2, 20))` |
| `shadow_cast: bool` (False) | `ShadowCast(shadow_side="random", shadow_opacity_range=(0.2, 0.5), shadow_blur_kernel_range=(101, 201))` |
| `lens_flare: bool` (False) | `LensFlare(lens_flare_location="random", lens_flare_size=(0.5, 3))` |
| `reflected_light: bool` (False) | `ReflectedLight(reflected_light_internal_max_brightness_range=(0.9, 1.0))` |
| `brightness: bool` (False) | `Brightness(brightness_range=(0.9, 1.1))` |
| `gamma: bool` (False) | `Gamma(gamma_range=(0.8, 1.2))` |
| `color_shift: bool` (False) | `ColorShift(color_shift_offset_x_range=(3, 5), color_shift_offset_y_range=(3, 5))` |
| `depth_blur: bool` (False) | `DepthSimulatedBlur(blur_major_axes_length_range=(120, 200), blur_minor_axes_length_range=(120, 200))` |
| `moire: bool` (False) | `Moire(moire_density=(15, 20), moire_blend_alpha=0.1)` |
| `lcd_pattern: bool` (False) | `LCDScreenPattern(pattern_type="random", pattern_overlay_alpha=0.3)` |
| `jpeg_artifacts: bool` (False) + `jpeg_quality: int` (50, clamp 10–95) | `Jpeg(quality_range=(max(10, jpeg_quality - 15), jpeg_quality))` |
| `double_exposure: bool` (False) | `DoubleExposure(gaussian_kernel_range=(9, 12), offset_range=(18, 25))` |
| `folding: bool` (False) + `fold_count: int` (2, clamp 1–6) | `Folding(fold_count=fold_count, fold_angle_range=(0, 0))` |
| `bindings: bool` (False) | `BindingsAndFasteners(overlay_types="random", ntimes=(2, 4), use_figshare_library=0)` — `use_figshare_library=0` always: the library=1 path downloads assets at runtime (no network in the pipeline) |
| `markup: bool` (False) | `Markup(num_lines_range=(2, 5), markup_type="random")` |
| `scribbles: bool` (False) | `Scribbles(scribbles_type="random", scribbles_count_range=(1, 4))` |

Not included: `AugmentationSequence`/`OneOf`/`ComposePipelines` combinators
(we gate with our own booleans), `GlitchEffect`/`NoisyLines`/`PixelBleed`/
`Squish`/`SectionShift`/`BookBinding`/`PageBorder` (poor fit for document
degradation), `InkGenerator`/`NoiseGenerator`/`TextureGenerator`/`PatternMaker`
(asset generators, not image modifiers).

---

## Phase 1 — Core adapter in `png_gen.py`

**Goal:** `distress_array` runs an augraphy pipeline built from
`DistressOptions` + seed, with warp/blur as unchanged tail stages.

### Task 1.1 — `_build_augraphy_pipeline(options, seed, h, w)`

**Files:** `document_gen/generators/png_gen.py`

- New private function returning an `AugraphyPipeline` (lazy `augraphy`
  import inside the function, per repo convention).
- Builds the three phase lists from the catalog above: an augmentation is
  appended iff its boolean flag is on; all get `p=1.0`; ranges fixed per
  D6. **`BadPhotoCopy` is appended before `SubtleNoise`** in the post
  phase (finding 12, numba compile-order crash).
- Pipeline kwargs: `overlay_alpha=0.85 if options.ink_fade else 1.0`,
  `random_seed=zlib.crc32(f"{seed}:{stain_seed}".encode()) if stain_seed is
  not None else seed` (D4), **masked to signed 32-bit** (`& 0x7FFFFFFF`)
  to survive `cv2.setRNGSeed` (finding 11).
- Returns `None` when no augmentation is enabled in any phase (caller
  skips `augment` entirely — keeps the "only warp/blur on" case fast and
  avoids the augraphy_cache write).

**Done when:** `uv run python -c "from document_gen.generators.png_gen import _build_augraphy_pipeline"` succeeds; a manual smoke call with
`paper_aging=True` tints a synthetic white-with-text image.

### Task 1.2 — Preserve the old pipeline as `distress_array_legacy`

**Files:** `document_gen/generators/png_gen.py`

- Rename the current `distress_array` body to `distress_array_legacy(clean,
  options, seed, stain_seed=None)` — **verbatim move, no refactoring**
  (D1). Its docstring gains a note: *legacy reference implementation of
  the pre-augraphy distress pass; kept for exact reproduction of old
  renders and as a fallback; new work goes to the augraphy path*.
- `_PAPER_BGR` / `_STAIN_FACTORS` stay (the legacy path uses them).

**Done when:** `uv run pytest tests/test_png_gen.py` still fully green —
the legacy path is byte-identical to today's behavior (the existing tests
exercise it unchanged via the temporary dispatch in Task 1.3).

### Task 1.3 — New `distress_array` with backend dispatch

**Files:** `document_gen/generators/png_gen.py`

- New `distress_array(clean, options, seed, stain_seed=None)`:
  `enabled` check → dispatch on `options.backend`:
  - `"legacy"` → `return distress_array_legacy(clean, options, seed, stain_seed)`.
  - `"augraphy"` → small-image guard (D7: if `min(h, w) < 30`, log a
    warning and skip augraphy) → `pipeline = _build_augraphy_pipeline(...)`
    → `out = pipeline.augment(clean, return_dict=0) if pipeline else
    clean.copy()` → **defensive re-normalization** (uint8; 2D→BGR and
    4-channel compositing; resize back to the input shape — `Faxify`
    returns grayscale at a resampled size, finding 13) → warp tail (same
    code as today) → blur tail (same code as today) → return.
- Wrap pipeline build + `augment` in a module-level `threading.Lock`
  (finding 6); the lock covers only the augraphy branch.
- `distress_image` / `distress_image_to_bytes` wrappers unchanged (they
  call the dispatching `distress_array`).
- Update the module docstring and `distress_array` docstring (stage list
  per backend, seed semantics: augraphy backend seeds the whole pipeline;
  legacy backend keeps unseeded stains).

**Done when:** `uv run pytest tests/test_png_gen.py` green with the
default backend now augraphy — stage assertions that encode the *old*
look (exact cream tint values, unseeded stain variance) are pointed at
`backend="legacy"` or rewritten per Phase 3; disabled path, decode errors,
RGBA compositing, and html_to_png tests pass on both backends.

### Task 1.4 — `.gitignore`

- Add `augraphy_cache/` (finding 5).

**Done when:** running a distressed render creates `augraphy_cache/` and
`git status` stays clean.

---

## Phase 2 — Model: `DistressOptions` extension

**Files:** `document_gen/models/distress.py`, `document_gen/models/__init__.py`
(no export change needed — `DistressOptions` is already exported),
`tests/test_models.py`

### Task 2.1 — Add the new fields

- Add `backend: Literal["augraphy", "legacy"] = "augraphy"` (D1b) with a
  description naming both backends.
- Add every field from the catalog (30 new booleans + `watermark_word: str`,
  `jpeg_quality: int`, `fold_count: int`) with `default=False` (D3),
  `Field(description=...)` one-liners, grouped in the class body under
  `# --- ink phase ---` / `# --- paper phase ---` / `# --- post phase ---`
  comments.
- Validators: clamp `jpeg_quality` to 10–95, `fold_count` to 1–6;
  `watermark_word` stripped, max length 40 (empty → augraphy `"random"`).
- Keep all existing fields, defaults, and validators untouched.

**Done when:** `DistressOptions().model_dump()` contains all new keys with
`False`/defaults and `backend="augraphy"`; an old-trace JSON payload (only
legacy keys) still validates (backend defaults to `"augraphy"`);
`tests/test_models.py` extended with: default-dump test, clamp tests for
the three new numeric fields, backend literal validation test (rejects
`backend="bogus"`), legacy-payload-compat test.
`uv run pytest tests/test_models.py` green.

---

## Phase 3 — Tests

**Files:** `tests/test_png_gen.py`, `tests/test_server.py`

### Task 3.1 — Rewrite stage tests for the new implementation

Two suites, sharing fixtures (small synthetic white-with-text PNG):

**Legacy-backend tests** — the existing stage-specific tests are kept and
parameterized with `backend="legacy"` (they already encode the old look:
exact cream tint, unseeded stain variance, etc.). They must pass
unchanged, proving the legacy path is byte-identical to the pre-migration
behavior.

**Augraphy-backend tests** — new behavior-level assertions for the default
backend:

- disabled → file/bytes unchanged (keep).
- `paper_aging` → output mean color shifts off-white (hue tint), shape
  preserved.
- `vignette` → output differs from clean and shape is preserved. (No
  corner-vs-center assertion: the native `LightingGradient` is a
  light-strip band, not a radial vignette — finding 9, D9.)
- `stains` → output differs from clean; `stain_count=0` + `stains=False`
  → no stain contribution.
- `noise` → per-channel variance increases vs clean.
- ink preservation → with `paper_aging` on, dark text pixels in the output
  stay dark (the ink_to_paper overlay keeps content legible).
- `ink_fade` → accepted without error on both settings. (No fade-vs-crisp
  assertion: the 8.2.6 `ink_to_paper` blend ignores `overlay_alpha` —
  finding 8, D9.)
- `warp` / `blur` → unchanged tests (custom tail stages).
- determinism → same `(options, seed, stain_seed)` twice → byte-identical
  PNG bytes; different seed → different output (holds for stains too on
  the augraphy backend; the unseeded-stain tests remain valid only under
  `backend="legacy"`).
- RGBA input composited over white (both backends).
- small image (<30 px) → returns without raising (augraphy backend only,
  D7).
- backend isolation → enabling augraphy-only toggles (e.g. `ink_bleed`)
  with `backend="legacy"` produces output identical to the legacy path
  with those toggles off (new toggles are no-ops on legacy, D1b).

**Done when:** `uv run pytest tests/test_png_gen.py` green, with the
legacy suite passing unmodified.

### Task 3.2 — Per-augmentation smoke tests

- Parametrized test over every new toggle: enable exactly one effect on a
  ~400×300 synthetic document image, assert output shape preserved and
  output differs from the clean input (fresh pipeline, fixed seed).
- Slow augmentations (`paper_factory`, `voronoi_tessellation`,
  `delaunay_tessellation`, `shadow_cast`) use the smallest catalog ranges
  and are tagged `@pytest.mark.slow` (add `slow` to
  `addopts`-free default run — keep them in the default run but with small
  params; only split out if the suite exceeds ~60 s).
- These tests are the guard against augraphy API drift (finding 1).
- **Combo guard (finding 12):** one test runs the full default options
  (`paper_aging`, `vignette`, `stains`, `noise`, `ink_fade`, `blur`) plus
  `bad_photo_copy=True` in a fresh process and asserts it completes
  without the numba `AssertionError`. This pins the `BadPhotoCopy`
  before `SubtleNoise` post-phase ordering in `_build_augraphy_pipeline`.

**Done when:** `uv run pytest tests/test_png_gen.py -k aug` green; full
`uv run pytest` green.

### Task 3.3 — Server tests

- `tests/test_server.py` mocks the distress call; verify the preview/save
  endpoints still pass unchanged. Add one test that a request body with
  only new fields (e.g. `{"ink_bleed": true}`) is accepted.

**Done when:** `uv run pytest tests/test_server.py` green.

---

## Phase 4 — Frontend

**Files:** `web/src/lib/api.ts`, `web/src/components/distress-toolbar.tsx`,
`web/src/components/generate-image-dialog.tsx`

### Task 4.1 — `api.ts`

- Extend the `DistressOptions` TS type with all new fields (mirror the
  pydantic model). `stainSeedFor` unchanged.

### Task 4.2 — `distress-toolbar.tsx`

- Replace the flat switch list with **three collapsible sections** —
  **Ink**, **Paper**, **Post** — each listing its toggles (legacy effects
  fold into their phase: Paper → paper aging, stains, noise, noise
  texturize, brightness texturize, watermark, patterns, tessellations,
  paper factory; Ink → ink fade + the 11 ink effects; Post → vignette,
  blur, warp + the 20 post effects). Master "Distress" switch stays at the
  top; sections collapse to their header when all their toggles are off.
- Sliders/inputs for the new numeric controls: `jpeg_quality` (10–95),
  `fold_count` (1–6), `watermark_word` (text input, only when watermark on).
  Legacy sliders unchanged except `stain_count` relabeled "Stain intensity".
- Add a **Reset** button (restores `CLEAN_OPTIONS` with `enabled: true`).
- Add a **Backend** select ("Augraphy" / "Legacy") under the master
  switch, bound to `options.backend` (default "augraphy"); on the legacy
  backend the augraphy-only sections render disabled with a hint (those
  toggles are no-ops there, D1b).
- Keep the debounce/preview/save flow, `initialOptions`/`traceSeed` logic,
  and disabled-state behavior exactly as they are (the options object is
  just larger; `initialOptions` already spreads stored trace options over
  `CLEAN_OPTIONS`, so old traces work with the new fields defaulting to
  false; old traces without a `backend` key default to "augraphy").

**Done when:** `cd web && pnpm build && pnpm lint` clean; manual check:
open a traced PNG document, toggle one ink, one paper, and one post effect,
preview updates, save persists, reopening the dialog restores the toggles.

### Task 4.3 — `generate-image-dialog.tsx`

- Keep the generation dialog minimal: master Distress switch + the four
  legacy sliders (unchanged). New effects are configured in the live
  editor after generation (avoids a 40-control generation form).

**Done when:** `pnpm build` clean; generation flow unchanged in manual check.

---

## Phase 5 — CLI

**Files:** `document_gen/cli.py`, `tests/test_cli.py`

### Task 5.1 — Presets

- Keep all existing `--distress*` flags (they map to unchanged field
  names).
- Add `--distress-preset {scanned,office,fax,archival}` (only meaningful
  with `--distress`):
  - `scanned`: legacy defaults + `dirty_screen`, `moire`, `jpeg_artifacts`,
    `color_shift`.
  - `office`: `paper_aging`, `stains`, `folding`, `bindings`, `markup`,
    `scribbles`, `shadow_cast`.
  - `fax`: `faxify`, `dithering`, `low_ink_random_lines`, `noise`,
    `brightness`.
  - `archival`: `paper_aging`, `vignette`, `stains`, `ink_bleed`,
    `letterpress`, `ink_mottling`, `bleed_through`, `watermark`.
- Add `--distress-backend {augraphy,legacy}` (default `augraphy`) setting
  `DistressOptions.backend`.
- Preset sets the named options on top of the flag-derived
  `DistressOptions` (explicit flags win over preset values); presets
  imply `backend="augraphy"` unless `--distress-backend legacy` is given
  explicitly.

**Done when:** `uv run document-gen --help` shows the preset and backend
flag; `tests/test_cli.py` gains a parse test for each preset and for
`--distress-backend legacy` (assert the resulting `DistressOptions`
flags).

---

## Phase 6 — Docs

**Files:** `README.md`

- Update the distress section: augraphy-based pipeline (default), effect
  catalog (link to the model for the full list), preset names, the
  `backend` switch (`augraphy` default / `legacy` = the preserved
  pre-augraphy reference implementation), note that `augraphy_cache/` is
  created at runtime and gitignored, note the behavior change that stain
  positions are now seed-reproducible on the augraphy backend.
- Note the **native-only** principle (D9): the augraphy backend uses
  augraphy augmentations as-is, so `paper_aging` (mottled tint),
  `vignette` (light-strip gradient) look different from the legacy
  hand-rolled stages, and `ink_fade` currently has no visible effect on
  8.2.6 (finding 8) — select `backend="legacy"` for the old look.

**Done when:** README matches implemented behavior; `uv run black .` clean.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| augraphy API drift / future 9.x rename | Pin `augraphy>=8.2.6,<9` in `pyproject.toml`; Phase 3.2 smoke tests fail loudly on rename |
| Heavy augmentations blow the live-preview budget (300 ms debounce, sync endpoint) | Conservative fixed ranges (D6); `paper_factory` off by default; during Phase 3.2 time each augmentation on an A4@96dpi image and tighten/flag any that exceed ~2 s |
| Global `random` seeding under server threadpool breaks preview/save determinism | Module-level `threading.Lock` around build+augment (Task 1.2) |
| `augraphy_cache/` litter in CWD | `.gitignore` entry (Task 1.3); bounded at 30 files by the library |
| <30×30 images raise inside augraphy | Guard in `distress_array` (Task 1.2, D7) + test |
| Old-trace documents re-render differently | Expected migration behavior; documented in README + PR description; stored originals are untouched so users can re-tune; `backend="legacy"` (toolbar select / CLI flag) reproduces the old look exactly |
| Dual-path maintenance drift | Legacy path is frozen: `distress_array_legacy` is verbatim-preserved, documented as reference/fallback only, and all new effects land exclusively on the augraphy path; the legacy test suite must keep passing unmodified |
| `stain_count` semantics change (count → intensity) | UI relabel ("Stain intensity"); field name/range unchanged for compat |
| augraphy augments mutate/return float or 4-channel in edge cases | Assert `uint8` + 3-channel after `augment` in `distress_array`; re-normalize defensively |
| numba 0.67 parfor compile crash (finding 12) | `BadPhotoCopy` appended before `SubtleNoise`; a per-augmentation smoke test (Phase 3.2) covering the full-defaults + `bad_photo_copy` combo guards the ordering |
| `Faxify` returns grayscale at a resampled size (finding 13) | Post-augment re-normalization (2D→BGR, resize to input shape) in `distress_array` |
| `ink_fade` has no visible effect on 8.2.6 (finding 8) | Accepted per D9; documented in the catalog and README (Phase 6); revisit if a future augraphy release honors `overlay_alpha` for `ink_to_paper` |

## Validation checklist (end of plan)

- `uv run pytest` green (incl. new per-augmentation smoke tests).
- `uv run black .` clean; `cd web && pnpm build && pnpm lint` clean.
- Manual: generate a PNG with tracing + `--distress-preset office`; open the
  live editor; toggle ink bleed, watermark, and folding; preview matches
  save byte-for-byte (same seed); `augraphy_cache/` is gitignored.
- Manual: switch the same document to the Legacy backend in the toolbar —
  the render matches the pre-migration look, and augraphy-only toggles
  are disabled.
