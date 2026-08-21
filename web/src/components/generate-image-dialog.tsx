import { type FormEvent, useEffect, useState } from "react"
import {
  AlertTriangle,
  Download,
  HelpCircle,
  Image as ImageIcon,
  LoaderCircle,
} from "lucide-react"

import {
  api,
  type CompanySummary,
  type FigureKind,
  type ImageJobResult,
  type DocumentTypeDoc,
} from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { JobStatus, useJobProgress } from "@/components/generate-dialogs"
import { truncateMiddle } from "@/lib/utils"

const FIGURE_KIND_OPTIONS: { kind: FigureKind; label: string }[] = [
  { kind: "bar", label: "Bar Chart" },
  { kind: "line", label: "Line Graph" },
  { kind: "area", label: "Area Graph" },
  { kind: "pie", label: "Pie Chart" },
  { kind: "scatter", label: "Scatter Plot" },
  { kind: "histogram", label: "Histogram" },
]

interface GenerateImageDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  company: CompanySummary
  docType: DocumentTypeDoc
  models: string[]
}

/**
 * Generate a single-page PNG image document for one document type:
 * optional free-text guidance + model override, A4 aspect ratio lock,
 * background job with live progress, and a download link when done.
 * Generated images are left undistressed; traced images can be
 * distressed from the stored clean original in the preview editor.
 */
export function GenerateImageDialog({
  open,
  onOpenChange,
  company,
  docType,
  models,
}: GenerateImageDialogProps) {
  const [userInput, setUserInput] = useState("")
  const [model, setModel] = useState("")
  const [genTrace, setGenTrace] = useState(false)
  const [a4Aspect, setA4Aspect] = useState(true)
  const [figureKinds, setFigureKinds] = useState<Record<FigureKind, boolean>>(
    () => Object.fromEntries(
      FIGURE_KIND_OPTIONS.map(({ kind }) => [kind, false])
    ) as Record<FigureKind, boolean>
  )
  const [submitError, setSubmitError] = useState<string | null>(null)
  const { state, start, reset } = useJobProgress<ImageJobResult>()

  // Reset the form and job state each time the dialog opens.
  useEffect(() => {
    if (open) {
      setUserInput("")
      setModel("")
      setGenTrace(false)
      setA4Aspect(true)
      setFigureKinds(
        Object.fromEntries(
          FIGURE_KIND_OPTIONS.map(({ kind }) => [kind, false])
        ) as Record<FigureKind, boolean>
      )
      setSubmitError(null)
      reset()
    }
  }, [open, docType, reset])

  const hasResult = state.finished && !state.error && !!state.result

  // Keep the dialog open while a generation job is running (blocks the X,
  // Escape, and outside-click dismissal).
  function handleOpenChange(next: boolean) {
    if (!next && state.running) return
    onOpenChange(next)
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (state.running) return
    setSubmitError(null)
    try {
      const job = await api.startDocumentImage(company.id, {
        report: docType.name,
        user_input: userInput.trim() === "" ? null : userInput.trim(),
        model: model || null,
        figure_kinds: FIGURE_KIND_OPTIONS.filter(({ kind }) => figureKinds[kind])
          .map(({ kind }) => kind),
        a4_aspect: a4Aspect,
        // No distress option in the modal: images are generated
        // undistressed; distress is applied later from the preview
        // editor (all settings off by default).
        gen_tracing: genTrace,
      })
      start(job.id, 1, () => {})
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Generate image document</DialogTitle>
          <DialogDescription className="mt-2">
            <span className="font-medium text-foreground">{docType.name}</span>{" "}
            for {company.name} — drafted by the LLM and rendered as a
            single-page PNG image.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            Additional instructions (optional)
            <textarea
              value={userInput}
              disabled={state.running}
              onChange={(e) => setUserInput(e.target.value)}
              rows={3}
              placeholder="e.g. Focus on Q3 segment performance and add a risk section for supply-chain exposure…"
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            Model
            <Select
              value={model}
              onValueChange={setModel}
              disabled={state.running}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Default" />
              </SelectTrigger>
              <SelectContent>
                {models.map((item) => (
                  <SelectItem key={item} value={item} title={item}>
                    {truncateMiddle(item)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <input
              type="checkbox"
              checked={a4Aspect}
              disabled={state.running}
              onChange={(e) => setA4Aspect(e.target.checked)}
            />
            <span className="flex items-center gap-1">
              A4 aspect ratio
              <span className="group relative inline-flex">
                <HelpCircle className="size-3.5 cursor-help text-muted-foreground" />
                <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 hidden w-64 -translate-x-1/2 rounded-md border bg-popover p-2 text-xs font-normal leading-snug text-popover-foreground shadow-md group-hover:block">
                  Lock the page to A4 portrait; unchecked lets the page size
                  itself to the content.
                </span>
              </span>
            </span>
          </label>
          <label
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <input
              type="checkbox"
              checked={genTrace}
              disabled={state.running}
              onChange={(e) => setGenTrace(e.target.checked)}
            />
            <span className="flex items-center gap-1">
              Generate Trace
              <span className="group relative inline-flex">
                <HelpCircle className="size-3.5 cursor-help text-muted-foreground" />
                <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 hidden w-64 -translate-x-1/2 rounded-md border bg-popover p-2 text-xs font-normal leading-snug text-popover-foreground shadow-md group-hover:block">
                  Saves the per-stage generation trace (LLM prompts, markdown,
                  figure specs, HTML, timings) on the document record. View it
                  later with the debug icon in the Documents tab.
                </span>
              </span>
            </span>
          </label>
          <fieldset
            disabled={state.running}
            className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground"
          >
            <legend className="mb-1">Figures (matplotlib, at most 1)</legend>
            {FIGURE_KIND_OPTIONS.map(({ kind, label }) => (
              <label key={kind} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={figureKinds[kind]}
                  onChange={(e) =>
                    setFigureKinds((prev) => ({ ...prev, [kind]: e.target.checked }))
                  }
                />
                {label}
              </label>
            ))}
          </fieldset>
          <JobStatus state={state} />
          {hasResult && state.result && (
            <a
              href={api.documentImageUrl(company.id, state.result.png)}
              download
              title={state.result.png}
              className="flex items-center gap-2 rounded-md border bg-secondary/40 px-3 py-2.5 text-sm text-primary transition-colors hover:bg-secondary/80"
            >
              <Download className="size-4 shrink-0" />
              Download {truncateMiddle(state.result.png)}
            </a>
          )}
          {submitError && (
            <p className="flex items-start gap-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {submitError}
            </p>
          )}
          <DialogFooter>
            <Button
              type="submit"
              disabled={state.running || hasResult}
            >
              {state.running ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <ImageIcon />
              )}
              {state.running ? "Generating…" : "Generate Image"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
