import { useEffect, useRef, useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronRight,
  Dices,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Save,
  X,
} from "lucide-react"

import {
  api,
  originalImagePath,
  type DistressOptions,
  type DocumentRecord,
} from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Slider } from "@/components/ui/slider"

/**
 * Defaults mirroring `document_gen.models.distress.DistressOptions`
 * (with `enabled` on; intensities resolved from the flags, as the
 * backend does). Used only when the document has no generation trace
 * at all, in which case the toolbar is disabled anyway. Kept so the
 * backend defaults stay documented in one place; the disabled toolbar
 * shows the clean baseline instead (see `initialEditorState`), so no
 * effect counters appear for images that were never distressed.
 */
const DEFAULT_OPTIONS: DistressOptions = {
  enabled: true,
  paper_aging: true,
  paper_aging_intensity: 1,
  vignette: true,
  vignette_strength: 0.3,
  stains: true,
  stain_count: 4,
  noise: true,
  noise_strength: 12,
  ink_fade: true,
  ink_fade_intensity: 1,
  blur: true,
  blur_intensity: 1,
  warp: false,
  warp_strength: 0.5,
  seed: null,
  ink_bleed: false,
  ink_bleed_intensity: 0,
  bleed_through: false,
  bleed_through_intensity: 0,
  letterpress: false,
  letterpress_intensity: 0,
  ink_mottling: false,
  ink_mottling_intensity: 0,
  ink_color_swap: false,
  ink_color_swap_intensity: 0,
  hollow: false,
  hollow_intensity: 0,
  dithering: false,
  dithering_intensity: 0,
  dot_matrix: false,
  dot_matrix_intensity: 0,
  low_ink_periodic_lines: false,
  low_ink_periodic_lines_intensity: 0,
  low_ink_random_lines: false,
  low_ink_random_lines_intensity: 0,
  lines_degradation: false,
  lines_degradation_intensity: 0,
  noise_texturize: false,
  noise_texturize_intensity: 0,
  brightness_texturize: false,
  brightness_texturize_intensity: 0,
  watermark: false,
  watermark_intensity: 0,
  watermark_word: "CONFIDENTIAL",
  pattern_generator: false,
  pattern_generator_intensity: 0,
  voronoi_tessellation: false,
  voronoi_tessellation_intensity: 0,
  delaunay_tessellation: false,
  delaunay_tessellation_intensity: 0,
  paper_factory: false,
  paper_factory_intensity: 0,
  bad_photo_copy: false,
  bad_photo_copy_intensity: 0,
  faxify: false,
  faxify_intensity: 0,
  dirty_drum: false,
  dirty_drum_intensity: 0,
  dirty_rollers: false,
  dirty_rollers_intensity: 0,
  dirty_screen: false,
  dirty_screen_intensity: 0,
  shadow_cast: false,
  shadow_cast_intensity: 0,
  lens_flare: false,
  lens_flare_intensity: 0,
  reflected_light: false,
  reflected_light_intensity: 0,
  brightness: false,
  brightness_intensity: 0,
  gamma: false,
  gamma_intensity: 0,
  color_shift: false,
  color_shift_intensity: 0,
  depth_blur: false,
  depth_blur_intensity: 0,
  moire: false,
  moire_intensity: 0,
  lcd_pattern: false,
  lcd_pattern_intensity: 0,
  jpeg_artifacts: false,
  jpeg_quality: 100,
  double_exposure: false,
  double_exposure_intensity: 0,
  folding: false,
  fold_count: 2,
  bindings: false,
  bindings_intensity: 0,
  markup: false,
  markup_intensity: 0,
  scribbles: false,
  scribbles_intensity: 0,
}

/**
 * Starting state for traced images generated without distress: no
 * effect flag is set, every intensity is 0 and every numeric value is
 * at its off value, so the render starts from a clean look.
 */
