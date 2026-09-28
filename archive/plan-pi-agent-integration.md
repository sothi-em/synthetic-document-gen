# pi-sdk Agent Integration

## Context
Integrate the pi agent (`@earendil-works/pi-coding-agent`, a Node.js/Bun TypeScript SDK) into the document-gen web app so users get an interactive chat + agentic assistant in the frontend. The agent (1) sees the user's current screen, (2) uses every accessible backend endpoint as a tool, and (3) answers queries and takes actions by reasoning over those endpoints.

End state: a persistent right-side chat panel. FastAPI spawns the Node agent on startup and proxies it at `/api/agent/*` (single origin, one command). The agent has a tool per endpoint (destructive ones gated behind propose→confirm), receives a serialized screen context with each message, and can navigate/select in the UI.

Locked decisions (confirmed with user): FastAPI spawns + proxies the Node agent; destructive actions require in-chat confirmation; persistent right-side panel; the agent can navigate & select in the UI.

## Implementation steps (sequential)
Each step is independently executable and verifiable; do them in order. A step's "Done when" is its completion check; the "how" lives in the referenced "Approach" section (e.g. §3).

**Phase 1 — Agent scaffold**
1. Create `agent/` project files (`package.json`, `tsconfig.json`, `.gitignore`) per §1; `cd agent && pnpm install`. Done when: install succeeds and `node_modules` has `@earendil-works/pi-coding-agent` + `typebox`.
2. Stub `agent/src/index.ts` as a trivial ESM entry (log "agent ok"). Done when: `pnpm build` emits `agent/dist/index.js` and `node dist/index.js` prints "agent ok".

**Phase 2 — Agent core modules**
3. `agent/src/backend.ts`: `apiGet`/`apiSend`/`apiWaitJob` (§2). Done when: `tsc` clean.
4. `agent/src/config.ts`: `getChatConfig()` + ollama/openai provider mapping (§2). Done when: `tsc` clean.
5. `agent/src/model.ts`: `buildModelRuntime()` (write `models.json`, resolve model, throw if missing) + `writeSystemPrompt()` (§2). Done when: `tsc` clean.
6. `agent/src/confirm.ts`: `propose`/`consume` in-memory `Map`, 5-min TTL (§3). Done when: `tsc` clean; `propose`→`consume` round-trips and an expired id returns null.
7. `agent/src/tools.ts`: `registerTools` with all endpoint tools — read, generate (block via `apiWaitJob`), mutate, destructive (propose-only), `confirm_action`, `ui` (§3). Done when: `tsc` clean; every endpoint has a tool and no destructive tool calls its endpoint directly.

**Phase 3 — Agent server (sessions + SSE)**
8. `agent/src/session.ts`: `getSession` with `DefaultResourceLoader` + `createAgentSession({ noTools:"builtin", excludeTools:BUILTIN_TOOL_NAMES })` + fail-fast built-in assertion + 30-min prune (§4). Done when: `tsc` clean and a created session reports 0 active built-ins.
9. `agent/src/index.ts`: `node:http` server — `GET /health`, `POST /chat` SSE (`token`/`tool`/`ui`/`done`/`error`, `<screen>` prefix, already-running guard) (§4). Done when: `pnpm build` passes; `node dist/index.js` + `curl localhost:8090/health` → `{"ok":true}`.
10. Standalone SSE smoke (backend + chat LLM up): `curl -N -X POST localhost:8090/chat` with a message. Done when: events stream in order and end with `done`.

**Phase 4 — Backend (Python)**
11. `pyproject.toml`: move `httpx>=0.27` from dev to main `dependencies`; `uv sync` (§5). Done when: `uv run python -c "import httpx"` succeeds in the main env.
12. `document_gen/agent.py`: `AgentHost` (free port, env, `node`/entry resolution, graceful no-node, `start`/`wait_ready`/`stop`) + `proxy_chat`/`proxy_health` (§5). Done when: the module imports cleanly.
13. `document_gen/server.py`: `lifespan` spawn/stop + `app.state.agent_host`; `GET /api/agent-config`; `POST /api/agent/chat` + `GET /api/agent/health` proxy routes before the static mount (§5). Done when: `uv run pytest` passes.
14. Backend smoke: `uv run document-gen serve --port 8000` → agent spawned. Done when: `curl localhost:8000/api/agent/health` → 200 and `curl localhost:8000/api/agent-config` → the chat endpoint.

