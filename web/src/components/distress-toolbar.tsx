import { useEffect, useRef, useState } from "react"
import { Check, LoaderCircle, Save, X } from "lucide-react"

import {
  api,
  originalImagePath,
  stainSeedFor,
  type DistressOptions,
  type DocumentRecord,
} from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"

/**
 * Defaults mirror `document_gen.models.distress.DistressOptions` (with
 * `enabled` on, since the toolbar only renders for distressed images).
 */
const DEFAULT_OPTIONS: DistressOptions = {
  enabled: true,
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
}

const SWITCHES: { key: keyof DistressOptions; label: string }[] = [
  { key: "paper_aging", label: "Paper aging" },
  { key: "vignette", label: "Vignette" },
  { key: "stains", label: "Stains" },
  { key: "noise", label: "Noise" },
  { key: "ink_fade", label: "Ink fade" },
  { key: "blur", label: "Blur" },
  { key: "warp", label: "Warp" },
]

const SLIDERS: {
  key: keyof DistressOptions
  label: string
  min: number
  max: number
  step: number
  /** Slider is disabled when the parent effect switch is off. */
  off: (o: DistressOptions) => boolean
  fmt: (v: number) => string
}[] = [
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
    key: "stain_count",
    label: "Stain count",
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
  {
    key: "warp_strength",
    label: "Warp strength",
    min: 0,
    max: 1,
    step: 0.05,
    off: (o) => !o.warp,
    fmt: (v) => v.toFixed(2),
  },
]

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

/** Options from the generation trace, falling back to defaults. */
function initialOptions(doc: DocumentRecord): DistressOptions {
  const raw = distressTrace(doc)?.options
  if (typeof raw !== "object" || raw === null) return DEFAULT_OPTIONS
  const o = raw as Partial<DistressOptions>
  return {
    ...DEFAULT_OPTIONS,
    ...o,
    // The toolbar only makes sense on a distressed image.
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)
  /** Monotonic id so stale preview responses are dropped. */
  const requestRef = useRef(0)
  const firstRunRef = useRef(true)

  useEffect(() => {
    onBusyChange?.(busy)
  }, [busy, onBusyChange])

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

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto border-r bg-muted/30 p-3">
      {!editable && (
        <p className="text-xs text-muted-foreground">
          No generation trace stored for this image — distress editing is
          unavailable.
        </p>
      )}
      <div className="flex flex-col gap-2">
        {SWITCHES.map(({ key, label }) => (
          <label
            key={key}
            className="flex items-center justify-between gap-2 text-sm"
          >
            <span className={editable ? "" : "text-muted-foreground"}>
              {label}
            </span>
            <Switch
              checked={Boolean(options[key])}
              disabled={!editable}
              onCheckedChange={(checked) =>
                setOptions((o) => ({ ...o, [key]: checked }))
              }
            />
          </label>
        ))}
        {SLIDERS.map(({ key, label, min, max, step, off, fmt }) => (
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
        ))}
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
          size="sm"
          className="w-full justify-center"
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
