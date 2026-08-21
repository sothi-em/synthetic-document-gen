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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"

/**
 * Defaults mirroring `document_gen.models.distress.DistressOptions`
 * (with `enabled` on). Used only when the document has no generation
 * trace at all, in which case the toolbar is disabled anyway.
 */
const DEFAULT_OPTIONS: DistressOptions = {
  enabled: true,
  backend: "augraphy",
  paper_aging: true,
  vignette: true,
  vignette_strength: 0.3,
  stains: true,
  stain_count: 4,
  noise: true,
  noise_strength: 12,
  ink_fade: true,
  blur: true,
  warp: false,
  warp_strength: 0.5,
  seed: null,
  ink_bleed: false,
  bleed_through: false,
  letterpress: false,
  ink_mottling: false,
  ink_color_swap: false,
  hollow: false,
  dithering: false,
  dot_matrix: false,
  low_ink_periodic_lines: false,
  low_ink_random_lines: false,
  lines_degradation: false,
  noise_texturize: false,
  brightness_texturize: false,
  watermark: false,
  watermark_word: "CONFIDENTIAL",
  pattern_generator: false,
  voronoi_tessellation: false,
  delaunay_tessellation: false,
  paper_factory: false,
  bad_photo_copy: false,
  faxify: false,
  dirty_drum: false,
  dirty_rollers: false,
  dirty_screen: false,
  shadow_cast: false,
  lens_flare: false,
  reflected_light: false,
  brightness: false,
  gamma: false,
  color_shift: false,
  depth_blur: false,
  moire: false,
  lcd_pattern: false,
  jpeg_artifacts: false,
  jpeg_quality: 50,
  double_exposure: false,
  folding: false,
  fold_count: 2,
  bindings: false,
  markup: false,
  scribbles: false,
}

/**
 * Starting state for traced images generated without distress: no
 * effect flag is set and every value is 0, so the render starts from
 * a clean look. Options that were not applied at generation are never
 * preset.
 */
const CLEAN_OPTIONS: DistressOptions = {
  ...DEFAULT_OPTIONS,
  enabled: false,
  paper_aging: false,
  vignette: false,
  vignette_strength: 0,
  stains: false,
  stain_count: 0,
  noise: false,
  noise_strength: 0,
  ink_fade: false,
  blur: false,
  warp: false,
  warp_strength: 0,
  watermark_word: "",
}

type SectionKey = "ink" | "paper" | "post"

interface EffectDef {
  key: keyof DistressOptions
  label: string
  /** Only effective on the augraphy backend (no-op on legacy). */
  augraphyOnly?: boolean
}

const INK_EFFECTS: EffectDef[] = [
  { key: "ink_fade", label: "Ink fade" },
  { key: "ink_bleed", label: "Ink bleed", augraphyOnly: true },
  { key: "bleed_through", label: "Bleed through", augraphyOnly: true },
  { key: "letterpress", label: "Letterpress", augraphyOnly: true },
  { key: "ink_mottling", label: "Ink mottling", augraphyOnly: true },
  { key: "ink_color_swap", label: "Ink color swap", augraphyOnly: true },
  { key: "hollow", label: "Hollow strokes", augraphyOnly: true },
  { key: "dithering", label: "Dithering", augraphyOnly: true },
  { key: "dot_matrix", label: "Dot matrix", augraphyOnly: true },
  { key: "low_ink_periodic_lines", label: "Low ink (periodic)", augraphyOnly: true },
  { key: "low_ink_random_lines", label: "Low ink (random)", augraphyOnly: true },
  { key: "lines_degradation", label: "Line degradation", augraphyOnly: true },
]

