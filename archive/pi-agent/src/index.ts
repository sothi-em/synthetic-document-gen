/**
 * Agent HTTP server.
 *
 * Routes:
 *   GET  /health  -> 200 { ok: true }
 *   POST /chat    -> SSE stream of agent events
 *
 * `POST /chat` body: `{ sessionId, message, screen }`. The response is an
 * SSE stream with `data:` payloads (JSON) for these events:
 *   token  { text }             assistant text deltas
 *   tool   { name, phase, args?, result? }
 *   ui     { type, ... }        UI action requested by the agent
 *   done   { text }             final assistant text
 *   error  { message }
 */

import * as http from "node:http";

import type {
  AgentSession,
  AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";

import { getSession } from "./session.js";
import { writeSystemPrompt } from "./model.js";
import type { UiAction } from "./tools.js";

const PORT = Number(process.env.AGENT_PORT ?? 8090);

/** sessionId -> response currently streaming (one prompt per session). */
const busy = new Map<string, http.ServerResponse>();

/** Write one SSE event; no-ops once the response is finished. */
function sse(res: http.ServerResponse, event: string, data: unknown): void {
  if (res.writableEnded) return;
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

interface ChatBody {
  sessionId?: unknown;
  message?: unknown;
  screen?: unknown;
}

function badRequest(res: http.ServerResponse, error: string): void {
  res.writeHead(400, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error }));
}

async function streamChat(
  sessionId: string,
  message: string,
  screen: unknown,
  res: http.ServerResponse,
): Promise<void> {
  const emitUi = (action: UiAction): void => {
    sse(res, "ui", action);
  };

  try {
    const session: AgentSession = await getSession(sessionId, { emitUi });
    if (busy.has(sessionId)) {
      sse(res, "error", { message: "already running" });
      return;
    }
    busy.set(sessionId, res);

    let lastAssistantText = "";
    const unsubscribe = session.subscribe((event: AgentSessionEvent) => {
      switch (event.type) {
        case "message_update": {
          const ame = event.assistantMessageEvent;
          if (ame.type === "text_delta") {
            sse(res, "token", { text: ame.delta });
          }
          break;
        }
        case "tool_execution_start":
          sse(res, "tool", {
            name: event.toolName,
            phase: "start",
            args: event.args,
          });
          break;
        case "tool_execution_end":
          sse(res, "tool", {
            name: event.toolName,
            phase: "end",
            result: event.result,
          });
          break;
        case "message_end": {
          const msg = event.message;
          if (msg.role === "assistant") {
            lastAssistantText = msg.content
              .filter((c) => c.type === "text")
              .map((c) => c.text)
              .join("");
          }
          break;
        }
        default:
          break;
      }
    });

    const screenBlock = `<screen>\n${JSON.stringify(screen)}\n</screen>\n\n`;
    try {
      // Resolves once the agent has fully settled (after agent_settled).
      await session.prompt(screenBlock + message);
    } finally {
      unsubscribe();
    }
    sse(res, "done", { text: lastAssistantText });
  } catch (err) {
    sse(res, "error", {
      message: err instanceof Error ? err.message : String(err),
    });
  } finally {
    busy.delete(sessionId);
    if (!res.writableEnded) {
      res.end();
    }
  }
}

function handleChat(req: http.IncomingMessage, res: http.ServerResponse): void {
  let raw = "";
  req.on("data", (chunk: string) => {
    raw += chunk;
  });
  req.on("end", () => {
    let body: ChatBody;
    try {
      body = JSON.parse(raw || "{}") as ChatBody;
    } catch {
      badRequest(res, "invalid JSON body");
      return;
    }
    const sessionId =
      typeof body.sessionId === "string" && body.sessionId !== ""
        ? body.sessionId
        : "default";
    const message =
      typeof body.message === "string" ? body.message.trim() : "";
    if (message === "") {
      badRequest(res, "message is required");
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    if (busy.has(sessionId)) {
      sse(res, "error", { message: "already running" });
      res.end();
      return;
    }

    void streamChat(sessionId, message, body.screen ?? {}, res);
  });
}

const server = http.createServer((req, res) => {
  const url = (req.url ?? "/").split("?")[0];
  if (req.method === "GET" && url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
    return;
  }
  if (req.method === "POST" && url === "/chat") {
    handleChat(req, res);
    return;
  }
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not found" }));
});

// Write the system prompt once at startup so `<agentDir>/SYSTEM.md`
// exists as soon as the agent is running (getSession also re-writes it
// when creating the first session).
writeSystemPrompt();

server.listen(PORT, "127.0.0.1", () => {
  console.log(`agent listening on http://127.0.0.1:${PORT}`);
});
