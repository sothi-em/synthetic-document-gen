# Plan: Slider-based distress effects + remove Legacy selector

Rework the distress editor in the image view modal:

1. Remove the Legacy backend dropdown (UI only; the backend stays for old renders).
2. Replace every effect toggle with a single slider. The effect boolean is
   inferred from the slider value (0 = off). Effects without a strength
   parameter get a 0–1 intensity, mapped into augraphy parameters on the
   backend.

Three stages, each independently shippable. Stage 2 depends on Stage 1;
Stage 3 depends on both.

---

## Stage 1 — Backend: intensity fields + augraphy mapping

**Goal:** the API accepts per-effect intensities and the augraphy pipeline
scales each effect by its intensity. Fully backward compatible: payloads
with only booleans render exactly as today.

### 1.1 Model — `document_gen/models/distress.py`

- Keep all existing boolean flags and numeric fields (saved options and
  gen traces in TinyDB contain them).
- Add a `*_intensity: float | None` field (0–1, `ge=0, le=1`) for every
  effect that has no numeric parameter today:
  - ink: `ink_fade`, `ink_bleed`, `bleed_through`, `letterpress`,
    `ink_mottling`, `ink_color_swap`, `hollow`, `dithering`, `dot_matrix`,
    `low_ink_periodic_lines`, `low_ink_random_lines`, `lines_degradation`
  - paper: `paper_aging`, `noise_texturize`, `brightness_texturize`,
    `watermark`, `pattern_generator`, `voronoi_tessellation`,
    `delaunay_tessellation`, `paper_factory`
  - post: `bad_photo_copy`, `faxify`, `dirty_drum`, `dirty_rollers`,
    `dirty_screen`, `shadow_cast`, `lens_flare`, `reflected_light`,
    `brightness`, `gamma`, `color_shift`, `depth_blur`, `moire`,
    `lcd_pattern`, `double_exposure`, `bindings`, `markup`, `scribbles`,
    `blur`
- A `model_validator` (after) resolves `None` → `1.0` when the matching
  flag is on, `0.0` when off. Old saved data (flags only) therefore keeps
  its current look; new data carries explicit intensities.
- The six effects that already have numeric params keep them as their
  intensity — no new fields: `vignette_strength` (0–1), `stain_count`
  (0–20), `noise_strength` (0–50), `warp_strength` (0–1), `jpeg_quality`
  (10–95, 95 = off), `fold_count` (1–6).

### 1.2 Pipeline — `document_gen/generators/png_gen.py`

- `_build_augraphy_pipeline`: gate each effect on `flag and intensity > 0`
  and scale augraphy strength params by the resolved intensity `i`:
  - General rule — scale strength ranges, e.g.:
    - `InkBleed(intensity_range=(0.1·i, 0.4·i))`
    - `BleedThrough(intensity_range=(0.1·i, 0.4·i), alpha=0.3·i)`
    - `InkMottling(ink_mottling_alpha_range=(0.1·i, 0.3·i))`
    - `Letterpress` / `DirtyScreen`: scale sample/cluster counts by `i`
    - `DotMatrix`: dot width/height ranges scaled by `i`
    - `LowInk*Lines` / `Scribbles` / `Markup` / `BindingsAndFasteners`:
      count ranges scaled by `i`
    - `LinesDegradation(line_split_probability=(0.2·i, 0.4·i))`
    - `ColorPaper(saturation_range=(10·i, 40·i))`
    - `NoiseTexturize(sigma_range=(3·i, 10·i), turbulence_range=(2·i, 5·i))`
    - `BrightnessTexturize(texturize_range=(1 − 0.15·i, 0.99))`
    - `Watermark` / `PatternGenerator` / `Voronoi` / `Delaunay`: alpha or
      cell counts scaled by `i`
    - `BadPhotoCopy`: sparsity/concentration ranges scaled by `i`
    - `DirtyDrum(line_concentration=0.1·i)`, `DirtyRollers` /
      `Faxify`: width/scale ranges scaled by `i`
    - `ShadowCast(shadow_opacity_range=(0.2·i, 0.5·i))`
    - `LensFlare(size=(0.5·i, 3·i))`, `ReflectedLight`, `Brightness`,
      `Gamma`: deviation from neutral scaled by `i`
    - `ColorShift(offset (3·i, 5·i))`, `DepthSimulatedBlur` (axis lengths
      scaled by `i`), `Moire(moire_blend_alpha=0.1·i)`,
      `LCDScreenPattern(pattern_overlay_alpha=0.3·i)`,
      `DoubleExposure(offset_range=(18·i, 25·i))`
  - Non-scalable effects use `p=i` (deterministic under the pipeline
    seed): `ink_color_swap`, `hollow`, `dithering`, `paper_factory`,
    `bindings` (and any other effect with no continuous parameter).
  - `ink_fade`: `overlay_alpha = 1.0 − 0.15·i` (i=1 reproduces today's
    0.85).
  - Numeric-param effects keep their current wiring (stain count/alpha
    from `stain_count`, `SubtleNoise` from `noise_strength`, `LightingGradient`
    from `vignette_strength`, `Jpeg` from `jpeg_quality`, `Folding` from
    `fold_count`).
- Tail stages in `distress_array`:
  - `warp` already scales via `warp_strength` — unchanged.
  - `blur`: kernel size from intensity → `(3 + 2·round(3·i))` (3/5/7/9),
    sigma 0; gate on `flag and intensity > 0`.
- `distress_array_legacy` untouched (old saved renders reproduce exactly).
- Server endpoints unchanged — new fields are optional with defaults.

### 1.3 Tests (Stage 1 gate)

