"""In-process LLM agent for the web UI chat panel.

Replaces the archived pi-sdk agent (see ``archive/pi-agent``): a streaming
tool loop over the configured OpenAI-compatible chat endpoint, where every
tool is the app's own server route handler called in-process (no HTTP).

Chat sessions are per-process and in-memory: the conversation history of a
session id is kept for the life of the process and pruned after
:data:`SESSION_TTL` of idle time. Nothing is persisted.

SSE wire format (consumed by ``web/src/lib/agent-api.ts``)::

    event: token\ndata: {"text": "..."}\n\n
    event: tool\ndata: {"name": "...", "phase": "start"|"end", ...}\n\n
    event: ui\ndata: {"type": "...", ...}\n\n
    event: done\ndata: {"text": "..."}\n\n
    event: error\ndata: {"message": "..."}\n\n
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from document_gen import llm
from document_gen.models import (
    CompanyProfile,
    DocumentType,
    LLMSettings,
    SyntheticCompany,
)

logger = logging.getLogger(__name__)

#: Maximum tool-loop iterations per chat turn (guards against model loops).
MAX_TOOL_ITERATIONS = 10

#: Seconds a session may be idle before its history is pruned.
SESSION_TTL = 30 * 60

#: Seconds a proposed destructive action stays confirmable.
CONFIRMATION_TTL = 5 * 60

#: Seconds a blocking generation tool waits for its background job.
JOB_TIMEOUT = 180.0

#: Tabs the ``ui`` tool may navigate to (mirrors the frontend tab list).
TABS = [
    "overview",
    "companies",
    "labels",
    "document-types",
    "documents",
    "settings",
]

#: Figure kinds the document generators support.
FIGURE_KINDS = ["bar", "line", "area", "pie", "scatter", "histogram"]

SYSTEM_PROMPT = """You are the assistant for the document-gen app, a synthetic company and document generator.

- You have tools that map 1:1 to the app's API. Use them to answer questions and perform actions; prefer calling a tool over guessing.
- Each user message starts with a <screen> JSON block describing the current UI state: activeTab, selectedCompanyId, selectedCompany, visibleCompanies, visibleDocuments. Use it to resolve "this company" / "these documents" to concrete ids.
- Destructive tools (delete_*, replace_document_types, save_settings, clear_settings) only PROPOSE an action and return { confirmation_id, summary }. Show the user the summary and ask them to confirm. Call confirm_action(confirmation_id) ONLY after the user explicitly agrees; never confirm on your own initiative.
- Use the ui tool to navigate_tab, select_company, or open_document when the user asks to "show", "go to", or "open" something.
- When listing data (list_companies, list_document_types, list_documents), always pass limit and filters matching the user's request (e.g. "5 companies" -> limit=5); never fetch the whole store when a subset suffices.
- Be concise and concrete."""


class ToolError(Exception):
    """A tool failed; the message is reported to the model as text."""


def _sse(event: str, data: Any) -> str:
    """Format one SSE frame (the wire format the frontend expects)."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _server() -> Any:
    """The server module (imported lazily to avoid an import cycle)."""
    from document_gen import server

    return server


