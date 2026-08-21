"""Tests for the CLI in ``document_gen.cli`` (parsing + migrate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from document_gen import document_query
from document_gen.cli import (
    _DISTRESS_PRESETS,
    _build_parser,
    _distress_options_from_args,
    _run_migrate,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestCliParser:
    def test_serve(self) -> None:
        args = _build_parser().parse_args(["serve"])
        assert args.command == "serve"
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        args = _build_parser().parse_args(
            ["serve", "--host", "0.0.0.0", "--port", "9000"]
        )
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_document_subcommand(self) -> None:
        args = _build_parser().parse_args(
            [
                "document",
                "--company-id",
                "1",
                "--document",
                "Onboarding Guide",
                "--output-dir",
                "docs",
            ]
        )
        assert args.command == "document"
        assert args.company_id == 1
        assert args.document == "Onboarding Guide"
        assert args.output_dir == Path("docs")

    def test_image_subcommand_defaults(self) -> None:
        args = _build_parser().parse_args(
            ["image", "--company-id", "2", "--document", "Invoice"]
        )
        assert args.command == "image"
        assert args.company_id == 2
        assert args.document == "Invoice"
        assert args.input is None
        assert args.output_dir is None
        assert args.model is None
        assert args.figure_kind == []
        assert args.no_a4 is False
        assert args.distress is False
        assert args.distress_preset is None
        assert args.distress_backend is None
        assert args.no_stains is None
        assert args.no_vignette is None
        assert args.no_noise is None
        assert args.no_ink_fade is None
        assert args.no_blur is None
        assert args.warp is None
        assert args.stain_count is None
        assert args.seed is None
        assert args.keep_intermediates is False

    def test_image_subcommand_flags(self) -> None:
        args = _build_parser().parse_args(
            [
                "image",
                "--company-id",
                "3",
                "--document",
                "Memo",
                "--input",
                "make it terse",
                "--output-dir",
                "imgs",
                "--model",
                "llama3",
                "--figure-kind",
                "bar",
                "--figure-kind",
                "line",
                "--no-a4",
                "--distress",
                "--no-stains",
                "--no-vignette",
                "--no-noise",
                "--no-ink-fade",
                "--no-blur",
                "--warp",
                "--stain-count",
                "7",
                "--seed",
                "42",
                "--keep-intermediates",
            ]
        )
        assert args.command == "image"
        assert args.company_id == 3
        assert args.document == "Memo"
        assert args.input == "make it terse"
        assert args.output_dir == Path("imgs")
        assert args.model == "llama3"
        assert args.figure_kind == ["bar", "line"]
        assert args.no_a4 is True
        assert args.distress is True
        assert args.no_stains is True
        assert args.no_vignette is True
        assert args.no_noise is True
        assert args.no_ink_fade is True
        assert args.no_blur is True
        assert args.warp is True
        assert args.stain_count == 7
        assert args.seed == 42
        assert args.keep_intermediates is True


class TestDistressOptionsFromArgs:
    def _args(self, *flags: str):
        return _build_parser().parse_args(
            ["image", "--company-id", "1", "--document", "Memo", *flags]
        )

    def test_no_distress_flag(self) -> None:
        assert _distress_options_from_args(self._args()) is None

    def test_distress_defaults(self) -> None:
        options = _distress_options_from_args(self._args("--distress"))
        assert options is not None
        assert options.enabled is True
        assert options.backend == "augraphy"
        assert options.paper_aging is True
        assert options.vignette is True
        assert options.stains is True
        assert options.stain_count == 4
        assert options.noise is True
        assert options.ink_fade is True
        assert options.blur is True
        assert options.warp is False
        assert options.seed is None

    @pytest.mark.parametrize("preset", sorted(_DISTRESS_PRESETS))
    def test_presets(self, preset: str) -> None:
        options = _distress_options_from_args(
            self._args("--distress", "--distress-preset", preset)
        )
        assert options is not None
        assert options.backend == "augraphy"
        for field, value in _DISTRESS_PRESETS[preset].items():
            assert getattr(options, field) is value, field

    def test_explicit_flags_win_over_preset(self) -> None:
        options = _distress_options_from_args(
            self._args(
                "--distress",
                "--distress-preset",
                "office",
                "--no-stains",
                "--no-vignette",
                "--no-noise",
                "--no-ink-fade",
                "--no-blur",
                "--warp",
                "--stain-count",
                "9",
                "--seed",
                "7",
            )
        )
        assert options is not None
        assert options.stains is False
        assert options.vignette is False
        assert options.noise is False
        assert options.ink_fade is False
        assert options.blur is False
        assert options.warp is True
        assert options.stain_count == 9
        assert options.seed == 7
        # preset values still apply where no flag was given
        assert options.folding is True
        assert options.bindings is True
        assert options.markup is True
        assert options.scribbles is True
        assert options.shadow_cast is True

    def test_distress_backend_legacy(self) -> None:
        options = _distress_options_from_args(
            self._args("--distress", "--distress-backend", "legacy")
        )
        assert options is not None
        assert options.backend == "legacy"

    def test_distress_backend_wins_over_preset(self) -> None:
        options = _distress_options_from_args(
            self._args(
                "--distress",
                "--distress-preset",
                "fax",
                "--distress-backend",
                "legacy",
            )
        )
        assert options is not None
        assert options.backend == "legacy"
        assert options.faxify is True

    def test_rejects_bad_preset(self) -> None:
        with pytest.raises(SystemExit):
            self._args("--distress", "--distress-preset", "bogus")

    def test_rejects_bad_backend(self) -> None:
        with pytest.raises(SystemExit):
            self._args("--distress", "--distress-backend", "bogus")

    def test_image_subcommand_rejects_bad_figure_kind(self) -> None:
        with pytest.raises(SystemExit):
            _build_parser().parse_args(
                [
                    "image",
                    "--company-id",
                    "1",
                    "--document",
                    "Memo",
                    "--figure-kind",
                    "sparkles",
                ]
            )

    def test_migrate(self) -> None:
        args = _build_parser().parse_args(["migrate"])
        assert args.command == "migrate"
        assert args.source == Path("data") / "companies.json"
        assert args.force is False
        args = _build_parser().parse_args(
            ["migrate", "--from", "legacy.json", "--force"]
        )
        assert args.source == Path("legacy.json")
        assert args.force is True

    def test_no_command_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _build_parser().parse_args([])


class TestMigrate:
    @pytest.fixture(autouse=True)
    def isolated_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYDB_PATH", str(tmp_path / "companies.db"))
        document_query.reset_db()
        yield
        document_query.reset_db()

    def _args(self, source: Path, force: bool = False):
        return _build_parser().parse_args(
            ["migrate", "--from", str(source), *(["--force"] if force else [])]
        )

    def test_migrate_imports_flatfile(self) -> None:
        _run_migrate(self._args(FIXTURES_DIR / "companies.json"))
        with open(FIXTURES_DIR / "companies.json", encoding="utf-8") as filereader:
            expected = len(json.load(filereader))
        assert document_query.count_companies() == expected
        assert len(document_query.list_companies()) == expected

    def test_migrate_missing_source_exits(self, tmp_path) -> None:
        with pytest.raises(SystemExit):
            _run_migrate(self._args(tmp_path / "nope.json"))

    def test_migrate_requires_persistent_path(self, monkeypatch) -> None:
        monkeypatch.delenv("TINYDB_PATH", raising=False)
        document_query.reset_db()
        with pytest.raises(SystemExit):
            _run_migrate(self._args(FIXTURES_DIR / "companies.json"))

    def test_migrate_refuses_nonempty_target(self) -> None:
        _run_migrate(self._args(FIXTURES_DIR / "companies.json"))
        with pytest.raises(SystemExit):
            _run_migrate(self._args(FIXTURES_DIR / "companies.json"))

    def test_migrate_force_appends(self) -> None:
        _run_migrate(self._args(FIXTURES_DIR / "companies.json"))
        _run_migrate(self._args(FIXTURES_DIR / "companies.json", force=True))
        with open(FIXTURES_DIR / "companies.json", encoding="utf-8") as filereader:
            expected = len(json.load(filereader))
        assert document_query.count_companies() == expected * 2