const PAPER_EFFECTS: EffectDef[] = [
  { key: "paper_aging", label: "Paper aging" },
  { key: "stains", label: "Stains" },
  { key: "noise", label: "Noise" },
  { key: "noise_texturize", label: "Noise texturize", augraphyOnly: true },
  {
    key: "brightness_texturize",
    label: "Brightness texturize",
    augraphyOnly: true,
  },
  { key: "watermark", label: "Watermark", augraphyOnly: true },
  { key: "pattern_generator", label: "Pattern", augraphyOnly: true },
  { key: "voronoi_tessellation", label: "Voronoi texture", augraphyOnly: true },
  { key: "delaunay_tessellation", label: "Delaunay texture", augraphyOnly: true },
  { key: "paper_factory", label: "Paper texture", augraphyOnly: true },
]

const POST_EFFECTS: EffectDef[] = [
  { key: "vignette", label: "Vignette" },
  { key: "blur", label: "Blur" },
  { key: "warp", label: "Warp" },
  { key: "bad_photo_copy", label: "Bad photo copy", augraphyOnly: true },
  { key: "faxify", label: "Faxify", augraphyOnly: true },
  { key: "dirty_drum", label: "Dirty drum", augraphyOnly: true },
  { key: "dirty_rollers", label: "Dirty rollers", augraphyOnly: true },
  { key: "dirty_screen", label: "Dirty screen", augraphyOnly: true },
  { key: "shadow_cast", label: "Shadow cast", augraphyOnly: true },
  { key: "lens_flare", label: "Lens flare", augraphyOnly: true },
  { key: "reflected_light", label: "Reflected light", augraphyOnly: true },
  { key: "brightness", label: "Brightness", augraphyOnly: true },
  { key: "gamma", label: "Gamma", augraphyOnly: true },
  { key: "color_shift", label: "Color shift", augraphyOnly: true },
  { key: "depth_blur", label: "Depth blur", augraphyOnly: true },
  { key: "moire", label: "Moire", augraphyOnly: true },
  { key: "lcd_pattern", label: "LCD pattern", augraphyOnly: true },
  { key: "jpeg_artifacts", label: "JPEG artifacts", augraphyOnly: true },
  { key: "double_exposure", label: "Double exposure", augraphyOnly: true },
  { key: "folding", label: "Folding", augraphyOnly: true },
  { key: "bindings", label: "Bindings", augraphyOnly: true },
  { key: "markup", label: "Markup", augraphyOnly: true },
  { key: "scribbles", label: "Scribbles", augraphyOnly: true },
]

const SECTIONS: { key: SectionKey; label: string; effects: EffectDef[] }[] = [
  { key: "ink", label: "Ink", effects: INK_EFFECTS },
  { key: "paper", label: "Paper", effects: PAPER_EFFECTS },
  { key: "post", label: "Post", effects: POST_EFFECTS },
]

interface SliderDef {
  key: keyof DistressOptions
  label: string
  min: number
  max: number
  step: number
  /** Slider is disabled when the parent effect switch is off. */
  off: (o: DistressOptions) => boolean
  fmt: (v: number) => string
}

const SLIDERS: Record<SectionKey, SliderDef[]> = {
  ink: [],
  paper: [
    {
      key: "stain_count",
      label: "Stain intensity",
      min: 0,
      max: 20,
      step: 1,
      off: (o) => !o.stains,
      fmt: (v) => String(v),
    },
    {
      key: "noise_strength",
      label: "Noise strength",
      min: 0,
      max: 50,
      step: 1,
      off: (o) => !o.noise,
      fmt: (v) => String(v),
    },
  ],
  post: [
    {
      key: "vignette_strength",
      label: "Vignette strength",
      min: 0,
      max: 1,
      step: 0.05,
      off: (o) => !o.vignette,
      fmt: (v) => v.toFixed(2),
    },
    {
      key: "warp_strength",
      label: "Warp strength",
      min: 0,
      max: 1,
      step: 0.05,
      off: (o) => !o.warp,
      fmt: (v) => v.toFixed(2),
    },
    {
      key: "jpeg_quality",
      label: "JPEG quality",
      min: 10,
      max: 95,
      step: 1,
      off: (o) => !o.jpeg_artifacts,
      fmt: (v) => String(v),
    },
    {
      key: "fold_count",
      label: "Fold count",
      min: 1,
      max: 6,
      step: 1,
      off: (o) => !o.folding,
      fmt: (v) => String(v),
    },
  ],
}

