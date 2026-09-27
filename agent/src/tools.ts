/**
 * Endpoint tools: one pi tool per backend API endpoint.
 *
 * Read/generate/mutate tools call the backend directly. Destructive tools
 * only PROPOSE an action (see confirm.ts) — they never call their endpoint;
 * execution happens in `confirm_action` after the user agrees.
 *
 * No tool ever throws: a thrown tool aborts the whole agent run, so every
 * backend/HTTP error is returned as `ERROR: <message>` text instead.
 */

import { Type, type TSchema } from "typebox";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

import { apiGet, apiSend, apiWaitJob, isJobTimeout } from "./backend.js";
import { consume, propose } from "./confirm.js";

/** UI action the agent can trigger in the frontend. */
export type UiAction =
  | { type: "navigate_tab"; tab: string }
  | { type: "select_company"; company_id: number }
  | { type: "open_document"; document_id: number };

export interface ToolDeps {
  emitUi: (action: UiAction) => void;
}

const TABS = [
  "overview",
  "companies",
  "labels",
  "document-types",
  "documents",
  "settings",
] as const;
const FIGURE_KINDS = ["bar", "line", "area", "pie", "scatter", "histogram"] as const;

const FigureKindSchema = Type.Union(FIGURE_KINDS.map((k) => Type.Literal(k)));
const TabSchema = Type.Union(TABS.map((t) => Type.Literal(t)));

const DocumentTypeSchema = Type.Object({
  name: Type.String({ description: "Name or title of the document." }),
  category: Type.String({
    description: "Document category (e.g. Report, Guide, Analysis, Onboarding).",
  }),
  purpose: Type.String({ description: "Key purpose of the document." }),
  user_input: Type.Optional(
    Type.String({ description: "Free-text request that guided generation, if any." }),
  ),
});

const CompanyProfileSchema = Type.Object({
  name: Type.String(),
  industry: Type.String(),
  description: Type.String(),
  headquarters: Type.String({ description: "U.S. city of the headquarters." }),
  size: Type.Union([Type.Literal("small"), Type.Literal("mid"), Type.Literal("large")]),
});

const CompanyProfileEntrySchema = Type.Object({
  profile: Type.Optional(CompanyProfileSchema),
  reports: Type.Optional(Type.Array(DocumentTypeSchema)),
  seed: Type.Optional(Type.Integer()),
  user_input: Type.Optional(Type.String()),
});

interface PdfJobResult {
  documents?: { pdf: string; report: string }[];
  pdf?: string;
  report?: string;
}
interface ExcelJobResult {
  documents?: { xlsx: string; report: string }[];
  xlsx?: string;
  report?: string;
}
interface ImageJobResult {
  documents?: { png: string; report: string }[];
  png?: string;
  report?: string;
}

/** Shape of a tool accepted by `pi.registerTool()`. */
type EndpointTool = Parameters<ExtensionAPI["registerTool"]>[0];

interface ToolResult {
  content: { type: "text"; text: string }[];
  details: Record<string, never>;
}

function ok(data: unknown): ToolResult {
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return { content: [{ type: "text", text }], details: {} };
}

function fail(err: unknown): ToolResult {
  const message = err instanceof Error ? err.message : String(err);
  return { content: [{ type: "text", text: `ERROR: ${message}` }], details: {} };
}

/** Run a single backend call; never throws. */
function run(call: () => Promise<unknown>): Promise<ToolResult> {
  return call().then(
    (data) => ok(data),
    (err) => fail(err),
  );
}

/** Run a multi-step tool body; never throws. */
async function guarded(body: () => Promise<ToolResult>): Promise<ToolResult> {
  try {
    return await body();
  } catch (err) {
    return fail(err);
  }
}

type PendingActionDraft = { method: string; path: string; body?: unknown; summary: string };

/**
 * Build a destructive tool that only proposes its action and returns
 * `{ confirmation_id, summary }` — it never calls the endpoint.
 */
function destructiveTool(
  name: string,
  label: string,
  description: string,
  parameters: TSchema,
  build: (params: Record<string, unknown>) => PendingActionDraft,
): EndpointTool {
  return defineTool({
    name,
    label,
    description: `${description} This tool only PROPOSES the action and returns { confirmation_id, summary }; it does not execute it. Show the summary to the user and ask them to confirm, then call confirm_action.`,
    parameters,
    execute: async (_id, params) => {
      const action = build(params as Record<string, unknown>);
      const confirmation_id = propose(action);
      return ok({ confirmation_id, summary: action.summary });
    },
  });
}

