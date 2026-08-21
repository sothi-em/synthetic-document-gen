import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Building2,
  ChevronDown,
  ChevronRight,
  FileDown,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Pencil,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"

import {
  api,
  type CompanyProfile,
  type CompanySummary,
  type DocumentRef,
  type DocumentType,
  type DocumentTypeDoc,
} from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { GenerateDocumentTypesDialog } from "@/components/generate-dialogs"
import { GeneratePdfDialog } from "@/components/generate-pdf-dialog"
import { GenerateExcelDialog } from "@/components/generate-excel-dialog"
import { GenerateImageDialog } from "@/components/generate-image-dialog"

/** localStorage key for the last selected company (survives tab switches). */
const LAST_COMPANY_KEY = "document-gen:documents:last-company"

/**
 * Static Tailwind classes for the per-filetype count pills. Kept as full
 * literal strings so the Tailwind compiler picks them up.
 */
const FILETYPE_PILL_STYLES: Record<string, string> = {
  pdf: "border-red-500/30 bg-red-500/15 text-red-600 dark:text-red-400",
  xlsx: "border-emerald-500/30 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  docx: "border-blue-500/30 bg-blue-500/15 text-blue-600 dark:text-blue-400",
  csv: "border-amber-500/30 bg-amber-500/15 text-amber-600 dark:text-amber-400",
}

/** Palette cycled through for filetypes without a dedicated color. */
const FILETYPE_PILL_FALLBACK_STYLES = [
  "border-violet-500/30 bg-violet-500/15 text-violet-600 dark:text-violet-400",
  "border-cyan-500/30 bg-cyan-500/15 text-cyan-600 dark:text-cyan-400",
  "border-pink-500/30 bg-pink-500/15 text-pink-600 dark:text-pink-400",
  "border-orange-500/30 bg-orange-500/15 text-orange-600 dark:text-orange-400",
]