/** Number of active (on) effect toggles in a section. */
function activeCount(effects: EffectDef[], options: DistressOptions): number {
  return effects.filter((e) => Boolean(options[e.key])).length
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
  return {
    options: {
      ...CLEAN_OPTIONS,
      ...(d.options as Partial<DistressOptions>),
      enabled: true,
    },
    seed: d.seed,
    stainSeed: d.stain_seed,
  }
}

/**
 * Starting state for the toolbar. Images distressed and saved from the
 * preview editor load the persisted editor state (options + the exact
 * pipeline seed of the saved render), so the toolbar matches the
 * persisted image and toggling one effect re-renders the rest
 * identically. Images with a generation trace that recorded distress
 * fall back to the trace's options (seed pinned to the one used at
 * generation). Everything else starts from :const:`CLEAN_OPTIONS` —
 * all flags false, values 0, blank seed. `enabled` stays on so
 * individual effect toggles take effect immediately; untraced
 * documents (toolbar disabled anyway) fall back to
 * :const:`DEFAULT_OPTIONS`.
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
  return {
    ...CLEAN_OPTIONS,
    ...o,
    enabled: true,
    seed,
  }
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
 * Post) mirroring the augraphy pipeline phases; legacy effects fold
 * into their phase. The backend select switches between the augraphy pipeline
 * (default) and the preserved legacy stages; augraphy-only effects are
 * no-ops on the legacy backend and render disabled there.
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
   * options change so each toggle gives a new random render, and held
   * stable afterwards so save persists exactly what the preview showed.
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

  const legacy = options.backend === "legacy"

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
            Backend
          </span>
          <Select
            value={options.backend}
            disabled={!editable}
            onValueChange={(value) =>
              setOptions((o) => ({
                ...o,
                backend: value as DistressOptions["backend"],
              }))
            }
          >
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="augraphy">Augraphy</SelectItem>
              <SelectItem value="legacy">Legacy</SelectItem>
            </SelectContent>
          </Select>
          {legacy && (
            <p className="text-xs text-muted-foreground">
              Legacy backend: augraphy-only effects are disabled
              (no-ops).
            </p>
          )}
        </div>
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
                  {section.effects.map(({ key, label, augraphyOnly }) => {
                    const disabled =
                      !editable || (augraphyOnly !== undefined && legacy)
                    return (
                      <label
                        key={key}
                        className="flex items-center justify-between gap-2 text-sm"
                      >
                        <span
                          className={
                            disabled ? "text-muted-foreground" : ""
                          }
                        >
                          {label}
                        </span>
                        <Switch
                          checked={Boolean(options[key])}
                          disabled={disabled}
                          onCheckedChange={(checked) =>
                            setOptions((o) => ({ ...o, [key]: checked }))
                          }
                        />
                      </label>
                    )
                  })}
                  {SLIDERS[section.key].map(
                    ({ key, label, min, max, step, off, fmt }) => (
                      <div key={key} className="flex flex-col gap-1">
                        <span
                          className={
                            "text-xs " +
                            (editable && !off(options)
                              ? "text-foreground"
                              : "text-muted-foreground")
                          }
                        >
                          {label}: {fmt(Number(options[key]))}
                        </span>
                        <Slider
                          value={[Number(options[key])]}
                          min={min}
                          max={max}
                          step={step}
                          disabled={!editable || off(options)}
                          onValueChange={(v) =>
                            setOptions((o) => ({ ...o, [key]: v[0] }))
                          }
                        />
                      </div>
                    ),
                  )}
                  {section.key === "paper" &&
                    options.watermark &&
                    !legacy && (
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
