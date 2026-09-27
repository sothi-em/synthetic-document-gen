import { useEffect, useRef, useState } from "react"
import { Bot, Check, Loader2, RotateCcw, Send, Sparkles } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { agentChat, agentHealth } from "@/lib/agent-api"
import { useScreenContext } from "@/lib/screen-context"

/** A single tool-execution chip shown under an assistant message. */
interface ToolChip {
  name: string
  phase: "start" | "end"
  result?: string
}

/** One chat message. */
interface ChatMessage {
  role: "user" | "assistant"
  text: string
  tools?: ToolChip[]
}

interface AssistantPanelProps {
  /** Switch the active app tab (from a `navigate_tab` ui action). */
  onNavigate: (tab: string) => void
  /** Select a company (from a `select_company` ui action). */
  onSelectCompany: (id: number) => void
  /** Open a document (from an `open_document` ui action). */
  onOpenDocument: (id: number) => void
  /** Bump the data-refresh key (called when a turn completes). */
  onRefresh: () => void
}

const SESSION_KEY = "document-gen.assistant-session"

function loadSessionId(): string {
  const existing = sessionStorage.getItem(SESSION_KEY)
  if (existing) return existing
  const id = crypto.randomUUID()
  sessionStorage.setItem(SESSION_KEY, id)
  return id
}

/** Render a tool result as short display text for the chip tooltip. */
function toolResultText(result: unknown): string | undefined {
  if (result === undefined || result === null) return undefined
  const text = typeof result === "string" ? result : JSON.stringify(result)
  return text.length > 300 ? `${text.slice(0, 300)}…` : text
}

