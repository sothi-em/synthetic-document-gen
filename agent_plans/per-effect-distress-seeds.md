# Plan: Per-effect distress seeds with override seed

Rework the seeding of distress image effects:

- On image generation with trace, generate a **random internal seed for each
  effect** (plain numbers, not derived from anything).
- The modal seed input becomes an **override seed**: rendering always uses the
  per-effect seeds; only when the user hits **Apply** is the entered value
  copied into `options.seed` (last override record) **and** into every
  effect's seed, and the active effects are regenerated.
- Remove the initial global distress seed that was generated at image
  creation (`random_distress_seed()` / `stages.distress.seed`).
- Clean rework: remove all legacy backend code (`backend` field,
  `distress_array_legacy`, `stain_seed`).

## Core semantics

- **Per-effect seeds**: every effect has its own plain-number seed. These are
  what the render always uses.
- **`DistressOptions.seed` stays**: it now means "last override seed"
  (`null` = no override, per-effect internal seeds in use). It is a *record*
  only — the render never reads it.
- **Apply** (modal): copies the entered value into `options.seed` **and**
  into every effect's seed, then re-renders the active effects. Blank + Apply
  restores the internal per-effect seeds and sets `seed: null`.

## Backend

### `document_gen/models/distress.py`

- Keep `seed: int | None` (re-doc as "last override seed; null = per-effect
  internal seeds").
- Remove the `backend` field (legacy engine).
- Old saved payloads carrying `backend`/`seed` still validate (pydantic
  ignores unknown extras; `seed` is kept).

### `document_gen/generators/png_gen.py`

- Remove `distress_array_legacy`, `_derived_seed` (no more CRC32 derivation),
  and `stain_seed` everywhere.
- `_SeededAugmentation` takes the effect's seed directly (plain number).
- New signatures:
  - `distress_array(clean, options, effect_seeds: dict[str, int])`
  - `distress_image(path, options, effect_seeds)`
  - `distress_image_to_bytes(data, options, effect_seeds)`
  - Each augmentation (and the warp tail stage) re-seeds from
    `effect_seeds[name]` (missing key → 0).
- The pipeline-level augraphy seed becomes a fixed constant (each effect
  re-seeds itself anyway).
- New `random_effect_seeds() -> dict[str, int]` over the canonical
  effect-name list (exported so the trace/frontend contract is documented in
  one place).

### `document_gen/document_png.py`

- Remove `random_distress_seed()` and the initial global distress seed
  created at image creation.
- Stage 5: when `gen_tracing` or the distress pass is enabled →
  `effect_seeds = random_effect_seeds()`; recorded on the trace as
  `stages.distress.effect_seeds` (replaces `stages.distress.seed`); passed to
  `distress_image`.

### `document_gen/server.py`

- `DistressEditRequest`: `{ distress: DistressOptions, effect_seeds:
  dict[str, int] }` (replaces `seed`/`stain_seed`).
- Preview/save endpoints pass `effect_seeds` through; save persists it.

### `document_gen/document_query.py`

- `save_document_distress(doc_id, options, effect_seeds)` → stores
  `distress: { options, effect_seeds }`.

### `document_gen/cli.py`

- Remove `--distress-backend`.
- `--seed` becomes the override: when given, all effect seeds are set to it
  (and `options.seed` records it); otherwise random per-effect seeds.

## Frontend

### `web/src/lib/api.ts`

- `DistressOptions`: keep `seed` (re-doc as last override seed), remove
  `backend`.
- `DistressEditBody`: `{ distress, effect_seeds: Record<string, number> }`.
- `DocumentRecord.distress`: `{ options, effect_seeds }`.
- Remove `stainSeedFor`.

### `web/src/components/distress-toolbar.tsx`

- New state: `effectSeeds: Record<string, number>` — initial value: saved
  editor state → trace `effect_seeds` → fresh random per effect (missing
  names filled randomly). Original internal seeds kept in a ref for restore.
- Seed input becomes **"Override seed"** with an **Apply** button:
  - Apply with a number → `options.seed = n`, all `effectSeeds = n`,
    re-render.
  - Apply with blank → `options.seed = null`, restore internal seeds,
    re-render.
- Preview loop fires on `options` **or** `effectSeeds` change; sends
  `effect_seeds`.
- Remove the ephemeral "blank = random" seed logic; Randomize/Reset keep the
  current seeds.
- Save persists `options` (incl. `seed`) + `effect_seeds`.

## Tests

- `tests/test_png_gen.py`: drop legacy-backend tests; new `effect_seeds`
  signatures; determinism per effect seed.
- `tests/test_per_effect_seeding.py`: rewrite for the `effect_seeds` map
  (independence + determinism + seed sensitivity).
- `tests/test_document_png.py`: trace records `effect_seeds` map (no global
  seed); the pass uses the recorded map.
- `tests/test_distress_model.py`, `tests/test_server.py`,
  `tests/test_document_query.py`, `tests/test_cli.py`: update to the new
  contracts.

## Edge case: pre-rework saved records

Old records have `distress: { options, seed, stain_seed }` but no
`effect_seeds`. Fallback: if the old `seed` is non-null, fill all effect
seeds with that value; otherwise fresh random per-effect seeds.
