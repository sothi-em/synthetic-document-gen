/** Chat LLM endpoint config and pi provider mapping. */

import { apiGet } from "./backend.js";

/** Shape of `GET /api/agent-config` (the resolved chat EndpointConfig). */
export interface ChatConfig {
  backend: "ollama" | "openai";
  host: string | null;
  api_key: string | null;
  model: string | null;
}

/** Provider entry written to `<agentDir>/models.json`. */
export interface ProviderConfig {
  baseUrl: string;
  api: "openai-completions";
  apiKey: string;
  models: { id: string }[];
}

/**
 * Fetch the app's chat endpoint config.
 *
 * Unmasked on purpose: the only consumer is the locally spawned agent.
 */
export async function getChatConfig(): Promise<ChatConfig> {
  const cfg = await apiGet<ChatConfig>("/api/agent-config");
  if (!cfg.model) {
    throw new Error(
      "agent chat model is not configured (set it in the Settings tab or via .env)",
    );
  }
  return cfg;
}

/** Map the app's chat config to a pi provider object. */
export function providerFromConfig(cfg: ChatConfig): ProviderConfig {
  const model = cfg.model as string;
  if (cfg.backend === "ollama") {
    const host = (cfg.host ?? "http://localhost:11434").replace(/\/+$/, "");
    return {
      baseUrl: `${host}/v1`,
      api: "openai-completions",
      apiKey: "ollama",
      models: [{ id: model }],
    };
  }
  if (!cfg.host) {
    throw new Error("agent chat host is not configured for the openai backend");
  }
  return {
    baseUrl: cfg.host,
    api: "openai-completions",
    apiKey: cfg.api_key ?? "sk-none",
    models: [{ id: model }],
  };
}
