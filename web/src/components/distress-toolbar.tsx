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
 * effect flag is set and every value is 0, so turning the Distress
 * toggle on starts from a clean render. Options that were not applied
 * at generation are never preset.
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
 * Options exactly as recorded in the generation trace, so the toolbar
 * presets match the persisted render. Traced images generated without
 * distress start from :const:`CLEAN_OPTIONS` (all flags false, values
 * 0) even though the trace stores the unused per-effect defaults; only
 * untraced documents fall back to :const:`DEFAULT_OPTIONS`. Old traces
 * without the new fields (or a `backend` key) pick up the defaults
 * (new effects off, backend "augraphy").
 */
function initialOptions(doc: DocumentRecord): DistressOptions {
  const trace = distressTrace(doc)
  if (trace === null) return DEFAULT_OPTIONS
  const enabled = typeof trace.enabled === "boolean" ? trace.enabled : false
  if (!enabled) return CLEAN_OPTIONS
  const raw = trace.options
  if (typeof raw !== "object" || raw === null) {
    return { ...CLEAN_OPTIONS, enabled: true }
  }
  const o = raw as Partial<DistressOptions>
  return {
    ...CLEAN_OPTIONS,
    ...o,
    enabled: true,
    seed: typeof o.seed === "number" ? o.seed : null,
  }
}

/** Noise/warp seed pinned at generation time (0 when absent). */
function traceSeed(doc: DocumentRecord): number {
  const seed = distressTrace(doc)?.seed
  return typeof seed === "number" ? seed : 0
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
 * re-renders the stored original server-side (debounced) and pushes the
 * result to the preview. Save persists the current render over the
 * document file. Fully disabled when the document has no stored
 * pre-distress original (no trace, or distress was off at generation).
 *
 * Effects are grouped into three collapsible sections (Ink / Paper /
 * Post) mirroring the augraphy pipeline phases; legacy effects fold
 * into their phase. Sections auto-collapse when all their toggles are
 * off. The backend select switches between the augraphy pipeline
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
  const firstRunRef = useRef(true)

  useEffect(() => {
    onBusyChange?.(busy)
  }, [busy, onBusyChange])

  // Auto-collapse a section when its last effect toggle is switched off.
  useEffect(() => {
    setOpenSections((cur) => {
      let changed = false
      const next = { ...cur }
      for (const section of SECTIONS) {
        if (
          next[section.key] &&
          activeCount(section.effects, options) === 0
        ) {
          next[section.key] = false
          changed = true
        }
      }
      return changed ? next : cur
    })
  }, [options])

  // Live preview loop: debounce ~300 ms per control change, then ask the
  // server to re-distress the stored original with the current options.
  // The first run is skipped — the dialog already shows the persisted
  // (saved) render, which matches the initial options.
  useEffect(() => {
    if (!editable) return
    if (firstRunRef.current) {
      firstRunRef.current = false
      return
    }
    const reqId = ++requestRef.current
    setBusy(true)
    setError(null)
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const blob = await api.distressPreview(doc.id, {
            distress: options,
            seed: traceSeed(doc),
            stain_seed: stainSeedFor(doc.id),
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
      const updated = await api.distressSave(doc.id, {
        distress: options,
        seed: traceSeed(doc),
        stain_seed: stainSeedFor(doc.id),
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
        <label className="flex items-center justify-between gap-2 text-sm">
          <span className={editable ? "" : "text-muted-foreground"}>
            Distress
          </span>
          <Switch
            checked={options.enabled}
            disabled={!editable}
            onCheckedChange={(checked) =>
              setOptions((o) => ({ ...o, enabled: checked }))
            }
          />
        </label>
        <div className="flex flex-col gap-1">
          <span
            className={
              "text-xs " +
              (editable && options.enabled
                ? "text-foreground"
                : "text-muted-foreground")
            }
          >
            Backend
          </span>
          <Select
            value={options.backend}
            disabled={!editable || !options.enabled}
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
          {legacy && options.enabled && (
            <p className="text-xs text-muted-foreground">
              Legacy backend: augraphy-only effects are disabled
              (no-ops).
            </p>
          )}
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
                      !editable ||
                      !options.enabled ||
                      (augraphyOnly !== undefined && legacy)
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
                            (editable &&
                            options.enabled &&
                            !off(options)
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
                          disabled={!editable || !options.enabled || off(options)}
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
                          disabled={!editable || !options.enabled}
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