def _call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a server route handler in-process and JSON-encode its result.

    HTTP errors raised by the handler become :class:`ToolError` so the
    agent loop can report them to the model as text.
    """
    try:
        result = getattr(_server(), fn_name)(*args, **kwargs)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc
    return jsonable_encoder(result)


def _wait_job(job_id: str) -> Any:
    """Poll a background job until it finishes (or times out).

    Returns the job's ``result`` payload.
    """
    server = _server()
    deadline = time.monotonic() + JOB_TIMEOUT
    while True:
        status = server.job_status(job_id)
        if status.status == "done":
            return jsonable_encoder(status.result)
        if status.status == "error":
            raise ToolError(f"generation failed: {status.error}")
        if time.monotonic() >= deadline:
            raise ToolError(
                f"generation timed out after {int(JOB_TIMEOUT)}s "
                f"(job {job_id} may still be running)"
            )
        time.sleep(1.0)


# ---------------------------------------------------------------------------
# Tool handlers (each maps 1:1 to a server route handler)
# ---------------------------------------------------------------------------


def _tool_health(args: dict) -> Any:
    return _call("health")


def _tool_list_models(args: dict) -> Any:
    return _call("list_models", args.get("purpose") or "chat")


def _tool_list_industries(args: dict) -> Any:
    return _call("list_industries")


def _tool_storage_info(args: dict) -> Any:
    return _call("storage_info")


def _tool_list_companies(args: dict) -> Any:
    return _call(
        "list_companies",
        industry=args.get("industry"),
        search=args.get("search"),
        favorite=args.get("favorite"),
        limit=args.get("limit"),
        offset=args.get("offset"),
    )


def _tool_get_company(args: dict) -> Any:
    return _call("get_company", args["company_id"])


def _tool_list_document_types(args: dict) -> Any:
    return _call(
        "list_company_document_types",
        args["company_id"],
        search=args.get("search"),
        limit=args.get("limit"),
        offset=args.get("offset"),
    )


def _tool_list_documents(args: dict) -> Any:
    return _call(
        "list_documents",
        company_id=args.get("company_id"),
        document_type_id=args.get("document_type_id"),
        filetype=args.get("filetype"),
        search=args.get("search"),
        limit=args.get("limit"),
        offset=args.get("offset"),
    )


def _tool_job_status(args: dict) -> Any:
    return _call("job_status", args["job_id"])


def _tool_generate_companies(args: dict) -> Any:
    server = _server()
    profiles: list[Any] = []
    for industry in args["industries"]:
        started = server.start_generation(
            server.GenerateRequest(
                num=args["count"],
                industry=industry,
                model=args.get("model"),
                user_input=args.get("user_input"),
            )
        )
        result = _wait_job(started["id"])
        profiles.extend(result or [])
    if args.get("save", True) is False:
        return {"saved": False, "companies": profiles}
    saved_ids = _call(
        "save_companies",
        [CompanyProfile.model_validate(profile) for profile in profiles],
    )
    return {"saved": True, "saved_ids": saved_ids, "count": len(profiles)}


def _tool_generate_document_types(args: dict) -> Any:
    server = _server()
    started = server.generate_company_document_types(
        args["company_id"],
        server.GenerateDocumentTypesRequest(
            document_request=args["document_request"],
            num=args.get("count", 5),
            model=args.get("model"),
        ),
    )
    return _wait_job(started["id"])


def _tool_generate_pdf(args: dict) -> Any:
    server = _server()
    started = server.generate_company_pdf(
        args["company_id"],
        server.DocumentPdfRequest(
            report=args["report"],
            user_input=args.get("user_input"),
            model=args.get("model"),
            figure_kinds=args.get("figure_kinds") or [],
            quick_doc=args.get("quick_doc", False),
            cover_page=args.get("cover_page", True),
            count=args.get("count", 1),
        ),
    )
    result = _wait_job(started["id"])
    company_id = args["company_id"]
    return [
        {**doc, "url": f"/api/companies/{company_id}/pdf/{doc['pdf']}"}
        for doc in result.get("documents") or []
    ]


def _tool_generate_excel(args: dict) -> Any:
    server = _server()
    started = server.generate_company_excel(
        args["company_id"],
        server.DocumentExcelRequest(
            report=args["report"],
            user_input=args.get("user_input"),
            model=args.get("model"),
            figure_kinds=args.get("figure_kinds") or [],
            quick_doc=args.get("quick_doc", False),
            simple_sheets=args.get("simple_sheets", False),
            cover_sheet=args.get("cover_sheet", True),
            glossary=args.get("glossary", False),
            count=args.get("count", 1),
        ),
    )
    result = _wait_job(started["id"])
    company_id = args["company_id"]
    return [
        {**doc, "url": f"/api/companies/{company_id}/excel/{doc['xlsx']}"}
        for doc in result.get("documents") or []
    ]


def _tool_generate_image(args: dict) -> Any:
    server = _server()
    started = server.generate_company_image(
        args["company_id"],
        server.DocumentImageRequest(
            report=args["report"],
            user_input=args.get("user_input"),
            model=args.get("model"),
            figure_kinds=args.get("figure_kinds") or [],
            a4_aspect=args.get("a4_aspect", True),
            count=args.get("count", 1),
        ),
    )
    result = _wait_job(started["id"])
    company_id = args["company_id"]
    return [
        {**doc, "url": f"/api/companies/{company_id}/image/{doc['png']}"}
        for doc in result.get("documents") or []
    ]


def _tool_save_companies(args: dict) -> Any:
    return _call(
        "save_companies",
        [CompanyProfile.model_validate(entry) for entry in args["companies"]],
    )


def _tool_update_company(args: dict) -> Any:
    return _call(
        "update_company",
        args["company_id"],
        SyntheticCompany.model_validate(args["profile"]),
    )


def _tool_set_favorite(args: dict) -> Any:
    server = _server()
    return _call(
        "set_company_favorite",
        args["company_id"],
        server.FavoriteCompanyRequest(favorite=args["favorite"]),
    )


def _tool_append_document_types(args: dict) -> Any:
    return _call(
        "append_company_document_types",
        args["company_id"],
        [DocumentType.model_validate(doc) for doc in args["documents"]],
    )


def _tool_update_document_type(args: dict) -> Any:
    return _call(
        "update_company_document_type",
        args["company_id"],
        args["type_id"],
        DocumentType.model_validate(args["document"]),
    )


def _tool_rename_document(args: dict) -> Any:
    server = _server()
    return _call(
        "rename_document",
        args["doc_id"],
        server.RenameDocumentRequest(filename=args["filename"]),
    )


def _tool_distress_save(args: dict) -> Any:
    server = _server()
    return _call(
        "distress_save",
        args["doc_id"],
        server.DistressEditRequest(
            distress=args["distress"], effect_seeds=args["effect_seeds"]
        ),
    )


def _tool_save_documents_settings(args: dict) -> Any:
    server = _server()
    return _call(
        "put_documents_settings",
        server.DocumentsSettingsRequest(output_dir=args["output_dir"]),
    )


def _tool_clear_documents_settings(args: dict) -> Any:
    return _call("delete_documents_settings")


def _propose(executor: Callable[[], Any], summary: str) -> dict:
    """Record a destructive action for later confirmation (never executes).

    *executor* performs the actual server call once the user confirms;
    it is captured with its arguments at propose time.
    """
    now = time.monotonic()
    with _CONFIRMATIONS_LOCK:
        expired = [
            cid for cid, pending in _CONFIRMATIONS.items() if now > pending["expires"]
        ]
        for cid in expired:
            del _CONFIRMATIONS[cid]
        confirmation_id = uuid.uuid4().hex[:12]
        _CONFIRMATIONS[confirmation_id] = {
            "executor": executor,
            "expires": now + CONFIRMATION_TTL,
        }
    return {"confirmation_id": confirmation_id, "summary": summary}


def _tool_delete_company(args: dict) -> Any:
    company_id = args["company_id"]
    return _propose(
        lambda: _call("delete_company", company_id),
        f"Delete company {company_id} and all its document types and documents.",
    )


def _tool_delete_document(args: dict) -> Any:
    doc_id = args["doc_id"]
    return _propose(
        lambda: _call("delete_document", doc_id),
        f"Delete document {doc_id} and its file on disk.",
    )


def _tool_delete_document_type(args: dict) -> Any:
    company_id = args["company_id"]
    type_id = args["type_id"]
    return _propose(
        lambda: _call("delete_company_document_type", company_id, type_id),
        f"Delete document type {type_id} from company {company_id}.",
    )


def _tool_clear_document_types(args: dict) -> Any:
    company_id = args["company_id"]
    return _propose(
        lambda: _call("clear_company_document_types", company_id),
        f"Delete ALL document types from company {company_id}.",
    )


def _tool_replace_document_types(args: dict) -> Any:
    company_id = args["company_id"]
    documents = [
        DocumentType.model_validate(doc) for doc in (args.get("documents") or [])
    ]
    return _propose(
        lambda: _call("replace_company_document_types", company_id, documents),
        f"Replace ALL document types of company {company_id} "
        f"with {len(documents)} new one(s).",
    )


def _tool_save_settings(args: dict) -> Any:
    settings = LLMSettings.model_validate(args["settings"])
    return _propose(
        lambda: _call("put_settings", settings),
        "Replace the saved LLM endpoint settings (chat and embed) with new values.",
    )


def _tool_clear_settings(args: dict) -> Any:
    return _propose(
        lambda: _call("delete_settings"),
        "Clear the saved LLM endpoint settings (falls back to .env defaults).",
    )


def _tool_confirm_action(args: dict) -> Any:
    """Consume a pending proposal and execute its stored action."""
    confirmation_id = args.get("confirmation_id")
    with _CONFIRMATIONS_LOCK:
        pending = _CONFIRMATIONS.pop(confirmation_id, None)
    if pending is None or time.monotonic() > pending["expires"]:
        raise ToolError("confirmation expired or unknown")
    return pending["executor"]()


def _tool_ui(args: dict) -> Any:
    action = args.get("action") or {}
    kind = action.get("type")
    if kind == "navigate_tab":
        if action.get("tab") not in TABS:
            raise ToolError(f"unknown tab {action.get('tab')!r}")
    elif kind == "select_company" and not isinstance(action.get("company_id"), int):
        raise ToolError("select_company requires an integer company_id")
    elif kind == "open_document" and not isinstance(action.get("document_id"), int):
        raise ToolError("open_document requires an integer document_id")
    elif kind is None:
        raise ToolError("ui action requires a type")
    return action


# ---------------------------------------------------------------------------
# Tool registry (OpenAI function schemas + handlers)
# ---------------------------------------------------------------------------

#: JSON Schema for one document type (mirrors ``DocumentType``).
_DOCUMENT_TYPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Document title."},
        "category": {
            "type": "string",
            "description": "Document category (e.g. Report, Guide, Analysis).",
        },
        "purpose": {"type": "string", "description": "Key purpose of the document."},
        "user_input": {
            "type": "string",
            "description": "Free-text request that guided the document (optional).",
        },
    },
    "required": ["name", "category", "purpose"],
}

#: JSON Schema for one company profile (mirrors ``SyntheticCompany``).
_COMPANY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "industry": {"type": "string"},
        "description": {"type": "string"},
        "headquarters": {"type": "string", "description": "U.S. city."},
        "size": {"type": "string", "enum": ["small", "mid", "large"]},
        "employees": {"type": "integer"},
    },
    "required": ["name", "industry", "description", "headquarters", "size"],
}

#: JSON Schema for one endpoint config (mirrors ``EndpointConfig``).
_ENDPOINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "host": {"type": "string", "description": "OpenAI-compatible base URL."},
        "api_key": {"type": "string"},
        "model": {"type": "string"},
    },
}

#: JSON Schema for the ``ui`` tool's action argument.
_UI_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["navigate_tab", "select_company", "open_document"],
        },
        "tab": {"type": "string", "enum": TABS},
        "company_id": {"type": "integer"},
        "document_id": {"type": "integer"},
    },
    "required": ["type"],
}


@dataclass(frozen=True)
class _Tool:
    """One agent tool: OpenAI function schema + in-process handler."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict], Any]

    def to_openai(self) -> dict[str, Any]:
        """The OpenAI ``tools`` entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS: dict[str, _Tool] = {
    tool.name: tool
    for tool in [
        # ------------------------------------------------------------- read
        _Tool(
            "health",
            "Check backend server health and LLM endpoint connectivity.",
            {"type": "object", "properties": {}},
            _tool_health,
        ),
        _Tool(
            "list_models",
            "List model IDs on the active LLM endpoint.",
            {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string", "enum": ["chat", "embed"]},
                },
            },
            _tool_list_models,
        ),
        _Tool(
            "list_industries",
            "List the industries usable as generation seeds.",
            {"type": "object", "properties": {}},
            _tool_list_industries,
        ),
        _Tool(
            "storage_info",
            "Return the configured company storage location.",
            {"type": "object", "properties": {}},
            _tool_storage_info,
        ),
        _Tool(
            "list_companies",
            "List stored companies. Filters: industry (exact), search (text in "
            "profile), favorite. Returns the WHOLE store unless limit is set "
            "- always pass limit when the user asks for a subset (e.g. "
            "limit=5 for '5 companies'); use offset to page.",
            {
                "type": "object",
                "properties": {
                    "industry": {"type": "string"},
                    "search": {"type": "string"},
                    "favorite": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
            _tool_list_companies,
        ),
        _Tool(
            "get_company",
            "Return the full profile of one company.",
            {
                "type": "object",
                "properties": {"company_id": {"type": "integer"}},
                "required": ["company_id"],
            },
            _tool_get_company,
        ),
        _Tool(
            "list_document_types",
            "List the document types linked to a company. Filter: search "
            "(text in name/category/purpose). Pass limit/offset for subsets.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                    "offset": {"type": "integer", "minimum": 0},
                },
                "required": ["company_id"],
            },
            _tool_list_document_types,
        ),
        _Tool(
            "list_documents",
            "List generated document records. Filters: company_id, "
            "document_type_id, filetype (pdf/xlsx/png), search (filename "
            "text). Returns ALL records unless limit is set - pass limit for "
            "subsets; use offset to page.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "document_type_id": {"type": "integer"},
                    "filetype": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
            _tool_list_documents,
        ),
        _Tool(
            "job_status",
            "Return a snapshot of a background generation job.",
            {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
            _tool_job_status,
        ),
        # ------------------------------------------------------------- generate
        _Tool(
            "generate_companies",
            "Generate synthetic companies. Blocking: starts one job per "
            "industry and waits up to 180s each. By default saves the "
            "results to the company store.",
            {
                "type": "object",
                "properties": {
                    "industries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Industries to generate from (one job per industry).",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Companies per industry (1-200).",
                    },
                    "save": {
                        "type": "boolean",
                        "description": "Save the generated companies (default true).",
                    },
                    "user_input": {
                        "type": "string",
                        "description": "Free-text instruction guiding generation.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Override the chat model id.",
                    },
                },
                "required": ["industries", "count"],
            },
            _tool_generate_companies,
        ),
        _Tool(
            "generate_document_types",
            "Generate document types for a company from a free-text request. "
            "Blocking: waits up to 180s. The result is NOT persisted; use "
            "append_document_types to add it to the company.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "document_request": {
                        "type": "string",
                        "description": "Free-text description of the document "
                        "types to generate (at least 20 characters).",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of document types (1-50, default 5).",
                    },
                    "model": {"type": "string"},
                },
                "required": ["company_id", "document_request"],
            },
            _tool_generate_document_types,
        ),
        _Tool(
            "generate_pdf",
            "Generate a PDF document for a company's document type. Blocking: "
            "waits up to 180s. Returns the documents with download URLs.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "report": {
                        "type": "string",
                        "description": "Name of the company's document type to generate.",
                    },
                    "user_input": {"type": "string"},
                    "model": {"type": "string"},
                    "figure_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": FIGURE_KINDS},
                    },
                    "quick_doc": {"type": "boolean"},
                    "cover_page": {
                        "type": "boolean",
                        "description": "Standalone cover page (default true).",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Documents to generate (1-10, default 1).",
                    },
                },
                "required": ["company_id", "report"],
            },
            _tool_generate_pdf,
        ),
        _Tool(
            "generate_excel",
            "Generate an Excel workbook for a company's document type. "
            "Blocking: waits up to 180s. Returns the workbooks with download URLs.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "report": {
                        "type": "string",
                        "description": "Name of the company's document type to generate.",
                    },
                    "user_input": {"type": "string"},
                    "model": {"type": "string"},
                    "figure_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": FIGURE_KINDS},
                    },
                    "quick_doc": {"type": "boolean"},
                    "simple_sheets": {
                        "type": "boolean",
                        "description": "Skip the cover sheet and embedded figures.",
                    },
                    "cover_sheet": {"type": "boolean"},
                    "glossary": {
                        "type": "boolean",
                        "description": "Add a Glossary lookup sheet.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Workbooks to generate (1-10, default 1).",
                    },
                },
                "required": ["company_id", "report"],
            },
            _tool_generate_excel,
        ),
        _Tool(
            "generate_image",
            "Generate a PNG image document for a company's document type. "
            "Blocking: waits up to 180s. Returns the images with download URLs.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "report": {
                        "type": "string",
                        "description": "Name of the company's document type to generate.",
                    },
                    "user_input": {"type": "string"},
                    "model": {"type": "string"},
                    "figure_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": FIGURE_KINDS},
                    },
                    "a4_aspect": {
                        "type": "boolean",
                        "description": "Lock the page to A4 portrait (default true).",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Images to generate (1-10, default 1).",
                    },
                },
                "required": ["company_id", "report"],
            },
            _tool_generate_image,
        ),
        # -------------------------------------------------------- mutate (safe)
        _Tool(
            "save_companies",
            "Persist company profiles (with their document types) to the company store.",
            {
                "type": "object",
                "properties": {
                    "companies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "profile": _COMPANY_SCHEMA,
                                "reports": {
                                    "type": "array",
                                    "items": _DOCUMENT_TYPE_SCHEMA,
                                },
                                "seed": {"type": "integer"},
                                "user_input": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["companies"],
            },
            _tool_save_companies,
        ),
        _Tool(
            "update_company",
            "Update the stored profile of a company.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "profile": _COMPANY_SCHEMA,
                },
                "required": ["company_id", "profile"],
            },
            _tool_update_company,
        ),
        _Tool(
            "set_favorite",
            "Mark or unmark a company as a favorite.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "favorite": {"type": "boolean"},
                },
                "required": ["company_id", "favorite"],
            },
            _tool_set_favorite,
        ),
        _Tool(
            "append_document_types",
            "Add document types to a company (existing ones are kept).",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "documents": {"type": "array", "items": _DOCUMENT_TYPE_SCHEMA},
                },
                "required": ["company_id", "documents"],
            },
            _tool_append_document_types,
        ),
        _Tool(
            "update_document_type",
            "Update one document type of a company.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "type_id": {"type": "integer"},
                    "document": _DOCUMENT_TYPE_SCHEMA,
                },
                "required": ["company_id", "type_id", "document"],
            },
            _tool_update_document_type,
        ),
        _Tool(
            "rename_document",
            "Rename a document record and its file on disk (extension preserved).",
            {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "integer"},
                    "filename": {
                        "type": "string",
                        "description": "New base name without extension.",
                    },
                },
                "required": ["doc_id", "filename"],
            },
            _tool_rename_document,
        ),
        _Tool(
            "distress_save",
            "Persist a distressed (scanned/aged) render over a PNG document file.",
            {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "integer"},
                    "distress": {
                        "type": "object",
                        "description": "Partial distress effect options (e.g. "
                        "{ enabled: true, vignette_strength: 0.5, stain_count: 6 }); "
                        "the server fills in defaults.",
                    },
                    "effect_seeds": {
                        "type": "object",
                        "description": "Per-effect seed map (effect name -> integer seed).",
                    },
                },
                "required": ["doc_id", "distress", "effect_seeds"],
            },
            _tool_distress_save,
        ),
        _Tool(
            "save_documents_settings",
            "Set the document output directory.",
            {
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string",
                        "description": "Absolute path of the document output directory.",
                    },
                },
                "required": ["output_dir"],
            },
            _tool_save_documents_settings,
        ),
        _Tool(
            "clear_documents_settings",
            "Clear the saved document output directory (falls back to the env default).",
            {"type": "object", "properties": {}},
            _tool_clear_documents_settings,
        ),
        # ------------------------------------------------- destructive (propose)
        _Tool(
            "delete_company",
            "Delete a company and everything it owns (document types and documents).",
            {
                "type": "object",
                "properties": {"company_id": {"type": "integer"}},
                "required": ["company_id"],
            },
            _tool_delete_company,
        ),
        _Tool(
            "delete_document",
            "Delete a document record and its file on disk.",
            {
                "type": "object",
                "properties": {"doc_id": {"type": "integer"}},
                "required": ["doc_id"],
            },
            _tool_delete_document,
        ),
        _Tool(
            "delete_document_type",
            "Delete one document type from a company.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "type_id": {"type": "integer"},
                },
                "required": ["company_id", "type_id"],
            },
            _tool_delete_document_type,
        ),
        _Tool(
            "clear_document_types",
            "Delete ALL document types from a company.",
            {
                "type": "object",
                "properties": {"company_id": {"type": "integer"}},
                "required": ["company_id"],
            },
            _tool_clear_document_types,
        ),
        _Tool(
            "replace_document_types",
            "Replace ALL of a company's document types with a new list.",
            {
                "type": "object",
                "properties": {
                    "company_id": {"type": "integer"},
                    "documents": {"type": "array", "items": _DOCUMENT_TYPE_SCHEMA},
                },
                "required": ["company_id", "documents"],
            },
            _tool_replace_document_types,
        ),
        _Tool(
            "save_settings",
            "Replace the saved LLM endpoint settings (chat and embed).",
            {
                "type": "object",
                "properties": {
                    "settings": {
                        "type": "object",
                        "properties": {
                            "chat": _ENDPOINT_SCHEMA,
                            "embed": _ENDPOINT_SCHEMA,
                        },
                        "required": ["chat", "embed"],
                        "description": "Full LLM settings: { chat: { host, api_key, model }, "
                        "embed: { host, api_key, model } }.",
                    },
                },
                "required": ["settings"],
            },
            _tool_save_settings,
        ),
        _Tool(
            "clear_settings",
            "Clear the saved LLM endpoint settings (falls back to .env defaults).",
            {"type": "object", "properties": {}},
            _tool_clear_settings,
        ),
        # --------------------------------------------------------------- confirm
        _Tool(
            "confirm_action",
            "Execute a previously proposed destructive action by its "
            "confirmation_id. Only call this after the user explicitly agreed.",
            {
                "type": "object",
                "properties": {"confirmation_id": {"type": "string"}},
                "required": ["confirmation_id"],
            },
            _tool_confirm_action,
        ),
        # -------------------------------------------------------------------- ui
        _Tool(
            "ui",
            "Control the app UI: navigate to a tab, select a company, or open "
            'a document. Use when the user asks to "show", "go to", or "open" '
            "something.",
            {
                "type": "object",
                "properties": {"action": _UI_ACTION_SCHEMA},
                "required": ["action"],
            },
            _tool_ui,
        ),
    ]
}


# ---------------------------------------------------------------------------
# Session + confirmation stores (in-memory)
# ---------------------------------------------------------------------------


@dataclass
class _Session:
    """One chat session's in-memory conversation history."""

    messages: list[dict] = field(default_factory=list)
    updated_at: float = field(default_factory=time.monotonic)


