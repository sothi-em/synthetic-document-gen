"""FastAPI server exposing the generation pipeline and web UI.

Run with ``uv run document-gen serve`` (requires the ``web`` extra).
The API is thin: it wraps ``document_gen.pipeline`` and serves the built
frontend (``web/dist``, produced by ``pnpm build`` in ``web/``) at the
site root.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import mimetypes
import os
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from document_gen import document_excel, document_pdf, document_png, document_query, llm
from document_gen.generators.png_gen import distress_image_to_bytes
from document_gen.models import (
    FIGURE_KINDS,
    CompanyProfile,
    DistressOptions,
    EndpointConfig,
    LLMSettings,
    DocumentType,
    SyntheticCompany,
    industry_list,
)
from document_gen.pipeline import (
    generate_company_profile,
    generate_documents_for_company,
)

WEB_DIR: Path = Path(__file__).resolve().parent.parent / "web" / "dist"
MAX_JOBS: int = 100

#: Maximum number of log lines kept per job (oldest are dropped).
MAX_JOB_LOGS: int = 200

#: Timeout for connectivity probes (health check, model listing) so a
#: downed LLM server cannot hang these endpoints for the full client
#: timeout.
PROBE_TIMEOUT: float = 5.0

#: Fixed worker count for company generation jobs; the work is split
#: across these threads (no longer user-configurable).
COMPANY_GEN_THREADS: int = 4

logger = logging.getLogger(__name__)


def _package_version() -> str:
    """Return the installed package version (fallback for source checkouts)."""
    try:
        return importlib.metadata.version("document-gen")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup/shutdown lifecycle: eagerly open the company store.

    Opening the database here (instead of lazily on the first request)
    loads the file-backed TinyDB into memory for the lifetime of the
    process, so the API always serves the on-disk data.
    """
    # uvicorn's default log config only attaches handlers to the
    # ``uvicorn.*`` loggers, leaving the root logger handler-less; without
    # this, our own loggers' INFO messages are dropped. basicConfig is a
    # no-op when a handler is already present (e.g. custom log_config).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Load .env at startup (not import time) so TINYDB_PATH and the LLM
    # settings env vars are in effect for the whole process.
    load_dotenv()
    db = document_query.get_db()
    logger.info(
        "Company store ready: %s (%d record(s))",
        document_query.db_path(),
        len(db.all()),
    )
    yield