const CLEAN_OPTIONS: DistressOptions = {
  ...DEFAULT_OPTIONS,
  enabled: false,
  paper_aging: false,
  paper_aging_intensity: 0,
  vignette: false,
  vignette_strength: 0,
  stains: false,
  stain_count: 0,
  noise: false,
  noise_strength: 0,
  ink_fade: false,
  ink_fade_intensity: 0,
  blur: false,
  blur_intensity: 0,
  warp: false,
  warp_strength: 0,
  watermark_word: "",
  jpeg_quality: 100,
  fold_count: 1,
}

type SectionKey = "ink" | "paper" | "post"

interface EffectDef {
  /** Boolean flag key; derived from the slider value (0 = off). */
  key: keyof DistressOptions
  label: string
  /** 0-1 intensity field (effects without a numeric parameter). */
  intensityKey?: keyof DistressOptions
  /** Existing numeric field acting as the intensity. */
  valueKey?: keyof DistressOptions
  min: number
  max: number
  step: number
  fmt: (v: number) => string
}

/** Default slider spec for 0-1 intensity effects. */
function intensityEffect(key: keyof DistressOptions, label: string): EffectDef {
  return {
    key,
    label,
    intensityKey: `${key}_intensity` as keyof DistressOptions,
    min: 0,
    max: 1,
    step: 0.05,
    fmt: (v) => v.toFixed(2),
  }
}

/** Slider spec for effects with a dedicated numeric parameter. */
function valueEffect(
  key: keyof DistressOptions,
  label: string,
  valueKey: keyof DistressOptions,
  min: number,
  max: number,
  step: number,
  fmt: (v: number) => string,
): EffectDef {
  return { key, label, valueKey, min, max, step, fmt }
}

const INK_EFFECTS: EffectDef[] = [
  intensityEffect("ink_fade", "Ink fade"),
  intensityEffect("ink_bleed", "Ink bleed"),
  intensityEffect("bleed_through", "Bleed through"),
  intensityEffect("letterpress", "Letterpress"),
  intensityEffect("ink_mottling", "Ink mottling"),
  intensityEffect("ink_color_swap", "Ink color swap"),
  intensityEffect("hollow", "Hollow strokes"),
  intensityEffect("dithering", "Dithering"),
  intensityEffect("dot_matrix", "Dot matrix"),
  intensityEffect("low_ink_periodic_lines", "Low ink (periodic)"),
  intensityEffect("low_ink_random_lines", "Low ink (random)"),
  intensityEffect("lines_degradation", "Line degradation"),
]

const PAPER_EFFECTS: EffectDef[] = [
  intensityEffect("paper_aging", "Paper aging"),
  valueEffect("stains", "Stains", "stain_count", 0, 20, 1, (v) => `${v} stains`),
  valueEffect("noise", "Noise", "noise_strength", 0, 50, 1, (v) => String(v)),
  intensityEffect("noise_texturize", "Noise texturize"),
  intensityEffect("brightness_texturize", "Brightness texturize"),
  intensityEffect("watermark", "Watermark"),
  intensityEffect("pattern_generator", "Pattern"),
  intensityEffect("voronoi_tessellation", "Voronoi texture"),
  intensityEffect("delaunay_tessellation", "Delaunay texture"),
  intensityEffect("paper_factory", "Paper texture"),
]

