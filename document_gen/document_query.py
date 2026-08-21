"""Central TinyDB storage helper for company records, settings, and documents.

All reads and writes of generated company profiles go through this module.
By default the database is **in-memory** (data does not survive a process
restart). Setting the ``TINYDB_PATH`` environment variable switches to a
persistent single-file JSON store at that path.

The database holds four collections:

- the default collection: one document per company (profile, seed,
  timestamps — documents are **not** embedded here anymore),
- ``user_settings``: one document per named settings group (e.g. the LLM
  chat/embedding configuration, future dashboard preferences),
- ``document_types``: one document per document type, linked back to its
  company via the ``company_id`` foreign key,
- ``documents``: one document per generated document file (any
  filetype), linked back via ``company_id`` and ``document_type_id``
  foreign keys; PDF documents additionally carry a ``gen_tracing``
  field with the per-stage generation trace (prompts, outputs,
  timings) as a JSON-serializable dict.

Legacy databases (collections ``report_types`` / ``report_documents``,
the ``report_type_id`` foreign key, and the ``"reports"`` user-settings
key) are migrated automatically when the database is opened.

TinyDB is not safe for concurrent access, so every operation in this module
is guarded by a module-level lock. Callers only ever see plain dicts and
integers — never a ``TinyDB`` instance.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tinydb import TinyDB
from tinydb.queries import where
from tinydb.storages import MemoryStorage

from document_gen.models.company import (
    CompanyProfile,
    DocumentType,
    SyntheticCompany,
)

MEMORY_DB_PATH = "<memory>"

#: Collection holding one document per named settings group.
USER_SETTINGS = "user_settings"
#: Collection holding one document per document type (FK: ``company_id``).
DOCUMENT_TYPES = "document_types"
#: Collection holding one document per generated document file
#: (FKs: ``company_id``, ``document_type_id``).
DOCUMENTS = "documents"

#: Legacy collection names (pre general-document rename), kept for the
#: one-shot open-time migration.
_LEGACY_DOCUMENT_TYPES = "report_types"
_LEGACY_DOCUMENTS = "report_documents"
#: Legacy user-settings key for the document output directory.
_LEGACY_DOCUMENTS_SETTINGS_KEY = "reports"

_LOCK = threading.RLock()
_DB: TinyDB | None = None


def get_db() -> TinyDB:
    """Return the cached TinyDB instance, creating it on first use.

    When ``TINYDB_PATH`` is set, a persistent file-backed database is used
    (parent directories are created as needed). Otherwise an in-memory
    database is used and its contents are lost when the process exits.

    On first open, legacy company documents that still embed their
    documents are migrated into the :data:`DOCUMENT_TYPES` collection, and
    legacy collection/field names are migrated (see
    :func:`_migrate_legacy_collections`).

    Returns:
        The shared :class:`~tinydb.db.TinyDB` instance.
    """
    global _DB
    if _DB is None:
        # Double-checked locking: two threads racing on first open must
        # not each construct their own TinyDB handle.
        with _LOCK:
            if _DB is None:
                path = os.environ.get("TINYDB_PATH")
                if path:
                    file_path = Path(path)
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    _DB = TinyDB(file_path)
                else:
                    _DB = TinyDB(storage=MemoryStorage)
                _migrate_embedded_documents(_DB)
                _migrate_legacy_collections(_DB)
    return _DB


def reset_db() -> None:
    """Close and drop the cached database handle.

    Intended for tests: subsequent calls to :func:`get_db` reopen the
    database (honoring a possibly changed ``TINYDB_PATH``).
    """
    global _DB
    if _DB is not None:
        _DB.close()
        _DB = None


def db_path() -> str:
    """Return the storage location as a string.

    The configured file path when ``TINYDB_PATH`` is set, otherwise
    :data:`MEMORY_DB_PATH`.
    """
    return os.environ.get("TINYDB_PATH") or MEMORY_DB_PATH


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _settings_table():
    """Return the ``user_settings`` collection of the shared database."""
    return get_db().table(USER_SETTINGS)


def _document_types_table():
    """Return the ``document_types`` collection of the shared database."""
    return get_db().table(DOCUMENT_TYPES)


def _documents_table():
    """Return the ``documents`` collection of the shared database."""
    return get_db().table(DOCUMENTS)


def _migrate_embedded_documents(db: TinyDB) -> None:
    """Move legacy embedded documents into the ``document_types`` collection.

    Company documents written before the denormalization keep their
    documents inline under the ``reports`` key. This one-shot migration
    copies each embedded document into a standalone document carrying a
    ``company_id`` foreign key and empties the embedded list. It is a
    no-op for databases that already use the new layout.

    Note:
        Legacy company documents may still carry a (possibly empty)
        ``reports`` key after migration; readers should always use
        :func:`get_company`, which attaches the documents from the
        :data:`DOCUMENT_TYPES` collection.

    Args:
        db: The freshly opened database to migrate.
    """
    table = db.table(DOCUMENT_TYPES)
    document_docs: list[dict[str, Any]] = []
    touched_ids: list[int] = []
    for doc in db.all():
        reports = doc.get("reports") or []
        if not reports:
            continue
        doc_id = doc.doc_id  # type: ignore[attr-defined]
        document_docs.extend({"company_id": doc_id, **report} for report in reports)
        touched_ids.append(doc_id)
    if document_docs:
        # One bulk write: the file-backed storage rewrites the whole
        # database on every write.
        table.insert_multiple(document_docs)
        db.update({"reports": []}, doc_ids=touched_ids)


def _migrate_legacy_collections(db: TinyDB) -> None:
    """One-shot migration of the legacy (finance-era) collection layout.

    - ``report_types`` -> :data:`DOCUMENT_TYPES`
    - ``report_documents`` -> :data:`DOCUMENTS`, remapping the
      ``report_type_id`` foreign key to ``document_type_id`` (ids change
      because the type documents are re-inserted),
    - user-settings key ``"reports"`` -> ``"documents"``.

    All steps are no-ops when the legacy names are already gone.

    Args:
        db: The freshly opened database to migrate.
    """
    legacy_types = db.table(_LEGACY_DOCUMENT_TYPES)
    legacy_type_docs = legacy_types.all()
    id_map: dict[int, int] = {}
    if legacy_type_docs:
        new_ids = db.table(DOCUMENT_TYPES).insert_multiple(
            [dict(doc) for doc in legacy_type_docs]
        )
        id_map = {
            old.doc_id: new_id  # type: ignore[attr-defined]
            for old, new_id in zip(legacy_type_docs, new_ids, strict=True)
        }
        legacy_types.truncate()

    legacy_documents = db.table(_LEGACY_DOCUMENTS)
    legacy_document_docs = legacy_documents.all()
    if legacy_document_docs:
        migrated: list[dict[str, Any]] = []
        for doc in legacy_document_docs:
            record = dict(doc)
            legacy_type_id = record.pop("report_type_id", None)
            record["document_type_id"] = id_map.get(legacy_type_id)
            migrated.append(record)
        db.table(DOCUMENTS).insert_multiple(migrated)
        legacy_documents.truncate()

    settings = db.table(USER_SETTINGS)
    settings.update(
        {"key": "documents"},
        where("key") == _LEGACY_DOCUMENTS_SETTINGS_KEY,
    )


def _profile_to_doc(profile: CompanyProfile) -> dict[str, Any]:
    """Serialize a :class:`CompanyProfile` into a TinyDB company document.

    Documents are intentionally excluded: they live in the
    :data:`DOCUMENT_TYPES` collection (see :func:`save_document_types`) and
    are attached on read by :func:`get_company`.
    """
    return {
        "profile": profile.profile.model_dump(mode="json") if profile.profile else None,
        "seed": profile.seed,
        "user_input": profile.user_input,
        "created_at": _now_iso(),
    }


def _attach_documents(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``num_documents`` and ``documents`` to each document-type dict.

    ``documents`` is a list of small ``{id, filename, filetype}`` objects
    in creation order; the id lets callers build per-document links for
    any filetype. The caller must hold :data:`_LOCK`.

    Args:
        reports: Document-type dicts (must carry their ``id``).

    Returns:
        The same list, mutated in place.
    """
    docs_by_type: dict[int, list[dict[str, Any]]] = {}
    for doc in _documents_table().all():
        docs_by_type.setdefault(doc.get("document_type_id"), []).append(doc)
    for report in reports:
        docs = docs_by_type.get(report["id"], [])
        report["num_documents"] = len(docs)
        report["documents"] = [
            {"id": d.doc_id, "filename": d["filename"], "filetype": d["filetype"]}
            for d in docs
        ]
    return reports


