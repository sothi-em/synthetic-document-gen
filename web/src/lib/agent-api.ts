/** Client for the proxied pi agent (``/api/agent/*``), which streams SSE. */

/** Payload of the agent ``GET /api/agent/health`` endpoint. */
export interface AgentHealth {
  ok: boolean
}

/** A tool-execution event streamed by the agent. */
export interface AgentToolEvent {
  name: string
  phase: "start" | "end"
  args?: unknown
  result?: unknown
}

/** A UI action the agent asks the app to perform (via the ``ui`` tool). */
export interface UiAction {
  type: "navigate_tab" | "select_company" | "open_document"
  tab?: string
  company_id?: number
  document_id?: number
}

/** Parameters for one chat turn. */
export interface AgentChatParams {
  sessionId: string
  message: string
  /** Serialized screen state, sent verbatim to the agent. */
  screen: unknown
}

/** Callbacks for the agent's streamed events. */
export interface AgentChatHandlers {
  onToken: (text: string) => void
  onTool: (tool: AgentToolEvent) => void
  onUi: (action: UiAction) => void
  onDone: (text: string) => void
  onError: (message: string) => void
}

/**
 * Check whether the agent is available.
 *
 * @returns The agent's health payload.
 * @throws When the agent is unreachable or reports unhealthy (e.g. 503).
 */
export async function agentHealth(): Promise<AgentHealth> {
  const response = await fetch("/api/agent/health")
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`)
  }
  return (await response.json()) as AgentHealth
}

/**
 * Send a chat turn and stream the agent's SSE response.
 *
 * @param params - The turn's session id, message, and screen state.
 * @param handlers - Callbacks invoked as events arrive.
 * @returns A cancel function that aborts the in-flight request.
 */
export function agentChat(
  params: AgentChatParams,
  handlers: AgentChatHandlers,
): () => void {
  const controller = new AbortController()

  void (async () => {
    try {
      const response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
        signal: controller.signal,
      })
      if (!response.ok || !response.body) {
        handlers.onError(`${response.status}: ${await response.text()}`)
        return
      }
      await readSse(response.body, handlers)
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError(err instanceof Error ? err.message : String(err))
      }
    }
  })()

  return () => controller.abort()
}

/**
 * Read an SSE stream, dispatching each complete event to the handlers.
 * Events are delimited by blank lines; an ``event:`` line names the event
 * and one or more ``data:`` lines carry its (JSON) payload.
 */
async function readSse(
  body: ReadableStream<Uint8Array>,
  handlers: AgentChatHandlers,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let eventName = ""
  let data = ""

  const dispatch = () => {
    if (data !== "") {
      handleEvent(eventName || "message", data, handlers)
    }
    eventName = ""
    data = ""
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let newline: number
    while ((newline = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, newline).replace(/\r$/, "")
      buffer = buffer.slice(newline + 1)

      if (line === "") {
        dispatch()
      } else if (line.startsWith("event:")) {
        eventName = line.slice("event:".length).trim()
      } else if (line.startsWith("data:")) {
        const chunk = line.slice("data:".length).replace(/^ /, "")
        data = data === "" ? chunk : `${data}\n${chunk}`
      }
      // Ignore comments (":…") and fields we don't use (id/, retry/).
    }
  }
  // Flush a trailing event that wasn't blank-line terminated.
  dispatch()
}

function handleEvent(
  event: string,
  data: string,
  handlers: AgentChatHandlers,
): void {
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    payload = data
  }
  const obj = (typeof payload === "object" && payload !== null ? payload : {}) as Record<
    string,
    unknown
  >
  switch (event) {
    case "token":
      handlers.onToken(typeof obj.text === "string" ? obj.text : "")
      break
    case "tool":
      handlers.onTool({
        name: typeof obj.name === "string" ? obj.name : "",
        phase: obj.phase === "end" ? "end" : "start",
        args: obj.args,
        result: obj.result,
      })
      break
    case "ui":
      handlers.onUi(payload as UiAction)
      break
    case "done":
      handlers.onDone(typeof obj.text === "string" ? obj.text : "")
      break
    case "error":
      handlers.onError(
        typeof obj.message === "string" ? obj.message : String(payload),
      )
      break
    default:
      break
  }
}
