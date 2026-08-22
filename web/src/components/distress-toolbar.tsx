import { useEffect, useRef, useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  RotateCcw,
  Save,
  X,
} from "lucide-react"

import {
  api,
  originalImagePath,
  stainSeedFor,
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
 * at all, in which case the toolbar is disabled anyway.
 */
const DEFAULT_OPTIONS: DistressOptions = {
  enabled: true,
  backend: "augraphy",
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
  jpeg_quality: 50,
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
  jpeg_quality: 95,
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
    95,
    1,
    (v) => String(v),
  ),
  intensityEffect("double_exposure", "Double exposure"),
  valueEffect("folding", "Folding", "fold_count", 1, 6, 1, (v) => `${v} folds`),
  intensityEffect("bindings", "Bindings"),
  intensityEffect("markup", "Markup"),
  intensityEffect("scribbles", "Scribbles"),
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

/** Whether an effect is active (slider above its off value). */
function isActive(e: EffectDef, o: DistressOptions): boolean {
  if (e.key === "jpeg_artifacts") return o.jpeg_quality < 95
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
      ;(next as unknown as Record<string, unknown>)[key] = merged[flag] ? 1 : 0
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
 * the exact seeds of the saved render), or `null` when the image has
 * never been distressed and saved from the preview editor.
 */
function savedDistress(
  doc: DocumentRecord,
): { options: DistressOptions; seed: number; stainSeed: number } | null {
  const d = doc.distress
  if (typeof d !== "object" || d === null) return null
  if (typeof d.options !== "object" || d.options === null) return null
  if (typeof d.seed !== "number" || typeof d.stain_seed !== "number") return null
  const raw = d.options as Partial<DistressOptions>
  return {
    options: resolveIntensities(raw, {
      ...CLEAN_OPTIONS,
      ...raw,
      enabled: true,
    }),
    seed: d.seed,
    stainSeed: d.stain_seed,
  }
}

/**
 * Starting state for the toolbar. Images distressed and saved from the
 * preview editor load the persisted editor state (options + the exact
 * pipeline seed of the saved render), so the toolbar matches the
 * persisted image and moving one slider re-renders the rest
 * identically. Images with a generation trace that recorded distress
 * fall back to the trace's options (seed pinned to the one used at
 * generation). Options that predate the intensity fields get their
 * intensities derived from the flags (on -> 1, off -> 0). Everything
 * else starts from :const:`CLEAN_OPTIONS` — all flags false, values at
 * their off point, blank seed. `enabled` stays on so individual sliders
 * take effect immediately; untraced documents (toolbar disabled anyway)
 * fall back to :const:`DEFAULT_OPTIONS`.
 */
function initialOptions(doc: DocumentRecord): DistressOptions {
  const saved = savedDistress(doc)
  if (saved !== null) return { ...saved.options, seed: saved.seed }
  const trace = distressTrace(doc)
  if (trace === null) return DEFAULT_OPTIONS
  const enabled = typeof trace.enabled === "boolean" ? trace.enabled : false
  if (!enabled) return { ...CLEAN_OPTIONS, enabled: true }
  const raw = trace.options
  if (typeof raw !== "object" || raw === null) {
    return { ...CLEAN_OPTIONS, enabled: true }
  }
  const o = raw as Partial<DistressOptions>
  const seed =
    typeof o.seed === "number"
      ? o.seed
      : typeof trace.seed === "number"
        ? trace.seed
        : null
  return resolveIntensities(o, {
    ...CLEAN_OPTIONS,
    ...o,
    enabled: true,
    seed,
  })
}

/** Fresh non-negative random seed for blank-seed (random) mode. */
function randomSeed(): number {
  return Math.floor(Math.random() * 0x7fffffff)
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
 * (0 = off; JPEG quality: 95 = off) and the toolbar always renders
 * with the augraphy backend.
 */
export function DistressToolbar({
  doc,
  onPreview,
  onBusyChange,
  onSaved,
}: DistressToolbarProps) {
  const editable = originalImagePath(doc) !== null
  const [options, setOptions] = useState<DistressOptions>(() =>
    initialOptions(doc),
  )
  const [openSections, setOpenSections] = useState<
    Record<SectionKey, boolean>
  >(() => {
    const o = initialOptions(doc)
    return {
      ink: activeCount(INK_EFFECTS, o) > 0,
      paper: activeCount(PAPER_EFFECTS, o) > 0,
      post: activeCount(POST_EFFECTS, o) > 0,
    }
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)
  /** Monotonic id so stale preview responses are dropped. */
  const requestRef = useRef(0)
  /**
   * Options as last rendered (or shown on open). The preview effect only
   * fires when this changes, so opening the dialog just displays the
   * persisted image — no re-render (also StrictMode-safe: the effect's
   * double invocation on mount sees unchanged options and skips).
   */
  const lastOptionsRef = useRef(options)
  /**
   * Stain seed for deterministic renders: the persisted one when the
   * image was already distressed and saved (so re-renders match the
   * saved image), otherwise derived from the document id.
   */
  const stainSeedRef = useRef<number>(
    savedDistress(doc)?.stainSeed ?? stainSeedFor(doc.id),
  )
  /**
   * Ephemeral seeds for blank-seed (random) mode: regenerated on every
   * options change so each slider move gives a new random render, and
   * held stable afterwards so save persists exactly what the preview
   * showed.
   */
  const ephemeralSeedsRef = useRef<{ seed: number; stainSeed: number } | null>(
    null,
  )

  /**
   * Seeds for the next preview/save request. A user-entered seed is
   * deterministic (with the pinned stain seed); a blank seed uses the
   * ephemeral random pair (refreshed per options change).
   */
  const seedsFor = (options: DistressOptions): { seed: number; stainSeed: number } => {
    if (options.seed !== null) {
      return { seed: options.seed, stainSeed: stainSeedRef.current }
    }
    if (ephemeralSeedsRef.current === null) {
      ephemeralSeedsRef.current = { seed: randomSeed(), stainSeed: randomSeed() }
    }
    return ephemeralSeedsRef.current
  }

  useEffect(() => {
    onBusyChange?.(busy)
  }, [busy, onBusyChange])

  // Live preview loop: debounce ~300 ms per control change, then ask the
  // server to re-distress the stored original with the current options.
  // Only fires when the options actually change — on open (and on
  // StrictMode's double effect invocation) the dialog just shows the
  // persisted (saved) render, which matches the initial options.
  useEffect(() => {
    if (!editable) return
    if (options === lastOptionsRef.current) return
    lastOptionsRef.current = options
    if (options.seed === null) {
      ephemeralSeedsRef.current = { seed: randomSeed(), stainSeed: randomSeed() }
    }
    const reqId = ++requestRef.current
    setBusy(true)
    setError(null)
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const seeds = seedsFor(options)
          const blob = await api.distressPreview(doc.id, {
            distress: options,
            seed: seeds.seed,
            stain_seed: seeds.stainSeed,
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
  }, [options, editable, doc.id, onPreview]) // eslint-disable-line react-hooks/exhaustive-deps -- doc fields are stable per id

  const handleSave = async () => {
    setBusy(true)
    setError(null)
    try {
      const seeds = seedsFor(options)
      const updated = await api.distressSave(doc.id, {
        distress: options,
        seed: seeds.seed,
        stain_seed: seeds.stainSeed,
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
      const flag = e.key === "jpeg_artifacts" ? value < 95 : value > 0
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
            Seed (blank = random)
          </span>
          <Input
            className="h-8"
            inputMode="numeric"
            placeholder="random"
            value={options.seed === null ? "" : String(options.seed)}
            disabled={!editable}
            onChange={(e) => {
              const raw = e.target.value.trim()
              if (raw === "") {
                setOptions((o) => ({ ...o, seed: null }))
                return
              }
              const value = Number.parseInt(raw, 10)
              if (Number.isNaN(value)) return
              setOptions((o) => ({ ...o, seed: value }))
            }}
          />
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
                        <span className="text-xs text-muted-foreground">
                          {e.fmt(effectValue(e, options))}
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
          onClick={() => setOptions({ ...CLEAN_OPTIONS, enabled: true })}
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
