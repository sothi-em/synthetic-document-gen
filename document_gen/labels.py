"""Data-label generation and ChromaDB embedding utilities.

ChromaDB is an optional dependency (``uv sync --extra embed``)
and is imported lazily so the rest of the package works without it.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from pydantic import Field, create_model
from tqdm import tqdm

from document_gen.models.company import (
    CompanyDataLabel,
    condensed_industry_list,
)
from document_gen.llm import get_chat_backend, get_embed_backend
from document_gen.prompts import generate_data_label

LABELS_FILE = Path("labels.json")

EMBED_OPTIONS: dict = {
    "num_ctx": 2048,  # Explicit context size
    "num_predict": -1,  # Keeps token allocation dynamic
    "temperature": 0.0,  # Disables creative variance
}

#: Number of texts per embedding round-trip in :func:`embed_labels`.
EMBED_CHUNK_SIZE = 64


def _chroma_client():
    """Create a persistent ChromaDB client.

    The database path comes from the ``CHROMA_DB_PATH`` environment
    variable (see ``.env.example``), defaulting to ``./data/chromadb``.
    """
    import chromadb

    db_path = os.getenv("CHROMA_DB_PATH", "./data/chromadb")
    Path(db_path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=db_path)


def _load_labels() -> list[dict]:
    """Load previously generated labels from ``labels.json``."""
    with open(LABELS_FILE, encoding="utf-8") as infile:
        return json.load(infile)


def generate_labels() -> list[dict]:
    """Generate data labels for every condensed industry and save them.

    Resumable: industries already present in an existing ``labels.json``
    are skipped, and the file is only rewritten when the aggregated list
    is non-empty — a fully failed run never clobbers a good file.

    Returns:
        The aggregated list of label dicts (also written to ``labels.json``
        when non-empty).
    """
    # Built per call (mirrors pipeline._generate_document_types) so the
    # module has no import-time dynamic-model side effect.
    list_document_model = create_model(
        "ListReportModel",
        data_labels=(
            list[CompanyDataLabel],
            Field(description="List of data labels.", min_length=5, max_length=40),
        ),
    )
    existing = _load_labels() if LABELS_FILE.exists() else []
    done_industries = {label.get("industry") for label in existing}
    aggregate_label_json: list[dict] = list(existing)
    for industry in condensed_industry_list:
        if industry in done_industries:
            print(f"Skipping {industry} (already in {LABELS_FILE})")
            continue
        try:
            prompt = generate_data_label.replace("<user_input>", industry)
            generated_data_label = get_chat_backend().query(
                prompt=prompt,
                model=list_document_model,
            )
            generated_labels: list[CompanyDataLabel] = generated_data_label.data_labels
            for generated_label in generated_labels:
                json_label = generated_label.model_dump()
                json_label["industry"] = industry
                json_label["uuid"] = str(uuid.uuid4())
                aggregate_label_json.append(json_label)
            print(f"Generated {len(generated_labels)} labels for {industry}")
        except Exception as e:  # noqa: BLE001 - keep going on per-industry failures
            print(e)

    if aggregate_label_json:
        with open(LABELS_FILE, "w", encoding="utf-8") as outfile:
            json.dump(aggregate_label_json, outfile, indent=4)
    else:
        print("No labels generated; keeping the existing " f"{LABELS_FILE} untouched.")
    return aggregate_label_json


def embed_labels() -> None:
    """Embed every label in ``labels.json`` into the ``variable_label`` collection.

    Labels are embedded in chunks of :data:`EMBED_CHUNK_SIZE` texts, so a
    large label set costs one embedding round-trip and one ChromaDB
    ``add`` per chunk instead of one per label.
    """
    client = _chroma_client()
    # allow_missing: the collection does not exist on the first run.
    client.delete_collection(name="variable_label", allow_missing=True)
    collection = client.get_or_create_collection(name="variable_label")

    labels = _load_labels()
    for start in tqdm(range(0, len(labels), EMBED_CHUNK_SIZE), desc="Embedding labels"):
        chunk = labels[start : start + EMBED_CHUNK_SIZE]
        texts = [
            "\n".join(f"{key} - {value}" for key, value in label.items())
            for label in chunk
        ]
        embeddings = get_embed_backend().embed(texts=texts, options=EMBED_OPTIONS)
        collection.add(
            ids=[str(uuid.uuid4()) for _ in chunk],
            documents=texts,
            embeddings=embeddings,
            metadatas=chunk,
        )


def query_labels(query: str, n_results: int = 5) -> None:
    """Query the ``variable_label`` collection with a free-text query."""
    client = _chroma_client()
    collection = client.get_or_create_collection(name="variable_label")
    if collection.count() == 0:
        print(
            "The 'variable_label' collection is empty; "
            "run `document-gen labels embed` first."
        )
        return

    embedding = get_embed_backend().embed(texts=[query], options=EMBED_OPTIONS)[0]
    results = collection.query(query_embeddings=[embedding], n_results=n_results)
    print(results)


def output_tags() -> None:
    """Print the unique tags across all labels in ``labels.json``, comma-joined."""
    tags = set()
    for label in _load_labels():
        tags.update(label.get("tags") or [])
    print(",".join(sorted(tags)))


def main() -> None:
    """CLI entry point for the labels utilities."""
    parser = argparse.ArgumentParser(
        prog="document-gen-labels",
        description="Generate and embed company data labels.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("generate", help="Generate data labels into labels.json")
    subparsers.add_parser("embed", help="Embed labels.json into ChromaDB")

    query_parser = subparsers.add_parser("query", help="Query the label collection")
    query_parser.add_argument("query", help="Free-text query")
    query_parser.add_argument(
        "--n-results", type=int, default=5, help="Number of results (default: 5)"
    )

    subparsers.add_parser("tags", help="Print unique tags from labels.json")

    args = parser.parse_args()

    if args.command == "generate":
        generate_labels()
    elif args.command == "embed":
        embed_labels()
    elif args.command == "query":
        query_labels(args.query, n_results=args.n_results)
    elif args.command == "tags":
        output_tags()


if __name__ == "__main__":
    main()