/** Register every endpoint tool with the pi extension API. */
export function registerTools(pi: ExtensionAPI, deps: ToolDeps): void {
  const tools: EndpointTool[] = [
    // ------------------------------------------------------------------ read
    defineTool({
      name: "health",
      label: "Health",
      description: "Check backend server health and LLM endpoint connectivity.",
      parameters: Type.Object({}),
      execute: async () => run(() => apiGet("/api/health")),
    }),
    defineTool({
      name: "list_models",
      label: "List models",
      description: "List model IDs on the active LLM backend.",
      parameters: Type.Object({
        purpose: Type.Optional(
          Type.Union([Type.Literal("chat"), Type.Literal("embed")]),
        ),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiGet(`/api/models${params.purpose ? `?purpose=${params.purpose}` : ""}`),
        ),
    }),
    defineTool({
      name: "list_industries",
      label: "List industries",
      description: "List the industries usable as generation seeds.",
      parameters: Type.Object({}),
      execute: async () => run(() => apiGet("/api/industries")),
    }),
    defineTool({
      name: "storage_info",
      label: "Storage info",
      description: "Return the configured company storage location.",
      parameters: Type.Object({}),
      execute: async () => run(() => apiGet("/api/storage")),
    }),
    defineTool({
      name: "list_companies",
      label: "List companies",
      description: "List stored companies, optionally filtered by industry or search text.",
      parameters: Type.Object({
        industry: Type.Optional(Type.String()),
        search: Type.Optional(Type.String()),
      }),
      execute: async (_id, params) =>
        run(() => {
          const q = new URLSearchParams();
          if (params.industry) q.set("industry", params.industry);
          if (params.search) q.set("search", params.search);
          const qs = q.toString();
          return apiGet(`/api/companies${qs ? `?${qs}` : ""}`);
        }),
    }),
    defineTool({
      name: "get_company",
      label: "Get company",
      description: "Return the full profile of one company.",
      parameters: Type.Object({ company_id: Type.Integer() }),
      execute: async (_id, params) =>
        run(() => apiGet(`/api/companies/${params.company_id}`)),
    }),
    defineTool({
      name: "list_document_types",
      label: "List document types",
      description: "List the document types linked to a company.",
      parameters: Type.Object({ company_id: Type.Integer() }),
      execute: async (_id, params) =>
        run(() =>
          apiGet(`/api/companies/${params.company_id}/document-types`),
        ),
    }),
    defineTool({
      name: "list_documents",
      label: "List documents",
      description: "List generated document records, optionally for one company.",
      parameters: Type.Object({
        company_id: Type.Optional(Type.Integer()),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiGet(
            `/api/documents${params.company_id != null ? `?company_id=${params.company_id}` : ""}`,
          ),
        ),
    }),
    defineTool({
      name: "job_status",
      label: "Job status",
      description: "Return a snapshot of a background generation job.",
      parameters: Type.Object({ job_id: Type.String() }),
      execute: async (_id, params) =>
        run(() => apiGet(`/api/companies/jobs/${params.job_id}`)),
    }),

    // ------------------------------------------------------------- generate
    defineTool({
      name: "generate_companies",
      label: "Generate companies",
      description:
        "Generate synthetic companies. Blocking: starts one job per industry and waits up to 180s each. By default saves the results to the company store.",
      parameters: Type.Object({
        industries: Type.Array(Type.String(), {
          description: "Industries to generate from (one job per industry).",
        }),
        count: Type.Integer({
          description: "Companies per industry (1-200).",
          minimum: 1,
          maximum: 200,
        }),
        save: Type.Optional(
          Type.Boolean({ description: "Save the generated companies (default true)." }),
        ),
        user_input: Type.Optional(
          Type.String({ description: "Free-text instruction guiding generation." }),
        ),
        model: Type.Optional(Type.String({ description: "Override the chat model id." })),
      }),
      execute: async (_id, params) =>
        guarded(async () => {
          const profiles: unknown[] = [];
          for (const industry of params.industries) {
            const started = await apiSend<{ id: string }>(
              "POST",
              "/api/companies/generate",
              {
                num: params.count,
                industry,
                model: params.model ?? null,
                user_input: params.user_input ?? null,
              },
            );
            const result = await apiWaitJob<unknown[]>(started.id);
            if (isJobTimeout(result)) {
              return fail(
                new Error(
                  `generation for "${industry}" timed out after 180s (job ${started.id} may still be running)`,
                ),
              );
            }
            profiles.push(...(result ?? []));
          }
          if (params.save === false) {
            return ok({ saved: false, companies: profiles });
          }
          const saved_ids = await apiSend<number[]>("POST", "/api/companies", profiles);
          return ok({ saved: true, saved_ids, count: profiles.length });
        }),
    }),
    defineTool({
      name: "generate_document_types",
      label: "Generate document types",
      description:
        "Generate document types for a company from a free-text request. Blocking: waits up to 180s.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        document_request: Type.String({
          description:
            "Free-text description of the document types to generate (at least 20 characters).",
        }),
        count: Type.Optional(
          Type.Integer({ description: "Number of document types (1-50, default 5)." }),
        ),
        model: Type.Optional(Type.String()),
      }),
      execute: async (_id, params) =>
        guarded(async () => {
          const started = await apiSend<{ id: string }>(
            "POST",
            `/api/companies/${params.company_id}/document-types/generate`,
            {
              document_request: params.document_request,
              num: params.count ?? 5,
              model: params.model ?? null,
            },
          );
          const result = await apiWaitJob<unknown>(started.id);
          if (isJobTimeout(result)) {
            return fail(
              new Error(`document type generation timed out after 180s (job ${started.id})`),
            );
          }
          return ok(result);
        }),
    }),
    defineTool({
      name: "generate_pdf",
      label: "Generate PDF",
      description:
        "Generate a PDF document for a company's document type. Blocking: waits up to 180s. Returns the documents with download URLs.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        report: Type.String({
          description: "Name of the company's document type to generate.",
        }),
        user_input: Type.Optional(Type.String()),
        model: Type.Optional(Type.String()),
        figure_kinds: Type.Optional(Type.Array(FigureKindSchema)),
        quick_doc: Type.Optional(Type.Boolean()),
        cover_page: Type.Optional(
          Type.Boolean({ description: "Standalone cover page (default true)." }),
        ),
        count: Type.Optional(
          Type.Integer({ description: "Documents to generate (1-10, default 1)." }),
        ),
      }),
      execute: async (_id, params) =>
        guarded(async () => {
          const { company_id, ...body } = params;
          const started = await apiSend<{ id: string }>(
            "POST",
            `/api/companies/${company_id}/pdf`,
            body,
          );
          const result = await apiWaitJob<PdfJobResult>(started.id);
          if (isJobTimeout(result)) {
            return fail(new Error(`PDF generation timed out after 180s (job ${started.id})`));
          }
          const docs = (
            result.documents ??
            (result.pdf ? [{ pdf: result.pdf, report: result.report ?? "" }] : [])
          ).map((d) => ({ ...d, url: `/api/companies/${company_id}/pdf/${d.pdf}` }));
          return ok(docs);
        }),
    }),
    defineTool({
      name: "generate_excel",
      label: "Generate Excel",
      description:
        "Generate an Excel workbook for a company's document type. Blocking: waits up to 180s. Returns the workbooks with download URLs.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        report: Type.String({
          description: "Name of the company's document type to generate.",
        }),
        user_input: Type.Optional(Type.String()),
        model: Type.Optional(Type.String()),
        figure_kinds: Type.Optional(Type.Array(FigureKindSchema)),
        quick_doc: Type.Optional(Type.Boolean()),
        simple_sheets: Type.Optional(
          Type.Boolean({ description: "Skip the cover sheet and embedded figures." }),
        ),
        cover_sheet: Type.Optional(Type.Boolean()),
        glossary: Type.Optional(
          Type.Boolean({ description: "Add a Glossary lookup sheet." }),
        ),
        count: Type.Optional(
          Type.Integer({ description: "Workbooks to generate (1-10, default 1)." }),
        ),
      }),
      execute: async (_id, params) =>
        guarded(async () => {
          const { company_id, ...body } = params;
          const started = await apiSend<{ id: string }>(
            "POST",
            `/api/companies/${company_id}/excel`,
            body,
          );
          const result = await apiWaitJob<ExcelJobResult>(started.id);
          if (isJobTimeout(result)) {
            return fail(new Error(`Excel generation timed out after 180s (job ${started.id})`));
          }
          const docs = (
            result.documents ??
            (result.xlsx ? [{ xlsx: result.xlsx, report: result.report ?? "" }] : [])
          ).map((d) => ({ ...d, url: `/api/companies/${company_id}/excel/${d.xlsx}` }));
          return ok(docs);
        }),
    }),
    defineTool({
      name: "generate_image",
      label: "Generate image",
      description:
        "Generate a PNG image document for a company's document type. Blocking: waits up to 180s. Returns the images with download URLs.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        report: Type.String({
          description: "Name of the company's document type to generate.",
        }),
        user_input: Type.Optional(Type.String()),
        model: Type.Optional(Type.String()),
        figure_kinds: Type.Optional(Type.Array(FigureKindSchema)),
        a4_aspect: Type.Optional(
          Type.Boolean({ description: "Lock the page to A4 portrait (default true)." }),
        ),
        count: Type.Optional(
          Type.Integer({ description: "Images to generate (1-10, default 1)." }),
        ),
      }),
      execute: async (_id, params) =>
        guarded(async () => {
          const { company_id, ...body } = params;
          const started = await apiSend<{ id: string }>(
            "POST",
            `/api/companies/${company_id}/image`,
            body,
          );
          const result = await apiWaitJob<ImageJobResult>(started.id);
          if (isJobTimeout(result)) {
            return fail(new Error(`image generation timed out after 180s (job ${started.id})`));
          }
          const docs = (
            result.documents ??
            (result.png ? [{ png: result.png, report: result.report ?? "" }] : [])
          ).map((d) => ({ ...d, url: `/api/companies/${company_id}/image/${d.png}` }));
          return ok(docs);
        }),
    }),

    // -------------------------------------------------------- mutate (safe)
    defineTool({
      name: "save_companies",
      label: "Save companies",
      description: "Persist company profiles (with their document types) to the company store.",
      parameters: Type.Object({
        companies: Type.Array(CompanyProfileEntrySchema),
      }),
      execute: async (_id, params) =>
        run(() => apiSend("POST", "/api/companies", params.companies)),
    }),
    defineTool({
      name: "update_company",
      label: "Update company",
      description: "Update the stored profile of a company.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        profile: CompanyProfileSchema,
      }),
      execute: async (_id, params) =>
        run(() => apiSend("PATCH", `/api/companies/${params.company_id}`, params.profile)),
    }),
    defineTool({
      name: "set_favorite",
      label: "Set favorite",
      description: "Mark or unmark a company as a favorite.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        favorite: Type.Boolean(),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiSend("POST", `/api/companies/${params.company_id}/favorite`, {
            favorite: params.favorite,
          }),
        ),
    }),
    defineTool({
      name: "append_document_types",
      label: "Append document types",
      description: "Add document types to a company (existing ones are kept).",
      parameters: Type.Object({
        company_id: Type.Integer(),
        documents: Type.Array(DocumentTypeSchema),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiSend(
            "POST",
            `/api/companies/${params.company_id}/document-types`,
            params.documents,
          ),
        ),
    }),
    defineTool({
      name: "update_document_type",
      label: "Update document type",
      description: "Update one document type of a company.",
      parameters: Type.Object({
        company_id: Type.Integer(),
        type_id: Type.Integer(),
        document: DocumentTypeSchema,
      }),
      execute: async (_id, params) =>
        run(() =>
          apiSend(
            "PATCH",
            `/api/companies/${params.company_id}/document-types/${params.type_id}`,
            params.document,
          ),
        ),
    }),
    defineTool({
      name: "rename_document",
      label: "Rename document",
      description: "Rename a document record and its file on disk (extension preserved).",
      parameters: Type.Object({
        doc_id: Type.Integer(),
        filename: Type.String({ description: "New base name without extension." }),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiSend("PATCH", `/api/documents/${params.doc_id}`, {
            filename: params.filename,
          }),
        ),
    }),
    defineTool({
      name: "distress_save",
      label: "Distress save",
      description:
        "Persist a distressed (scanned/aged) render over a PNG document file.",
      parameters: Type.Object({
        doc_id: Type.Integer(),
        distress: Type.Any({
          description:
            "Partial distress effect options (e.g. { enabled: true, vignette_strength: 0.5, stain_count: 6 }); the server fills in defaults.",
        }),
        effect_seeds: Type.Record(Type.String(), Type.Integer(), {
          description: "Per-effect seed map (effect name -> integer seed).",
        }),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiSend("POST", `/api/documents/${params.doc_id}/image/distress-save`, {
            distress: params.distress,
            effect_seeds: params.effect_seeds,
          }),
        ),
    }),
    defineTool({
      name: "save_documents_settings",
      label: "Save documents settings",
      description: "Set the document output directory.",
      parameters: Type.Object({
        output_dir: Type.String({
          description: "Absolute path of the document output directory.",
        }),
      }),
      execute: async (_id, params) =>
        run(() =>
          apiSend("PUT", "/api/settings/documents", {
            output_dir: params.output_dir,
          }),
        ),
    }),
    defineTool({
      name: "clear_documents_settings",
      label: "Clear documents settings",
      description:
        "Clear the saved document output directory (falls back to the env default).",
      parameters: Type.Object({}),
      execute: async () => run(() => apiSend("DELETE", "/api/settings/documents")),
    }),

    // ------------------------------------------------- destructive (propose)
    destructiveTool(
      "delete_company",
      "Delete company",
      "Delete a company and everything it owns (document types and documents).",
      Type.Object({ company_id: Type.Integer() }),
      (p) => ({
        method: "DELETE",
        path: `/api/companies/${p.company_id}`,
        summary: `Delete company ${p.company_id} and all its document types and documents.`,
      }),
    ),
    destructiveTool(
      "delete_document",
      "Delete document",
      "Delete a document record and its file on disk.",
      Type.Object({ doc_id: Type.Integer() }),
      (p) => ({
        method: "DELETE",
        path: `/api/documents/${p.doc_id}`,
        summary: `Delete document ${p.doc_id} and its file on disk.`,
      }),
    ),
    destructiveTool(
      "delete_document_type",
      "Delete document type",
      "Delete one document type from a company.",
      Type.Object({ company_id: Type.Integer(), type_id: Type.Integer() }),
      (p) => ({
        method: "DELETE",
        path: `/api/companies/${p.company_id}/document-types/${p.type_id}`,
        summary: `Delete document type ${p.type_id} from company ${p.company_id}.`,
      }),
    ),
    destructiveTool(
      "clear_document_types",
      "Clear document types",
      "Delete ALL document types from a company.",
      Type.Object({ company_id: Type.Integer() }),
      (p) => ({
        method: "DELETE",
        path: `/api/companies/${p.company_id}/document-types`,
        summary: `Delete ALL document types from company ${p.company_id}.`,
      }),
    ),
    destructiveTool(
      "replace_document_types",
      "Replace document types",
      "Replace ALL of a company's document types with a new list.",
      Type.Object({
        company_id: Type.Integer(),
        documents: Type.Array(DocumentTypeSchema),
      }),
      (p) => ({
        method: "PUT",
        path: `/api/companies/${p.company_id}/document-types`,
        body: p.documents,
        summary: `Replace ALL document types of company ${p.company_id} with ${
          Array.isArray(p.documents) ? p.documents.length : 0
        } new one(s).`,
      }),
    ),
    destructiveTool(
      "save_settings",
      "Save settings",
      "Replace the saved LLM endpoint settings (chat and embed).",
      Type.Object({
        settings: Type.Any({
          description:
            'Full LLM settings: { chat: { backend, host, api_key, model }, embed: { backend, host, api_key, model } } where backend is "ollama" or "openai".',
        }),
      }),
      (p) => ({
        method: "PUT",
        path: "/api/settings",
        body: p.settings,
        summary: "Replace the saved LLM endpoint settings (chat and embed) with new values.",
      }),
    ),
    destructiveTool(
      "clear_settings",
      "Clear settings",
      "Clear the saved LLM endpoint settings (falls back to .env defaults).",
      Type.Object({}),
      () => ({
        method: "DELETE",
        path: "/api/settings",
        summary: "Clear the saved LLM endpoint settings (falls back to .env defaults).",
      }),
    ),

    // --------------------------------------------------------------- confirm
    defineTool({
      name: "confirm_action",
      label: "Confirm action",
      description:
        "Execute a previously proposed destructive action by its confirmation_id. Only call this after the user explicitly agreed.",
      parameters: Type.Object({ confirmation_id: Type.String() }),
      execute: async (_id, params) =>
        guarded(async () => {
          const action = consume(params.confirmation_id);
          if (!action) {
            return fail(new Error("confirmation expired or unknown"));
          }
          return ok(await apiSend(action.method, action.path, action.body));
        }),
    }),

    // -------------------------------------------------------------------- ui
    defineTool({
      name: "ui",
      label: "UI control",
      description:
        'Control the app UI: navigate to a tab, select a company, or open a document. Use when the user asks to "show", "go to", or "open" something.',
      parameters: Type.Object({
        action: Type.Union([
          Type.Object({ type: Type.Literal("navigate_tab"), tab: TabSchema }),
          Type.Object({ type: Type.Literal("select_company"), company_id: Type.Integer() }),
          Type.Object({ type: Type.Literal("open_document"), document_id: Type.Integer() }),
        ]),
      }),
      execute: async (_id, params) => {
        deps.emitUi(params.action);
        return ok("OK");
      },
    }),
  ];

  for (const tool of tools) {
    pi.registerTool(tool);
  }
}
