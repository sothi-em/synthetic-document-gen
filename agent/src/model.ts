/** Model runtime setup and system prompt for the agent. */

import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";

import { getChatConfig, providerFromConfig } from "./config.js";

/**
 * Agent config directory. FastAPI sets `PI_CODING_AGENT_DIR` at spawn;
 * the default is `<agent project>/.pi` (this file lives in `src/` or
 * `dist/`, one level below the project root).
 */
export const agentDir: string =
  process.env.PI_CODING_AGENT_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", ".pi");

const SYSTEM_PROMPT = `You are the assistant for the document-gen app, a synthetic company and document generator.

- You have tools that map 1:1 to the app's API. Use them to answer questions and perform actions; prefer calling a tool over guessing.
- Each user message starts with a <screen> JSON block describing the current UI state: activeTab, selectedCompanyId, selectedCompany, visibleCompanies, visibleDocuments. Use it to resolve "this company" / "these documents" to concrete ids.
- Destructive tools (delete_*, replace_document_types, save_settings, clear_settings) only PROPOSE an action and return { confirmation_id, summary }. Show the user the summary and ask them to confirm. Call confirm_action(confirmation_id) ONLY after the user explicitly agrees; never confirm on your own initiative.
- Use the ui tool to navigate_tab, select_company, or open_document when the user asks to "show", "go to", or "open" something.
- Be concise and concrete.`;

/** Write the system prompt to `<agentDir>/SYSTEM.md`. */
export function writeSystemPrompt(): void {
  fs.mkdirSync(agentDir, { recursive: true });
  fs.writeFileSync(path.join(agentDir, "SYSTEM.md"), SYSTEM_PROMPT + "\n", "utf8");
}

/**
 * Build the pi model runtime with the app's chat model registered under
 * provider id `app` (via `<agentDir>/models.json`, which
 * `ModelRuntime.create()` picks up).
 *
 * Throws when the chat model is missing or unknown to the runtime.
 */
export async function buildModelRuntime() {
  const cfg = await getChatConfig();
  const provider = providerFromConfig(cfg);
  fs.mkdirSync(agentDir, { recursive: true });
  fs.writeFileSync(
    path.join(agentDir, "models.json"),
    JSON.stringify({ providers: { app: provider } }, null, 2),
    "utf8",
  );
  const modelRuntime = await ModelRuntime.create({
    modelsPath: path.join(agentDir, "models.json"),
    authPath: path.join(agentDir, "auth.json"),
  });
  const model = modelRuntime.getModel("app", cfg.model as string);
  if (!model) {
    throw new Error(`agent model not found: app/${cfg.model}`);
  }
  return { modelRuntime, model };
}
