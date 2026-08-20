import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  AlertTriangle,
  CircleCheck,
  FileText,
  LoaderCircle,
  RefreshCw,
  Sparkles,
} from "lucide-react"

import {
  api,
  type CompanySummary,
  type GeneratedCompany,
  type JobEvent,
  type DocumentType,
} from "@/lib/api"
import { cn, truncateMiddle } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// ---------------------------------------------------------------------------
// Shared job-progress plumbing (SSE subscription for a background job)
// ---------------------------------------------------------------------------

interface JobState<T = unknown> {
  running: boolean
  completed: number
  total: number
  error: string | null
  finished: boolean
  /** Payload published by the job when it finishes (e.g. generated document types). */
  result: T | null
  /** Backend log lines for the job (oldest first, bounded by the server). */
  logs: string[]
}

const IDLE_JOB: JobState<never> = {
  running: false,
  completed: 0,
  total: 0,
  error: null,
  finished: false,
  result: null,
  logs: [],
}

/** Subscribe to a background job's SSE stream and track its progress.
 *
 * @typeParam T - Type of the job's result payload (e.g. `DocumentType[]` or
 *   `PdfJobResult`).
 */
export function useJobProgress<T = unknown>() {
  const [state, setState] = useState<JobState<T>>(IDLE_JOB)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => () => sourceRef.current?.close(), [])

  // Stable identities: callers use these in effect dependency arrays, so a
  // new function per render would re-fire those effects (e.g. clearing the
  // form on every keystroke).
  const start = useCallback((jobId: string, total: number, onDone: () => void) => {
    setState({
      running: true,
      completed: 0,
      total,
      error: null,
      finished: false,
      result: null,
      logs: [],
    })
    const source = new EventSource(`/api/companies/jobs/${jobId}/events`)
    sourceRef.current = source
    source.onmessage = (event) => {
      const data: JobEvent = JSON.parse(event.data)
      setState((s) => ({
        ...s,
        completed: data.completed,
        total: data.total,
        error: data.error,
        logs: data.logs ?? [],
      }))
      if (data.status !== "running") {
        source.close()
        sourceRef.current = null
        setState((s) => ({
          ...s,
          running: false,
          finished: true,
          error: data.error,
          result: data.result as T | null,
        }))
        if (data.status === "done") onDone()
      }
    }
    source.onerror = () => {
      source.close()
      sourceRef.current = null
      setState((s) => ({ ...s, running: false }))
    }
  }, [])

  const reset = useCallback(() => {
    setState(IDLE_JOB)
  }, [])

  return { state, start, reset }
}

/** Latest backend log line for a running job.
 *
 * The current line pulses to signal that work is ongoing. When a new
 * line arrives, the previous one briefly solidifies to green before the
 * new line fades in. Text is clamped to one line (long tokens wrap) so
 * log lines can never stretch the dialog.
 */
function JobLogLine({ text, className }: { text: string | null; className?: string }) {
  const [shown, setShown] = useState<{ text: string; settled: boolean } | null>(null)
  const prevRef = useRef<string | null>(null)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (text === null) {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      prevRef.current = null
      setShown(null)
      return
    }
    if (text === prevRef.current) return
    const previous = prevRef.current
    prevRef.current = text
    if (previous === null) {
      setShown({ text, settled: false })
      return
    }
    // A new line arrived: solidify the previous one to green, then
    // reveal the new line (pulsing) after a short beat.
    setShown({ text: previous, settled: true })
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      setShown({ text, settled: false })
    }, 700)
  }, [text])

  // Clear any pending reveal if the component unmounts first.
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    },
    [],
  )

  if (shown === null) return null
  return (
    <p
      className={cn(
        "min-w-0 max-w-full text-xs transition-colors duration-500",
        shown.settled
          ? "text-success"
          : "animate-pulse text-muted-foreground/70",
        className,
      )}
      title={shown.text}
    >
      <span
        key={shown.text}
        className="line-clamp-1 animate-in break-words fade-in-0 duration-300"
      >
        {shown.text}
      </span>
    </p>
  )
}

/** Progress bar + completion/error status line for a running job.
 *
 * While running, the most recent backend log line is shown as small
 * subtext under the progress indicator (the current pipeline stage).
 */
