import { useCallback, useEffect, useState } from "react"
import {
  ArrowRight,
  Building2,
  FileText,
  Files,
  FolderOpen,
  Layers,
  Sparkles,
} from "lucide-react"

import { api, type CompanySummary } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

interface OverviewPanelProps {
  /** Bumped by the parent after a generation run completes. */
  refreshKey: number
  onNavigate: (tab: string) => void
  /** Opens the generate-companies dialog (owned by the parent). */
  onGenerateCompanies: () => void
}

/** Soft color tints for the stat-card icon tiles. */
const STAT_TONES: Record<string, string> = {
  blue: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  violet: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  amber: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  emerald: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  sky: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
}

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = "blue",
}: {
  icon: typeof Building2
  label: string
  value: string
  hint?: string
  tone?: keyof typeof STAT_TONES
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-5">
        <div
          className={`flex size-11 shrink-0 items-center justify-center rounded-lg ${STAT_TONES[tone]}`}
        >
          <Icon className="size-5" />
        </div>
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="truncate font-heading text-2xl font-semibold tabular-nums">
            {value}
          </p>
          {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

export function OverviewPanel({
  refreshKey,
  onNavigate,
  onGenerateCompanies,
}: OverviewPanelProps) {
  const [companies, setCompanies] = useState<CompanySummary[]>([])
  const [documentCount, setDocumentCount] = useState(0)
  const [storagePath, setStoragePath] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.storage().then((s) => setStoragePath(s.path)).catch(() => undefined)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [companyList, docList] = await Promise.all([
        api.companies(),
        api.documents(),
      ])
      setCompanies(companyList)
      setDocumentCount(docList.length)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  const totalDocumentTypes = companies.reduce((sum, c) => sum + c.num_reports, 0)
  const industryCount = new Set(
    companies.map((c) => c.industry),
  ).size

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          icon={Building2}
          label="Companies"
          value={loading ? "…" : String(companies.length)}
          hint={storagePath || undefined}
        />
        <StatCard
          icon={FileText}
          label="Document types"
          value={loading ? "…" : String(totalDocumentTypes)}
          hint="across all companies"
          tone="violet"
        />
        <StatCard
          icon={Files}
          label="Documents"
          value={loading ? "…" : String(documentCount)}
          hint="generated document files"
          tone="amber"
        />
        <StatCard
          icon={Layers}
          label="Industries"
          value={loading ? "…" : String(industryCount)}
          hint="represented in dataset"
          tone="emerald"
        />
        <StatCard
          icon={FolderOpen}
          label="Storage"
          value="TinyDB"
          hint="file-backed store"
          tone="sky"
        />
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>Recent companies</CardTitle>
            <CardDescription>
              The first few entries in your dataset.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onNavigate("companies")}
          >
            View all
            <ArrowRight />
          </Button>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="text-sm text-destructive">{error}</p>
          ) : loading ? (
            <div className="flex flex-col gap-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : companies.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <div className="flex size-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
                <Sparkles className="size-6" />
              </div>
              <div>
                <p className="font-medium">No companies yet</p>
                <p className="text-sm text-muted-foreground">
                  Generate your first dataset to see it here.
                </p>
              </div>
              <Button onClick={onGenerateCompanies}>
                <Sparkles />
                Generate companies
              </Button>
            </div>
          ) : (
            <ul className="divide-y">
              {companies.slice(0, 5).map((company) => (
                <li
                  key={company.id}
                  className="flex items-center justify-between gap-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">{company.name}</p>
                    <p className="truncate text-sm text-muted-foreground">
                      {company.industry} · {company.headquarters}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant="secondary" className="capitalize">
                      {company.size}
                    </Badge>
                    <span className="text-sm tabular-nums text-muted-foreground">
                      {company.num_reports} document types
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
