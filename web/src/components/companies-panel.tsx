import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  MousePointerClick,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react"

import { api, type CompanyDetail, type CompanySummary } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

interface CompaniesPanelProps {
  industries: string[]
  /** Bumped by the parent after a generation run completes. */
  refreshKey: number
  /** Opens the generate-companies dialog (owned by the parent). */
  onGenerate: () => void
  /**
   * Selected company id, owned by the parent so the selection survives
   * tab switches (the panel itself is unmounted while inactive).
   */
  selectedCompanyId: number | null
  onSelectCompany: (id: number | null) => void
}

type SortKey = "name" | "industry" | "headquarters" | "size" | "num_reports"
type SortDir = "asc" | "desc"

/** Soft color overrides for the company-size badge, keyed by lowercase size. */
const SIZE_STYLES: Record<string, string> = {
  small: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  medium: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  large: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
}

function sizeBadgeClass(size: string): string {
  return SIZE_STYLES[size.toLowerCase()] ?? ""
}

function SortableHead({
  label,
  sortKey,
  activeKey,
  dir,
  onSort,
  className,
}: {
  label: string
  sortKey: SortKey
  activeKey: SortKey
  dir: SortDir
  onSort: (key: SortKey) => void
  className?: string
}) {
  const active = activeKey === sortKey
  const Icon = !active ? ArrowUpDown : dir === "asc" ? ArrowUp : ArrowDown
  return (
    <TableHead className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1.5 uppercase tracking-wide transition-colors hover:text-foreground ${
          active ? "text-foreground" : "text-muted-foreground"
        }`}
        aria-label={`Sort by ${label}`}
      >
        {label}
        <Icon
          className={`size-3.5 ${active ? "" : "opacity-50"}`}
          aria-hidden
        />
      </button>
    </TableHead>
  )
}

export function CompaniesPanel({
  industries,
  refreshKey,
  onGenerate,
  selectedCompanyId,
  onSelectCompany,
}: CompaniesPanelProps) {
  const [search, setSearch] = useState("")
  const [industry, setIndustry] = useState("")
  const [companies, setCompanies] = useState<CompanySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<CompanyDetail | null>(null)
  // Start in the loading state when a selection is being restored.
  const [detailLoading, setDetailLoading] = useState(selectedCompanyId !== null)
  const [sortKey, setSortKey] = useState<SortKey>("name")
  const [sortDir, setSortDir] = useState<SortDir>("asc")

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir("asc")
    }
  }

  // Load the full company list once (and on refresh); typing and industry
  // changes never hit the API — the table is filtered locally against this
  // cache.
  const loadCompanies = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setCompanies(await api.companies())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadCompanies()
  }, [loadCompanies, refreshKey])

  // Case-insensitive partial match on the cached companies, plus the
  // industry dropdown filter.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return companies.filter((company) => {
      if (industry && company.industry !== industry) return false
      if (!q) return true
      return (
        company.name.toLowerCase().includes(q) ||
        company.industry.toLowerCase().includes(q) ||
        company.headquarters.toLowerCase().includes(q)
      )
    })
  }, [companies, industry, search])

  const sorted = useMemo(() => {
    const factor = sortDir === "asc" ? 1 : -1
    return [...filtered].sort((a, b) => {
      if (sortKey === "num_reports") {
        return (a.num_reports - b.num_reports) * factor
      }
      return String(a[sortKey] ?? "")
        .localeCompare(String(b[sortKey] ?? ""), undefined, {
          sensitivity: "base",
        }) * factor
    })
  }, [filtered, sortKey, sortDir])

  const openDetail = useCallback(
    async (id: number) => {
      setDetail(null)
      setDetailLoading(true)
      try {
        setDetail(await api.company(id))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setDetailLoading(false)
      }
    },
    [],
  )

  // Restore the parent-owned selection whenever the panel (re)mounts.
  useEffect(() => {
    if (selectedCompanyId !== null) {
      void openDetail(selectedCompanyId)
    }
  }, [openDetail, selectedCompanyId])

  async function removeDocumentType(id: number) {
    if (!detail) return
    const remaining = detail.reports.filter((r) => r.id !== id)
    try {
      await api.saveCompanyDocumentTypes(detail.id, remaining)
      setDetail({ ...detail, reports: remaining })
      loadCompanies()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function clearDocumentTypes() {
    if (!detail) return
    try {
      await api.deleteCompanyDocumentTypes(detail.id)
      setDetail({ ...detail, reports: [] })
      loadCompanies()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="grid items-start gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Companies</CardTitle>
          <CardDescription>
            Browsing the TinyDB company store — click a row for details.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                placeholder="Search name, industry, HQ…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-8"
              />
            </div>
            <Select value={industry} onValueChange={setIndustry}>
              <SelectTrigger className="w-full sm:w-52">
                <SelectValue placeholder="All industries" />
              </SelectTrigger>
              <SelectContent>
                {industries.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" onClick={loadCompanies}>
              <RefreshCw />
              Refresh
            </Button>
            <Button onClick={onGenerate}>
              <Sparkles />
              Generate
            </Button>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          {loading ? (
            <div className="flex flex-col gap-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : companies.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No companies found. Generate some first.
            </p>
          ) : filtered.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No companies match the current search and filters.
            </p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                {filtered.length} compan{filtered.length === 1 ? "y" : "ies"}
                {filtered.length !== companies.length
                  ? ` (of ${companies.length})`
                  : ""}
              </p>
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHead
                      label="Name"
                      sortKey="name"
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={handleSort}
                    />
                    <SortableHead
                      label="Industry"
                      sortKey="industry"
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={handleSort}
                    />
                    <SortableHead
                      label="HQ"
                      sortKey="headquarters"
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={handleSort}
                    />
                    <SortableHead
                      label="Size"
                      sortKey="size"
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={handleSort}
                    />
                    <SortableHead
                      label="Document types"
                      sortKey="num_reports"
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={handleSort}
                      className="text-right"
                    />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sorted.map((company) => (
                    <TableRow
                      key={company.id}
                      data-state={
                        selectedCompanyId === company.id
                          ? "selected"
                          : undefined
                      }
                      className="cursor-pointer"
                      onClick={() => {
                        onSelectCompany(company.id)
                        void openDetail(company.id)
                      }}
                    >
                      <TableCell className="font-medium">
                        {company.name}
                      </TableCell>
                      <TableCell>{company.industry}</TableCell>
                      <TableCell>{company.headquarters}</TableCell>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className={cn("capitalize", sizeBadgeClass(company.size))}
                        >
                          {company.size}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {company.num_reports}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
        </CardContent>
      </Card>

      <Card className="flex flex-col lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-hidden">
        <CardHeader>
          <CardTitle>Details</CardTitle>
          <CardDescription>
            Full profile and document types for a company.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex-1 overflow-y-auto">
          {detailLoading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : detail && detail.profile ? (
            <div className="flex flex-col gap-4">
              <div>
                <h3 className="font-heading text-lg font-semibold">
                  {detail.profile.name}
                </h3>
                <p className="text-sm text-muted-foreground">
                  {detail.profile.industry} · {detail.profile.headquarters} ·{" "}
                  {detail.profile.size}
                </p>
              </div>
              <p className="text-sm leading-relaxed">
                {detail.profile.description}
              </p>
              <div>
                <h4 className="mb-2 font-heading text-sm font-medium">
                  Document types
                </h4>
                <ul className="flex flex-col gap-2">
                  {detail.reports.map((docType) => (
                    <li
                      key={docType.id}
                      className="flex items-start justify-between gap-2 rounded-lg bg-secondary/60 p-2.5 text-sm"
                    >
                      <div>
                        <span className="font-medium">{docType.name}</span>{" "}
                        {docType.num_documents > 0 && (
                          <span className="text-xs text-muted-foreground">
                            · {docType.num_documents} doc
                            {docType.num_documents === 1 ? "" : "s"}
                          </span>
                        )}
                        <span className="text-muted-foreground">
                          ({docType.category})
                        </span>
                        <div className="text-muted-foreground">
                          {docType.purpose}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => removeDocumentType(docType.id)}
                        aria-label={`Delete document type ${docType.name}`}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </li>
                  ))}
                  {detail.reports.length === 0 && (
                    <li className="text-sm text-muted-foreground">none</li>
                  )}
                </ul>
                {detail.reports.length > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2 text-muted-foreground"
                    onClick={clearDocumentTypes}
                  >
                    <Trash2 />
                    Clear all document types
                  </Button>
                )}
              </div>
              <details>
                <summary className="cursor-pointer text-sm text-muted-foreground">
                  Raw JSON
                </summary>
                <pre className="mt-2 overflow-x-auto rounded-lg bg-muted p-3 text-xs">
                  {JSON.stringify(detail, null, 2)}
                </pre>
              </details>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <div className="flex size-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
                <MousePointerClick className="size-6" />
              </div>
              <p className="text-sm text-muted-foreground">
                Select a company from the table to see its profile here.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