app = FastAPI(title="document-gen", version=_package_version(), lifespan=lifespan)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Request body for ``POST /api/companies/generate``."""

    num: int = Field(default=1, ge=1, le=200)
    industry: str | None = None
    model: str | None = None
    user_input: str | None = Field(
        default=None,
        description=(
            "Optional free-text instruction guiding the generated "
            "companies (e.g. 'a startup focused on solar storage')."
        ),
    )


class GenerateDocumentTypesRequest(BaseModel):
    """Request body for ``POST /api/companies/{company_id}/document-types/generate``."""

    document_request: str = Field(
        min_length=20,
        description=(
            "Free-text description of the document type(s) to generate. "
            "Combined with the company profile as prompt input."
        ),
    )
    num: int = Field(default=5, ge=1, le=50)
    model: str | None = None


class RenameDocumentRequest(BaseModel):
    """Request body for ``PATCH /api/documents/{doc_id}``."""

    filename: str = Field(
        min_length=1,
        description="New base name (without extension); the extension is preserved.",
    )


class DistressEditRequest(BaseModel):
    """Request body for the distress preview/save endpoints."""

    distress: DistressOptions
    seed: int = Field(description="Noise/warp seed (from the generation trace).")
    stain_seed: int = Field(
        description="Editor-derived stain seed (deterministic per document)."
    )


class DocumentsSettingsRequest(BaseModel):
    """Request body for ``PUT /api/settings/documents``."""

    output_dir: str | None = Field(
        default=None,
        description=(
            "Absolute path of the document output directory. ``null`` (or "
            "empty) clears the saved value and falls back to the "
            "DOCUMENTS_DIR env default."
        ),
    )


def _validate_figure_kinds(value: list[str]) -> list[str]:
    """Reject figure kinds the renderer does not support."""
    unknown = [kind for kind in value if kind not in FIGURE_KINDS]
    if unknown:
        raise ValueError(
            f"Unknown figure kind(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(FIGURE_KINDS)}"
        )
    return value


class DocumentPdfRequest(BaseModel):
    """Request body for ``POST /api/companies/{company_id}/pdf``."""

    report: str = Field(min_length=1, description="Document type name or index")
    user_input: str | None = Field(
        default=None, description="Optional free-text guidance for the content"
    )
    model: str | None = None
    figure_kinds: list[str] = Field(
        default_factory=list,
        description=(
            'Allowed matplotlib figure kinds (e.g. "bar", '
            '"line", "area", "pie", "scatter", "histogram"). '
            "Empty (default) includes no figures."
        ),
    )
    quick_doc: bool = Field(
        default=False,
        description=(
            "When true, use the smaller, content-focused quick prompt "
            "set (no TOC, at most one data table, at most two figures, "
            "minimal styling) and cut the output-token caps for the "
            "markdown and HTML+CSS stages by 80% for a shorter, faster "
            "document."
        ),
    )
    gen_tracing: bool = Field(
        default=False,
        description=(
            "When true, persist the per-stage generation trace (prompts, "
            "outputs, timings) on the document record under the "
            "gen_tracing field."
        ),
    )

    _check_figure_kinds = field_validator("figure_kinds")(_validate_figure_kinds)


class DocumentExcelRequest(BaseModel):
    """Request body for ``POST /api/companies/{company_id}/excel``."""

    report: str = Field(min_length=1, description="Document type name or index")
    user_input: str | None = Field(
        default=None, description="Optional free-text guidance for the content"
    )
    model: str | None = None
    figure_kinds: list[str] = Field(
        default_factory=list,
        description=(
            'Allowed matplotlib figure kinds (e.g. "bar", '
            '"line", "area", "pie", "scatter", "histogram"). '
            "Empty (default) includes no figures. Ignored when "
            "simple_sheets is true."
        ),
    )
    quick_doc: bool = Field(
        default=False,
        description=(
            "When true, cut the markdown output-token cap by 80% and "
            "disable model thinking/reasoning for a shorter, faster "
            "workbook."
        ),
    )
    simple_sheets: bool = Field(
        default=False,
        description=(
            "When true, skip the cover sheet, force the figure kinds to "
            "empty, and keep the workbook to at most 4 sheets with 1-2 "
            "simple tables each."
        ),
    )
    glossary: bool = Field(
        default=False,
        description=(
            "When true, add a single Glossary lookup sheet defining the "
            "abbreviated terms used in the workbook (used sparingly: "
            "readable 4+ character terms, not every sheet)."
        ),
    )
    gen_tracing: bool = Field(
        default=False,
        description=(
            "When true, persist the per-stage generation trace (prompts, "
            "outputs, timings) on the document record under the "
            "gen_tracing field."
        ),
    )

    _check_figure_kinds = field_validator("figure_kinds")(_validate_figure_kinds)


class DocumentImageRequest(BaseModel):
    """Request body for ``POST /api/companies/{company_id}/image``."""

    report: str = Field(min_length=1, description="Document type name or index")
    user_input: str | None = Field(
        default=None, description="Optional free-text guidance for the content"
    )
    model: str | None = None
    figure_kinds: list[str] = Field(
        default_factory=list,
        description=(
            'Allowed matplotlib figure kinds (e.g. "bar", '
            '"line", "area", "pie", "scatter", "histogram"). '
            "Empty (default) includes no figures. At most one figure is "
            "used (single-page contract)."
        ),
    )
    a4_aspect: bool = Field(
        default=True,
        description=(
            "When true, lock the page to A4 portrait; when false the page "
            "sizes itself to the content."
        ),
    )
    distress: DistressOptions = Field(
        default_factory=DistressOptions,
        description=(
            "Optional post-processing pass making the PNG look like a "
            "scanned, aged document (no-op when enabled is false)."
        ),
    )
    gen_tracing: bool = Field(
        default=False,
        description=(
            "When true, persist the per-stage generation trace (prompts, "
            "outputs, timings) on the document record under the "
            "gen_tracing field."
        ),
    )

    _check_figure_kinds = field_validator("figure_kinds")(_validate_figure_kinds)


class JobStatus(BaseModel):
    """Snapshot of a generation job."""

    id: str
    status: str  # "running" | "done" | "error"
    total: int
    completed: int
    error: str | None = None
    company_ids: list[int] = Field(default_factory=list)
    result: Any = None
    logs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    """Tracks one generation job and fans out progress to SSE subscribers."""

    id: str
    total: int
    completed: int = 0
    status: str = "running"
    error: str | None = None
    company_ids: list[int] = field(default_factory=list)
    result: list[dict] | None = None
    logs: list[str] = field(default_factory=list)
    subscribers: list[queue.Queue] = field(default_factory=list)
    _subscribers_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def _snapshot(self) -> dict:
        return {
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "error": self.error,
            "result": self.result,
            "logs": list(self.logs),
        }

    def add_log(self, message: str) -> None:
        """Append a log line (bounded) and fan it out to subscribers."""
        self.logs.append(message)
        if len(self.logs) > MAX_JOB_LOGS:
            del self.logs[: len(self.logs) - MAX_JOB_LOGS]
        self._publish()

    def _publish(self) -> None:
        """Send the current snapshot to every subscriber (thread-safe)."""
        with self._subscribers_lock:
            queues = list(self.subscribers)
        event = self._snapshot()
        for q in queues:
            q.put_nowait(event)

    def subscribe(self) -> queue.Queue:
        """Register a new SSE subscriber and return its event queue.

        The current snapshot is always pushed as the first event so a
        mid-flight subscriber sees a consistent baseline. If the job has
        already finished, a ``None`` sentinel follows immediately so the
        stream closes instead of hanging.
        """
        q: queue.Queue = queue.Queue()
        with self._subscribers_lock:
            self.subscribers.append(q)
            q.put_nowait(self._snapshot())
            if self.status != "running":
                q.put_nowait(None)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Remove an SSE subscriber's queue (thread-safe)."""
        with self._subscribers_lock:
            self.subscribers.remove(q)

    def _close_subscribers(self) -> None:
        """Push the end-of-stream sentinel to every subscriber."""
        with self._subscribers_lock:
            queues = list(self.subscribers)
        for q in queues:
            q.put_nowait(None)  # sentinel: close the SSE stream


