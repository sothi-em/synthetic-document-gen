/** Typed client for the document-gen JSON API. */

export interface HealthInfo {
  status: string
  chat: EndpointStatus
  embed: EndpointStatus
}

export interface EndpointStatus {
  backend: "ollama" | "openai"
  status: "up" | "down"
  model: string | null
}

export interface EndpointConfig {
  backend: "ollama" | "openai"
  host: string | null
  api_key: string | null
  model: string | null
}

export interface MaskedEndpoint {
  backend: "ollama" | "openai"
  host: string | null
  model: string | null
  api_key: string | null
  has_api_key: boolean
}

export interface LLMSettings {
  chat: EndpointConfig
  embed: EndpointConfig
}

export interface MaskedSettings {
  chat: MaskedEndpoint
  embed: MaskedEndpoint
}

export interface SettingsTestResult {
  ok: boolean
  model_count: number
  models: string[]
  error: string | null
}

export interface GenerateRequest {
  num: number
  industry: string | null
  model: string | null
  /** Free-text instruction guiding the generated companies. */
  user_input: string | null
}

/** A company produced by a generation job, before it is persisted. */
export interface GeneratedCompany {
  profile: CompanyProfile | null
  reports: DocumentType[]
  seed: number
  user_input?: string | null
}

export interface GenerateDocumentTypesRequest {
  num: number
  model: string | null
  document_request: string
}

export interface StorageInfo {
  path: string
}

export interface JobStart {
  id: string
  status: string
  total: number
}

export interface JobEvent {
  status: "running" | "done" | "error"
  completed: number
  total: number
  error: string | null
  result: unknown
  /** Recent backend log lines for the job (oldest first, bounded). */
  logs: string[]
}

export interface DocumentsSettings {
  output_dir: string | null
  default: string | null
  source: "saved" | "env" | "none"
}

export interface BrowseEntry {
  name: string
  path: string
}

export interface BrowseResult {
  path: string
  parent: string | null
  entries: BrowseEntry[]
}

export type FigureKind =
  | "bar"
  | "line"
  | "area"
  | "pie"
  | "scatter"
  | "histogram"

export interface DocumentPdfRequest {
  report: string
  user_input: string | null
  model: string | null
  figure_kinds?: FigureKind[]
  quick_doc?: boolean
  /** Persist the per-stage generation trace on the document record. */
  gen_tracing?: boolean
}

export interface PdfJobResult {
  pdf: string
  report: string
}

export interface DocumentExcelRequest {
  report: string
  user_input: string | null
  model: string | null
  figure_kinds?: FigureKind[]
  quick_doc?: boolean
  /** Skip the cover sheet and embedded figures. */
  simple_sheets?: boolean
  /** Add a single Glossary lookup sheet for abbreviated terms. */
  glossary?: boolean
  /** Persist the per-stage generation trace on the document record. */
  gen_tracing?: boolean
}

export interface ExcelJobResult {
  xlsx: string
  report: string
}

export interface CompanySummary {
  id: number
  name: string
  industry: string
  headquarters: string
  size: string
  num_reports: number
}

export interface DocumentType {
  name: string
  category: string
  purpose: string
  /** User-provided context that guided generation, if any. */
  user_input?: string | null
}

export interface DocumentTypeDoc extends DocumentType {
  id: number
  company_id: number
  /** Number of generated documents linked to this document type. */
  num_documents: number
  /** Generated documents (any filetype), in creation order. */
  documents: DocumentRef[]
}

export interface DocumentRef {
  id: number
  filename: string
  filetype: string
}

export interface DocumentRecord {
  id: number
  company_id: number
  document_type_id: number
  filename: string
  filetype: string
  filepath: string
  size_kb: number
  created_at: string
  company_name: string | null
  report_name: string | null
  /** Per-stage generation trace (only when generated with tracing on). */
  gen_tracing?: Record<string, unknown> | null
}

export interface CompanyProfile {
  name: string
  industry: string
  description: string
  headquarters: string
  size: string
}

