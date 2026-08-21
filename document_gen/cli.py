"""Document-gen CLI: web server, legacy migration, and document generation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from document_gen import document_query
from document_gen.models import FIGURE_KINDS, CompanyProfile, DistressOptions
from document_gen.document_pdf import generate_document_pdf
from document_gen.document_png import generate_document_image

#: Curated ``--distress-preset`` bundles (applied on top of the flag-derived
#: ``DistressOptions``; explicit ``--distress*`` flags win over preset values).
#: Each preset implies ``backend="augraphy"`` (the model default) unless
#: ``--distress-backend legacy`` is given explicitly.
_DISTRESS_PRESETS: dict[str, dict[str, object]] = {
    "scanned": {
        "dirty_screen": True,
        "moire": True,
        "jpeg_artifacts": True,
        "color_shift": True,
    },
    "office": {
        "paper_aging": True,
        "stains": True,
        "folding": True,
        "bindings": True,
        "markup": True,
        "scribbles": True,
        "shadow_cast": True,
    },
    "fax": {
        "faxify": True,
        "dithering": True,
        "low_ink_random_lines": True,
        "noise": True,
        "brightness": True,
    },
    "archival": {
        "paper_aging": True,
        "vignette": True,
        "stains": True,
        "ink_bleed": True,
        "letterpress": True,
        "ink_mottling": True,
        "bleed_through": True,
        "watermark": True,
    },
}


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the available subcommands."""
    parser = argparse.ArgumentParser(
        prog="document-gen",
        description="Generate synthetic companies and data-driven documents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser(
        "serve", help="Run the web UI server (requires the 'web' extra)"
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Import legacy companies.json flatfile records into the TinyDB store",
    )
    migrate_parser.add_argument(
        "--from",
        dest="source",
        type=Path,
        default=Path("data") / "companies.json",
        help="Legacy flatfile to import (default: ./data/companies.json)",
    )
    migrate_parser.add_argument(
        "--force",
        action="store_true",
        help="Import even when the target database already contains records",
    )

    document_parser = subparsers.add_parser(
        "document", help="Generate a PDF document for a stored company"
    )
    document_parser.add_argument(
        "--company-id",
        type=int,
        required=True,
        help="TinyDB doc_id of the company",
    )
    document_parser.add_argument(
        "--document",
        required=True,
        help="Document type name (case-insensitive) or 0-based index",
    )
    document_parser.add_argument(
        "--input",
        default=None,
        help="Optional free-text guidance for the document content",
    )
    document_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Output directory (default: the saved setting, then the "
            "DOCUMENTS_DIR env var)"
        ),
    )
    document_parser.add_argument(
        "--model",
        default=None,
        help="Model ID override for the configured chat backend",
    )
    document_parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Also save the generated markdown and HTML next to the PDF",
    )
    document_parser.add_argument(
        "--figure-kind",
        action="append",
        choices=list(FIGURE_KINDS),
        default=[],
        metavar="KIND",
        help=(
            "Allowed matplotlib figure kind (repeatable); no figures are "
            "included unless at least one kind is given"
        ),
    )

    image_parser = subparsers.add_parser(
        "image",
        help="Generate a single-page PNG image document for a stored company",
    )
    image_parser.add_argument(
        "--company-id",
        type=int,
        required=True,
        help="TinyDB doc_id of the company",
    )
    image_parser.add_argument(
        "--document",
        required=True,
        help="Document type name (case-insensitive) or 0-based index",
    )
    image_parser.add_argument(
        "--input",
        default=None,
        help="Optional free-text guidance for the document content",
    )
    image_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Output directory (default: the saved setting, then the "
            "DOCUMENTS_DIR env var)"
        ),
    )
    image_parser.add_argument(
        "--model",
        default=None,
        help="Model ID override for the configured chat backend",
    )
    image_parser.add_argument(
        "--figure-kind",
        action="append",
        choices=list(FIGURE_KINDS),
        default=[],
        metavar="KIND",
        help=(
            "Allowed matplotlib figure kind (repeatable); no figures are "
            "included unless at least one kind is given"
        ),
    )
    image_parser.add_argument(
        "--no-a4",
        action="store_true",
        help="Let the page size itself to the content instead of A4 portrait",
    )
    image_parser.add_argument(
        "--distress",
        action="store_true",
        help="Post-process the PNG to look like a scanned, aged document",
    )
    image_parser.add_argument(
        "--distress-preset",
        choices=sorted(_DISTRESS_PRESETS),
        default=None,
        help=(
            "Curated distress effect bundle (with --distress); explicit "
            "--distress* flags win over preset values"
        ),
    )
    image_parser.add_argument(
        "--distress-backend",
        choices=["augraphy", "legacy"],
        default=None,
        help=("Distress rendering engine (with --distress; default: augraphy)"),
    )
    image_parser.add_argument(
        "--no-stains",
        action="store_true",
        default=None,
        help="Disable the stain blobs (with --distress)",
    )
    image_parser.add_argument(
        "--no-vignette",
        action="store_true",
        default=None,
        help="Disable the dark-edge vignette (with --distress)",
    )
    image_parser.add_argument(
        "--no-noise",
        action="store_true",
        default=None,
        help="Disable the scanner grain (with --distress)",
    )
    image_parser.add_argument(
        "--no-ink-fade",
        action="store_true",
        default=None,
        help="Disable the faded-ink blend (with --distress)",
    )
    image_parser.add_argument(
        "--no-blur",
        action="store_true",
        default=None,
        help="Disable the scanner focus-loss blur (with --distress)",
    )
    image_parser.add_argument(
        "--warp",
        action="store_true",
        default=None,
        help="Enable the subtle feed/lens warp (with --distress)",
    )
    image_parser.add_argument(
        "--stain-count",
        type=int,
        default=None,
        help="Number of stain centers (default: 4, with --distress)",
    )
    image_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Seed for the distress pass (augraphy backend: drives the whole "
            "pipeline, including stain positions; legacy backend: stain "
            "positions are random every run; default: company seed)"
        ),
    )
    image_parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Also save the generated markdown and HTML next to the PNG",
    )

    return parser