**Phase 5 — Frontend: screen context**
15. `web/src/lib/screen-context.tsx`: `ScreenState`, `ScreenProvider`, `useScreenContext` (§6). Done when: `tsc` clean.
16. Wire reporting: `App.tsx` (wrap in `ScreenProvider`, `report({activeTab, selectedCompanyId})`), `CompaniesPanel.tsx` (`visibleCompanies` + `selectedCompany`), `DocumentsPanel.tsx` (`visibleDocuments`) (§6). Done when: `tsc` clean.

**Phase 6 — Frontend: chat panel**
17. `web/package.json`: add `react-markdown` + `remark-gfm`; `pnpm install` (§7). Done when: installed.
18. `web/src/lib/agent-api.ts`: `agentHealth` + `agentChat` (fetch + SSE line parser + cancel) (§7). Done when: `tsc` clean.
19. `web/src/components/assistant-panel.tsx`: messages/streaming state, markdown rendering, context-size subtext, `ui`-event handling, confirm affordance, availability badge (§7). Done when: `tsc` clean.
20. `web/src/App.tsx`: flex-row layout docking `<AssistantPanel>` on the right (~360px) (§7). Done when: `cd web && pnpm build` passes and the panel is visible.

**Phase 7 — System prompt + end-to-end**
21. `agent/.pi/SYSTEM.md` content (written by `model.ts` at startup): the 6 prompt points (§8). Done when: the file exists after agent start.
22. End-to-end: run the "Verification" checklist (query, screen context, agentic action, UI nav, markdown + context size, destructive confirm, no-shell, graceful degradation, `uv run pytest`, `cd web && pnpm build`). Done when: every check passes.

## Approach

### 1. Agent service scaffold (`agent/`)
New top-level `agent/` directory — a standalone pnpm project mirroring `web/` (own `package.json` + lockfile, not added to any root workspace).
- `agent/package.json`: `"type":"module"`; dependencies `@earendil-works/pi-coding-agent` (`^0.87.1`) and `typebox` (`^1.3.27`); devDependencies `typescript` (`~5.6`), `@types/node` (`^22`), `tsx` (`^4`); scripts `"build":"tsc -p tsconfig.json"`, `"start":"node dist/index.js"`, `"dev":"tsx src/index.ts"`; `"engines":{"node":">=22.19.0"}`.
- `agent/tsconfig.json`: `module:"esnext"`, `moduleResolution:"bundler"`, `target:"es2022"`, `outDir:"dist"`, `rootDir:"src"`, `strict:true`, `esModuleInterop:true`, `skipLibCheck:true`, `types:["node"]`.
- `agent/.gitignore`: `node_modules/`, `dist/`.