class _JobLogHandler(logging.Handler):
    """Collect log lines emitted by a job's worker thread onto the job.

    Attached to the ``document_gen`` logger for the lifetime of one job;
    records from other threads (concurrent jobs, the event loop) are
    ignored, so simultaneous jobs do not mix their progress lines.
    """

    def __init__(self, job: _Job, thread_id: int) -> None:
        super().__init__(level=logging.INFO)
        self._job = job
        self._thread_id = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._thread_id:
            return
        message = record.getMessage()
        if record.levelno >= logging.WARNING:
            message = f"{record.levelname}: {message}"
        self._job.add_log(message)


@contextmanager
def _capture_job_logs(job: _Job):
    """Route this thread's ``document_gen`` log lines onto *job*.

    Must be entered inside the job's worker thread so the handler can
    filter records by that thread's ID. The ``document_gen`` logger is
    forced to INFO for the job's duration so stage lines are not dropped
    by a quieter root level (e.g. under pytest, where the root keeps its
    WARNING default).
    """
    gen_logger = logging.getLogger("document_gen")
    handler = _JobLogHandler(job, threading.get_ident())
    previous_level = gen_logger.level
    gen_logger.setLevel(logging.INFO)
    gen_logger.addHandler(handler)
    try:
        yield
    finally:
        gen_logger.removeHandler(handler)
        gen_logger.setLevel(previous_level)


_JOBS: dict[str, _Job] = {}


def _prune_jobs() -> None:
    """Drop finished jobs beyond :data:`MAX_JOBS` (oldest first)."""
    finished = [j for j in _JOBS.values() if j.status != "running"]
    for job in finished[: max(0, len(finished) - MAX_JOBS)]:
        _JOBS.pop(job.id, None)


def _run_job(job: _Job, request: GenerateRequest) -> None:
    """Worker thread: generate companies and publish progress.

    The work is split across a fixed pool of :data:`COMPANY_GEN_THREADS`
    workers. Generated companies are attached to the job (``job.result``)
    for the user to review; they are only persisted when the user
    explicitly adds them via ``POST /api/companies``.

    Per-item failures are collected instead of aborting the job: the
    successful subset is returned, and the job ends in ``error`` only when
    every company failed. A partial failure keeps the status ``done`` and
    is reported via ``job.error`` (e.g. ``"3 of 10 failed: <msg>"``).
    """
    with _capture_job_logs(job):
        try:
            results: list[CompanyProfile] = []
            errors: list[str] = []
            logger.info(
                "Generating %d compan%s (threads=%d, industry=%s)",
                request.num,
                "y" if request.num == 1 else "ies",
                COMPANY_GEN_THREADS,
                request.industry or "random",
            )
            with ThreadPoolExecutor(max_workers=COMPANY_GEN_THREADS) as executor:
                futures = {
                    executor.submit(
                        generate_company_profile,
                        target_industry=request.industry,
                        log_output=False,
                        model_name=request.model,
                        user_input=request.user_input,
                    ): i
                    for i in range(request.num)
                }
                for future in as_completed(futures):
                    try:
                        company = future.result()
                        results.append(company)
                        logger.info(
                            "Company %d/%d generated: %s",
                            job.completed + 1,
                            request.num,
                            company.profile.name if company.profile else "(unnamed)",
                        )
                    except Exception as exc:  # keep going on per-item failures
                        errors.append(str(exc))
                        logger.warning(
                            "Company %d/%d failed: %s",
                            job.completed + 1,
                            request.num,
                            exc,
                        )
                    job.completed += 1
                    job._publish()

            if results:
                job.result = [profile.model_dump() for profile in results]
                job.status = "done"
                if errors:
                    job.error = f"{len(errors)} of {request.num} failed: {errors[0]}"
            else:
                job.status = "error"
                job.error = errors[0] if errors else "No companies generated"
        except Exception as exc:  # surface unexpected errors to the client
            job.status = "error"
            job.error = str(exc)
        finally:
            job._publish()
            job._close_subscribers()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    """Report server and LLM endpoint connectivity (per purpose)."""
    settings = llm.load_settings()

    def _endpoint_status(purpose: str) -> dict:
        config = settings.chat if purpose == "chat" else settings.embed
        backend = (
            llm.get_chat_backend() if purpose == "chat" else llm.get_embed_backend()
        )
        try:
            backend.list_models(timeout=PROBE_TIMEOUT)
            state = "up"
        except Exception:
            state = "down"
        return {"backend": config.backend, "status": state, "model": config.model}

    return {
        "status": "ok",
        "chat": _endpoint_status("chat"),
        "embed": _endpoint_status("embed"),
    }