const POST_EFFECTS: EffectDef[] = [
  valueEffect(
    "vignette",
    "Vignette",
    "vignette_strength",
    0,
    1,
    0.05,
    (v) => v.toFixed(2),
  ),
  intensityEffect("blur", "Blur"),
  valueEffect(
    "warp",
    "Warp",
    "warp_strength",
    0,
    1,
    0.05,
    (v) => v.toFixed(2),
  ),
  intensityEffect("bad_photo_copy", "Bad photo copy"),
  intensityEffect("faxify", "Faxify"),
  intensityEffect("dirty_drum", "Dirty drum"),
  intensityEffect("dirty_rollers", "Dirty rollers"),
  intensityEffect("dirty_screen", "Dirty screen"),
  intensityEffect("shadow_cast", "Shadow cast"),
  intensityEffect("lens_flare", "Lens flare"),
  intensityEffect("reflected_light", "Reflected light"),
  intensityEffect("brightness", "Brightness"),
  intensityEffect("gamma", "Gamma"),
  intensityEffect("color_shift", "Color shift"),
  intensityEffect("depth_blur", "Depth blur"),
  intensityEffect("moire", "Moire"),
  intensityEffect("lcd_pattern", "LCD pattern"),
  valueEffect(
    "jpeg_artifacts",
    "JPEG quality",
    "jpeg_quality",
    10,
    100,
    5,
    (v) => String(v),
  ),
  intensityEffect("double_exposure", "Double exposure"),
  valueEffect("folding", "Folding", "fold_count", 0, 6, 1, (v) =>
    v === 0 ? "off" : `${v} folds`,
  ),
  intensityEffect("bindings", "Bindings"),
  {
    key: "markup",
    label: "Markup",
    intensityKey: "markup_intensity",
    min: 0,
    max: 10,
    step: 1,
    fmt: (v) => (v === 0 ? "off" : `${Math.round(v)} lines`),
  },
  {
    key: "scribbles",
    label: "Scribbles",
    intensityKey: "scribbles_intensity",
    min: 0,
    max: 10,
    step: 1,
    fmt: (v) => (v === 0 ? "off" : `${Math.round(v)} scribbles`),
  },
]

const SECTIONS: { key: SectionKey; label: string; effects: EffectDef[] }[] = [
  { key: "ink", label: "Ink", effects: INK_EFFECTS },
  { key: "paper", label: "Paper", effects: PAPER_EFFECTS },
  { key: "post", label: "Post", effects: POST_EFFECTS },
]

/**
 * Boolean flags that carry a 0-1 ``*_intensity`` field (mirrors the
 * backend model); every other effect has a dedicated numeric field.
 */
const INTENSITY_EFFECTS: (keyof DistressOptions)[] = [
  "ink_fade",
  "ink_bleed",
  "bleed_through",
  "letterpress",
  "ink_mottling",
  "ink_color_swap",
  "hollow",
  "dithering",
  "dot_matrix",
  "low_ink_periodic_lines",
  "low_ink_random_lines",
  "lines_degradation",
  "paper_aging",
  "noise_texturize",
  "brightness_texturize",
  "watermark",
  "pattern_generator",
  "voronoi_tessellation",
  "delaunay_tessellation",
  "paper_factory",
  "bad_photo_copy",
  "faxify",
  "dirty_drum",
  "dirty_rollers",
  "dirty_screen",
  "shadow_cast",
  "lens_flare",
  "reflected_light",
  "brightness",
  "gamma",
  "color_shift",
  "depth_blur",
  "moire",
  "lcd_pattern",
  "double_exposure",
  "bindings",
  "markup",
  "scribbles",
  "blur",
]

/** Current slider value for an effect (non-finite values read as 0). */
function effectValue(e: EffectDef, o: DistressOptions): number {
  const v = Number(o[e.intensityKey ?? e.valueKey!])
  return Number.isFinite(v) ? v : 0
}

/** Whether an effect draws from a PRNG stream (i.e. has a reseedable seed). */
function hasSeed(e: EffectDef): boolean {
  return EFFECT_SEED_NAMES.includes(e.key)
}

/** Whether an effect is active (slider above its off value). */
function isActive(e: EffectDef, o: DistressOptions): boolean {
  if (e.key === "jpeg_artifacts") return o.jpeg_quality < 100
  return effectValue(e, o) > 0
}

/** Number of active effects in a section. */
function activeCount(effects: EffectDef[], options: DistressOptions): number {
  return effects.filter((e) => isActive(e, options)).length
}

