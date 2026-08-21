import { useEffect, useRef, useState } from "react"
import { Download, EyeOff, LoaderCircle } from "lucide-react"
import { api, type DocumentRecord } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

/** Max rows rendered per sheet to keep the browser responsive. */
const MAX_SHEET_ROWS = 500

type PreviewKind = "pdf" | "sheet" | "docx" | "image" | "unsupported"

/** Which renderer to use for a filetype (lowercase suffix). */
function previewKind(filetype: string): PreviewKind {
  const ft = filetype.toLowerCase()
  if (ft === "pdf") return "pdf"
  if (ft === "xlsx" || ft === "xls" || ft === "csv") return "sheet"
  if (ft === "docx") return "docx"
  if (ft === "png") return "image"
  return "unsupported"
}

interface SheetData {
  name: string
  rows: string[][]
  truncated: boolean
}

async function fetchBuffer(doc: DocumentRecord): Promise<ArrayBuffer> {
  const response = await fetch(api.documentPreviewUrl(doc.id))
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`)
  }
  return response.arrayBuffer()
}

/** Parse a workbook/CSV buffer into per-sheet row grids. */
async function parseSheets(data: ArrayBuffer): Promise<SheetData[]> {
  const XLSX = await import("xlsx")
  const workbook = XLSX.read(data, { type: "array" })
  return workbook.SheetNames.map((name) => {
    const rows = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets[name], {
      header: 1,
      defval: "",
    })
    // Drop trailing fully-empty rows.
    while (rows.length && rows[rows.length - 1].every((cell) => cell === "")) {
      rows.pop()
    }
    const truncated = rows.length > MAX_SHEET_ROWS
    return {
      name,
      rows: rows.slice(0, MAX_SHEET_ROWS).map((row) => row.map(String)),
      truncated,
    }
  })
}

interface DocumentViewDialogProps {
  /** Document to preview; `null` keeps the dialog closed. */
  doc: DocumentRecord | null
  onClose: () => void
}

/**
 * Modal preview for a generated document. PDFs render in an iframe,
 * XLSX/XLS/CSV as tables (SheetJS) and DOCX as HTML (docx-preview).
 * Anything else falls back to a download prompt.
 */
export function DocumentViewDialog({ doc, onClose }: DocumentViewDialogProps) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading")
  const [error, setError] = useState<string | null>(null)
  const [sheets, setSheets] = useState<SheetData[]>([])
  const [activeSheet, setActiveSheet] = useState(0)
  const docxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!doc) return
    let cancelled = false
    setStatus("loading")
    setError(null)
    setSheets([])
    setActiveSheet(0)

    const kind = previewKind(doc.filetype)
    if (kind === "pdf" || kind === "image" || kind === "unsupported") {
      setStatus("ready")
      return
    }

    void (async () => {
      try {
        const data = await fetchBuffer(doc)
        if (cancelled) return
        if (kind === "sheet") {
          setSheets(await parseSheets(data))
        } else if (kind === "docx" && docxRef.current) {
          const { renderAsync } = await import("docx-preview")
          await renderAsync(data, docxRef.current)
        }
        if (!cancelled) setStatus("ready")
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
          setStatus("error")
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [doc])

  if (!doc) return null

  const kind = previewKind(doc.filetype)
  const sheet = sheets[activeSheet]

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-5xl">
        <DialogHeader>
          <div className="flex items-center gap-2 pr-6">
            <DialogTitle className="min-w-0 truncate">{doc.filename}</DialogTitle>
            <Badge variant="secondary" className="shrink-0 uppercase">
              {doc.filetype}
            </Badge>
          </div>
          <DialogDescription>
            {[doc.company_name, doc.report_name].filter(Boolean).join(" · ") ||
              "No company or document type linked"}
            {" · "}
            {doc.size_kb.toFixed(1)} KB
          </DialogDescription>
        </DialogHeader>

        <div className="relative h-[70vh] overflow-hidden rounded-lg border bg-muted/30">
          {/* Mounted while loading too, so the ref exists when rendering. */}
          {kind === "docx" && status !== "error" && (
            <div ref={docxRef} className="h-full overflow-auto p-4" />
          )}
          {status === "loading" && (
            <p className="absolute inset-0 flex items-center justify-center gap-2 bg-background/60 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
              Loading preview…
            </p>
          )}
          {status === "error" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center">
              <p className="text-sm text-destructive">
                Could not load this document: {error}
              </p>
              <Button asChild variant="outline" size="sm">
                <a href={api.documentDownloadUrl(doc.id)}>
                  <Download />
                  Download instead
                </a>
              </Button>
            </div>
          )}
          {status === "ready" && kind === "pdf" && (
            <iframe
              src={api.documentPreviewUrl(doc.id)}
              title={doc.filename}
              className="h-full w-full"
            />
          )}
          {status === "ready" && kind === "image" && (
            <div className="flex h-full items-center justify-center overflow-auto p-4">
              <img
                src={api.documentPreviewUrl(doc.id)}
                alt={doc.filename}
                className="max-h-full max-w-full object-contain"
                onError={() => {
                  setError("Could not load this image")
                  setStatus("error")
                }}
              />
            </div>
          )}
          {status === "ready" && kind === "sheet" && (
            <div className="h-full overflow-auto">
              {sheets.length > 1 && (
                <div className="sticky top-0 z-10 flex flex-wrap gap-1 border-b bg-background p-2">
                  {sheets.map((s, i) => (
                    <Button
                      key={s.name}
                      size="sm"
                      variant={i === activeSheet ? "secondary" : "ghost"}
                      className="h-7 text-xs"
                      onClick={() => setActiveSheet(i)}
                    >
                      {s.name}
                    </Button>
                  ))}
                </div>
              )}
              {sheet && sheet.rows.length === 0 ? (
                <p className="p-4 text-sm text-muted-foreground">Empty sheet.</p>
              ) : (
                sheet && (
                  <>
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr>
                          {sheet.rows[0].map((cell, j) => (
                            <th
                              key={j}
                              className="sticky top-0 border-b bg-muted px-2 py-1.5 text-left font-semibold"
                            >
                              {cell || `Column ${j + 1}`}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {sheet.rows.slice(1).map((row, i) => (
                          <tr key={i} className="even:bg-muted/20">
                            {row.map((cell, j) => (
                              <td
                                key={j}
                                className="border-b px-2 py-1 tabular-nums"
                              >
                                {cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {sheet.truncated && (
                      <p className="p-2 text-xs text-muted-foreground">
                        Showing first {MAX_SHEET_ROWS} rows.
                      </p>
                    )}
                  </>
                )
              )}
            </div>
          )}
          {status === "ready" && kind === "unsupported" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center">
              <EyeOff className="size-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Preview is not supported for .{doc.filetype} files.
              </p>
              <Button asChild variant="outline" size="sm">
                <a href={api.documentDownloadUrl(doc.id)}>
                  <Download />
                  Download
                </a>
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