export function JobStatus({ state }: { state: JobState<unknown> }) {
  const pct =
    state.total > 0 ? Math.round((100 * state.completed) / state.total) : 0
  const lastLog =
    state.running && state.logs.length > 0
      ? state.logs[state.logs.length - 1]
      : null
  return (
    <div className="flex flex-col gap-2">
      {state.running &&
        (state.total > 1 ? (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-3">
              <Progress value={pct} className="flex-1" />
              <span className="text-sm tabular-nums text-muted-foreground">
                {state.completed}/{state.total}
              </span>
            </div>
            <JobLogLine text={lastLog} />
          </div>
        ) : (
          <div className="flex flex-col gap-1 rounded-md border bg-secondary/40 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <LoaderCircle className="size-4 shrink-0 animate-spin text-primary" />
              <span className="text-sm text-muted-foreground">
                Generating… the agent is working on this, it can take a minute.
              </span>
            </div>
            <JobLogLine text={lastLog} className="pl-6" />
          </div>
        ))}
      {state.finished && !state.error && (
        <p className="flex items-center gap-2 text-sm text-success">
          <CircleCheck className="size-4 shrink-0" />
          Done.
        </p>
      )}
      {state.error && (
        <p className="flex items-start gap-2 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {state.error}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Generate companies dialog
// ---------------------------------------------------------------------------

interface GenerateCompaniesDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  industries: string[]
  models: string[]
  /** Called when a run finishes successfully (to refresh data). */
  onGenerated: () => void
}

export function GenerateCompaniesDialog({
  open,
  onOpenChange,
  industries,
  models,
  onGenerated,
}: GenerateCompaniesDialogProps) {
  const [num, setNum] = useState("3")
  const [industry, setIndustry] = useState("")
  const [model, setModel] = useState("")
  const [instruction, setInstruction] = useState("")
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(false)
  /** Indices (into state.result) the user chose to persist. */
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [confirmClose, setConfirmClose] = useState(false)
  const closeTimer = useRef<number | null>(null)
  const { state, start, reset } = useJobProgress<GeneratedCompany[]>()

  // Reset the form and job state each time the dialog opens.
  useEffect(() => {
    if (open) {
      setNum("3")
      setIndustry("")
      setModel("")
      setInstruction("")
      setSelected(new Set())
      setSubmitError(null)
      setAdding(false)
      setAdded(false)
      setConfirmClose(false)
      if (closeTimer.current !== null) {
        window.clearTimeout(closeTimer.current)
        closeTimer.current = null
      }
      reset()
    }
  }, [open, reset])

  // Clear any pending auto-close if the component unmounts first.
  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    },
    [],
  )

  const hasResult = state.finished && !state.error && !!state.result

  function toggleSelected(index: number) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  function toggleAll() {
    setSelected((current) =>
      current.size === (state.result?.length ?? 0)
        ? new Set()
        : new Set((state.result ?? []).map((_, index) => index)),
    )
  }

  /** Intercept closes: block while generating; confirm first when generated
   * companies haven't been added. */
  function handleOpenChange(next: boolean) {
    if (state.running && !next) return
    if (next || !hasResult || added) {
      onOpenChange(next)
      return
    }
    setConfirmClose(true)
  }

  // Memoized so typing in the textarea does not re-create every option
  // element on each keystroke.
  const industryOptions = useMemo(
    () =>
      industries.map((item) => (
        <SelectItem key={item} value={item}>
          {item}
        </SelectItem>
      )),
    [industries],
  )
  const modelOptions = useMemo(
    () =>
      models.map((item) => (
        <SelectItem key={item} value={item} title={item}>
          {truncateMiddle(item)}
        </SelectItem>
      )),
    [models],
  )

  async function startGeneration() {
    if (state.running) return
    setSubmitError(null)
    setAdded(false)
    setSelected(new Set())
    try {
      const job = await api.startGeneration({
        num: Number(num),
        industry: industry || null,
        model: model || null,
        user_input: instruction.trim() || null,
      })
      start(job.id, Number(num), () => {})
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error))
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    await startGeneration()
  }

  async function handleAdd() {
    if (!state.result || adding || added || selected.size === 0) return
    setAdding(true)
    setSubmitError(null)
    const chosen = state.result.filter((_, index) => selected.has(index))
    try {
      await api.saveCompanies(chosen)
      setAdded(true)
      onGenerated()
      // Briefly show the success message, then close the dialog.
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
      closeTimer.current = window.setTimeout(() => {
        closeTimer.current = null
        onOpenChange(false)
      }, 1200)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error))
    } finally {
      setAdding(false)
    }
  }

  return (
    <>
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        // Dismissal only via the X or the Close button (which both route
        // through handleOpenChange so the discard prompt can intercept).
        onInteractOutside={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Generate companies</DialogTitle>
          <DialogDescription>
            Companies are generated in the background with live progress.
            Review the results and pick which ones to keep — nothing is
            stored until you add them.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            Instructions (optional)
            <textarea
              value={instruction}
              disabled={state.running}
              onChange={(e) => setInstruction(e.target.value)}
              rows={3}
              placeholder="e.g. A renewable-energy startup focused on solar storage for rural clinics, founded in the last five years…"
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
            <span className="text-xs text-muted-foreground">
              Free-text guidance applied to every generated company.
            </span>
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              Number of companies
              <Input
                type="number"
                min={1}
                max={200}
                value={num}
                disabled={state.running}
                onChange={(e) => setNum(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              Industry
              <Select
                value={industry}
                onValueChange={setIndustry}
                disabled={state.running}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Random" />
                </SelectTrigger>
                <SelectContent>{industryOptions}</SelectContent>
              </Select>
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
                <SelectContent>{modelOptions}</SelectContent>
              </Select>
            </label>
          </div>
          <JobStatus state={state} />
          {hasResult && state.result && (
            <div className="flex max-h-64 flex-col gap-2 overflow-y-auto rounded-lg border bg-secondary/30 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Generated {state.result.length} compan
                  {state.result.length === 1 ? "y" : "ies"} — {selected.size} selected.
                  Pick which to keep, or regenerate.
                </p>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="shrink-0 text-xs"
                  disabled={adding}
                  onClick={toggleAll}
                >
                  {selected.size === state.result.length
                    ? "Deselect all"
                    : "Select all"}
                </Button>
              </div>
              <ul className="flex flex-col gap-1">
                {state.result.map((company, index) => (
                  <li key={`${company.seed}-${index}`}>
                    <label className="flex cursor-pointer items-start gap-2.5 rounded-md p-1.5 transition-colors hover:bg-secondary/60">
                      <input
                        type="checkbox"
                        className="mt-0.5 size-4 shrink-0 accent-ring"
                        checked={selected.has(index)}
                        disabled={adding}
                        onChange={() => toggleSelected(index)}
                      />
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="text-sm">
                          <span className="font-medium">
                            {company.profile?.name ?? "(unnamed)"}
                          </span>
                          <span className="ml-2 text-xs text-muted-foreground">
                            {company.profile?.industry ?? "—"} ·{" "}
                            {company.profile?.headquarters ?? "—"} ·{" "}
                            {company.profile?.size ?? "—"}
                          </span>
                        </span>
                        {company.profile?.description && (
                          <span className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                            {company.profile.description}
                          </span>
                        )}
                        <span className="text-xs text-muted-foreground">
                          {company.reports.length} document type
                          {company.reports.length === 1 ? "" : "s"}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {added && (
            <p className="flex items-center gap-2 text-sm text-success">
              <CircleCheck className="size-4 shrink-0" />
              Added {selected.size} compan
              {selected.size === 1 ? "y" : "ies"} to the company store.
            </p>
          )}
          {submitError && (
            <p className="flex items-start gap-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {submitError}
            </p>
          )}
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Close
            </Button>
            {hasResult && !added && (
              <Button
                type="button"
                variant="outline"
                disabled={state.running || adding}
                onClick={() => void startGeneration()}
              >
                <RefreshCw />
                Regenerate
              </Button>
            )}
            {hasResult && !added ? (
              <Button
                type="button"
                disabled={state.running || adding || selected.size === 0}
                onClick={() => void handleAdd()}
              >
                {adding ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <CircleCheck />
                )}
                {adding
                  ? "Adding…"
                  : `Add ${selected.size} compan${selected.size === 1 ? "y" : "ies"}`}
              </Button>
            ) : (
              <Button type="submit" disabled={state.running || added}>
                {state.running ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <Sparkles />
                )}
                {state.running
                  ? "Generating…"
                  : added
                    ? "Added"
                    : hasResult
                      ? "Regenerate"
                      : "Generate"}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={confirmClose} onOpenChange={setConfirmClose}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Discard generated companies?</DialogTitle>
          <DialogDescription>
            {state.result?.length} compan
            {state.result?.length === 1 ? "y" : "ies"} have been generated but
            not added to the store yet. Closing will discard them.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => setConfirmClose(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              setConfirmClose(false)
              onOpenChange(false)
            }}
          >
            Discard
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// Generate document types dialog (document types for one existing company)
// ---------------------------------------------------------------------------

interface GenerateDocumentTypesDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Company pre-selected by the caller (e.g. the Document types tab selection). */
  company: CompanySummary | null
  /** All companies, used by the picker when nothing is pre-selected. */
  companies: CompanySummary[]
  models: string[]
  /** Called when generation finishes successfully (to refresh data). */
  onGenerated: () => void
}

const MIN_REQUEST_LENGTH = 20

export function GenerateDocumentTypesDialog({
  open,
  onOpenChange,
  company,
  companies,
  models,
  onGenerated,
}: GenerateDocumentTypesDialogProps) {
  const [companyId, setCompanyId] = useState("")
  const [num, setNum] = useState("5")
  const [model, setModel] = useState("")
  const [documentRequest, setDocumentRequest] = useState("")
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(false)
  /** Indices (into state.result) the user chose to append. */
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [confirmClose, setConfirmClose] = useState(false)
  const closeTimer = useRef<number | null>(null)
  const { state, start, reset } = useJobProgress<DocumentType[]>()

  // Reset the form and job state each time the dialog opens.
  useEffect(() => {
    if (open) {
      setCompanyId(company ? String(company.id) : "")
      setNum("5")
      setModel("")
      setDocumentRequest("")
      setSelected(new Set())
      setSubmitError(null)
      setAdding(false)
      setAdded(false)
      setConfirmClose(false)
      if (closeTimer.current !== null) {
        window.clearTimeout(closeTimer.current)
        closeTimer.current = null
      }
      reset()
    }
  }, [open, company, reset])

  // Clear any pending auto-close if the component unmounts first.
  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    },
    [],
  )

  const hasResult = state.finished && !state.error && !!state.result
  const requestLength = documentRequest.trim().length

  function toggleSelected(index: number) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  /** Intercept closes: block while generating; confirm first when generated
   * document types haven't been added. */
  function handleOpenChange(next: boolean) {
    if (state.running && !next) return
    if (next || !hasResult || added) {
      onOpenChange(next)
      return
    }
    setConfirmClose(true)
  }

  // Memoized so typing in the textarea does not re-create every option
  // element on each keystroke.
  const companyOptions = useMemo(
    () =>
      companies.map((item) => (
        <SelectItem key={item.id} value={String(item.id)}>
          {item.name}
        </SelectItem>
      )),
    [companies],
  )
  const modelOptions = useMemo(
    () =>
      models.map((item) => (
        <SelectItem key={item} value={item} title={item}>
          {truncateMiddle(item)}
        </SelectItem>
      )),
    [models],
  )

  async function startGeneration() {
    if (state.running || !companyId) return
    const request = documentRequest.trim()
    if (request.length < MIN_REQUEST_LENGTH) {
      setSubmitError(
        `Describe the document type(s) in at least ${MIN_REQUEST_LENGTH} characters (currently ${request.length}).`,
      )
      return
    }
    setSubmitError(null)
    setAdded(false)
    setSelected(new Set())
    try {
      const job = await api.generateCompanyDocumentTypes(Number(companyId), {
        num: Number(num),
        model: model || null,
        document_request: request,
      })
      start(job.id, 1, () => {})
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error))
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    await startGeneration()
  }

  async function handleAdd() {
    if (!companyId || !state.result || adding || added || selected.size === 0)
      return
    setAdding(true)
    setSubmitError(null)
    const chosen = state.result.filter((_, index) => selected.has(index))
    try {
      await api.appendCompanyDocumentTypes(Number(companyId), chosen)
      setAdded(true)
      onGenerated()
      // Briefly show the success message, then close the dialog.
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
      closeTimer.current = window.setTimeout(() => {
        closeTimer.current = null
        onOpenChange(false)
      }, 1200)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error))
    } finally {
      setAdding(false)
    }
  }

  return (
    <>
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        // Dismissal only via the X or the Close button (which both route
        // through handleOpenChange so the discard prompt can intercept).
        onInteractOutside={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Generate document types</DialogTitle>
          <DialogDescription>
            Describe the document type(s) you want for a single company. New
            types are appended to the company's existing list — nothing is
            replaced.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            Company
            <Select
              value={companyId}
              onValueChange={setCompanyId}
              disabled={state.running}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select a company" />
              </SelectTrigger>
              <SelectContent>{companyOptions}</SelectContent>
            </Select>
          </label>
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            What kind of document(s) do you want?
            <textarea
              value={documentRequest}
              disabled={state.running}
              onChange={(e) => setDocumentRequest(e.target.value)}
              rows={3}
              placeholder="e.g. Quarterly operations and KPI reviews covering throughput, quality, and regional performance…"
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
            <span
              className={`text-xs tabular-nums ${
                requestLength > 0 && requestLength < MIN_REQUEST_LENGTH
                  ? "text-destructive"
                  : "text-muted-foreground"
              }`}
            >
              {requestLength}/{MIN_REQUEST_LENGTH} characters minimum
            </span>
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              Number of document types
              <Input
                type="number"
                min={1}
                max={50}
                value={num}
                disabled={state.running}
                onChange={(e) => setNum(e.target.value)}
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
                <SelectContent>{modelOptions}</SelectContent>
              </Select>
            </label>
          </div>
          <JobStatus state={state} />
          {hasResult && state.result && (
            <div className="flex max-h-64 flex-col gap-2 overflow-y-auto rounded-lg border bg-secondary/30 p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Generated {state.result.length} document type
                {state.result.length === 1 ? "" : "s"} — {selected.size} selected.
                Pick which to add, or regenerate.
              </p>
              <ul className="flex flex-col gap-1">
                {state.result.map((docType, index) => (
                  <li key={`${docType.name}-${index}`}>
                    <label className="flex cursor-pointer items-start gap-2.5 rounded-md p-1.5 transition-colors hover:bg-secondary/60">
                      <input
                        type="checkbox"
                        className="mt-0.5 size-4 shrink-0 accent-ring"
                        checked={selected.has(index)}
                        disabled={adding}
                        onChange={() => toggleSelected(index)}
                      />
                      <span className="flex flex-col gap-0.5">
                        <span className="text-sm">
                          <span className="font-medium">{docType.name}</span>
                          <span className="ml-2 text-xs text-muted-foreground">
                            {docType.category}
                          </span>
                        </span>
                        <span className="text-xs leading-relaxed text-muted-foreground">
                          {docType.purpose}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {added && (
            <p className="flex items-center gap-2 text-sm text-success">
              <CircleCheck className="size-4 shrink-0" />
              Added {selected.size} document type{selected.size === 1 ? "" : "s"} to the
              company's document type list.
            </p>
          )}
          {submitError && (
            <p className="flex items-start gap-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {submitError}
            </p>
          )}
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Close
            </Button>
            {hasResult && !added && (
              <Button
                type="button"
                variant="outline"
                disabled={state.running || adding}
                onClick={() => void startGeneration()}
              >
                <RefreshCw />
                Regenerate
              </Button>
            )}
            {hasResult && !added ? (
              <Button
                type="button"
                disabled={state.running || adding || selected.size === 0}
                onClick={() => void handleAdd()}
              >
                {adding ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <CircleCheck />
                )}
                {adding
                  ? "Adding…"
                  : `Add ${selected.size} to document type list`}
              </Button>
            ) : (
              <Button
                type="submit"
                disabled={state.running || !companyId || added}
              >
                {state.running ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <FileText />
                )}
                {state.running
                  ? "Generating…"
                  : added
                    ? "Added"
                    : hasResult
                      ? "Regenerate"
                      : "Generate document types"}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={confirmClose} onOpenChange={setConfirmClose}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Discard generated document types?</DialogTitle>
          <DialogDescription>
            {state.result?.length} document type
            {state.result?.length === 1 ? "" : "s"} have been generated but not
            added to the company yet. Closing will discard them.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => setConfirmClose(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              setConfirmClose(false)
              onOpenChange(false)
            }}
          >
            Discard
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}