export interface CompanyDetail {
  id: number
  profile: CompanyProfile | null
  /** Stored document types (carry their TinyDB ``id``). */
  reports: DocumentTypeDoc[]
  seed: number
  /** User-provided context that guided the company's generation, if any. */
  user_input?: string | null
  created_at: string
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<HealthInfo>("/api/health"),
  models: (purpose: "chat" | "embed" = "chat") =>
    request<string[]>(`/api/models?purpose=${purpose}`),
  industries: () => request<string[]>("/api/industries"),
  settings: () => request<MaskedSettings>("/api/settings"),
  saveSettings: (body: LLMSettings) =>
    request<MaskedSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  clearSettings: () =>
    request<MaskedSettings>("/api/settings", { method: "DELETE" }),
  documentsSettings: () => request<DocumentsSettings>("/api/settings/documents"),
  saveDocumentsSettings: (output_dir: string | null) =>
    request<DocumentsSettings>("/api/settings/documents", {
      method: "PUT",
      body: JSON.stringify({ output_dir }),
    }),
  browse: (path?: string) =>
    request<BrowseResult>(
      `/api/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`,
    ),
  startDocumentPdf: (id: number, body: DocumentPdfRequest) =>
    request<JobStart>(`/api/companies/${id}/pdf`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  documentPdfUrl: (id: number, filename: string) =>
    `/api/companies/${id}/pdf/${encodeURIComponent(filename)}`,
  startDocumentExcel: (id: number, body: DocumentExcelRequest) =>
    request<JobStart>(`/api/companies/${id}/excel`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  documentExcelUrl: (id: number, filename: string) =>
    `/api/companies/${id}/excel/${encodeURIComponent(filename)}`,
  testSettings: (purpose: "chat" | "embed", endpoint: EndpointConfig) =>
    request<SettingsTestResult>("/api/settings/test", {
      method: "POST",
      body: JSON.stringify({ purpose, endpoint }),
    }),
  startGeneration: (body: GenerateRequest) =>
    request<JobStart>("/api/companies/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Persist companies selected from a generation job's result. */
  saveCompanies: (companies: GeneratedCompany[]) =>
    request<number[]>("/api/companies", {
      method: "POST",
      body: JSON.stringify(companies),
    }),
  storage: () => request<StorageInfo>("/api/storage"),
  companies: (industry?: string, search?: string) => {
    const params = new URLSearchParams()
    if (industry) params.set("industry", industry)
    if (search) params.set("search", search)
    const query = params.toString()
    return request<CompanySummary[]>(`/api/companies${query ? `?${query}` : ""}`)
  },
  company: (id: number) => request<CompanyDetail>(`/api/companies/${id}`),
  updateCompany: (id: number, profile: CompanyProfile) =>
    request<CompanyDetail>(`/api/companies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(profile),
    }),
  companyDocumentTypes: (id: number) =>
    request<DocumentTypeDoc[]>(`/api/companies/${id}/document-types`),
  documents: (companyId?: number) => {
    const query = companyId ? `?company_id=${companyId}` : ""
    return request<DocumentRecord[]>(`/api/documents${query}`)
  },
  renameDocument: (id: number, filename: string) =>
    request<DocumentRecord>(`/api/documents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ filename }),
    }),
  documentDownloadUrl: (id: number) => `/api/documents/${id}/download`,
  /** Serves the file inline (Content-Disposition: inline) for previews. */
  documentPreviewUrl: (id: number) => `/api/documents/${id}/preview`,
  deleteDocument: (id: number) =>
    request<{ deleted: boolean }>(`/api/documents/${id}`, {
      method: "DELETE",
    }),
  saveCompanyDocumentTypes: (id: number, documents: DocumentType[]) =>
    request<DocumentTypeDoc[]>(`/api/companies/${id}/document-types`, {
      method: "PUT",
      body: JSON.stringify(documents),
    }),
  appendCompanyDocumentTypes: (id: number, documents: DocumentType[]) =>
    request<DocumentTypeDoc[]>(`/api/companies/${id}/document-types`, {
      method: "POST",
      body: JSON.stringify(documents),
    }),
  deleteCompanyDocumentTypes: (id: number) =>
    request<{ deleted: boolean }>(`/api/companies/${id}/document-types`, {
      method: "DELETE",
    }),
  deleteCompanyDocumentType: (id: number, typeId: number) =>
    request<{ deleted: boolean }>(
      `/api/companies/${id}/document-types/${typeId}`,
      { method: "DELETE" },
    ),
  updateCompanyDocumentType: (id: number, typeId: number, document: DocumentType) =>
    request<DocumentTypeDoc>(
      `/api/companies/${id}/document-types/${typeId}`,
      { method: "PATCH", body: JSON.stringify(document) },
    ),
  generateCompanyDocumentTypes: (id: number, body: GenerateDocumentTypesRequest) =>
    request<JobStart>(`/api/companies/${id}/document-types/generate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
}