@app.get("/api/models")
def list_models(purpose: Literal["chat", "embed"] = "chat") -> list[str]:
    """List model IDs on the active backend for *purpose* (empty if down)."""
    backend = llm.get_chat_backend() if purpose == "chat" else llm.get_embed_backend()
    try:
        return backend.list_models(timeout=PROBE_TIMEOUT)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# LLM settings
# ---------------------------------------------------------------------------

MASKED_API_KEY = "****"


def _mask_endpoint(config: EndpointConfig) -> dict:
    """Return *config* with the API key masked."""
    return {
        "backend": config.backend,
        "host": config.host,
        "model": config.model,
        "api_key": MASKED_API_KEY if config.api_key else None,
        "has_api_key": bool(config.api_key),
    }


def _mask_settings(settings: LLMSettings) -> dict:
    """Return *settings* with both API keys masked."""
    return {
        "chat": _mask_endpoint(settings.chat),
        "embed": _mask_endpoint(settings.embed),
    }


class TestEndpointRequest(BaseModel):
    """Request body for ``POST /api/settings/test``."""

    purpose: Literal["chat", "embed"]
    endpoint: EndpointConfig


@app.get("/api/settings")
def get_settings() -> dict:
    """Return the effective LLM settings (API keys masked)."""
    return _mask_settings(llm.load_settings())


@app.put("/api/settings")
def put_settings(payload: LLMSettings) -> dict:
    """Persist new LLM settings and return them masked.

    An ``api_key`` of ``"****`` (the masked placeholder) keeps the stored
    value unchanged.
    """
    current = llm.load_settings()
    for purpose in ("chat", "embed"):
        new_purpose = getattr(payload, purpose)
        if new_purpose.api_key == MASKED_API_KEY:
            new_purpose.api_key = getattr(current, purpose).api_key
    llm.save_settings(payload)
    return _mask_settings(payload)


@app.delete("/api/settings")
def delete_settings() -> dict:
    """Clear saved settings, falling back to ``.env`` defaults."""
    llm.clear_settings()
    return _mask_settings(llm.load_settings())