### 2. Agent → backend client + model config
- `agent/src/backend.ts`: typed `backend` helper wrapping global `fetch` against `process.env.DOCUMENT_GEN_API_URL` (default `http://127.0.0.1:8000`). Exports `apiGet<T>(path)`, `apiSend<T>(method, path, body?)`, and `apiWaitJob(jobId)` (polls `GET /api/companies/jobs/{jobId}` every 1s until `status` is `done`/`error` or 180s elapses; returns the job's `result`; on timeout returns `{ __timeout: true }`). Every tool calls these (the agent never talks to the browser).
- `agent/src/config.ts`: `getChatConfig()` → `GET /api/agent-config` (step 5) returning `{ backend, host, model, api_key }`. Maps to a pi provider object:
  - `backend === "ollama"` → `{ baseUrl: host.replace(/\/$/,"") + "/v1", api: "openai-completions", apiKey: "ollama", models: [{ id: model }] }`
  - `backend === "openai"` → `{ baseUrl: host, api: "openai-completions", apiKey: api_key ?? "sk-none", models: [{ id: model }] }`
- `agent/src/model.ts`:
  - `const agentDir = process.env.PI_CODING_AGENT_DIR` (set by FastAPI at spawn; default `<repo>/agent/.pi`). `fs.mkdirSync(agentDir, { recursive: true })`.
  - `buildModelRuntime()`: write `<agentDir>/models.json` = `{ "providers": { "app": <provider from getChatConfig()> } }`; `const modelRuntime = await ModelRuntime.create()`; `const model = modelRuntime.getModel("app", <model>)`; if `model` is falsy throw `Error("agent model not found: app/<model>")`; return `{ modelRuntime, model }`.
  - `writeSystemPrompt()`: write the step-8 prompt to `<agentDir>/SYSTEM.md` once at startup.

### 3. Agent endpoint tools (core)
`agent/src/tools.ts`: `export function registerTools(pi: ExtensionAPI, deps: ToolDeps)`. Called from an inline extension factory (step 4). `import { Type } from "typebox"`; `import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"`. Each tool: `{ name, label, description, parameters: Type.Object({...}), execute: async (_id, params, _signal, _onUpdate, _ctx) => ({ content: [{ type: "text", text }], details: {} }) }`. `execute` calls `backend.*`, JSON-stringifies the result into `text`; on any backend/HTTP error it returns `{ content: [{ type: "text", text: "ERROR: <message>" }] }` — it NEVER throws (a thrown tool aborts the whole run).

`ToolDeps = { emitUi: (action: UiAction) => void }`.

Tools (name → endpoint):
- Read (single tool each): `health`→GET `/api/health`; `list_models(purpose?: "chat"|"embed")`→GET `/api/models`; `list_industries()`→GET `/api/industries`; `storage_info()`→GET `/api/storage`; `list_companies(industry?, search?)`→GET `/api/companies`; `get_company(company_id)`→GET `/api/companies/{id}`; `list_document_types(company_id)`→GET `/api/companies/{id}/document-types`; `list_documents(company_id?)`→GET `/api/documents`; `job_status(job_id)`→GET `/api/companies/jobs/{id}`.
- Generate (block-until-complete via `apiWaitJob`): `generate_companies(industries: string[], count: number, save?: boolean)`→POST `/api/companies/generate`, wait, then if `save` (default true) POST `/api/companies` with the job `result` and return the saved ids; `generate_document_types(company_id, user_input?, model?)`→POST `/api/companies/{id}/document-types/generate`, wait; `generate_pdf(company_id, ...)`→POST `/api/companies/{id}/pdf`, wait, return docs + `/api/companies/{id}/pdf/{filename}` URLs; `generate_excel(company_id, ...)`→POST `/api/companies/{id}/excel`, wait; `generate_image(company_id, ...)`→POST `/api/companies/{id}/image`, wait. Param sets mirror the matching `*Request` models in `web/src/lib/api.ts`, exposing the key fields with sensible defaults (omit rarely-used ones).
- Mutate, non-destructive (single tool each): `save_companies(companies)`→POST `/api/companies`; `update_company(company_id, profile)`→PATCH `/api/companies/{id}`; `set_favorite(company_id, favorite)`→POST `/api/companies/{id}/favorite`; `append_document_types(company_id, documents)`→POST `/api/companies/{id}/document-types`; `update_document_type(company_id, type_id, document)`→PATCH `/api/companies/{id}/document-types/{type_id}`; `rename_document(doc_id, filename)`→PATCH `/api/documents/{id}`; `distress_save(doc_id, body)`→POST `/api/documents/{id}/image/distress-save`; `save_documents_settings(output_dir)`→PUT `/api/settings/documents`; `clear_documents_settings()`→DELETE `/api/settings/documents`.
- Destructive (PROPOSE only — must NOT call the endpoint): `delete_company(company_id)`, `delete_document(doc_id)`, `delete_document_type(company_id, type_id)`, `clear_document_types(company_id)`, `replace_document_types(company_id, documents)`, `save_settings(settings)`, `clear_settings()`. Each builds a plain-language `summary` of the exact action, calls `propose(...)` (step 3b) to get a `confirmation_id`, and returns `{ confirmation_id, summary }`.
- Confirm (one shared tool): `confirm_action(confirmation_id)`→ `consume(confirmation_id)`; if null return `ERROR: confirmation expired or unknown`; else execute the stored `{ method, path, body }` via `backend`, return the result.
- UI: `ui(action: { type: "navigate_tab", tab } | { type: "select_company", company_id } | { type: "open_document", document_id })` where `tab` ∈ `overview|companies|labels|document-types|documents|settings`. Calls `deps.emitUi(action)` and returns `"OK"`.

`agent/src/confirm.ts`: `type PendingAction = { method: string; path: string; body?: unknown; summary: string; createdAt: number }`. `propose(a: Omit<PendingAction,"createdAt">): string` (returns `crypto.randomUUID()`, stores with TTL 5 min). `consume(id: string): PendingAction | null` (null if absent/expired; deletes on success). In-memory `Map`.

### 4. Agent sessions + HTTP/SSE server
- `agent/src/session.ts`: `const sessions = new Map<string, { session: AgentSession; lastActive: number }>()`. `async getSession(sessionId, deps)`: if absent, build a fresh session:
  - `const resourceLoader = new DefaultResourceLoader({ cwd: process.cwd(), agentDir, extensionFactories: [(pi) => registerTools(pi, deps)] })`; `await resourceLoader.reload()`.
  - `const { modelRuntime, model } = await buildModelRuntime()`. Define `const BUILTIN_TOOL_NAMES = ["read","bash","powershell","edit","write","grep","find","ls"]` (exactly pi's built-in `allToolNames`; `bash` and `powershell` are the two shell tools). Call `createAgentSession({ resourceLoader, model, modelRuntime, noTools: "builtin", excludeTools: BUILTIN_TOOL_NAMES, sessionManager: SessionManager.inMemory() })` — `noTools: "builtin"` disables the default active built-ins (`read, bash, edit, write`) while the constructor keeps extension-registered custom tools active; `excludeTools` is an explicit denylist (also applied to the initial active set) so every built-in, including `bash` and `powershell`, is blocked even if re-activated later.
  - Fail-fast: immediately after creation, `const active = session.getActiveToolNames(); const leaked = active.filter((n) => BUILTIN_TOOL_NAMES.includes(n)); if (leaked.length) throw new Error("agent exposed forbidden built-in tool(s): " + leaked.join(", "))`. A session exposing any built-in (esp. `bash`/`powershell`) is refused, so the HTTP server never serves a session with shell or file access.
  - Prune entries idle >30 min on each access; update `lastActive`.
- `agent/src/index.ts`: `node:http` server on `process.env.AGENT_PORT` (default `8090`). Routes:
  - `GET /health` → `200 { ok: true }`.
  - `POST /chat` body `{ sessionId, message, screen }` → SSE (`Content-Type: text/event-stream`, `Cache-Control: no-cache`). Get/create the session, subscribe, then `session.prompt(text)` where `text = "<screen>\n" + JSON.stringify(screen) + "\n</screen>\n\n" + message`. Forward pi events as SSE (each `data:` is JSON):
    - `event: token` `{ text }` — from `message_update` where `assistantMessageEvent.type === "text_delta"`.
    - `event: tool` `{ name, phase: "start"|"end", args?, result? }` — from tool-execution events.
    - `event: ui` `{ ...action }` — written by `deps.emitUi` to the active response.
    - `event: done` `{ text }` — final assistant text (from `message_end` / `agent_settled`).
    - `event: error` `{ message }`.
    End the stream on `agent_settled`. Concurrency: if the session is already streaming, respond `event: error` `{ message: "already running" }` and close (the frontend disables send while busy).

### 5. FastAPI: spawn + proxy + config endpoint
- `pyproject.toml`: add `httpx>=0.27` to the main `dependencies` list (currently dev-only; needed for the async streaming proxy).
- New `document_gen/agent.py`:
  - `class AgentHost`:
    - `async start()`: pick a free TCP port (bind a socket to `127.0.0.1:0`, read the port, close). Build `env = { **os.environ, "DOCUMENT_GEN_API_URL": f"http://127.0.0.1:{self._api_port}", "AGENT_PORT": str(port), "PI_CODING_AGENT_DIR": str(_repo_root / "agent" / ".pi") }`. Resolve `node` via `shutil.which("node")` and `entry = _repo_root / "agent" / "dist" / "index.js"`. If `node` is missing or `entry` doesn't exist: log a warning, set `self._available = False`, return (no raise). Else `self._proc = await asyncio.create_subprocess_exec(node, str(entry), env=env, cwd=str(_repo_root / "agent"))`; set `self._port = port`.
    - `async wait_ready(timeout: float = 30.0)`: poll `GET http://127.0.0.1:{port}/health` (httpx) until 200 or timeout; set `self._available` accordingly.
    - `async stop()`: if running, `self._proc.terminate()` then `await` with a short timeout, `kill()` on timeout.
    - Properties: `available`, `base_url` (`http://127.0.0.1:{port}`).
  - `_repo_root = Path(__file__).resolve().parent.parent`.
  - Proxy helpers: `async proxy_chat(body: dict) -> StreamingResponse` (open `httpx.AsyncClient`, `stream("POST", base_url + "/chat", json=body)`, yield each line from `aiter_lines()` verbatim as the SSE body, `media_type="text/event-stream"`); `async proxy_health() -> dict`.
- `document_gen/server.py`:
  - In `lifespan` (before `yield`): `agent_host = AgentHost(api_port=<bound port>); await agent_host.start(); await agent_host.wait_ready()`; `app.state.agent_host = agent_host`. After `yield`: `await agent_host.stop()`. (The API port is known from the uvicorn run; pass it in or read from the running server — simplest: default `8000`, overridable.)
  - New endpoint `GET /api/agent-config` → `return llm.load_settings().chat.model_dump()` = `{ backend, host, api_key, model }` (unmasked; the only consumer is the locally-spawned agent — acceptable for a local single-user tool).
  - Proxy routes, registered before the static mount (L1628): `POST /api/agent/chat` → if `app.state.agent_host.available`, `return await proxy_chat(request.json())`; else `raise HTTPException(503, "agent unavailable")`. `GET /api/agent/health` → `proxy_health()` or `HTTPException(503, "agent unavailable")`.
  - Routing note: the proxy matches the `/api/agent/` prefix; `/api/agent-config` (no trailing slash segment) is a distinct FastAPI route and is NOT proxied.

### 6. Frontend: screen context
- New `web/src/lib/screen-context.tsx`:
  - `type ScreenState = { activeTab: string; selectedCompanyId: number | null; selectedCompany: { id: number; name: string; industry: string } | null; visibleCompanies: { id: number; name: string; industry: string }[]; visibleDocuments: { id: number; filename: string; company_id: number; filetype: string }[] }`.
  - `ScreenProvider` + `useScreenContext()` → `{ screen: ScreenState, report: (partial: Partial<ScreenState>) => void }`. `report` merges into state. Defaults: `activeTab:"overview"`, empty lists, nulls.
- `App.tsx`: wrap the tree in `<ScreenProvider>`; inside, `const { report } = useScreenContext()`; `useEffect(() => report({ activeTab: tab, selectedCompanyId }), [tab, selectedCompanyId])`.
- `CompaniesPanel.tsx`: after the companies list loads and on selection change, `report({ visibleCompanies: companies.map(c => ({ id: c.id, name: c.name, industry: c.industry })), selectedCompany: selected ? { id: selected.id, name: selected.name, industry: selected.industry } : null })`. Reuse the existing `companies` state and the fetched selected-company detail.
- `DocumentsPanel.tsx`: after the documents list loads, `report({ visibleDocuments: documents.map(d => ({ id: d.id, filename: d.filename, company_id: d.company_id, filetype: d.filetype })) })`.

### 7. Frontend: chat panel + agent client
- New `web/src/lib/agent-api.ts`:
  - `agentHealth(): Promise<{ ok: boolean }>` → `GET /api/agent/health`.
  - `agentChat({ sessionId, message, screen }, h: { onToken, onTool, onUi, onDone, onError }): () => void` → `fetch("/api/agent/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sessionId, message, screen }) })`; read `response.body.getReader()`, decode, split on `\n`, parse `event:`/`data:` pairs, dispatch to the matching handler; return a cancel function (abort the fetch).
- New `web/src/components/assistant-panel.tsx`:
  - Props: `onNavigate(tab: string)`, `onSelectCompany(id: number)`, `onOpenDocument(id: number)`, `onRefresh()`.
  - State: `messages: { role: "user"|"assistant"; text: string; tools?: { name: string; phase: string; result?: string }[] }[]`, `busy: boolean`, `available: boolean`; `sessionId` = a UUID persisted in `sessionStorage`.
  - On mount: `agentHealth()` → set `available`.
  - On send: read `screen` from `useScreenContext()`; push the user message; call `agentChat` with handlers that append an assistant message and stream tokens in, render a tool chip per `onTool`, and on `onUi` call `onNavigate`/`onSelectCompany`/`onOpenDocument`; on `onDone` call `onRefresh()`; on `onError`/503 set `available=false`. Disable the input while `busy` or `!available`.
  - Rendering: the frontend has no markdown renderer today (no `react-markdown`/`marked`/`markdown-it` in `web/package.json`), so add `react-markdown` (`^9`) + `remark-gfm` (`^4`) to `web/package.json` dependencies; render each message's `text` with `<ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>` (both roles) inside a `prose prose-sm dark:prose-invert` container, with code blocks as monospace `<pre className="overflow-x-auto rounded bg-muted p-2 text-xs">` (no syntax-highlighting lib — keep it dependency-light).
  - Context-size subtext: a small muted line in the panel header (under the title) showing accumulated context size, recomputed every render: `const contextChars = messages.reduce((s, m) => s + m.text.length, 0) + JSON.stringify(screen).length`; render `Context ≈ {(contextChars / 1024).toFixed(1)} KB · ~{Math.round(contextChars / 4)} tokens` in `text-xs text-muted-foreground`. It grows as messages and the serialized screen context accumulate.
  - Confirm affordance: the destructive flow is text-driven (the agent's `propose_*` tool returns a summary and the agent asks); no special button is required — the user confirms by replying, which triggers `confirm_action`.
- `App.tsx` layout: change `<main>` (L97) to a flex row — existing content wrapped in a `flex-1 min-w-0` div, plus `<AssistantPanel onNavigate={setTab} onSelectCompany={setSelectedCompanyId} onOpenDocument={(id) => { setTab("documents") }} onRefresh={() => setRefreshKey(k => k + 1)} />` docked to the right (fixed ~360px width, full height, internal scroll).

### 8. System prompt
`agent/.pi/SYSTEM.md` (written by `model.ts` at startup):
- You are the assistant for the document-gen app (a synthetic company + document generator).
- You have tools that map 1:1 to the app's API. Use them to answer questions and perform actions; prefer calling a tool over guessing.
- Each user message starts with a `<screen>` JSON block describing the current UI state: `activeTab`, `selectedCompanyId`, `selectedCompany`, `visibleCompanies`, `visibleDocuments`. Use it to resolve "this company"/"these documents" to concrete ids.
- Destructive tools (`delete_*`, `replace_document_types`, `save_settings`, `clear_settings`) only PROPOSE an action and return `{ confirmation_id, summary }`. Show the user the summary and ask them to confirm. Call `confirm_action(confirmation_id)` ONLY after the user explicitly agrees; never confirm on your own initiative.
- Use the `ui` tool to `navigate_tab`, `select_company`, or `open_document` when the user asks to "show"/"go to"/"open" something.
- Be concise and concrete.

## Critical files & anchors
- `document_gen/server.py` — `lifespan` (L77-103) + `app` (L105): spawn/stop the agent, add `GET /api/agent-config` and the `/api/agent/*` proxy before the static mount (L1628).
- `document_gen/agent.py` (new) — Node subprocess lifecycle + httpx SSE proxy.
- `agent/src/tools.ts` (new) — the endpoint→tool mapping (the core of the feature).
- `web/src/App.tsx` (L55-181) — `ScreenProvider` wrap + right-side panel layout.
- `web/src/components/assistant-panel.tsx` (new) — chat UI, SSE consumption, `ui`-event handling.

## Verification
Prereqs: Node 22.19+ on PATH; a chat LLM endpoint configured (Settings tab or `.env`); `cd agent && pnpm install && pnpm build` (produces `agent/dist/index.js`); frontend built (`cd web && pnpm build`) or `pnpm dev`.
1. `uv run document-gen serve --port 8000` → startup log shows the agent spawned; `curl http://127.0.0.1:8000/api/agent/health` → `200 {"ok":true}`.
2. `curl http://127.0.0.1:8000/api/agent-config` → returns the chat endpoint (`backend`/`host`/`model`, unmasked `api_key`).
3. Open the app; in the right-side chat:
   - Query: "How many companies do I have, and which industries?" → agent calls `list_companies` (+ `list_industries`) → the answer matches the Companies tab. (New-behavior check.)
   - Screen context: select a company, ask "What documents does this company have?" → agent uses `selectedCompanyId` from `<screen>` → `list_documents(company_id)` → lists them.
   - Agentic action: "Generate 2 companies in the Technology industry and save them" → agent calls `generate_companies` (blocks until the job finishes) → the Companies tab then shows the new companies (refreshKey bumped).
   - UI navigation: "Show me the Documents tab" → agent calls `ui` → the app switches to the Documents tab.
   - Markdown + context size: ask for a structured answer (e.g. "List my companies as a markdown table") → the reply renders as formatted markdown (tables/bold/code, not raw `**`/`#`/`|`); the context-size subtext in the panel header grows as messages and the serialized screen context accumulate.
   - Destructive confirm: "Delete company <id>" → agent calls `delete_company` (returns summary + `confirmation_id`, does NOT delete) → asks to confirm → reply "yes" → agent calls `confirm_action` → the company is gone (Companies tab refreshes). Reply "no" instead → the company remains.
   - No shell access: ask "Run `ls -la` using the bash tool" → the agent has no `bash`/`powershell` tool and reports it cannot (no shell access). (New-behavior check: built-in tools are not exposed; the fail-fast assertion also guarantees a leaked built-in would have degraded the agent to 503 rather than served it.)
4. Graceful degradation: rename `agent/dist` away, restart the server → `POST /api/agent/chat` returns 503, the chat panel shows "agent unavailable", and the rest of the app (tabs, generation) still works.
5. `uv run pytest` still passes (no regressions); `cd web && pnpm build` (tsc) passes.

## Assumptions & contingencies
- The agent reuses the app's chat LLM settings (fetched on demand via `/api/agent-config`), so it uses the same model as the rest of the app. Contingency: if exposing the unmasked key via that endpoint is unacceptable, FastAPI instead writes the config to `agent/.pi/chat-config.json` at spawn and on settings change, and the agent reads the file; prefer the endpoint (simpler, tracks live changes).
- Tool isolation is verified against pi source (`core/sdk.ts`, `core/agent-session.ts`, `core/tools/index.ts`): built-in tools are exactly `read, bash, powershell, edit, write, grep, find, ls` (`allToolNames`); the default *active* set is `read, bash, edit, write`. The session is created with `noTools: "builtin"` + `excludeTools: <all 8>` and a fail-fast assertion that none are active, so the agent has no shell (`bash`/`powershell`) or file (`read`/`write`/`edit`) access — only the endpoint tools.
- Node 22.19+ is required by pi. If absent, the agent degrades to 503 and the app still works (no hard dependency).
- `agent/` is a standalone pnpm project (like `web/`), not added to any root workspace.
- Generation tools block up to 180s per job; longer jobs time out and the agent reports the timeout (the job may still finish server-side).