_SESSIONS: dict[str, _Session] = {}
_SESSIONS_LOCK = threading.Lock()

#: Session ids with a chat turn currently running.
_BUSY: set[str] = set()
_BUSY_LOCK = threading.Lock()

#: Pending destructive-action proposals: confirmation id -> record.
_CONFIRMATIONS: dict[str, dict] = {}
_CONFIRMATIONS_LOCK = threading.Lock()


def _get_session(session_id: str) -> _Session:
    """Return the session for *session_id*, pruning idle sessions first."""
    now = time.monotonic()
    with _SESSIONS_LOCK:
        stale = [
            sid
            for sid, session in _SESSIONS.items()
            if now - session.updated_at > SESSION_TTL
        ]
        for sid in stale:
            del _SESSIONS[sid]
        session = _SESSIONS.get(session_id)
        if session is None:
            session = _Session()
            _SESSIONS[session_id] = session
        session.updated_at = now
        return session


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def agent_available() -> bool:
    """True when a chat endpoint (host and model) is configured."""
    chat = llm.load_settings().chat
    return bool(chat.host and chat.model)


async def _run_tool(name: str, args: dict) -> Any:
    """Run one tool; failures become ``ERROR: ...`` text for the model."""
    tool = TOOLS.get(name)
    if tool is None:
        return f"ERROR: unknown tool {name!r}"
    try:
        return await asyncio.to_thread(tool.handler, args)
    except ToolError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        logger.exception("agent tool %s failed", name)
        return f"ERROR: {type(exc).__name__}: {exc}"