def _run_serve(args: argparse.Namespace) -> None:
    """Handle the ``serve`` subcommand (web UI)."""
    try:
        import uvicorn

        from document_gen.server import app
    except ImportError as exc:
        raise SystemExit(
            "Web UI dependencies are missing. Install with: uv sync --extra web"
        ) from exc
    print(f"Web UI: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


def _run_migrate(args: argparse.Namespace) -> None:
    """Handle the ``migrate`` subcommand (legacy flatfile import)."""
    if not os.environ.get("TINYDB_PATH"):
        raise SystemExit(
            "TINYDB_PATH is not set: the company store is in-memory and "
            "migrated records would be lost on exit. Set TINYDB_PATH in .env "
            "to a file path first."
        )
    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")

    existing = document_query.count_companies()
    if existing and not args.force:
        raise SystemExit(
            f"Target database already contains {existing} record(s) "
            f"({document_query.db_path()}). Use --force to import anyway."
        )

    with open(args.source, encoding="utf-8") as filereader:
        entries = json.load(filereader)
    profiles = [CompanyProfile.model_validate(entry) for entry in entries]
    doc_ids = document_query.save_companies(profiles)
    print(
        f"Migrated {len(doc_ids)} company profile(s) from {args.source} "
        f"to {document_query.db_path()}"
    )


def _run_document(args: argparse.Namespace) -> None:
    """Handle the ``document`` subcommand."""
    try:
        artifact = generate_document_pdf(
            args.company_id,
            args.document,
            user_input=args.input,
            model_name=args.model,
            output_dir=args.output_dir,
            figure_kinds=args.figure_kind,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.keep_intermediates:
        artifact.pdf_path.with_suffix(".md").write_text(
            artifact.markdown, encoding="utf-8"
        )
        artifact.pdf_path.with_suffix(".html").write_text(
            artifact.html, encoding="utf-8"
        )
    print(f"Wrote {artifact.pdf_path}")


def _distress_options_from_args(args: argparse.Namespace) -> DistressOptions | None:
    """Build ``DistressOptions`` from the ``image`` subcommand flags.

    Returns ``None`` when ``--distress`` is not given. The preset bundle
    (if any) is applied on top of the flag defaults, and explicitly passed
    ``--distress*`` flags win over preset values.
    """
    if not args.distress:
        return None
    options = DistressOptions(enabled=True, seed=args.seed)
    if args.distress_preset:
        options = options.model_copy(update=_DISTRESS_PRESETS[args.distress_preset])
    if args.distress_backend is not None:
        options = options.model_copy(update={"backend": args.distress_backend})
    if args.no_stains is not None:
        options.stains = not args.no_stains
    if args.no_vignette is not None:
        options.vignette = not args.no_vignette
    if args.no_noise is not None:
        options.noise = not args.no_noise
    if args.no_ink_fade is not None:
        options.ink_fade = not args.no_ink_fade
    if args.no_blur is not None:
        options.blur = not args.no_blur
    if args.warp is not None:
        options.warp = args.warp
    if args.stain_count is not None:
        options.stain_count = args.stain_count
    return options


def _run_image(args: argparse.Namespace) -> None:
    """Handle the ``image`` subcommand."""
    distress = _distress_options_from_args(args)
    try:
        artifact = generate_document_image(
            args.company_id,
            args.document,
            user_input=args.input,
            model_name=args.model,
            output_dir=args.output_dir,
            figure_kinds=args.figure_kind,
            a4_aspect=not args.no_a4,
            distress=distress,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.keep_intermediates:
        artifact.png_path.with_suffix(".md").write_text(
            artifact.markdown, encoding="utf-8"
        )
        artifact.png_path.with_suffix(".html").write_text(
            artifact.html, encoding="utf-8"
        )
    print(f"Wrote {artifact.png_path}")


def main() -> None:
    """CLI entry point."""
    # Load .env at entry time (not import time) so TINYDB_PATH and the LLM
    # settings env vars are available to every subcommand.
    load_dotenv()
    args = _build_parser().parse_args()

    if args.command == "serve":
        _run_serve(args)
    elif args.command == "migrate":
        _run_migrate(args)
    elif args.command == "document":
        _run_document(args)
    elif args.command == "image":
        _run_image(args)


if __name__ == "__main__":
    main()
