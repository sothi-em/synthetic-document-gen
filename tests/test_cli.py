"""Tests for the CLI in ``document_gen.cli`` (parsing + migrate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from document_gen import document_query
from document_gen.cli import _build_parser, _run_migrate

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