@app.post("/api/settings/test")
def check_endpoint(payload: TestEndpointRequest) -> dict:
    """Check connectivity of a submitted endpoint draft (not yet saved)."""
    backend = llm.build_backend(payload.endpoint)
    try:
        models = backend.list_models()
        if payload.purpose == "embed":
            model = payload.endpoint.model or (models[0] if models else None)
            if model is None:
                return {
                    "ok": False,
                    "model_count": len(models),
                    "models": [],
                    "error": "No embedding model available on this endpoint",
                }
            backend.embed(texts=["connectivity check"], model=model)
        return {
            "ok": True,
            "model_count": len(models),
            "models": models[:20],
            "error": None,
        }
    except Exception as exc:
        return {"ok": False, "model_count": 0, "models": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Document output directory settings
# ---------------------------------------------------------------------------


def _documents_settings_response() -> dict:
    """Build the effective document-output-directory settings response."""
    env_default = os.getenv(document_pdf.DOCUMENTS_DIR_ENV)
    saved = document_query.get_setting(document_pdf.DOCUMENTS_SETTINGS_KEY)
    saved_dir: str | None = None
    if saved:
        value = saved.get("output_dir")
        if isinstance(value, str) and value.strip():
            saved_dir = value
    if saved_dir:
        return {"output_dir": saved_dir, "default": env_default, "source": "saved"}
    if env_default:
        return {
            "output_dir": env_default,
            "default": env_default,
            "source": "env",
        }
    return {"output_dir": None, "default": None, "source": "none"}


@app.get("/api/settings/documents")
def get_documents_settings() -> dict:
    """Return the effective document output directory and its source."""
    return _documents_settings_response()


@app.put("/api/settings/documents")
def put_documents_settings(payload: DocumentsSettingsRequest) -> dict:
    """Persist (or clear, when ``output_dir`` is null) the saved directory."""
    if payload.output_dir is None or not payload.output_dir.strip():
        document_query.delete_setting(document_pdf.DOCUMENTS_SETTINGS_KEY)
    else:
        path = Path(payload.output_dir).expanduser()
        if not path.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
        document_query.set_setting(
            document_pdf.DOCUMENTS_SETTINGS_KEY, {"output_dir": str(path)}
        )
    return _documents_settings_response()


@app.delete("/api/settings/documents")
def delete_documents_settings() -> dict:
    """Clear the saved document output directory (falls back to the env default)."""
    document_query.delete_setting(document_pdf.DOCUMENTS_SETTINGS_KEY)
    return _documents_settings_response()


# ---------------------------------------------------------------------------
# Directory browsing (backing the settings UI directory browser)
# ---------------------------------------------------------------------------


@app.get("/api/fs/browse")
def browse(path: str | None = None) -> dict:
    """List the subdirectories of *path* (defaults to the user's home dir).

    Read-only listing used by the settings UI directory browser.
    """
    target = (Path(path).expanduser() if path else Path.home()).resolve()
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")
    entries = sorted(
        ({"name": p.name, "path": str(p)} for p in target.iterdir() if p.is_dir()),
        key=lambda entry: entry["name"].lower(),
    )
    parent = target.parent
    return {
        "path": str(target),
        "parent": str(parent) if parent != target else None,
        "entries": entries,
    }


@app.get("/api/industries")
def list_industries() -> list[str]:
    """List the industries usable as generation seeds."""
    return industry_list


@app.post("/api/companies/generate", status_code=202)
def start_generation(request: GenerateRequest) -> dict:
    """Start a background generation job and return its ID."""
    job = _Job(id=uuid.uuid4().hex[:12], total=request.num)
    _JOBS[job.id] = job
    _prune_jobs()
    threading.Thread(target=_run_job, args=(job, request), daemon=True).start()
    return {"id": job.id, "status": job.status, "total": job.total}


@app.post("/api/companies", status_code=201)
def save_companies(profiles: list[CompanyProfile]) -> list[int]:
    """Persist companies generated by a background job.

    The body is the list of generated company profiles (the job's
    ``result`` payload, or a subset of it when the user only selected
    some of the generated companies). Returns the TinyDB ``doc_id`` of
    each stored company, in order.
    """
    if not profiles:
        raise HTTPException(status_code=422, detail="At least one company is required")
    return document_query.save_companies(profiles)


def _run_document_types_job(
    job: _Job, company_id: int, request: GenerateDocumentTypesRequest
) -> None:
    """Worker thread: generate document types for one company, publish progress.

    The generated documents are attached to the job (``job.result``) for
    the user to review; they are only persisted when the user explicitly
    adds them via the append endpoint.
    """
    with _capture_job_logs(job):
        try:
            logger.info(
                "Generating %d document type(s) for company %d",
                request.num,
                company_id,
            )
            reports = generate_documents_for_company(
                company_id,
                document_request=request.document_request,
                model_name=request.model,
                num_documents=request.num,
            )
            job.result = [report.model_dump() for report in reports]
            job.completed += 1
            job.company_ids = [company_id]
            job.status = "done"
        except Exception as exc:  # surface any pipeline error to the client
            job.status = "error"
            job.error = str(exc)
        finally:
            job._publish()
            job._close_subscribers()


@app.get("/api/companies/jobs/{job_id}")
def job_status(job_id: str) -> JobStatus:
    """Return a snapshot of a generation job."""
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job ID")
    return JobStatus(
        id=job.id,
        status=job.status,
        total=job.total,
        completed=job.completed,
        error=job.error,
        company_ids=job.company_ids,
        result=job.result,
        logs=job.logs,
    )


@app.get("/api/companies/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Stream job progress as server-sent events."""
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job ID")
    q = job.subscribe()

    async def stream():
        try:
            while True:
                event = await asyncio.to_thread(q.get)
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            job.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/storage")
def storage_info() -> dict:
    """Return the configured company storage location."""
    return {"path": document_query.db_path()}


@app.get("/api/companies")
def list_companies(
    industry: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """List company summaries, optionally filtered by industry or search text."""
    return document_query.list_companies(industry=industry, search=search)


@app.get("/api/companies/{company_id}")
def get_company(company_id: int) -> dict:
    """Return the full company document with the given TinyDB ``doc_id``."""
    company = document_query.get_company(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@app.patch("/api/companies/{company_id}")
def update_company(company_id: int, profile: SyntheticCompany) -> dict:
    """Update the stored profile of the given company."""
    company = document_query.update_company(company_id, profile)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


# ---------------------------------------------------------------------------
# Company document types
# ---------------------------------------------------------------------------


def _require_company(company_id: int) -> None:
    """Raise 404 when no company with *company_id* exists."""
    if document_query.get_company(company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")


@app.get("/api/companies/{company_id}/document-types")
def list_company_document_types(company_id: int) -> list[dict]:
    """Return the document types linked to the given company."""
    _require_company(company_id)
    return document_query.get_document_types(company_id)


@app.put("/api/companies/{company_id}/document-types")
def replace_company_document_types(
    company_id: int, documents: list[DocumentType]
) -> list[dict]:
    """Replace all document types linked to the given company.

    The body is the complete new list (an empty list clears the company's
    document types).
    """
    _require_company(company_id)
    document_query.save_document_types(company_id, documents)
    return document_query.get_document_types(company_id)


@app.post("/api/companies/{company_id}/document-types")
def append_company_document_types(
    company_id: int, documents: list[DocumentType]
) -> list[dict]:
    """Append document types to the company's existing list.

    Existing document types are kept; the body is added to them.
    """
    _require_company(company_id)
    document_query.append_document_types(company_id, documents)
    return document_query.get_document_types(company_id)


@app.delete("/api/companies/{company_id}/document-types/{document_type_id}")
def delete_company_document_type(company_id: int, document_type_id: int) -> dict:
    """Delete a single document type linked to the given company.

    Generated documents belonging to the document type are removed too.
    """
    _require_company(company_id)
    if not document_query.delete_document_type(company_id, document_type_id):
        raise HTTPException(status_code=404, detail="Document type not found")
    return {"deleted": True}


@app.patch("/api/companies/{company_id}/document-types/{document_type_id}")
def update_company_document_type(
    company_id: int, document_type_id: int, document: DocumentType
) -> dict:
    """Update a single document type linked to the given company."""
    _require_company(company_id)
    updated = document_query.update_document_type(
        company_id, document_type_id, document
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Document type not found")
    return updated


@app.delete("/api/companies/{company_id}/document-types")
def clear_company_document_types(company_id: int) -> dict:
    """Delete all document types linked to the given company."""
    _require_company(company_id)
    document_query.delete_document_types(company_id)
    return {"deleted": True}


@app.post("/api/companies/{company_id}/document-types/generate", status_code=202)
def generate_company_document_types(
    company_id: int, request: GenerateDocumentTypesRequest
) -> dict:
    """Start a background job generating document types for one company."""
    _require_company(company_id)
    job = _Job(id=uuid.uuid4().hex[:12], total=1)
    _JOBS[job.id] = job
    _prune_jobs()
    threading.Thread(
        target=_run_document_types_job, args=(job, company_id, request), daemon=True
    ).start()
    return {"id": job.id, "status": job.status, "total": job.total}


# ---------------------------------------------------------------------------
# PDF document generation
# ---------------------------------------------------------------------------


def _run_pdf_job(job: _Job, company_id: int, request: DocumentPdfRequest) -> None:
    """Worker thread: generate one PDF document, publish progress."""
    with _capture_job_logs(job):
        logger.info(
            "PDF job %s: starting (company %s, report=%r, model=%r)",
            job.id,
            company_id,
            request.report,
            request.model,
        )
        t0 = time.perf_counter()
        try:
            artifact = document_pdf.generate_document_pdf(
                company_id,
                request.report,
                user_input=request.user_input,
                model_name=request.model,
                figure_kinds=request.figure_kinds,
                quick_doc=request.quick_doc,
                gen_tracing=request.gen_tracing,
            )
            logger.info(
                "PDF job %s: done in %.3fs -> %s",
                job.id,
                time.perf_counter() - t0,
                artifact.pdf_path,
            )
            job.result = {"pdf": artifact.pdf_path.name, "report": artifact.report_name}
            job.completed += 1
            job.company_ids = [company_id]
            job.status = "done"
        except Exception as exc:  # surface any pipeline error to the client
            logger.exception(
                "PDF job %s: failed after %.3fs", job.id, time.perf_counter() - t0
            )
            job.status = "error"
            job.error = str(exc)
        finally:
            job._publish()
            job._close_subscribers()


@app.post("/api/companies/{company_id}/pdf", status_code=202)
def generate_company_pdf(company_id: int, request: DocumentPdfRequest) -> dict:
    """Start a background job generating a PDF document for one company.

    Rejected with 400 when no effective document output directory is
    configured (saved setting or ``DOCUMENTS_DIR`` env var).
    """
    _require_company(company_id)
    if document_pdf.resolve_output_dir() is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No document output directory set: configure it in the "
                "Settings tab or set the DOCUMENTS_DIR environment variable."
            ),
        )
    job = _Job(id=uuid.uuid4().hex[:12], total=1)
    _JOBS[job.id] = job
    _prune_jobs()
    threading.Thread(
        target=_run_pdf_job, args=(job, company_id, request), daemon=True
    ).start()
    return {"id": job.id, "status": job.status, "total": job.total}


@app.get("/api/companies/{company_id}/pdf/{filename}")
def download_company_pdf(company_id: int, filename: str) -> FileResponse:
    """Serve a generated PDF from the document output directory."""
    _require_company(company_id)
    if (
        not filename.endswith(".pdf")
        or filename != Path(filename).name
        or "/" in filename
        or "\\" in filename
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")
    out_dir = document_pdf.resolve_output_dir()
    if out_dir is None:
        raise HTTPException(status_code=400, detail="No document output directory set")
    path = (out_dir / filename).resolve()
    if not path.is_file() or out_dir.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=filename)


# ---------------------------------------------------------------------------
# Excel workbook generation
# ---------------------------------------------------------------------------

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _run_excel_job(job: _Job, company_id: int, request: DocumentExcelRequest) -> None:
    """Worker thread: generate one Excel workbook, publish progress."""
    with _capture_job_logs(job):
        logger.info(
            "Excel job %s: starting (company %s, report=%r, model=%r)",
            job.id,
            company_id,
            request.report,
            request.model,
        )
        t0 = time.perf_counter()
        try:
            artifact = document_excel.generate_document_excel(
                company_id,
                request.report,
                user_input=request.user_input,
                model_name=request.model,
                figure_kinds=request.figure_kinds,
                quick_doc=request.quick_doc,
                simple_sheets=request.simple_sheets,
                glossary=request.glossary,
                gen_tracing=request.gen_tracing,
            )
            logger.info(
                "Excel job %s: done in %.3fs -> %s",
                job.id,
                time.perf_counter() - t0,
                artifact.xlsx_path,
            )
            job.result = {
                "xlsx": artifact.xlsx_path.name,
                "report": artifact.report_name,
            }
            job.completed += 1
            job.company_ids = [company_id]
            job.status = "done"
        except Exception as exc:  # surface any pipeline error to the client
            logger.exception(
                "Excel job %s: failed after %.3fs", job.id, time.perf_counter() - t0
            )
            job.status = "error"
            job.error = str(exc)
        finally:
            job._publish()
            job._close_subscribers()


@app.post("/api/companies/{company_id}/excel", status_code=202)
def generate_company_excel(company_id: int, request: DocumentExcelRequest) -> dict:
    """Start a background job generating an Excel workbook for one company.

    Rejected with 400 when no effective document output directory is
    configured (saved setting or ``DOCUMENTS_DIR`` env var).
    """
    _require_company(company_id)
    if document_pdf.resolve_output_dir() is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No document output directory set: configure it in the "
                "Settings tab or set the DOCUMENTS_DIR environment variable."
            ),
        )
    job = _Job(id=uuid.uuid4().hex[:12], total=1)
    _JOBS[job.id] = job
    _prune_jobs()
    threading.Thread(
        target=_run_excel_job, args=(job, company_id, request), daemon=True
    ).start()
    return {"id": job.id, "status": job.status, "total": job.total}


@app.get("/api/companies/{company_id}/excel/{filename}")
def download_company_excel(company_id: int, filename: str) -> FileResponse:
    """Serve a generated Excel workbook from the document output directory."""
    _require_company(company_id)
    if (
        not filename.endswith(".xlsx")
        or filename != Path(filename).name
        or "/" in filename
        or "\\" in filename
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")
    out_dir = document_pdf.resolve_output_dir()
    if out_dir is None:
        raise HTTPException(status_code=400, detail="No document output directory set")
    path = (out_dir / filename).resolve()
    if not path.is_file() or out_dir.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="Excel workbook not found")
    return FileResponse(path, media_type=XLSX_MEDIA_TYPE, filename=filename)


# ---------------------------------------------------------------------------
# PNG image document generation
# ---------------------------------------------------------------------------


def _run_image_job(job: _Job, company_id: int, request: DocumentImageRequest) -> None:
    """Worker thread: generate one PNG image document, publish progress."""
    with _capture_job_logs(job):
        logger.info(
            "Image job %s: starting (company %s, report=%r, model=%r)",
            job.id,
            company_id,
            request.report,
            request.model,
        )
        t0 = time.perf_counter()
        try:
            artifact = document_png.generate_document_image(
                company_id,
                request.report,
                user_input=request.user_input,
                model_name=request.model,
                figure_kinds=request.figure_kinds,
                a4_aspect=request.a4_aspect,
                distress=request.distress,
                gen_tracing=request.gen_tracing,
            )
            logger.info(
                "Image job %s: done in %.3fs -> %s",
                job.id,
                time.perf_counter() - t0,
                artifact.png_path,
            )
            job.result = {
                "png": artifact.png_path.name,
                "report": artifact.report_name,
            }
            job.completed += 1
            job.company_ids = [company_id]
            job.status = "done"
        except Exception as exc:  # surface any pipeline error to the client
            logger.exception(
                "Image job %s: failed after %.3fs", job.id, time.perf_counter() - t0
            )
            job.status = "error"
            job.error = str(exc)
        finally:
            job._publish()
            job._close_subscribers()


@app.post("/api/companies/{company_id}/image", status_code=202)
def generate_company_image(company_id: int, request: DocumentImageRequest) -> dict:
    """Start a background job generating a PNG image document for one company.

    Rejected with 400 when no effective document output directory is
    configured (saved setting or ``DOCUMENTS_DIR`` env var).
    """
    _require_company(company_id)
    if document_pdf.resolve_output_dir() is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No document output directory set: configure it in the "
                "Settings tab or set the DOCUMENTS_DIR environment variable."
            ),
        )
    job = _Job(id=uuid.uuid4().hex[:12], total=1)
    _JOBS[job.id] = job
    _prune_jobs()
    threading.Thread(
        target=_run_image_job, args=(job, company_id, request), daemon=True
    ).start()
    return {"id": job.id, "status": job.status, "total": job.total}