- Model tests (new `tests/test_distress_model.py` or extend existing):
  - flag on + intensity absent → resolves to 1.0; flag off → 0.0;
    explicit values preserved; bounds 0–1 enforced.
  - `DistressOptions()` default dump is JSON-serializable.
- `tests/test_png_gen.py`:
  - flag on + intensity 0 → no-op (output == clean copy) for a couple of
    effects (e.g. ink_bleed, shadow_cast).
  - intensity 1.0 reproduces the pre-change output for the same seed
    (spot-check 2–3 effects where the i=1 mapping is exact).
  - mid intensity (0.5) changes the image, shape/dtype preserved.
- `uv run pytest` green, `uv run black .` clean.

---

## Stage 2 — Frontend: sliders replace toggles, Legacy selector removed

**Goal:** every effect in the distress toolbar is a single slider; the
effect boolean is inferred from the slider value (0 = off). The Backend
dropdown is gone; the toolbar always sends `backend: "augraphy"`.

### 2.1 Types — `web/src/lib/api.ts`

- Add the new `*_intensity: number` fields to the `DistressOptions`
  interface (mirror the backend model).

### 2.2 Toolbar — `web/src/components/distress-toolbar.tsx`

Remove:

- The Backend `Select` (and the "Legacy backend: augraphy-only effects
  are disabled" note), the `legacy` variable, the `Select*` imports.
- `augraphyOnly` on `EffectDef` and every place it disables a control —
  all effects are always editable when the document is editable.
- The `Switch` import and all toggle rows.
- The separate `SLIDERS` record (sliders merge into the effect list).

Add:

- `EffectDef` gains a slider spec:
  - `intensityKey` (a `*_intensity` field) **or** `valueKey` (one of the
    six existing numeric fields), plus `min`, `max`, `step`, `fmt`.
  - Default spec for intensity effects: min 0, max 1, step 0.05,
    `fmt = (v) => v.toFixed(2)`.
  - Numeric-effect specs:
    - stains → `stain_count`, 0–20, step 1, `fmt = "n stains"`
    - noise → `noise_strength`, 0–50, step 1
    - vignette → `vignette_strength`, 0–1, step 0.05
    - warp → `warp_strength`, 0–1, step 0.05
    - jpeg_artifacts → `jpeg_quality`, 10–95, step 1 (95 = off; label
      "JPEG quality")
    - folding → `fold_count`, 1–6, step 1
- Slider `onValueChange` writes the value and derives the boolean:
  `flag = value > 0` (jpeg: `jpeg_quality < 95`); for intensity effects
  also write `intensityKey = value`.
- Render one row per effect inside each collapsible section: label left,
  value readout, full-width `Slider` below (replacing the old
  label+Switch row; the old standalone slider blocks disappear).
- `activeCount` (section badge) counts effects whose value > 0 (jpeg:
  `< 95`).
- `CLEAN_OPTIONS`: all intensities 0, all flags false, numeric values 0
  (jpeg_quality 95, fold_count 1). Reset button keeps using it.
- `initialOptions`: when loading saved/trace options that predate
  intensities, derive client-side `intensity = saved ?? (flag ? 1 : 0)`
  so old renders match their toolbar state; numeric fields keep raw
  values.
- Watermark word input stays, shown when the watermark slider > 0
  (drop the `!legacy` condition).
- Seed input, busy/error badges, Reset/Save footer unchanged.
- Toolbar always sends `backend: "augraphy"` in preview/save payloads
  (hardcode in `DEFAULT_OPTIONS`/`CLEAN_OPTIONS`; no UI control).

### 2.3 Stage 2 gate

- `cd web && pnpm lint && pnpm build` clean.
- Manual check: open an image with a saved distress state — sliders match
  the persisted look; moving a slider re-renders the preview; 0 turns the
  effect off; Save persists and reopening restores the same sliders.

---

## Stage 3 — Integration, cleanup, final verification

**Goal:** end-to-end verification and removal of now-dead code/paths that
the rework orphaned.

### 3.1 Cleanup

- `web/src/components/distress-toolbar.tsx`: drop the `Switch` import if
  fully unused; update the component docstring (no more backend select /
  toggle language; describe the slider semantics: 0 = off).
- `document_gen/models/distress.py`: update field descriptions that still
  describe toggle-only behavior (e.g. note that the flag is derived from
  the slider in the UI, and that intensity scales the augraphy params).
- `README.md` / any docs mentioning the Legacy selector or effect toggles
  in the UI: update to slider semantics.
- Keep (do not delete): the `"legacy"` backend in the model +
  `distress_array_legacy` — documents saved with it must still render
  identically.

### 3.2 Verification

- `uv run pytest` (full suite, including `tests/test_png_gen.py` and
  `tests/test_server.py`).
- `uv run black .` clean.
- `cd web && pnpm lint && pnpm build` clean.
- Manual pass (dev server + `uv run document-gen serve`):
  1. New PNG with tracing → open image modal → all sections show sliders
     only, no Backend dropdown, no toggles.
  2. Raise each section's sliders to mid values → preview degrades
     progressively; set to 0 → effect disappears.
  3. Save → reopen → sliders identical, render identical.
  4. Open a document saved before this change (boolean-only options) →
     sliders infer the old look (on-effects at 1.0 / their numeric
     values), render matches the persisted image.
  5. Reset → clean image; Seed input still pins deterministic renders.
  6. Watermark slider > 0 reveals the watermark word input.

### 3.3 Commits (Conventional Commits)

- `feat(distress): per-effect intensity fields + augraphy scaling`
  (Stage 1, backend + tests)
- `feat(web): slider-based distress effects; remove legacy backend selector`
  (Stage 2)
- `chore(distress): cleanup + docs after slider rework` (Stage 3)
