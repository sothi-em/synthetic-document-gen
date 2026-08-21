import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Bug,
  Eye,
  FileText,
  FolderOpen,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react"

import { api, type DocumentRecord } from "@/lib/api"
import { DocumentViewDialog } from "@/components/document-view-dialog"
import { TraceViewDialog } from "@/components/trace-view-dialog"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
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
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"

interface DocumentsPanelProps {
  /** Bumped by the parent after a generation run completes. */
  refreshKey: number
}

/** Soft color overrides for the filetype badge, keyed by lowercase suffix. */
const FILETYPE_STYLES: Record<string, string> = {
  pdf: "bg-red-500/10 text-red-600 dark:text-red-400",
  xlsx: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  xls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  csv: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
  docx: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  doc: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  png: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
}

export function DocumentsPanel({ refreshKey }: DocumentsPanelProps) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [viewingDoc, setViewingDoc] = useState<DocumentRecord | null>(null)
  const [tracingDoc, setTracingDoc] = useState<DocumentRecord | null>(null)
  const [deletingDoc, setDeletingDoc] = useState<DocumentRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  /** Document being renamed (null = dialog closed). */
  const [renamingDoc, setRenamingDoc] = useState<DocumentRecord | null>(null)
  const [renameName, setRenameName] = useState("")
  const [renaming, setRenaming] = useState(false)

  /** Strip the extension so the user edits only the base name. */
  const baseName = (filename: string) => filename.replace(/\.[^.]+$/, "")

  const startRename = useCallback((doc: DocumentRecord) => {
    setRenameName(baseName(doc.filename))
    setRenamingDoc(doc)
  }, [])

  const confirmRename = useCallback(async () => {
    if (!renamingDoc) return
    setRenaming(true)
    setError(null)
    try {
      const updated = await api.renameDocument(renamingDoc.id, renameName)
      // The response lacks the joined names; keep them from the old record.
      setDocuments((docs) =>
        docs.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)),
      )
      setRenamingDoc(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setRenamingDoc(null)
    } finally {
      setRenaming(false)
    }
  }, [renamingDoc, renameName])

  // Size the delete dialog to the filename: long underscored names have
  // no wrap points, so grow the dialog (clamped between the default
  // 28rem and 64rem) until the name fits on one line. ~0.55rem per
  // character at the description's text-sm size, plus dialog padding.
  const deleteModalMaxWidth = `clamp(28rem, ${((deletingDoc?.filename.length ?? 0) * 0.55 + 3).toFixed(1)}rem, 64rem)`

  // Load the full list once (and on refresh); typing never hits the API —
  // the table is filtered locally against this cache.
  const loadDocuments = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setDocuments(await api.documents())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  const confirmDelete = useCallback(async () => {
    if (!deletingDoc) return
    setDeleting(true)
    setError(null)
    try {
      await api.deleteDocument(deletingDoc.id)
      // Remove locally; the server already deleted record + file.
      setDocuments((docs) => docs.filter((d) => d.id !== deletingDoc.id))
      setDeletingDoc(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setDeletingDoc(null)
      // The list may be stale (e.g. concurrent delete) — resync it.
      void loadDocuments()
    } finally {
      setDeleting(false)
    }
  }, [deletingDoc, loadDocuments])

  useEffect(() => {
    loadDocuments()
  }, [loadDocuments, refreshKey])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return documents
    return documents.filter((doc) =>
      [doc.filename, doc.filetype, doc.company_name, doc.report_name, doc.filepath].some(
        (value) => value?.toLowerCase().includes(q),
      ),
    )
  }, [documents, query])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
        <CardDescription>
          Every generated document (any filetype), linked back to its
          company and document type.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              placeholder="Search filename, company, document type…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-8"
            />
          </div>
          <Button variant="outline" onClick={loadDocuments}>
            <RefreshCw />
            Refresh
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {loading ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
          </div>
        ) : documents.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <div className="flex size-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
              <FolderOpen className="size-6" />
            </div>
            <p className="text-sm text-muted-foreground">
              No documents generated yet. Generate a PDF from the Document types
              tab to see it here.
            </p>
          </div>
        ) : filtered.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No documents match the current search.
          </p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {filtered.length} document{filtered.length === 1 ? "" : "s"}
              {filtered.length !== documents.length
                ? ` (of ${documents.length})`
                : ""}
            </p>
            <ul className="flex flex-col gap-2">
              {filtered.map((doc) => (
                <li
                  key={doc.id}
                  className="rounded-lg border bg-card px-3 py-2.5"
                >
                  <div className="flex items-center gap-2">
                    <FileText className="size-4 shrink-0 text-muted-foreground" />
                    <a
                      href={api.documentDownloadUrl(doc.id)}
                      title={doc.filename}
                      className="min-w-0 truncate font-medium hover:text-foreground"
                    >
                      {doc.filename}
                    </a>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 shrink-0"
                      title={`View ${doc.filename}`}
                      onClick={() => setViewingDoc(doc)}
                    >
                      <Eye className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 shrink-0"
                      title={`Rename ${doc.filename}`}
                      onClick={() => startRename(doc)}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Badge
                      variant="secondary"
                      className={cn(
                        "shrink-0 uppercase",
                        FILETYPE_STYLES[doc.filetype.toLowerCase()],
                      )}
                    >
                      {doc.filetype}
                    </Badge>
                    {doc.gen_tracing && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0"
                        title={`View generation trace for ${doc.filename}`}
                        onClick={() => setTracingDoc(doc)}
                      >
                        <Bug className="size-4" />
                      </Button>
                    )}
                    <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
                      {doc.size_kb.toFixed(1)} KB
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 shrink-0 hover:text-destructive"
                      title={`Delete ${doc.filename}`}
                      onClick={() => setDeletingDoc(doc)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                  <p className="mt-1 truncate pl-6 text-xs text-muted-foreground">
                    {[doc.company_name, doc.report_name]
                      .filter(Boolean)
                      .join(" · ") || "No company or document type linked"}
                    {" · "}
                    {new Date(doc.created_at).toLocaleString()}
                  </p>
                  <p
                    className="mt-0.5 truncate pl-6 text-xs text-muted-foreground/70"
                    title={doc.filepath}
                  >
                    {doc.filepath}
                  </p>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
      <DocumentViewDialog
        doc={viewingDoc}
        onClose={() => setViewingDoc(null)}
      />
      <TraceViewDialog doc={tracingDoc} onClose={() => setTracingDoc(null)} />
      <Dialog
        open={renamingDoc !== null}
        onOpenChange={(open) => !open && setRenamingDoc(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename document</DialogTitle>
            <DialogDescription>
              The <span className="font-medium text-foreground">.
              {renamingDoc?.filetype}</span> extension is kept; the file on
              disk is renamed too.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Name
            </span>
            <Input
              value={renameName}
              onChange={(e) => setRenameName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void confirmRename()}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={renaming}
              onClick={() => setRenamingDoc(null)}
            >
              Cancel
            </Button>
            <Button
              disabled={renaming || !renameName.trim()}
              onClick={() => void confirmRename()}
            >
              <Pencil />
              {renaming ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={deletingDoc !== null} onOpenChange={(open) => !open && setDeletingDoc(null)}>
        <DialogContent style={{ maxWidth: deleteModalMaxWidth }}>
          <DialogHeader>
            <DialogTitle>Delete document</DialogTitle>
            <DialogDescription>
              Delete{" "}
              <span className="break-all font-medium text-foreground">
                {deletingDoc?.filename}
              </span>
              ? The file on disk will be removed too. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" disabled={deleting} onClick={() => setDeletingDoc(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleting}
              onClick={() => void confirmDelete()}
            >
              <Trash2 />
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
