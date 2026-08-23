# Plan: Per-effect seeding for the augraphy distress pipeline

## Problem

In the distress editor, moving one slider (e.g. JPEG quality) changes the
random output of *other* effects (e.g. the scribbles land in new
positions). Two compounding causes:

1. **Blank-seed (random) mode** — `web/src/components/distress-toolbar.tsx`
   re-rolls a fresh `seed` + `stain_seed` on *every* options change, so the
   whole render is re-randomized. This is deliberate ("each slider move
   gives a new random render") and stays as-is.
2. **Shared PRNG stream (the real defect)** — augraphy seeds one set of
   process-global PRNGs per pipeline run
   (`augmentationpipeline.py:95-97`: `random.seed`, `np.random.seed`,
   `cv2.setRNGSeed`) and every effect draws from that single stream in
   pipeline order. An upstream effect's draws (and how many it consumes)
   shift the stream position where downstream effects draw theirs.

Reproduced against the live pipeline (same seed 42, blank white page so
JPEG is a visual no-op):

```
scribbles only            vs  jpeg(50) + scribbles
→ 197,587 pixels differ   (scribble strokes land elsewhere)
```

So a pinned seed guarantees *same options → same image*, but not *one
effect independent of the others*.

## Goal

Each effect draws from its own PRNG stream, derived from the base seed, so
changing one effect's parameters never moves another effect's random
output. Same (image, options, seed, stain_seed) must still produce
byte-identical output (preview/save byte-identity guarantee).

## Approach

Wrap each augraphy augmentation in a thin proxy that re-seeds the three
global PRNGs with a per-effect derived seed before delegating.

Viability check (augraphy 8.x internals):

- `AugraphyPipeline.apply_phase` (`augmentationpipeline.py:716-746`)
  iterates `phase.augmentations` and calls `augmentation.should_run()`
  then `augmentation(..., force=True)` per object. Objects in the phase
  lists are therefore the right interception point.
- The builder uses no `OneOf`/nested compound augmentations, so one
  wrapper level suffices.
- The pipeline's own between-effect random draws are inert under our
  config: `paper_color`/`ink_color` `random.randint` calls only fire when
  `paper_color_range`/`ink_color_range` are non-default (we pass neither).

## Changes

### 1. `_SeededAugmentation` wrapper — `document_gen/generators/png_gen.py`

~25 lines. Holds the inner augmentation and a derived seed:

- `should_run()`: re-seed `random`, `np.random`, and
  `cv2.setRNGSeed` with the derived seed, then delegate to the inner
  `should_run()` (its `p` draw now comes from the effect's own stream).
- `__call__(...)`: delegate to the inner augmentation (stream already
  seeded; all of the effect's parameter draws consume its own stream).
- All three RNGs must be seeded — different effects use `random`,
  `np.random`, and `cv2` respectively; missing one leaves that effect
  order-dependent.

Derived seed: `zlib.crc32(f"{base_seed}:{effect_name}".encode()) & 0x7FFFFFFF`,
where `base_seed` is the existing combined seed
(`seed` + CRC32-combined `stain_seed`, as computed today at
`png_gen.py:431-433`). Effect name = the `DistressOptions` flag name
(e.g. `"scribbles"`, `"jpeg_artifacts"`), stable across versions.

### 2. `_build_augraphy_pipeline` — same file

~30-50 lines changed. Collect `(effect_name, augmentation)` pairs per
phase instead of bare augmentations (e.g. via a small
`_add(phase_list, name, aug)` helper at the ~40 append sites), then wrap
each pair in `_SeededAugmentation` before building the
`AugraphyPipeline`. The pipeline-level `random_seed` is kept (harmless;
the wrappers override per effect).

### 3. Not changed

- **Frontend** — seed UI, blank-seed re-roll behaviour, and saved
  records all work as today.
- **API / models / TinyDB** — no schema change, no migration; saved
  documents keep their stored options + seeds.
- **Legacy backend** — already per-stage RNGs (`stain_rng`,
  `cv2.setRNGSeed` for noise, `default_rng(seed)` for warp).
- **Tail stages** — augraphy-path warp/blur already use their own
  `np.random.default_rng(seed)`.

## Behavioural consequences

- **One-time visual shift**: every pinned-seed render differs from the
  old scheme (each effect now draws from its own stream). Files on disk
  are untouched until re-saved from the editor.
- **Preserved guarantees**: same (image, options, seed, stain_seed) →
  identical bytes, so the live preview still matches the saved render.
- Moving one slider now only changes that effect's *random* output.
  Downstream effects still see different *pixels* when an upstream effect
  changes (e.g. blur after warp) — inherent to a pipeline, not seeding.

## Tests — new `tests/test_per_effect_seeding.py`

`png_gen` needs augraphy/cv2 (available in the venv; currently untested
per AGENTS.md, but a focused new file is feasible):

1. **Determinism** — same (image, options, seed, stain_seed) rendered
   twice → `np.array_equal`.
2. **Independence** — render with effect A at two different intensities
   while effect B stays fixed → B's contribution is pixel-identical
   (regression test for the exact JPEG/scribbles failure mode above; use
   a flat page so the upstream effect is a visual no-op and any
   difference is purely B's redrawn random output).
3. **Seed sensitivity** — different base seeds → different output
   (sanity: derived seeds actually vary).

## Risks / mitigations

- The wrapper relies on augraphy's `apply_phase` call pattern
  (`should_run()` then `__call__(force=True)`) — internal API, stable
  across 8.x but not a public contract. Mitigated by the regression
  tests and the existing augraphy version pin.
- Effects that internally use `random.SystemRandom` or time-based
  randomness are unaffected by seeding (same as today).

## Effort estimate

~100 lines changed in one file + ~60-80 lines of new tests. 1-2 h core
change; half a day including tests and visual verification.