export function AssistantPanel({
  onNavigate,
  onSelectCompany,
  onOpenDocument,
  onRefresh,
}: AssistantPanelProps) {
  const { screen } = useScreenContext()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  // null = still checking agent availability on mount.
  const [available, setAvailable] = useState<boolean | null>(null)
  const [sessionId, setSessionId] = useState(loadSessionId)

  const cancelRef = useRef<(() => void) | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    agentHealth()
      .then(() => setAvailable(true))
      .catch(() => setAvailable(false))
  }, [])

  // Keep the newest message in view as tokens stream in.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  // Abort any in-flight stream on unmount.
  useEffect(() => () => cancelRef.current?.(), [])

  const contextChars =
    messages.reduce((sum, m) => sum + m.text.length, 0) +
    JSON.stringify(screen).length

  const canSend = !busy && available === true && input.trim() !== ""

  const updateLast = (fn: (m: ChatMessage) => ChatMessage) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev
      const next = prev.slice()
      next[next.length - 1] = fn(next[next.length - 1])
      return next
    })
  }

  const handleUi = (action: {
    type: string
    tab?: string
    company_id?: number
    document_id?: number
  }) => {
    if (action.type === "navigate_tab" && action.tab) {
      onNavigate(action.tab)
    } else if (action.type === "select_company" && action.company_id != null) {
      onNavigate("companies")
      onSelectCompany(action.company_id)
    } else if (action.type === "open_document" && action.document_id != null) {
      onNavigate("documents")
      onOpenDocument(action.document_id)
    }
  }

  const send = () => {
    const message = input.trim()
    if (!canSend || message === "") return
    setInput("")
    setBusy(true)
    setMessages((prev) => [
      ...prev,
      { role: "user", text: message },
      { role: "assistant", text: "", tools: [] },
    ])

    cancelRef.current = agentChat(
      { sessionId, message, screen },
      {
        onToken: (text) =>
          updateLast((m) => ({ ...m, text: m.text + text })),
        onTool: (tool) =>
          updateLast((m) => {
            const result = toolResultText(tool.result)
            const tools = m.tools ?? []
            const pending =
              tool.phase === "end"
                ? tools.findLast(
                    (t) => t.name === tool.name && t.phase === "start",
                  )
                : undefined
            if (pending) {
              return {
                ...m,
                tools: tools.map((t) =>
                  t === pending ? { ...t, phase: "end", result } : t,
                ),
              }
            }
            return {
              ...m,
              tools: [...tools, { name: tool.name, phase: tool.phase, result }],
            }
          }),
        onUi: handleUi,
        onDone: () => {
          setBusy(false)
          cancelRef.current = null
          onRefresh()
        },
        onError: (err) => {
          setBusy(false)
          cancelRef.current = null
          setAvailable(false)
          updateLast((m) => ({
            ...m,
            text: m.text === "" ? `⚠️ ${err}` : `${m.text}\n\n⚠️ ${err}`,
          }))
        },
      },
    )
  }

  const reset = () => {
    cancelRef.current?.()
    cancelRef.current = null
    setBusy(false)
    setMessages([])
    setInput("")
    const id = crypto.randomUUID()
    sessionStorage.setItem(SESSION_KEY, id)
    setSessionId(id)
  }

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <aside className="flex h-full min-h-0 w-[360px] shrink-0 flex-col rounded-xl border bg-card">
      <header className="border-b px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex size-6 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Bot className="size-4" />
            </div>
            <span className="font-medium">Assistant</span>
            <StatusBadge available={available} />
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={reset}
            disabled={busy}
            aria-label="Start a new chat"
            title="Start a new chat"
          >
            <RotateCcw className="size-3.5" />
          </Button>
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          Context ≈ {(contextChars / 1024).toFixed(1)} KB · ~
          {Math.round(contextChars / 4)} tokens
        </p>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <EmptyState available={available} />
        ) : (
          messages.map((m, i) =>
            m.role === "user" ? (
              <div
                key={i}
                className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
              >
                {m.text}
              </div>
            ) : (
              <div key={i} className="max-w-full">
                {m.tools && m.tools.length > 0 && (
                  <ToolChips tools={m.tools} />
                )}
                {m.text !== "" && (
                  <div className="prose prose-sm max-w-none dark:prose-invert [&_a]:text-primary [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-muted [&_pre]:p-2 [&_pre]:text-xs [&_table]:w-full [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        pre: (props) => (
                          <pre
                            className="overflow-x-auto rounded bg-muted p-2 text-xs"
                            {...props}
                          />
                        ),
                      }}
                    >
                      {m.text}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            ),
          )
        )}
      </div>

      <footer className="border-t p-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={
              available === false
                ? "Agent unavailable"
                : "Ask about your companies or documents…"
            }
            disabled={available === false}
            rows={2}
            className="min-h-12 flex-1 resize-none"
          />
          <Button
            onClick={send}
            disabled={!canSend}
            size="icon"
            aria-label="Send message"
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
          </Button>
        </div>
        <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
          <Sparkles className="size-3" />
          Enter to send · Shift+Enter for a new line
        </p>
      </footer>
    </aside>
  )
}

function StatusBadge({ available }: { available: boolean | null }) {
  if (available === null) {
    return (
      <Badge variant="secondary" className="gap-1 bg-warning/10 text-warning">
        <Loader2 className="size-3 animate-spin" />
        checking
      </Badge>
    )
  }
  if (available) {
    return (
      <Badge variant="secondary" className="gap-1 bg-success/10 text-success">
        <Check className="size-3" />
        ready
      </Badge>
    )
  }
  return (
    <Badge variant="destructive" className="gap-1">
      <Bot className="size-3" />
      unavailable
    </Badge>
  )
}

function ToolChips({ tools }: { tools: ToolChip[] }) {
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5">
      {tools.map((t, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-md border bg-secondary/50 px-1.5 py-0.5 text-xs text-secondary-foreground"
          title={t.result}
        >
          {t.phase === "start" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Check className="size-3 text-success" />
          )}
          <span className="font-mono">{t.name}</span>
        </span>
      ))}
    </div>
  )
}

function EmptyState({ available }: { available: boolean | null }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 py-12 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
        <Bot className="size-6" />
      </div>
      <p className="font-medium">
        {available === false ? "Agent unavailable" : "Ask the assistant"}
      </p>
      <p className="max-w-xs text-sm text-muted-foreground">
        {available === false
          ? "The agent could not be reached. Check that it is running, then reload."
          : "Query your companies and documents, or ask it to generate and save new data. It sees the screen you're on."}
      </p>
    </div>
  )
}
