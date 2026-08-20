import { useEffect, useState } from "react"
import {
  AlertTriangle,
  CircleCheck,
  FolderOpen,
  LoaderCircle,
  Moon,
  Palette,
  PlugZap,
  RotateCcw,
  Save,
  Sun,
} from "lucide-react"

import {
  api,
  type EndpointConfig,
  type MaskedEndpoint,
  type DocumentsSettings,
  type SettingsTestResult,
} from "@/lib/api"
import {
  THEMES,
  type ThemeMode,
  type ThemeName,
  getStoredMode,
  getStoredTheme,
  setMode,
  setTheme,
} from "@/lib/theme"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { DirectoryBrowserDialog } from "@/components/directory-browser-dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

interface SettingsPanelProps {
  onSaved: () => void
}

/** Dot colors shown on the theme picker buttons. */
const THEME_SWATCHES: Record<ThemeName, string> = {
  blue: "bg-blue-600 dark:bg-blue-400",
  emerald: "bg-emerald-600 dark:bg-emerald-400",
  violet: "bg-violet-600 dark:bg-violet-400",
  rose: "bg-rose-600 dark:bg-rose-400",
  gray: "bg-neutral-500",
}

/** Brand theme + light/dark picker. Persists in localStorage (browser-local). */
function AppearanceCard() {
  const [theme, setThemeState] = useState<ThemeName>(getStoredTheme)
  const [mode, setModeState] = useState<ThemeMode>(getStoredMode)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Palette />
          Appearance
        </CardTitle>
        <CardDescription>
          Brand color and light/dark mode. Saved in this browser only.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm text-muted-foreground">Theme</span>
          <div className="flex flex-wrap gap-2">
            {THEMES.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => {
                  setTheme(name)
                  setThemeState(name)
                }}
                aria-pressed={theme === name}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm capitalize transition-colors",
                  theme === name
                    ? "border-primary bg-primary/10 text-primary"
                    : "hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <span
                  className={cn(
                    "size-3.5 rounded-full",
                    THEME_SWATCHES[name],
                  )}
                  aria-hidden
                />
                {name}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm text-muted-foreground">Mode</span>
          <div className="flex gap-2">
            {([
              ["light", Sun, "Light"],
              ["dark", Moon, "Dark"],
            ] as const).map(([value, Icon, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setMode(value)
                  setModeState(value)
                }}
                aria-pressed={mode === value}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors",
                  mode === value
                    ? "border-primary bg-primary/10 text-primary"
                    : "hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </button>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

interface EndpointDraft {
  backend: "ollama" | "openai"
  host: string
  api_key: string
  model: string
}

interface TestState {
  testing: boolean
  result: SettingsTestResult | null
}

function toDraft(masked: MaskedEndpoint): EndpointDraft {
  return {
    backend: masked.backend,
    host: masked.host ?? "",
    api_key: masked.api_key ?? "",
    model: masked.model ?? "",
  }
}

function toConfig(draft: EndpointDraft): EndpointConfig {
  return {
    backend: draft.backend,
    host: draft.host.trim() || null,
    api_key: draft.api_key.trim() === "" ? null : draft.api_key.trim(),
    model: draft.model.trim() || null,
  }
}

interface EndpointCardProps {
  title: string
  description: string
  draft: EndpointDraft
  onDraftChange: (draft: EndpointDraft) => void
  test: TestState
  onTest: () => void
}

function EndpointCard({
  title,
  description,
  draft,
  onDraftChange,
  test,
  onTest,
}: EndpointCardProps) {
  const set = (patch: Partial<EndpointDraft>) => onDraftChange({ ...draft, ...patch })

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
          Backend
          <Select
            value={draft.backend}
            onValueChange={(value) =>
              set({ backend: value as EndpointDraft["backend"] })
            }
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ollama">Ollama</SelectItem>
              <SelectItem value="openai">OpenAI-compatible (llama.cpp)</SelectItem>
            </SelectContent>
          </Select>
        </label>
        {draft.backend === "ollama" ? (
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            Host
            <Input
              value={draft.host}
              onChange={(e) => set({ host: e.target.value })}
              placeholder="http://localhost:11434"
            />
          </label>
        ) : (
          <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
            Base URL
            <Input
              value={draft.host}
              onChange={(e) => set({ host: e.target.value })}
              placeholder="http://localhost:8080/v1"
            />
          </label>
        )}
        <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
          API key
          <Input
            type="password"
            value={draft.api_key}
            onChange={(e) => set({ api_key: e.target.value })}
            placeholder={
              draft.backend === "ollama"
                ? "optional (e.g. for an authenticated proxy)"
                : "optional for local servers"
            }
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
          Model
          <Input
            value={draft.model}
            onChange={(e) => set({ model: e.target.value })}
            placeholder="default model"
          />
        </label>
      </CardContent>
      <CardFooter className="flex items-center gap-3">
        <Button variant="outline" onClick={onTest} disabled={test.testing}>
          {test.testing ? (
            <LoaderCircle className="animate-spin" />
          ) : (
            <PlugZap />
          )}
          Test
        </Button>
        {test.result &&
          (test.result.ok ? (
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <CircleCheck className="size-4 text-success" />
              {test.result.model_count} models reachable
            </span>
          ) : (
            <span className="flex min-w-0 items-center gap-1.5 text-sm text-destructive">
              <AlertTriangle className="size-4 shrink-0" />
              <span className="truncate" title={test.result.error ?? undefined}>
                {test.result.error}
              </span>
            </span>
          ))}
      </CardFooter>
    </Card>
  )
}

export function SettingsPanel({ onSaved }: SettingsPanelProps) {
  const [chat, setChat] = useState<EndpointDraft | null>(null)
  const [embed, setEmbed] = useState<EndpointDraft | null>(null)
  const [chatTest, setChatTest] = useState<TestState>({ testing: false, result: null })
  const [embedTest, setEmbedTest] = useState<TestState>({
    testing: false,
    result: null,
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Document output directory
  const [docSettings, setDocSettings] = useState<DocumentsSettings | null>(null)
  const [dirDraft, setDirDraft] = useState("")
  const [dirInitialized, setDirInitialized] = useState(false)
  const [browserOpen, setBrowserOpen] = useState(false)
  const [savingDir, setSavingDir] = useState(false)
  const [dirSaved, setDirSaved] = useState(false)
  const [dirError, setDirError] = useState<string | null>(null)

  async function load() {
    try {
      const settings = await api.settings()
      setChat(toDraft(settings.chat))
      setEmbed(toDraft(settings.embed))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
    try {
      const docSettings = await api.documentsSettings()
      setDocSettings(docSettings)
      // Only seed the draft from the server once, so a later reload
      // (e.g. after "Reset to .env defaults") doesn't clobber edits.
      if (!dirInitialized) {
        setDirDraft(docSettings.output_dir ?? "")
        setDirInitialized(true)
      }
    } catch (err) {
      setDirError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleSaveDir(value?: string) {
    const target = (value ?? dirDraft).trim()
    setSavingDir(true)
    setDirSaved(false)
    setDirError(null)
    try {
      const updated = await api.saveDocumentsSettings(target === "" ? null : target)
      setDocSettings(updated)
      setDirDraft(updated.output_dir ?? "")
      setDirSaved(true)
      onSaved()
    } catch (err) {
      setDirError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingDir(false)
    }
  }

  async function handleTest(
    purpose: "chat" | "embed",
    draft: EndpointDraft,
  ) {
    const setState = purpose === "chat" ? setChatTest : setEmbedTest
    setState({ testing: true, result: null })
    try {
      const result = await api.testSettings(purpose, toConfig(draft))
      setState({ testing: false, result })
    } catch (err) {
      setState({
        testing: false,
        result: {
          ok: false,
          model_count: 0,
          models: [],
          error: err instanceof Error ? err.message : String(err),
        },
      })
    }
  }

  async function handleSave() {
    if (!chat || !embed || saving) return
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      await api.saveSettings({ chat: toConfig(chat), embed: toConfig(embed) })
      setSaved(true)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleReset() {
    setError(null)
    try {
      await api.clearSettings()
      await load()
      setChatTest({ testing: false, result: null })
      setEmbedTest({ testing: false, result: null })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  if (!chat || !embed) {
    return (
      <p className="text-sm text-muted-foreground">
        {error ?? "Loading settings…"}
      </p>
    )
  }

  return (
    <div className="grid items-start gap-6">
      <AppearanceCard />
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <EndpointCard
          title="Chat model"
          description="Endpoint used for company and label generation."
          draft={chat}
          onDraftChange={setChat}
          test={chatTest}
          onTest={() => void handleTest("chat", chat)}
        />
        <EndpointCard
          title="Embedding model"
          description="Endpoint used to embed data labels into ChromaDB."
          draft={embed}
          onDraftChange={setEmbed}
          test={embedTest}
          onTest={() => void handleTest("embed", embed)}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Document output directory</CardTitle>
          <CardDescription>
            Where generated PDF documents are saved. PDF generation stays
            disabled until a directory is set here or via the DOCUMENTS_DIR
            env var.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span className="shrink-0 text-muted-foreground">Effective:</span>
            {docSettings?.output_dir ? (
              <>
                <code
                  className="min-w-0 truncate rounded-md bg-secondary px-2 py-1 text-xs"
                  title={docSettings.output_dir}
                >
                  {docSettings.output_dir}
                </code>
                <Badge variant="secondary" className="shrink-0">
                  {docSettings.source === "saved" ? "saved" : "env default"}
                </Badge>
              </>
            ) : (
              <Badge variant="destructive" className="shrink-0">
                not set — PDF generation disabled
              </Badge>
            )}
          </div>
          <div className="flex gap-2">
            <Input
              value={dirDraft}
              disabled={savingDir}
              onChange={(e) => setDirDraft(e.target.value)}
              placeholder="e.g. C:\documents or /home/user/documents"
              aria-label="Document output directory path"
            />
            <Button
              variant="outline"
              disabled={savingDir}
              onClick={() => setBrowserOpen(true)}
            >
              <FolderOpen />
              Browse
            </Button>
            <Button disabled={savingDir} onClick={() => void handleSaveDir()}>
              {savingDir ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Save />
              )}
              Save
            </Button>
          </div>
          {dirSaved && (
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <CircleCheck className="size-4 text-success" />
              Saved
            </span>
          )}
          {dirError && (
            <span className="flex min-w-0 items-center gap-1.5 text-sm text-destructive">
              <AlertTriangle className="size-4 shrink-0" />
              <span className="truncate" title={dirError}>
                {dirError}
              </span>
            </span>
          )}
        </CardContent>
      </Card>
      <DirectoryBrowserDialog
        open={browserOpen}
        onOpenChange={setBrowserOpen}
        initialPath={
          dirDraft.trim() !== "" ? dirDraft.trim() : (docSettings?.output_dir ?? null)
        }
        onSelect={(path) => void handleSaveDir(path)}
      />

      <Card>
        <CardFooter className="flex items-center gap-4 rounded-t-xl">
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <Save />
            )}
            Save settings
          </Button>
          <Button variant="ghost" onClick={() => void handleReset()}>
            <RotateCcw />
            Reset to .env defaults
          </Button>
          {saved && (
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <CircleCheck className="size-4 text-success" />
              Saved
            </span>
          )}
          {error && (
            <span className="flex min-w-0 items-center gap-1.5 text-sm text-destructive">
              <AlertTriangle className="size-4 shrink-0" />
              <span className="truncate" title={error}>
                {error}
              </span>
            </span>
          )}
        </CardFooter>
      </Card>
    </div>
  )
}
