/**
 * Per-session agent sessions.
 *
 * A session is created lazily per `sessionId` and kept alive for 30 minutes
 * of inactivity. Sessions are built with `noTools: "builtin"` plus an
 * explicit denylist of every pi built-in tool, so only the endpoint tools
 * from `tools.ts` are active — the agent has no shell or file access.
 */

import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager,
  type AgentSession,
} from "@earendil-works/pi-coding-agent";

import { agentDir, buildModelRuntime, writeSystemPrompt } from "./model.js";
import { registerTools, type ToolDeps } from "./tools.js";

/**
 * Exactly pi's built-in tool names (`allToolNames` in `core/tools/index.ts`).
 * `bash` and `powershell` are the two shell tools. None of these may ever be
 * active in an agent session.
 */
export const BUILTIN_TOOL_NAMES = [
  "read",
  "bash",
  "powershell",
  "edit",
  "write",
  "grep",
  "find",
  "ls",
] as const;

/** Sessions idle longer than this are dropped on the next access. */
const IDLE_TTL_MS = 30 * 60 * 1000;

interface Entry {
  session: AgentSession;
  lastActive: number;
}

const sessions = new Map<string, Entry>();

function prune(now: number): void {
  for (const [id, entry] of sessions) {
    if (now - entry.lastActive > IDLE_TTL_MS) {
      sessions.delete(id);
    }
  }
}

/**
 * Get (or create) the agent session for `sessionId`.
 *
 * Throws when the chat model is missing, or when any built-in tool leaks
 * into the active tool set (fail-fast: a session with shell or file access
 * is refused rather than served).
 */
export async function getSession(
  sessionId: string,
  deps: ToolDeps,
): Promise<AgentSession> {
  const now = Date.now();
  prune(now);

  const existing = sessions.get(sessionId);
  if (existing) {
    existing.lastActive = now;
    return existing.session;
  }

  writeSystemPrompt();

  const resourceLoader = new DefaultResourceLoader({
    cwd: process.cwd(),
    agentDir,
    extensionFactories: [(pi) => registerTools(pi, deps)],
  });
  await resourceLoader.reload();

  const { modelRuntime, model } = await buildModelRuntime();

  const { session } = await createAgentSession({
    resourceLoader,
    model,
    modelRuntime,
    noTools: "builtin",
    excludeTools: [...BUILTIN_TOOL_NAMES],
    sessionManager: SessionManager.inMemory(),
  });

  const active = session.getActiveToolNames();
  const leaked = active.filter((name) =>
    (BUILTIN_TOOL_NAMES as readonly string[]).includes(name),
  );
  if (leaked.length > 0) {
    throw new Error(
      "agent exposed forbidden built-in tool(s): " + leaked.join(", "),
    );
  }

  sessions.set(sessionId, { session, lastActive: now });
  return session;
}