/**
 * Fill in intensities for options saved before the intensity fields
 * existed: a missing intensity resolves from its flag (on -> 1, off ->
 * 0) so old renders match their toolbar state. Explicit intensities
 * are kept as saved.
 */
function resolveIntensities(
  raw: Partial<DistressOptions>,
  merged: DistressOptions,
): DistressOptions {
  const next: DistressOptions = { ...merged }
  for (const flag of INTENSITY_EFFECTS) {
    const key = `${flag}_intensity` as keyof DistressOptions
    if (raw[key] == null) {
      const full = flag === "markup" || flag === "scribbles" ? 3 : 1
      ;(next as unknown as Record<string, unknown>)[key] = merged[flag] ? full : 0
    }
  }
  return next
}

/** Defensive read of `gen_tracing.stages.distress` (opaque record). */
function distressTrace(
  doc: DocumentRecord,
): Record<string, unknown> | null {
  const stages = doc.gen_tracing?.stages
  if (typeof stages !== "object" || stages === null) return null
  const distress = (stages as Record<string, unknown>).distress
  return typeof distress === "object" && distress !== null
    ? (distress as Record<string, unknown>)
    : null
}

/**
 * Editor state persisted by the distress save endpoint (options plus
 * the exact per-effect seeds of the saved render), or `null` when the
 * image has never been distressed and saved from the preview editor.
 *
 * Records saved before the per-effect rework carry a single global
 * `seed` (and a `stain_seed`) instead of `effect_seeds`; the old seed
 * is surfaced as `legacySeed` so the caller can fill every effect seed
 * with it.
 */
function savedDistress(doc: DocumentRecord): {
  options: DistressOptions
  effectSeeds: Record<string, number> | null
  legacySeed: number | null
} | null {
  const d = doc.distress
  if (typeof d !== "object" || d === null) return null
  if (typeof d.options !== "object" || d.options === null) return null
  const raw = d.options as Partial<DistressOptions>
  const options = resolveIntensities(raw, {
    ...CLEAN_OPTIONS,
    ...raw,
    enabled: true,
  })
  const es = d.effect_seeds
  if (typeof es === "object" && es !== null) {
    return {
      options,
      effectSeeds: fillMissingSeeds(es as Record<string, number>),
      legacySeed: null,
    }
  }
  // Pre-rework record: single global seed (always present there).
  const legacy = d as { seed?: unknown }
  const legacySeed = typeof legacy.seed === "number" ? legacy.seed : null
  return { options, effectSeeds: null, legacySeed }
}

/**
 * Legacy normalization: renders saved before the JPEG off-point moved to
 * 100 carry the effect off at a lower quality (e.g. 95); snap them to the
 * off-point so the slider matches the (inactive) effect.
 */
function normalizeJpegOff(o: DistressOptions): DistressOptions {
  if (!o.jpeg_artifacts && o.jpeg_quality < 100) {
    return { ...o, jpeg_quality: 100 }
  }
  return o
}

/**
 * Canonical per-effect seed names: every effect that draws from a PRNG
 * stream (mirrors `document_gen.generators.png_gen.EFFECT_SEED_NAMES`).
 * `ink_fade` only scales the overlay alpha, `blur` is a deterministic
 * Gaussian kernel, and `dithering` (floyd-steinberg) makes no random
 * draws, so none of them carries a seed.
 */
const EFFECT_SEED_NAMES: string[] = [...INK_EFFECTS, ...PAPER_EFFECTS, ...POST_EFFECTS]
  .map((e) => e.key)
  .filter((k) => k !== "ink_fade" && k !== "blur" && k !== "dithering")

/** Fresh non-negative random seed (matches the backend's seed range). */
function randomSeed(): number {
  return Math.floor(Math.random() * 0x7fffffff)
}

/** Fresh random per-effect seed map (one plain number per effect). */
function randomEffectSeeds(): Record<string, number> {
  const seeds: Record<string, number> = {}
  for (const name of EFFECT_SEED_NAMES) seeds[name] = randomSeed()
  return seeds
}