@app.get("/api/companies/{company_id}/image/{filename}")
def download_company_image(company_id: int, filename: str) -> FileResponse:
    """Serve a generated PNG image from the document output directory."""
    _require_company(company_id)
    if (
        not filename.endswith(".png")
        or filename != Path(filename).name
        or "/" in filename
        or "\\" in filename
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")
    out_dir = document_pdf.resolve_output_dir()
    if out_dir is None:
        raise HTTPException(status_code=400, detail="No document output directory set")
    path = (out_dir / filename).resolve()
    if not path.is_file() or out_dir.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="PNG image not found")
    return FileResponse(path, media_type="image/png", filename=filename)


# ---------------------------------------------------------------------------
# Generated documents
# ---------------------------------------------------------------------------


@app.get("/api/documents")
def list_documents(
    company_id: int | None = None,
    document_type_id: int | None = None,
) -> list[dict]:
    """List generated documents, optionally filtered by foreign key."""
    return document_query.list_documents(
        company_id=company_id, document_type_id=document_type_id
    )


@app.get("/api/documents/{doc_id}/download")
def download_document(doc_id: int) -> FileResponse:
    """Serve the file a document record points at (any filetype).

    The stored path was written by our own generators (never user input),
    so only the record and file existence are checked.
    """
    record = document_query.get_document(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(record["filepath"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path, filename=record["filename"])


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int) -> dict[str, bool]:
    """Delete a document record and its file on disk."""
    record = document_query.get_document(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    document_query.delete_document(doc_id)
    path = Path(record["filepath"])
    if path.is_file():
        path.unlink(missing_ok=True)
    return {"deleted": True}


@app.patch("/api/documents/{doc_id}")
def rename_document(doc_id: int, payload: RenameDocumentRequest) -> dict:
    """Rename a document's stored name and its file on disk.

    The payload carries the new base name; the original file extension
    is preserved.
    """
    record = document_query.get_document(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        updated = document_query.rename_document(doc_id, payload.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated


def _original_image_path(record: dict) -> Path | None:
    """Return the stored pre-distress original for a PNG document record.

    The path lives at ``gen_tracing.stages.distress.original_path`` and is
    written when the document was generated with tracing on. Returns
    ``None`` when the trace is missing, the field is absent, or the file
    no longer exists on disk.
    """
    trace = record.get("gen_tracing")
    if not isinstance(trace, dict):
        return None
    stages = trace.get("stages")
    if not isinstance(stages, dict):
        return None
    distress = stages.get("distress")
    if not isinstance(distress, dict):
        return None
    original = distress.get("original_path")
    if not isinstance(original, str) or not original:
        return None
    path = Path(original)
    return path if path.is_file() else None


def _distress_source_bytes(doc_id: int) -> tuple[dict, bytes]:
    """Load a PNG document's stored original for the distress editor.

    Shared by the preview and save endpoints: performs the 404/400/409
    checks and returns the record plus the original's bytes.
    """
    record = document_query.get_document(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if record["filetype"] != "png":
        raise HTTPException(status_code=400, detail="Document is not a PNG image")
    original = _original_image_path(record)
    if original is None:
        raise HTTPException(
            status_code=409, detail="No stored original image for this document"
        )
    return record, original.read_bytes()


@app.post("/api/documents/{doc_id}/image/distress-preview")
def distress_preview(doc_id: int, payload: DistressEditRequest) -> Response:
    """Render the distressed image for the live editor (not persisted).

    Re-runs the exact server-side distress pipeline on the stored
    original, so the preview is byte-identical to what the save endpoint
    will persist. Sync ``def`` on purpose: FastAPI runs it in the thread
    pool so the cv2 work does not block the event loop.
    """
    _, original_bytes = _distress_source_bytes(doc_id)
    try:
        png_bytes = distress_image_to_bytes(
            original_bytes, payload.distress, payload.seed, payload.stain_seed
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": "inline"},
    )


@app.post("/api/documents/{doc_id}/image/distress-save")
def distress_save(doc_id: int, payload: DistressEditRequest) -> dict:
    """Persist the editor's distressed render over the document file.

    The render is re-derived from the stored original (which is left
    untouched, so the document stays re-editable) and written over the
    record's ``filepath``; the record's ``size_kb`` is refreshed and the
    editor state (options + seeds) is stored so re-opening the preview
    loads the same settings.
    """
    record, original_bytes = _distress_source_bytes(doc_id)
    try:
        png_bytes = distress_image_to_bytes(
            original_bytes, payload.distress, payload.seed, payload.stain_seed
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    Path(record["filepath"]).write_bytes(png_bytes)
    try:
        updated = document_query.save_document_distress(
            doc_id,
            payload.distress.model_dump(mode="json"),
            payload.seed,
            payload.stain_seed,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Document file not found on disk"
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return updated


@app.get("/api/documents/{doc_id}/preview")
def preview_document(doc_id: int) -> FileResponse:
    """Serve a document with ``Content-Disposition: inline``.

    Unlike the download endpoint, the browser can render the file
    directly (e.g. PDFs in an iframe) instead of saving it.
    """
    record = document_query.get_document(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(record["filepath"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")
    media_type = mimetypes.guess_type(record["filename"])[0] or (
        "application/octet-stream"
    )
    headers = {"Content-Disposition": f'inline; filename="{record["filename"]}"'}
    return FileResponse(path, media_type=media_type, headers=headers)


# Static frontend (mounted last so /api routes take precedence).
# Requires a built frontend: `cd web && pnpm install && pnpm build`.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    # No reload here on purpose: the reloader would watch the whole working
    # directory (including web/node_modules) and stall. For auto-reload use:
    #   uv run uvicorn document_gen.server:app --reload --reload-dir document_gen
    uvicorn.run("document_gen.server:app", host="127.0.0.1", port=8000)