def _doc_to_dict(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a plain-dict copy of *doc* with its ``doc_id`` exposed as ``id``.

    TinyDB 4 documents are ``dict`` subclasses whose id lives in the
    ``doc_id`` attribute, so a plain ``dict()`` copy drops it — re-add it
    under the friendlier ``id`` key.
    """
    result = dict(doc)
    result["id"] = doc.doc_id  # type: ignore[attr-defined]
    return result


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------


def get_setting(key: str) -> dict[str, Any] | None:
    """Fetch one settings group by name.

    Args:
        key: The settings group name (e.g. ``"llm"``).

    Returns:
        The stored value dict, or ``None`` when no such setting exists.
    """
    with _LOCK:
        doc = _settings_table().get(where("key") == key)
    return dict(doc["value"]) if doc is not None else None


def set_setting(key: str, value: dict[str, Any]) -> None:
    """Create or update one settings group.

    Args:
        key: The settings group name (e.g. ``"llm"``).
        value: The full JSON-serializable value to store.
    """
    with _LOCK:
        table = _settings_table()
        updated = table.update(
            {"value": value, "updated_at": _now_iso()}, where("key") == key
        )
        if not updated:
            table.insert({"key": key, "value": value, "updated_at": _now_iso()})


def delete_setting(key: str) -> bool:
    """Delete one settings group by name.

    Args:
        key: The settings group name to remove.

    Returns:
        ``True`` when a setting was deleted, ``False`` when none matched.
    """
    with _LOCK:
        removed = _settings_table().remove(where("key") == key)
    return len(removed) > 0


def list_settings() -> list[dict[str, Any]]:
    """List all stored settings groups.

    Returns:
        A list of dicts with ``key``, ``value`` and ``updated_at`` keys.
    """
    with _LOCK:
        return [dict(doc) for doc in _settings_table().all()]


# ---------------------------------------------------------------------------
# Document types
# ---------------------------------------------------------------------------


def _get_document_types_unlocked(company_id: int) -> list[dict[str, Any]]:
    """Return the document-type documents for *company_id* (caller holds lock)."""
    docs = _document_types_table().search(where("company_id") == company_id)
    return [_doc_to_dict(doc) for doc in docs]


def _document_type_docs(
    company_id: int, documents: list[DocumentType]
) -> list[dict[str, Any]]:
    """Serialize *documents* into ``document_types`` documents for *company_id*."""
    return [
        {"company_id": company_id, **document.model_dump(mode="json")}
        for document in documents
    ]


def _insert_document_types(company_id: int, documents: list[DocumentType]) -> list[int]:
    """Insert document-type documents for *company_id* (caller holds lock)."""
    docs = _document_type_docs(company_id, documents)
    if not docs:
        return []
    return _document_types_table().insert_multiple(docs)


def save_document_types(company_id: int, documents: list[DocumentType]) -> list[int]:
    """Replace all document types stored for a company.

    Existing documents for *company_id* are removed first, so this is a
    full replacement rather than an append.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.
        documents: The complete new list of document types (empty clears).

    Returns:
        The TinyDB ``doc_id`` of each inserted document, in order.
    """
    with _LOCK:
        existing = _document_types_table().search(where("company_id") == company_id)
        _document_types_table().remove(where("company_id") == company_id)
        if existing:
            _documents_table().remove(
                where("document_type_id").one_of([d.doc_id for d in existing])  # type: ignore[attr-defined]
            )
        return _insert_document_types(company_id, documents)


def append_document_types(company_id: int, documents: list[DocumentType]) -> list[int]:
    """Append document types to a company's existing list.

    Unlike :func:`save_document_types`, existing documents are kept; the
    new documents are added to them.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.
        documents: The document types to append.

    Returns:
        The TinyDB ``doc_id`` of each inserted document, in order.
    """
    with _LOCK:
        return _insert_document_types(company_id, documents)


def get_document_types(company_id: int) -> list[dict[str, Any]]:
    """Fetch all document types linked to a company.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.

    Returns:
        A list of document-type dicts with ``id``, ``company_id``,
        ``name``, ``category``, ``purpose``, ``num_documents`` and
        ``documents`` keys.
    """
    with _LOCK:
        return _attach_documents(_get_document_types_unlocked(company_id))


def delete_document_types(company_id: int) -> bool:
    """Delete all document types linked to a company.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.

    Returns:
        ``True`` when at least one document was deleted.
    """
    with _LOCK:
        existing = _document_types_table().search(where("company_id") == company_id)
        removed = _document_types_table().remove(where("company_id") == company_id)
        if existing:
            _documents_table().remove(
                where("document_type_id").one_of([d.doc_id for d in existing])  # type: ignore[attr-defined]
            )
    return len(removed) > 0


def delete_document_type(company_id: int, document_type_id: int) -> bool:
    """Delete a single document type linked to a company.

    Any generated documents belonging to the document type are removed
    as well.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.
        document_type_id: The TinyDB ``doc_id`` of the document type.

    Returns:
        ``True`` when a document type was deleted, ``False`` when no
        document type with that id is linked to the company.
    """
    with _LOCK:
        table = _document_types_table()
        # ``doc_id`` is not queryable in TinyDB, so filter in Python.
        owned = [
            doc
            for doc in table.search(where("company_id") == company_id)
            if doc.doc_id == document_type_id
        ]
        if not owned:
            return False
        table.remove(doc_ids=[document_type_id])
        _documents_table().remove(where("document_type_id") == document_type_id)
    return True


def update_document_type(
    company_id: int, document_type_id: int, document: DocumentType
) -> dict[str, Any] | None:
    """Update a single document type linked to a company.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.
        document_type_id: The TinyDB ``doc_id`` of the document type.
        document: The new name/category/purpose values.

    Returns:
        The updated document-type dict (with attached documents), or
        ``None`` when no document type with that id is linked to the
        company.
    """
    with _LOCK:
        table = _document_types_table()
        # ``doc_id`` is not queryable in TinyDB, so filter in Python.
        owned = [
            doc
            for doc in table.search(where("company_id") == company_id)
            if doc.doc_id == document_type_id
        ]
        if not owned:
            return None
        values = document.model_dump(mode="json")
        # ``user_input`` tracks the generation-time context: a plain edit
        # (which never sends it) must not wipe the stored value.
        if values.get("user_input") is None:
            values["user_input"] = owned[0].get("user_input")
        table.update(values, doc_ids=[document_type_id])
        updated = [
            doc
            for doc in _attach_documents(_get_document_types_unlocked(company_id))
            if doc["id"] == document_type_id
        ]
    return updated[0] if updated else None


# ---------------------------------------------------------------------------
# Generated documents
# ---------------------------------------------------------------------------


def _document_fields(path: Path) -> dict[str, Any]:
    """Build the stored file fields for one generated file.

    The filetype is derived from the path suffix (without dot), so this
    works for any filetype (``pdf``, ``csv``, ``xlsx``, ``docx``, …).

    Args:
        path: Path of the generated file (must exist).

    Returns:
        The ``filename``, ``filetype``, ``filepath``, ``size_kb`` and
        ``created_at`` fields.
    """
    return {
        "filename": path.name,
        "filetype": path.suffix.lstrip("."),
        "filepath": str(path.resolve()),
        "size_kb": round(path.stat().st_size / 1024, 2),
        "created_at": _now_iso(),
    }


def save_document(
    company_id: int,
    document_type_id: int,
    path: Path,
    gen_tracing: dict[str, Any] | None = None,
) -> int:
    """Record one generated document file (any filetype).

    Generic entry point for every generator: the stored fields are
    derived from *path*, so CSV/DOCX/Excel/etc. generators call the same
    function after their write step.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.
        document_type_id: The TinyDB ``doc_id`` of the owning document type.
        path: Path of the generated file (must exist).
        gen_tracing: Optional per-stage generation trace (prompts,
            outputs, timings) stored on the record as ``gen_tracing``.
            ``None`` (the default) omits the field, so generators
            without a trace are unaffected.

    Returns:
        The TinyDB ``doc_id`` of the new document record.
    """
    doc: dict[str, Any] = {
        "company_id": company_id,
        "document_type_id": document_type_id,
        **_document_fields(path),
    }
    if gen_tracing is not None:
        doc["gen_tracing"] = gen_tracing
    with _LOCK:
        return _documents_table().insert(doc)


def get_document(doc_id: int) -> dict[str, Any] | None:
    """Fetch one document record by its ``doc_id``.

    Args:
        doc_id: The TinyDB document id to look up.

    Returns:
        The record as a plain dict with an ``id`` key, or ``None`` when
        no record with that id exists.
    """
    with _LOCK:
        doc = _documents_table().get(doc_id=doc_id)
    return _doc_to_dict(doc) if doc is not None else None


def list_documents(
    company_id: int | None = None,
    document_type_id: int | None = None,
) -> list[dict[str, Any]]:
    """List document records, optionally filtered by foreign key.

    Args:
        company_id: When set, only records for this company are returned.
        document_type_id: When set, only records for this document type
            are returned.

    Returns:
        A list of record dicts with ``id``, the stored file fields,
        ``company_name`` and ``report_name`` (``None`` when the FK target
        has been deleted), newest first.
    """
    with _LOCK:
        db = get_db()
        docs = _documents_table().all()
        if company_id is not None:
            docs = [d for d in docs if d.get("company_id") == company_id]
        if document_type_id is not None:
            docs = [d for d in docs if d.get("document_type_id") == document_type_id]
        company_names = {
            d.doc_id: (d.get("profile") or {}).get("name")  # type: ignore[attr-defined]
            for d in db.all()
        }
        report_names = {
            d.doc_id: d.get("name")  # type: ignore[attr-defined]
            for d in db.table(DOCUMENT_TYPES).all()
        }
    items: list[dict[str, Any]] = []
    for doc in sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True):
        item = _doc_to_dict(doc)
        item["company_name"] = company_names.get(doc.get("company_id"))
        item["report_name"] = report_names.get(doc.get("document_type_id"))
        items.append(item)
    return items


def delete_documents(
    company_id: int | None = None,
    document_type_ids: list[int] | None = None,
) -> int:
    """Delete document records by company or document type.

    Args:
        company_id: When set, all records for this company are removed.
        document_type_ids: When set, all records for these document types
            are removed. Takes precedence over *company_id*.

    Returns:
        The number of records deleted.
    """
    with _LOCK:
        table = _documents_table()
        if document_type_ids:
            removed = table.remove(where("document_type_id").one_of(document_type_ids))
        elif company_id is not None:
            removed = table.remove(where("company_id") == company_id)
        else:
            return 0
    return len(removed)


def delete_document(doc_id: int) -> bool:
    """Delete a single document record by its ``doc_id``.

    Args:
        doc_id: The TinyDB document id to remove.

    Returns:
        ``True`` when a record was deleted, ``False`` when no record
        with that id existed.
    """
    with _LOCK:
        table = _documents_table()
        if table.get(doc_id=doc_id) is None:
            return False
        table.remove(doc_ids=[doc_id])
    return True


def rename_document(doc_id: int, new_base: str) -> dict[str, Any] | None:
    """Rename a document record and its file on disk.

    The caller supplies the new base name (without extension); the
    original file extension is preserved.

    Args:
        doc_id: The TinyDB document id to rename.
        new_base: New base name (no extension).

    Returns:
        The updated record as a plain dict with an ``id`` key, or
        ``None`` when no record with that id exists.

    Raises:
        ValueError: When *new_base* is empty or contains path separators.
        FileExistsError: When a file with the new name already exists.
    """
    new_base = new_base.strip()
    if not new_base:
        raise ValueError("Filename must not be empty")
    if "/" in new_base or "\\" in new_base or new_base in {".", ".."}:
        raise ValueError("Filename must not contain path separators")
    with _LOCK:
        table = _documents_table()
        doc = table.get(doc_id=doc_id)
        if doc is None:
            return None
        old_path = Path(doc["filepath"])
        suffix = Path(doc["filename"]).suffix
        new_path = old_path.with_name(new_base + suffix)
        if new_path != old_path and new_path.exists():
            raise FileExistsError(f"{new_path.name} already exists")
        if old_path.is_file():
            old_path.rename(new_path)
        table.update(
            {"filename": new_path.name, "filepath": str(new_path.resolve())},
            doc_ids=[doc_id],
        )
        doc = table.get(doc_id=doc_id)
    return _doc_to_dict(doc) if doc is not None else None


def update_document_size(doc_id: int) -> dict[str, Any] | None:
    """Recompute the stored size of one document from its file on disk.

    Used after a document file is rewritten in place (e.g. a re-run
    distress pass) so the record's ``size_kb`` stays accurate.

    Args:
        doc_id: The TinyDB ``doc_id`` of the document to refresh.

    Returns:
        The updated record as a plain dict with an ``id`` key, or
        ``None`` when no record with that id exists.

    Raises:
        FileNotFoundError: When the file the record points at no longer
            exists on disk.
    """
    with _LOCK:
        table = _documents_table()
        doc = table.get(doc_id=doc_id)
        if doc is None:
            return None
        path = Path(doc["filepath"])
        if not path.is_file():
            raise FileNotFoundError(path)
        table.update({"size_kb": _document_fields(path)["size_kb"]}, doc_ids=[doc_id])
        doc = table.get(doc_id=doc_id)
    return _doc_to_dict(doc) if doc is not None else None


def get_document_type_id(company_id: int, document: str) -> int | None:
    """Resolve a document type name or 0-based index to its TinyDB ``doc_id``.

    Matching rules mirror :func:`document_gen.document_pdf.resolve_document_type`:
    case-insensitive name first, then a purely numeric *document* is
    treated as an index.

    Args:
        company_id: The TinyDB ``doc_id`` of the owning company.
        document: Document type name or index string.

    Returns:
        The document type's ``doc_id``, or ``None`` when nothing matches.
    """
    documents = get_document_types(company_id)
    key = document.strip().lower()
    for item in documents:
        if item["name"].lower() == key:
            return item["id"]
    if key.isdigit():
        index = int(key)
        if 0 <= index < len(documents):
            return documents[index]["id"]
    return None


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------


def save_company(profile: CompanyProfile) -> int:
    """Insert a single company profile into the database.

    The company's documents are written to the :data:`DOCUMENT_TYPES`
    collection, linked back via the returned ``doc_id``.

    Args:
        profile: The company profile to store.

    Returns:
        The TinyDB ``doc_id`` of the new company document.
    """
    with _LOCK:
        doc_id = get_db().insert(_profile_to_doc(profile))
        _insert_document_types(doc_id, profile.reports)
    return doc_id


def save_companies(profiles: list[CompanyProfile]) -> list[int]:
    """Insert multiple company profiles in one bulk write.

    Args:
        profiles: The company profiles to store.

    Returns:
        The TinyDB ``doc_id`` of each inserted company document, in order.
    """
    with _LOCK:
        doc_ids = get_db().insert_multiple([_profile_to_doc(p) for p in profiles])
        # One bulk write for all documents: the file-backed storage
        # rewrites the whole database on every write, so per-company
        # inserts would be quadratic for large batches.
        document_docs = [
            doc
            for doc_id, profile in zip(doc_ids, profiles, strict=True)
            for doc in _document_type_docs(doc_id, profile.reports)
        ]
        if document_docs:
            _document_types_table().insert_multiple(document_docs)
    return doc_ids


def get_company(doc_id: int) -> dict[str, Any] | None:
    """Fetch one company document by its ``doc_id``.

    The ``reports`` key is populated from the :data:`DOCUMENT_TYPES`
    collection so callers see the same shape as before the
    denormalization.

    Args:
        doc_id: The TinyDB document id to look up.

    Returns:
        The document as a plain dict with an ``id`` key, or ``None`` when
        no document with that id exists.
    """
    with _LOCK:
        doc = get_db().get(doc_id=doc_id)
        if doc is None:
            return None
        result = _doc_to_dict(doc)
        result["reports"] = _attach_documents(_get_document_types_unlocked(doc_id))
    return result


def list_companies(
    industry: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """List company summaries, optionally filtered.

    Args:
        industry: When set, only companies whose profile industry matches
            exactly are returned.
        search: When set, only companies whose profile JSON contains the
            text (case-insensitive) are returned.

    Returns:
        A list of summary dicts with ``id``, ``name``, ``industry``,
        ``headquarters``, ``size`` and ``num_reports`` keys.
    """
    with _LOCK:
        db = get_db()
        docs = db.all()
        document_docs = db.table(DOCUMENT_TYPES).all()
    counts: dict[int, int] = {}
    for document_doc in document_docs:
        company_id = document_doc.get("company_id")
        counts[company_id] = counts.get(company_id, 0) + 1
    items: list[dict[str, Any]] = []
    for doc in docs:
        profile = doc.get("profile")
        if profile is None:
            continue
        if industry and profile.get("industry") != industry:
            continue
        if (
            search
            and search.lower() not in json.dumps(profile, ensure_ascii=False).lower()
        ):
            continue
        items.append(
            {
                "id": doc.doc_id,  # type: ignore[attr-defined]
                "name": profile.get("name"),
                "industry": profile.get("industry"),
                "headquarters": profile.get("headquarters"),
                "size": profile.get("size"),
                "num_reports": counts.get(doc.doc_id, 0),  # type: ignore[attr-defined]
            }
        )
    return items


def update_company(doc_id: int, profile: SyntheticCompany) -> dict[str, Any] | None:
    """Update the stored profile of one company.

    Args:
        doc_id: The TinyDB ``doc_id`` of the company.
        profile: The new profile values (replaces the stored profile).

    Returns:
        The updated company document (same shape as :func:`get_company`),
        or ``None`` when no company with that id exists.
    """
    with _LOCK:
        updated = get_db().update(
            {"profile": profile.model_dump(mode="json")}, doc_ids=[doc_id]
        )
        if not updated:
            return None
    return get_company(doc_id)


def count_companies() -> int:
    """Return the total number of stored company documents."""
    with _LOCK:
        return len(get_db().all())


def delete_company(doc_id: int) -> bool:
    """Delete one company document by its ``doc_id``.

    The document types linked to the company are deleted as well.

    Args:
        doc_id: The TinyDB document id to remove.

    Returns:
        ``True`` when a document was deleted, ``False`` when none matched.
    """
    with _LOCK:
        removed_ids = get_db().remove(doc_ids=[doc_id])
        if removed_ids:
            _document_types_table().remove(where("company_id") == doc_id)
            _documents_table().remove(where("company_id") == doc_id)
    return len(removed_ids) > 0