/** Fill any missing effect names with fresh random seeds. */
function fillMissingSeeds(seeds: Record<string, number>): Record<string, number> {
  const next = { ...seeds }
  for (const name of EFFECT_SEED_NAMES) {
    if (!Number.isFinite(next[name])) next[name] = randomSeed()
  }
  return next
}

/** Fill every effect seed with a single (override) value. */
function fillAllSeeds(seed: number): Record<string, number> {
  const seeds: Record<string, number> = {}
  for (const name of EFFECT_SEED_NAMES) seeds[name] = seed
  return seeds
}

/**
 * Starting state for the toolbar: options (carrying the last override
 * seed) plus the per-effect seed map the render uses.
 *
 * Priority for the seed map: persisted editor state (exact seeds of
 * the saved render) -> generation trace `effect_seeds` -> fresh random
 * per effect. Pre-rework records/traces carry a single global `seed`
 * instead; when present it fills every effect seed (reproducibility),
 * otherwise fresh random per-effect seeds are used.
 *
 * Traced images whose generation-time pass was disabled start from the
 * clean baseline (all flags false, blank override) instead of the
 * defaults, so the toolbar reflects that the image was generated
 * undistressed. Untraced documents (toolbar disabled anyway) also
 * start from the clean baseline, so no effect counters appear for
 * images that were never distressed.
 */
function initialEditorState(doc: DocumentRecord): {
  options: DistressOptions
  effectSeeds: Record<string, number>
} {
  const saved = savedDistress(doc)
  if (saved !== null) {
    const effectSeeds =
      saved.effectSeeds ??
      (saved.legacySeed !== null ? fillAllSeeds(saved.legacySeed) : randomEffectSeeds())
    return { options: normalizeJpegOff(saved.options), effectSeeds }
  }
  const trace = distressTrace(doc)
  if (trace === null) {
    return {
      options: { ...CLEAN_OPTIONS, enabled: true },
      effectSeeds: randomEffectSeeds(),
    }
  }
  const effectSeeds =
    typeof trace.effect_seeds === "object" && trace.effect_seeds !== null
      ? fillMissingSeeds(trace.effect_seeds as Record<string, number>)
      : typeof trace.seed === "number"
        ? fillAllSeeds(trace.seed)
        : randomEffectSeeds()
  const enabled = typeof trace.enabled === "boolean" ? trace.enabled : false
  if (!enabled) {
    return { options: { ...CLEAN_OPTIONS, enabled: true }, effectSeeds }
  }
  const raw = trace.options
  if (typeof raw !== "object" || raw === null) {
    return { options: { ...CLEAN_OPTIONS, enabled: true }, effectSeeds }
  }
  const o = raw as Partial<DistressOptions>
  const seed = typeof o.seed === "number" ? o.seed : null
  return {
    options: normalizeJpegOff(
      resolveIntensities(o, {
        ...CLEAN_OPTIONS,
        ...o,
        enabled: true,
        seed,
      }),
    ),
    effectSeeds,
  }
}

/**
 * Narrowed random ranges for effects whose full slider range would
 * produce extreme or useless results (overrides the slider min/max).
 */
const RANDOM_RANGES: Partial<Record<keyof DistressOptions, [number, number]>> = {
  stains: [1, 10],
  noise: [5, 30],
  vignette: [0.1, 0.8],
  warp: [0.1, 0.8],
  jpeg_artifacts: [10, 60],
  folding: [1, 3],
}

/** Random value for an effect, snapped to its step, in a sane range. */
function randomEffectValue(e: EffectDef): number {
  let [lo, hi] = RANDOM_RANGES[e.key] ?? [e.min + e.step, e.max]
  // 0-1 intensity effects stay clearly visible (above 0.2).
  if (e.intensityKey !== undefined) lo = Math.max(lo, 0.2)
  const raw = lo + Math.random() * (hi - lo)
  const snapped = Math.round(raw / e.step) * e.step
  return Math.min(hi, Math.max(lo, Number(snapped.toFixed(4))))
}

