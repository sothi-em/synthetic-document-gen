import { useEffect, useState } from "react"
import {
  Activity,
  Building2,
  Database,
  FileText,
  FolderOpen,
  LayoutDashboard,
  Settings,
  Sparkles,
} from "lucide-react"

import { api, type HealthInfo } from "@/lib/api"
import { truncateMiddle } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CompaniesPanel } from "@/components/companies-panel"
import { GenerateCompaniesDialog } from "@/components/generate-dialogs"
import { LabelsPanel } from "@/components/labels-panel"
import { DocumentsPanel } from "@/components/documents-panel"
import { DocumentTypesPanel } from "@/components/document-types-panel"
import { OverviewPanel } from "@/components/overview-panel"
import { SettingsPanel } from "@/components/settings-panel"

export default function App() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [industries, setIndustries] = useState<string[]>([])
  const [models, setModels] = useState<string[]>([])
  const [refreshKey, setRefreshKey] = useState(0)
  const [settingsVersion, setSettingsVersion] = useState(0)
  const [tab, setTab] = useState("overview")
  const [documentCount, setDocumentCount] = useState(0)
  const [generateCompaniesOpen, setGenerateCompaniesOpen] = useState(false)
  // Owned here (not in the panel) so the Companies tab's selection
  // survives tab switches — inactive tab content is unmounted.
  const [selectedCompanyId, setSelectedCompanyId] = useState<number | null>(
    null,
  )

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.industries().then(setIndustries).catch(() => setIndustries([]))
    api.models().then(setModels).catch(() => setModels([]))
  }, [settingsVersion])

  useEffect(() => {
    api
      .documents()
      .then((docs) => setDocumentCount(docs.length))
      .catch(() => setDocumentCount(0))
  }, [refreshKey, tab])

  const chatUp = health?.chat.status === "up"

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Sparkles className="size-4.5" />
            </div>
            <div>
              <h1 className="font-heading text-base font-semibold leading-tight">
                document-gen
              </h1>
              <p className="text-xs text-muted-foreground">
                synthetic company dashboard
              </p>
            </div>
          </div>
          {health === null ? (
            <Badge variant="secondary" className="bg-warning/10 text-warning">
              api unreachable
            </Badge>
          ) : (
            <Badge
              variant={chatUp ? "secondary" : "destructive"}
              className={
                chatUp
                  ? "gap-1.5 bg-success/10 text-success"
                  : "gap-1.5"
              }
            >
              <Activity className="size-3" />
              {health.chat.backend} {health.chat.status}
              {chatUp && health.chat.model ? (
                <span title={health.chat.model}>
                  {` · ${truncateMiddle(health.chat.model)}`}
                </span>
              ) : ("")}
            </Badge>
          )}
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-6">
        <Tabs value={tab} onValueChange={setTab} className="flex flex-col">
          <TabsList className="mb-6 grid h-auto w-fit grid-cols-2 gap-1 sm:grid-cols-6">
            <TabsTrigger value="overview" className="gap-2">
              <LayoutDashboard className="size-4" />
              Overview
            </TabsTrigger>
            <TabsTrigger value="companies" className="gap-2">
              <Building2 className="size-4" />
              Companies
            </TabsTrigger>
            <TabsTrigger value="labels" className="gap-2">
              <Database className="size-4" />
              Labels
            </TabsTrigger>
            <TabsTrigger value="document-types" className="gap-2">
              <FileText className="size-4" />
              Document types
            </TabsTrigger>
            <TabsTrigger value="documents" className="gap-2">
              <FolderOpen className="size-4" />
              Documents
              <Badge
                className="h-5 rounded-full bg-primary px-1.5 font-semibold text-primary-foreground"
              >
                {documentCount}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="settings" className="gap-2">
              <Settings className="size-4" />
              Settings
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview">
            <OverviewPanel
              refreshKey={refreshKey}
              onNavigate={setTab}
              onGenerateCompanies={() => setGenerateCompaniesOpen(true)}
            />
          </TabsContent>
          <TabsContent value="companies">
            <CompaniesPanel
              industries={industries}
              refreshKey={refreshKey}
              onGenerate={() => setGenerateCompaniesOpen(true)}
              selectedCompanyId={selectedCompanyId}
              onSelectCompany={setSelectedCompanyId}
            />
          </TabsContent>
          <TabsContent value="labels">
            <LabelsPanel />
          </TabsContent>
          <TabsContent value="document-types">
            <DocumentTypesPanel
              models={models}
              refreshKey={refreshKey}
              settingsVersion={settingsVersion}
              onGenerated={() => setRefreshKey((key) => key + 1)}
            />
          </TabsContent>
          <TabsContent value="documents">
            <DocumentsPanel refreshKey={refreshKey} />
          </TabsContent>
          <TabsContent value="settings">
            <SettingsPanel onSaved={() => setSettingsVersion((v) => v + 1)} />
          </TabsContent>
        </Tabs>
      </main>

      <GenerateCompaniesDialog
        open={generateCompaniesOpen}
        onOpenChange={setGenerateCompaniesOpen}
        industries={industries}
        models={models}
        onGenerated={() => setRefreshKey((key) => key + 1)}
      />

      <footer className="border-t px-6 py-3">
        <p className="mx-auto w-full max-w-6xl text-xs text-muted-foreground">
          API docs at <a href="/docs" className="underline">/docs</a>
        </p>
      </footer>
    </div>
  )
}