/** Count a document type's generated documents grouped by filetype. */
function documentFiletypeCounts(
  documents: DocumentTypeDoc["documents"],
): { filetype: string; count: number }[] {
  const counts = new Map<string, number>()
  for (const doc of documents) {
    const key = doc.filetype.toLowerCase()
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return [...counts.entries()].map(([filetype, count]) => ({
    filetype,
    count,
  }))
}

/** Pick the pill color for a filetype, cycling the fallback palette. */
function filetypePillClass(filetype: string, index: number): string {
  return (
    FILETYPE_PILL_STYLES[filetype] ??
    FILETYPE_PILL_FALLBACK_STYLES[index % FILETYPE_PILL_FALLBACK_STYLES.length]
  )
}

interface DocumentTypesPanelProps {
  models: string[]
  /** Bumped by the parent after a generation run completes. */
  refreshKey: number
  /** Bumped by the parent after settings are saved (re-checks the PDF gate). */
  settingsVersion: number
  /** Called after a document-type generation job finishes successfully. */
  onGenerated: () => void
}

export function DocumentTypesPanel({
  models,
  refreshKey,
  settingsVersion,
  onGenerated,
}: DocumentTypesPanelProps) {
  const [query, setQuery] = useState("")
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [allCompanies, setAllCompanies] = useState<CompanySummary[]>([])
  const [companiesLoading, setCompaniesLoading] = useState(true)
  const [selected, setSelected] = useState<CompanySummary | null>(null)
  const [types, setTypes] = useState<DocumentTypeDoc[]>([])
  const [typesLoading, setTypesLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [generateOpen, setGenerateOpen] = useState(false)
  const [pdfTarget, setPdfTarget] = useState<DocumentTypeDoc | null>(null)
  const [excelTarget, setExcelTarget] = useState<DocumentTypeDoc | null>(null)
  const [imageTarget, setImageTarget] = useState<DocumentTypeDoc | null>(null)
  /** Document type awaiting delete confirmation. */
  const [deletingType, setDeletingType] = useState<DocumentTypeDoc | null>(null)
  const [deleting, setDeleting] = useState(false)
  /** Document type being edited (null = dialog closed). */
  const [editingType, setEditingType] = useState<DocumentTypeDoc | null>(null)
  /** Generated document being renamed (null = dialog closed). */
  const [renamingDoc, setRenamingDoc] = useState<DocumentRef | null>(null)
  const [renameName, setRenameName] = useState("")
  const [renaming, setRenaming] = useState(false)

  const confirmRename = useCallback(async () => {
    if (!renamingDoc) return
    setRenaming(true)
    setError(null)
    try {
      const updated = await api.renameDocument(renamingDoc.id, renameName)
      setTypes((current) =>
        current.map((type) => ({
          ...type,
          documents: type.documents.map((doc) =>
            doc.id === updated.id
              ? { ...doc, filename: updated.filename }
              : doc,
          ),
        })),
      )
      setRenamingDoc(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setRenamingDoc(null)
    } finally {
      setRenaming(false)
    }
  }, [renamingDoc, renameName])
  /** Whether the company profile edit dialog is open. */
  const [editingProfile, setEditingProfile] = useState(false)
  /** Full profile of the selected company, shown in the side panel. */
  const [profile, setProfile] = useState<CompanyProfile | null>(null)
  /** User-provided context that guided the company's generation, if any. */
  const [companyUserInput, setCompanyUserInput] = useState<string | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  /** Effective document output dir; null disables PDF generation. */
  const [pdfDir, setPdfDir] = useState<string | null>(null)
  const searchRef = useRef<HTMLDivElement>(null)

  // Gate: PDF generation is only allowed when an output directory is set
  // (saved setting or DOCUMENTS_DIR env default). Re-checked after settings
  // are saved and after a refresh.
  useEffect(() => {
    api
      .documentsSettings()
      .then((settings) => setPdfDir(settings.output_dir))
      .catch(() => setPdfDir(null))
  }, [settingsVersion, refreshKey])

  // Load the full company list once (and on refresh); typing never hits the
  // API — suggestions are filtered locally against this cache.
  const loadCompanies = useCallback(async () => {
    setCompaniesLoading(true)
    setError(null)
    try {
      setAllCompanies(await api.companies())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCompaniesLoading(false)
    }
  }, [])

  useEffect(() => {
    loadCompanies()
  }, [loadCompanies, refreshKey])

  // Restore the last selected company once the company list is available
  // (the panel unmounts when switching tabs, so the choice is persisted in
  // localStorage and re-applied on the first mount of each visit).
  const restoredRef = useRef(false)
  useEffect(() => {
    if (restoredRef.current || companiesLoading || allCompanies.length === 0)
      return
    const raw = localStorage.getItem(LAST_COMPANY_KEY)
    const saved =
      raw && allCompanies.find((company) => company.id === Number(raw))
    if (saved) {
      restoredRef.current = true
      void pickCompany(saved)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allCompanies, companiesLoading])

  // Reload the selected company's document types when a generation run adds to
  // them (refreshKey is bumped by the parent on success).
  const firstRefresh = useRef(true)
  useEffect(() => {
    if (firstRefresh.current) {
      firstRefresh.current = false
      return
    }
    if (selected) loadDocumentTypes(selected.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey])

  // Case-insensitive partial match on the cached company names.
  const suggestions = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return allCompanies
    return allCompanies.filter((company) =>
      company.name.toLowerCase().includes(q),
    )
  }, [allCompanies, query])

  // Close the suggestion dropdown on outside click or Escape.
  useEffect(() => {
    if (!dropdownOpen) return
    function onPointerDown(event: MouseEvent) {
      if (!searchRef.current?.contains(event.target as Node)) {
        setDropdownOpen(false)
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setDropdownOpen(false)
    }
    document.addEventListener("mousedown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("mousedown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [dropdownOpen])

  const loadDocumentTypes = useCallback(async (id: number) => {
    setTypesLoading(true)
    setError(null)
    try {
      setTypes(await api.companyDocumentTypes(id))
      setExpanded(new Set())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setTypes([])
    } finally {
      setTypesLoading(false)
    }
  }, [])

  const confirmDeleteType = useCallback(async () => {
    if (!deletingType || !selected) return
    setDeleting(true)
    setError(null)
    try {
      await api.deleteCompanyDocumentType(selected.id, deletingType.id)
      // Remove locally; the server already deleted the record + documents.
      setTypes((current) =>
        current.filter((type) => type.id !== deletingType.id),
      )
      setDeletingType(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setDeletingType(null)
      // The list may be stale (e.g. concurrent delete) — resync it.
      void loadDocumentTypes(selected.id)
    } finally {
      setDeleting(false)
    }
  }, [deletingType, selected, loadDocumentTypes])

  async function pickCompany(company: CompanySummary) {
    setSelected(company)
    setQuery(company.name)
    setDropdownOpen(false)
    localStorage.setItem(LAST_COMPANY_KEY, String(company.id))
    setProfile(null)
    setCompanyUserInput(null)
    setProfileLoading(true)
    try {
      // Load the types list and the full profile in parallel; a missing
      // profile (e.g. company saved without one) just hides the details.
      await Promise.all([
        loadDocumentTypes(company.id),
        api
          .company(company.id)
          .then((detail) => {
            setProfile(detail.profile)
            setCompanyUserInput(detail.user_input ?? null)
          })
          .catch(() => setProfile(null)),
      ])
    } finally {
      setProfileLoading(false)
    }
  }

  function clearSelection() {
    setSelected(null)
    setQuery("")
    setTypes([])
    setExpanded(new Set())
    setError(null)
    setProfile(null)
    setCompanyUserInput(null)
    localStorage.removeItem(LAST_COMPANY_KEY)
  }

  function toggleExpanded(id: number) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Document types</CardTitle>
        <CardDescription>
          Pick a company to browse its document types — expand a row for full
          details.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div ref={searchRef} className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="text"
              role="combobox"
              aria-expanded={dropdownOpen}
              aria-label="Search companies by name"
              placeholder={
                companiesLoading ? "Loading companies…" : "Search companies…"
              }
              value={query}
              disabled={companiesLoading}
              onChange={(e) => {
                setQuery(e.target.value)
                setDropdownOpen(true)
              }}
              onFocus={() => setDropdownOpen(true)}
              className={selected ? "pr-9 pl-8" : "pl-8"}
            />
            {selected && (
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-1.5 top-1/2 size-7 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={clearSelection}
                aria-label="Clear selected company"
              >
                <X className="size-3.5" />
              </Button>
            )}
            {dropdownOpen && !companiesLoading && (
              <div className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border bg-popover p-1 shadow-md">
                {suggestions.length === 0 ? (
                  <p className="px-2.5 py-2 text-sm text-muted-foreground">
                    No companies match “{query.trim()}”.
                  </p>
                ) : (
                  suggestions.map((company) => (
                    <button
                      key={company.id}
                      type="button"
                      onClick={() => pickCompany(company)}
                      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground ${
                        selected?.id === company.id
                          ? "bg-accent text-accent-foreground"
                          : ""
                      }`}
                    >
                      <Building2
                        className="size-4 shrink-0 text-muted-foreground"
                        aria-hidden
                      />
                      <span className="flex-1 truncate">
                        {company.name}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {company.industry}
                      </span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          <Button variant="outline" onClick={loadCompanies}>
            <RefreshCw />
            Refresh
          </Button>
          <Button onClick={() => setGenerateOpen(true)}>
            <Sparkles />
            Generate document types
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {selected === null ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <div className="flex size-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
              <FileText className="size-6" />
            </div>
            <p className="text-sm text-muted-foreground">
              Select a company above to see its document types.
            </p>
          </div>
        ) : (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="flex min-w-0 flex-col gap-4">
              {typesLoading ? (
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : types.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  {selected.name} has no document types.
                </p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">
                    {types.length} document type
                    {types.length === 1 ? "" : "s"} for{" "}
                    <span className="font-medium text-foreground">
                      {selected.name}
                    </span>
                  </p>
                  <ul className="flex flex-col gap-2">
              {types.map( (docType) => {
                const isOpen = expanded.has(docType.id)
                return (
                  <li
                    key={docType.id}
                    className="overflow-hidden rounded-lg border bg-secondary/40"
                  >
                    <button
                      type="button"
                      onClick={() => toggleExpanded(docType.id)}
                      aria-expanded={isOpen}
                      className="flex w-full items-center gap-3 p-3 text-left transition-colors hover:bg-secondary/80"
                    >
                      {isOpen ? (
                        <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                      )}
                      <span className="flex-1 truncate text-sm font-medium">
                        {docType.name}
                      </span>
                      {docType.num_documents > 0 && (
                        <span className="flex shrink-0 items-center gap-1">
                          {documentFiletypeCounts(docType.documents).map(
                            ({ filetype, count }, index) => (
                              <span
                                key={filetype}
                                title={`${count} ${filetype} document${
                                  count === 1 ? "" : "s"
                                }`}
                                className={`rounded-full border px-2 py-0.5 text-xs font-medium ${filetypePillClass(filetype, index)}`}
                              >
                                {filetype.toUpperCase()} · {count}
                              </span>
                            ),
                          )}
                        </span>
                      )}
                      <Badge
                        variant="secondary"
                        className="shrink-0 bg-primary/10 text-primary"
                      >
                        {docType.category}
                      </Badge>
                    </button>
                    {isOpen && (
                      <div className="flex items-center justify-between border-t bg-background/60 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Edit ${docType.name}`}
                            title="Edit document type"
                            onClick={() => setEditingType(docType)}
                          >
                            <Pencil />
                            Edit
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() => setDeletingType(docType)}
                          >
                            <Trash2 />
                            Delete
                          </Button>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={pdfDir === null}
                            title={
                              pdfDir === null
                                ? "Set a document output directory in Settings to enable PDF generation"
                                : `Generate a PDF (saved to ${pdfDir})`
                            }
                            onClick={() => setPdfTarget(docType)}
                          >
                            <FileDown />
                            Generate PDF
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={pdfDir === null}
                            title={
                              pdfDir === null
                                ? "Set a document output directory in Settings to enable Excel generation"
                                : `Generate an Excel workbook (saved to ${pdfDir})`
                            }
                            onClick={() => setExcelTarget(docType)}
                          >
                            <FileSpreadsheet />
                            Generate Excel
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={pdfDir === null}
                            title={
                              pdfDir === null
                                ? "Set a document output directory in Settings to enable image generation"
                                : `Generate a PNG image (saved to ${pdfDir})`
                            }
                            onClick={() => setImageTarget(docType)}
                          >
                            <ImageIcon />
                            Generate Image
                          </Button>
                        </div>
                      </div>
                    )}
                    {isOpen && (
                      <div className="border-t bg-background/60 px-3 py-3 pl-10">
                        <div className="flex flex-col gap-1.5">
                          <div>
                            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                              Category
                            </span>
                            <p className="text-sm">{docType.category}</p>
                          </div>
                          <div>
                            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                              Purpose
                            </span>
                            <p className="text-sm leading-relaxed">
                              {docType.purpose}
                            </p>
                          </div>
                          {docType.user_input && (
                            <div>
                              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                User input
                              </span>
                              <p className="text-sm leading-relaxed">
                                {docType.user_input}
                              </p>
                            </div>
                          )}
                          <div>
                            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                              Documents
                            </span>
                            {docType.documents.length === 0 ? (
                              <p className="text-sm text-muted-foreground">
                                No documents generated yet.
                              </p>
                            ) : (
                              <ul className="mt-1 flex flex-col gap-1">
                                {docType.documents.map((doc) => (
                                  <li
                                    key={doc.id}
                                    className="flex items-center gap-1 text-sm"
                                  >
                                    <a
                                      href={api.documentDownloadUrl(doc.id)}
                                      title={`Download ${doc.filename}`}
                                      className="min-w-0 truncate hover:text-foreground"
                                    >
                                      {doc.filename}
                                    </a>
                                    <span className="shrink-0 text-xs uppercase text-muted-foreground">
                                      {doc.filetype}
                                    </span>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="size-6 shrink-0 text-muted-foreground hover:text-foreground"
                                      title={`Rename ${doc.filename}`}
                                      onClick={() => {
                                        setRenameName(
                                          doc.filename.replace(
                                            /\.[^.]+$/, "",
                                          ),
                                        )
                                        setRenamingDoc(doc)
                                      }}
                                    >
                                      <Pencil className="size-3" />
                                    </Button>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </li>
                )
              })}
                  </ul>
                </>
              )}
            </div>
            <CompanyProfileCard
              profile={profile}
              loading={profileLoading}
              userInput={companyUserInput}
              onEdit={() => setEditingProfile(true)}
            />
          </div>
        )}
      </CardContent>
      <GenerateDocumentTypesDialog
        open={generateOpen}
        onOpenChange={setGenerateOpen}
        company={selected}
        companies={allCompanies}
        models={models}
        onGenerated={onGenerated}
      />
      <Dialog
        open={deletingType !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) setDeletingType(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete document type</DialogTitle>
            <DialogDescription>
              Delete{" "}
              <span className="break-all font-medium text-foreground">
                {deletingType?.name}
              </span>{" "}
              for {selected?.name}? Any generated documents for this type will
              be removed too. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={deleting}
              onClick={() => setDeletingType(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleting}
              onClick={() => void confirmDeleteType()}
            >
              <Trash2 />
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {renamingDoc && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) setRenamingDoc(null)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Rename document</DialogTitle>
              <DialogDescription>
                The{" "}
                <span className="font-medium text-foreground">
                  .{renamingDoc.filetype}
                </span>{" "}
                extension is kept; the file on disk is renamed too.
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
      )}
      {selected && editingType && (
        <EditDocumentTypeDialog
          key={editingType.id}
          docType={editingType}
          onOpenChange={(open) => {
            if (!open) setEditingType(null)
          }}
          onSave={async (values) => {
            const updated = await api.updateCompanyDocumentType(
              selected.id,
              editingType.id,
              values,
            )
            setTypes((current) =>
              current.map((type) => (type.id === updated.id ? updated : type)),
            )
            setEditingType(null)
          }}
        />
      )}
      {selected && profile && editingProfile && (
        <EditCompanyDialog
          profile={profile}
          onOpenChange={(open) => {
            if (!open) setEditingProfile(false)
          }}
          onSave={async (values) => {
            const detail = await api.updateCompany(selected.id, values)
            setProfile(detail.profile)
            const summary = {
              name: values.name,
              industry: values.industry,
              headquarters: values.headquarters,
              size: values.size,
            }
            setSelected((current) =>
              current ? { ...current, ...summary } : current,
            )
            setAllCompanies((current) =>
              current.map((company) =>
                company.id === selected.id ? { ...company, ...summary } : company,
              ),
            )
            setEditingProfile(false)
          }}
        />
      )}
      {selected && pdfTarget && (
        <GeneratePdfDialog
          open={pdfTarget !== null}
          onOpenChange={(open) => {
            if (!open) setPdfTarget(null)
          }}
          company={selected}
          docType={pdfTarget}
          models={models}
        />
      )}
      {selected && excelTarget && (
        <GenerateExcelDialog
          open={excelTarget !== null}
          onOpenChange={(open) => {
            if (!open) setExcelTarget(null)
          }}
          company={selected}
          docType={excelTarget}
          models={models}
        />
      )}
      {selected && imageTarget && (
        <GenerateImageDialog
          open={imageTarget !== null}
          onOpenChange={(open) => {
            if (!open) setImageTarget(null)
          }}
          company={selected}
          docType={imageTarget}
          models={models}
        />
      )}
    </Card>
  )
}

/** Side panel with the full profile of the selected company. */
function CompanyProfileCard({
  profile,
  loading,
  userInput,
  onEdit,
}: {
  profile: CompanyProfile | null
  loading: boolean
  userInput?: string | null
  onEdit?: () => void
}) {
  if (loading || profile === null) {
    return (
      <div className="rounded-lg border bg-secondary/40 p-4">
        <Skeleton className="mb-3 h-4 w-28" />
        <Skeleton className="mb-2 h-3 w-full" />
        <Skeleton className="mb-2 h-3 w-4/5" />
        <Skeleton className="h-3 w-3/5" />
      </div>
    )
  }
  return (
    <div className="rounded-lg border bg-secondary/40 p-4">
      <div className="mb-1 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Company profile
        </p>
        {onEdit && (
          <Button
            variant="ghost"
            size="icon"
            className="size-6 text-muted-foreground hover:text-foreground"
            aria-label="Edit company profile"
            title="Edit company profile"
            onClick={onEdit}
          >
            <Pencil className="size-3" />
          </Button>
        )}
      </div>
      <h3 className="text-sm font-semibold">{profile.name}</h3>
      <div className="mt-3 flex flex-col gap-2">
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Industry
          </span>
          <p className="text-sm">{profile.industry}</p>
        </div>
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Headquarters
          </span>
          <p className="text-sm">{profile.headquarters}</p>
        </div>
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Size
          </span>
          <p className="text-sm capitalize">{profile.size}</p>
        </div>
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Description
          </span>
          <p className="text-sm leading-relaxed">{profile.description}</p>
        </div>
        {userInput && (
          <div>
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              User input
            </span>
            <p className="text-sm leading-relaxed">{userInput}</p>
          </div>
        )}
      </div>
    </div>
  )
}

/** Dialog for editing the name/category/purpose of one document type. */
function EditDocumentTypeDialog({
  docType,
  onOpenChange,
  onSave,
}: {
  docType: DocumentTypeDoc
  onOpenChange: (open: boolean) => void
  onSave: (values: DocumentType) => Promise<void>
}) {
  const [name, setName] = useState(docType.name)
  const [category, setCategory] = useState(docType.category)
  const [purpose, setPurpose] = useState(docType.purpose)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      await onSave({
        name: name.trim(),
        category: category.trim(),
        purpose: purpose.trim(),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit document type</DialogTitle>
          <DialogDescription>
            Update the details for{" "}
            <span className="break-all font-medium text-foreground">
              {docType.name}
            </span>
            .
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Name
            </span>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Category
            </span>
            <Input value={category} onChange={(e) => setCategory(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Purpose
            </span>
            <Textarea
              rows={3}
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button disabled={saving || !name.trim()} onClick={() => void handleSave()}>
            <Pencil />
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Dialog for editing the profile of the selected company. */
function EditCompanyDialog({
  profile,
  onOpenChange,
  onSave,
}: {
  profile: CompanyProfile
  onOpenChange: (open: boolean) => void
  onSave: (values: CompanyProfile) => Promise<void>
}) {
  const [name, setName] = useState(profile.name)
  const [industry, setIndustry] = useState(profile.industry)
  const [headquarters, setHeadquarters] = useState(profile.headquarters)
  const [size, setSize] = useState(profile.size)
  const [description, setDescription] = useState(profile.description)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      await onSave({
        name: name.trim(),
        industry: industry.trim(),
        headquarters: headquarters.trim(),
        size,
        description: description.trim(),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit company</DialogTitle>
          <DialogDescription>
            Update the profile for{" "}
            <span className="break-all font-medium text-foreground">
              {profile.name}
            </span>
            .
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Name
            </span>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Industry
              </span>
              <Input
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Headquarters
              </span>
              <Input
                value={headquarters}
                onChange={(e) => setHeadquarters(e.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Size
            </span>
            <Select value={size} onValueChange={setSize}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="small">Small</SelectItem>
                <SelectItem value="mid">Mid</SelectItem>
                <SelectItem value="large">Large</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Description
            </span>
            <Textarea
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button disabled={saving || !name.trim()} onClick={() => void handleSave()}>
            <Pencil />
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