/**
 * Fresh random options: a clean baseline with a random subset of 3-6
 * effects (across all sections) at random severities. The caller's
 * seed choice (pinned or blank/random) is preserved.
 */
function randomizeOptions(seed: number | null): DistressOptions {
  const pool = [...INK_EFFECTS, ...PAPER_EFFECTS, ...POST_EFFECTS]
  // Fisher-Yates shuffle, take the first 3-6.
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  const picked = pool.slice(0, 3 + Math.floor(Math.random() * 4))
  const next: DistressOptions = { ...CLEAN_OPTIONS, enabled: true, seed }
  const rec = next as unknown as Record<string, unknown>
  for (const e of picked) {
    const value = randomEffectValue(e)
    if (e.intensityKey !== undefined) {
      rec[e.intensityKey] = value
      rec[e.key] = value > 0
    } else {
      rec[e.valueKey!] = value
      rec[e.key] = e.key === "jpeg_artifacts" ? value < 100 : value > 0
    }
  }
  return next
}

interface DistressToolbarProps {
  doc: DocumentRecord
  /** Called with a freshly rendered preview (server-side re-distress). */
  onPreview: (blob: Blob) => void
  /** Reports whether a preview request is in flight (spinner badge). */
  onBusyChange?: (busy: boolean) => void
  /** Called after a successful save with the refreshed record. */
  onSaved?: (doc: DocumentRecord) => void
}

/**
 * Live distress editor for a PNG document. Every control change
 * re-renders the stored pre-distress original server-side (debounced)
 * and pushes the result to the preview — the pass always starts from
 * the fresh clean render, never from a previously distressed image.
 * Save persists the current render over the document file. Fully
 * disabled when the document has no stored pre-distress original
 * (no trace).
 *
 * Effects are grouped into three collapsible sections (Ink / Paper /
 * Post) mirroring the augraphy pipeline phases. Every effect is a
 * single slider: the effect flag is derived from the slider value
 * (0 = off; JPEG quality: 100 = off).
 *
 * Rendering is driven by a per-effect seed map (one plain-number seed
 * per effect, always sent with preview/save requests). The seed input
 * is an explicit override: entering a number and pressing Apply copies
 * it into every effect's seed; leaving it blank restores the
 * per-effect internal seeds (the map first derived from the saved
 * state or the generation trace, or fresh random).
 */