async def _run_turn(session_id: str, message: str, screen: Any) -> AsyncIterator[str]:
    """Run one chat turn to completion, yielding SSE frames."""
    settings = llm.load_settings()
    chat = settings.chat
    if not chat.host or not chat.model:
        yield _sse(
            "error",
            {"message": "chat endpoint not configured (host and model are required)"},
        )
        return
    client = llm.build_async_client(chat)
    session = _get_session(session_id)

    text = message
    if screen is not None:
        text = f"<screen>\n{json.dumps(screen)}\n</screen>\n\n{message}"
    session.messages.append({"role": "user", "content": text})

    tools = [tool.to_openai() for tool in TOOLS.values()]
    for _ in range(MAX_TOOL_ITERATIONS):
        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        stream = await client.chat.completions.create(
            model=chat.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *session.messages],
            tools=tools,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text_parts.append(delta.content)
                yield _sse("token", {"text": delta.content})
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function is not None:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        text = "".join(text_parts)
        ordered = [calls[index] for index in sorted(calls)]
        if not ordered:
            if text:
                session.messages.append({"role": "assistant", "content": text})
            yield _sse("done", {"text": text})
            return
        session.messages.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"] or "{}",
                        },
                    }
                    for call in ordered
                ],
            }
        )
        for call in ordered:
            name = call["name"]
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield _sse("tool", {"name": name, "phase": "start", "args": args})
            result = await _run_tool(name, args)
            if name == "ui":
                yield _sse("ui", result)
                result = "OK"
            yield _sse("tool", {"name": name, "phase": "end", "result": result})
            session.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": (
                        result if isinstance(result, str) else json.dumps(result)
                    ),
                }
            )
    yield _sse(
        "error",
        {"message": f"tool loop limit reached ({MAX_TOOL_ITERATIONS} iterations)"},
    )


async def run_agent(session_id: str, message: str, screen: Any) -> AsyncIterator[str]:
    """Run one chat turn, yielding SSE frames.

    Events: ``token`` (text delta), ``tool`` (start/end), ``ui`` (action),
    ``done`` (final text), ``error`` (message). A second turn on the same
    session while one is running yields a single ``error`` frame.
    """
    with _BUSY_LOCK:
        if session_id in _BUSY:
            yield _sse("error", {"message": "already running"})
            return
        _BUSY.add(session_id)
    try:
        async for frame in _run_turn(session_id, message, screen):
            yield frame
    except Exception as exc:
        logger.exception("agent turn failed (session %s)", session_id)
        yield _sse("error", {"message": str(exc)})
    finally:
        with _BUSY_LOCK:
            _BUSY.discard(session_id)