export function DistressToolbar({
  doc,
  onPreview,
  onBusyChange,
  onSaved,
}: DistressToolbarProps) {
  const editable = originalImagePath(doc) !== null
  const [initial] = useState(() => initialEditorState(doc))
  const [options, setOptions] = useState<DistressOptions>(initial.options)
  /** Per-effect seeds the render uses (always sent to the server). */
  const [effectSeeds, setEffectSeeds] = useState<Record<string, number>>(
    initial.effectSeeds,
  )
  /** Override-seed input text (applied explicitly via the Apply button). */
  const [seedInput, setSeedInput] = useState(
    initial.options.seed === null ? "" : String(initial.options.seed),
  )
  /** Effect sections start collapsed; the active-effect badges still show counts. */
  const [openSections, setOpenSections] = useState<Record<SectionKey, boolean>>(
    () => ({ ink: false, paper: false, post: false }),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)
  /** Monotonic id so stale preview responses are dropped. */
  const requestRef = useRef(0)
  /**
   * The per-effect seeds as first derived (saved state, trace, or
   * fresh random). "Apply" with a blank override restores these.
   */
  const internalSeedsRef = useRef(initial.effectSeeds)
  /**
   * (options, effectSeeds) as last rendered (or shown on open). The
   * preview effect only fires when this pair changes, so opening the
   * dialog just displays the persisted image — no re-render, no
   * request (also StrictMode-safe: the effect's double invocation on
   * mount sees the unchanged pair and skips).
   */
  const lastRenderRef = useRef<{
    options: DistressOptions
    effectSeeds: Record<string, number>
  }>({
    options: initial.options,
    effectSeeds: initial.effectSeeds,
  })

  useEffect(() => {
    onBusyChange?.(busy)
  }, [busy, onBusyChange])

  // Live preview loop: debounce ~300 ms per control change, then ask the
  // server to re-distress the stored original with the current options
  // and per-effect seeds. Only fires when the (options, effectSeeds)
  // pair actually changes — on open (and on StrictMode's double effect
  // invocation) the dialog just shows the persisted (saved) render,
  // which matches the initial pair.
  useEffect(() => {
    if (!editable) return
    const last = lastRenderRef.current
    if (last.options === options && last.effectSeeds === effectSeeds) return
    lastRenderRef.current = { options, effectSeeds }
    const reqId = ++requestRef.current
    setBusy(true)
    setError(null)
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const blob = await api.distressPreview(doc.id, {
            distress: options,
            effect_seeds: effectSeeds,
          })
          if (requestRef.current === reqId) onPreview(blob)
        } catch (err) {
          if (requestRef.current === reqId) {
            setError(err instanceof Error ? err.message : String(err))
          }
        } finally {
          if (requestRef.current === reqId) setBusy(false)
        }
      })()
    }, 300)
    return () => clearTimeout(timer)
  }, [options, effectSeeds, editable, doc.id, onPreview]) // eslint-disable-line react-hooks/exhaustive-deps -- doc fields are stable per id

  const handleSave = async () => {
    setBusy(true)
    setError(null)
    try {
      const updated = await api.distressSave(doc.id, {
        distress: options,
        effect_seeds: effectSeeds,
      })
      setJustSaved(true)
      onSaved?.(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!justSaved) return
    const timer = setTimeout(() => setJustSaved(false), 1500)
    return () => clearTimeout(timer)
  }, [justSaved])

  /**
   * Apply the override seed: a number copies it into every effect's
   * seed; blank restores the per-effect internal seeds (and clears the
   * recorded override).
   */
  const handleApplySeed = () => {
    const raw = seedInput.trim()
    if (raw === "") {
      setOptions((o) => ({ ...o, seed: null }))
      setEffectSeeds({ ...internalSeedsRef.current })
      setSeedInput("")
      return
    }
    const value = Number.parseInt(raw, 10)
    if (Number.isNaN(value)) return
    setOptions((o) => ({ ...o, seed: value }))
    setEffectSeeds(fillAllSeeds(value))
    setSeedInput(String(value))
  }

  /** Write a slider value: set the intensity/numeric field and derive the flag. */
  const applyEffectValue = (e: EffectDef, value: number) => {
    setOptions((o) => {
      if (e.intensityKey !== undefined) {
        return {
          ...o,
          [e.intensityKey]: value,
          [e.key]: value > 0,
        } as DistressOptions
      }
      const flag = e.key === "jpeg_artifacts" ? value < 100 : value > 0
      return { ...o, [e.valueKey!]: value, [e.key]: flag } as DistressOptions
    })
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto border-r bg-muted/30 p-3">
      {!editable && (
        <p className="text-xs text-muted-foreground">
          No original render stored for this image — distress editing is
          unavailable. Generate a new PNG with tracing enabled to make it
          editable.
        </p>
      )}
      <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          <span
            className={
              "text-xs " +
              (editable ? "text-foreground" : "text-muted-foreground")
            }
          >
            Override seed (blank = per-effect random)
          </span>
          <div className="flex gap-1">
            <Input
              className="h-8"
              inputMode="numeric"
              placeholder="random"
              value={seedInput}
              disabled={!editable}
              onChange={(e) => setSeedInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  handleApplySeed()
                }
              }}
            />
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0"
              disabled={!editable || busy}
              onClick={handleApplySeed}
            >
              Apply
            </Button>
          </div>
        </div>
        {SECTIONS.map((section) => {
          const open = openSections[section.key]
          const count = activeCount(section.effects, options)
          return (
            <div key={section.key} className="flex flex-col gap-1">
              <button
                type="button"
                className="flex w-full items-center gap-1 text-xs font-medium uppercase tracking-wide text-muted-foreground"
                onClick={() =>
                  setOpenSections((cur) => ({
                    ...cur,
                    [section.key]: !cur[section.key],
                  }))
                }
              >
                {open ? (
                  <ChevronDown className="size-3.5" />
                ) : (
                  <ChevronRight className="size-3.5" />
                )}
                {section.label}
                {count > 0 && (
                  <Badge
                    variant="secondary"
                    className="ml-auto h-4 min-w-4 px-1 text-[10px]"
                  >
                    {count}
                  </Badge>
                )}
              </button>
              {open && (
                <div className="flex flex-col gap-2 pl-4">
                  {section.effects.map((e) => (
                    <div key={e.key} className="flex flex-col gap-1">
                      <div className="flex items-center justify-between gap-2 text-sm">
                        <span
                          className={
                            editable ? "text-foreground" : "text-muted-foreground"
                          }
                        >
                          {e.label}
                        </span>
                        <span className="flex items-center gap-1.5">
                          {hasSeed(e) && (
                            <button
                              type="button"
                              title={`Reseed ${e.label}`}
                              aria-label={`Reseed ${e.label}`}
                              className="text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-muted-foreground"
                              disabled={!editable || !isActive(e, options)}
                              onClick={() =>
                                setEffectSeeds((prev) => ({
                                  ...prev,
                                  [e.key]: randomSeed(),
                                }))
                              }
                            >
                              <RefreshCw className="size-3.5" />
                            </button>
                          )}
                          <span className="text-xs text-muted-foreground">
                            {e.fmt(effectValue(e, options))}
                          </span>
                        </span>
                      </div>
                      <Slider
                        value={[effectValue(e, options)]}
                        min={e.min}
                        max={e.max}
                        step={e.step}
                        disabled={!editable}
                        onValueChange={(v) => applyEffectValue(e, v[0])}
                      />
                    </div>
                  ))}
                  {section.key === "paper" && options.watermark && (
                    <div className="flex flex-col gap-1">
                      <span className="text-xs text-foreground">
                        Watermark word (empty = random)
                      </span>
                      <Input
                        value={options.watermark_word}
                        maxLength={40}
                        placeholder="random"
                        disabled={!editable}
                        onChange={(e) =>
                          setOptions((o) => ({
                            ...o,
                            watermark_word: e.target.value,
                          }))
                        }
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-2">
        {busy && (
          <Badge variant="secondary" className="gap-1.5">
            <LoaderCircle className="size-3.5 animate-spin" />
            Rendering…
          </Badge>
        )}
        {error && (
          <span className="flex items-center gap-1.5 text-xs text-destructive">
            {error}
            <Button
              variant="ghost"
              size="icon"
              className="size-5"
              aria-label="Dismiss error"
              onClick={() => setError(null)}
            >
              <X className="size-3" />
            </Button>
          </span>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={!editable || busy}
          onClick={() => setOptions(randomizeOptions(options.seed))}
        >
          <Dices />
          Randomize
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!editable || busy}
          onClick={() =>
            setOptions({ ...CLEAN_OPTIONS, enabled: true, seed: options.seed })
          }
        >
          <RotateCcw />
          Reset
        </Button>
        <Button
          size="sm"
          className="flex-1 justify-center"
          disabled={!editable || busy}
          onClick={() => void handleSave()}
        >
          {justSaved ? <Check /> : <Save />}
          {justSaved ? "Saved" : "Save"}
        </Button>
      </div>
    </div>
  )
}
