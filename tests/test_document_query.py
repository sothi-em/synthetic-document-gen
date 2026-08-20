"""Tests for the TinyDB storage helper in ``document_gen.document_query``.

Each test runs against a throwaway TinyDB file in ``tmp_path``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from document_gen import document_query
from document_gen.models import CompanyProfile, DocumentType, SyntheticCompany


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the helper at a temp TinyDB file and reset the cached handle."""
    monkeypatch.setenv("TINYDB_PATH", str(tmp_path / "companies.db"))
    document_query.reset_db()
    yield
    document_query.reset_db()


def _make_profile(name: str, industry: str) -> CompanyProfile:
    return CompanyProfile(
        profile={
            "name": name,
            "industry": industry,
            "description": f"Description of {name}",
            "headquarters": "Springfield",
            "size": "mid",
        },
        reports=[
            {"name": "Onboarding Guide", "category": "Guide", "purpose": "Orientation"}
        ],
        seed=42,
    )


class TestConnection:
    def test_db_file_created_and_reset_closes_handle(self, tmp_path) -> None:
        document_query.get_db()
        assert (tmp_path / "companies.db").exists()
        assert document_query.db_path() == str(tmp_path / "companies.db")
        db = document_query.get_db()
        document_query.reset_db()
        assert document_query.get_db() is not db


class TestInMemoryDefault:
    """Without ``TINYDB_PATH`` the helper uses an in-memory database."""

    @pytest.fixture(autouse=True)
    def memory_db(self, monkeypatch):
        monkeypatch.delenv("TINYDB_PATH", raising=False)
        document_query.reset_db()
        yield
        document_query.reset_db()

    def test_memory_location_and_no_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert document_query.db_path() == document_query.MEMORY_DB_PATH
        document_query.save_company(_make_profile("Acme Corp", "Retail"))
        assert list(tmp_path.iterdir()) == []

    def test_roundtrip_and_reset(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        doc = document_query.get_company(doc_id)
        assert doc is not None
        assert doc["profile"]["name"] == "Acme Corp"
        assert document_query.count_companies() == 1
        # In-memory data is lost on reset.
        document_query.reset_db()
        assert document_query.count_companies() == 0


class TestSave:
    def test_save_companies_roundtrip(self) -> None:
        profiles = [_make_profile(f"Corp {i}", "Retail") for i in range(3)]
        doc_ids = document_query.save_companies(profiles)
        assert len(doc_ids) == 3
        assert len(set(doc_ids)) == 3
        assert document_query.count_companies() == 3
        for profile, doc_id in zip(profiles, doc_ids, strict=True):
            assert isinstance(doc_id, int)
            doc = document_query.get_company(doc_id)
            assert doc is not None
            assert doc["id"] == doc_id
            assert doc["profile"]["name"] == profile.profile.name
            assert doc["profile"]["industry"] == "Retail"
            assert doc["seed"] == 42
            assert doc["reports"][0]["name"] == "Onboarding Guide"
            assert "created_at" in doc

    def test_save_profile_without_company_data(self) -> None:
        doc_id = document_query.save_company(CompanyProfile(seed=7))
        doc = document_query.get_company(doc_id)
        assert doc is not None
        assert doc["profile"] is None
        assert doc["reports"] == []

    def test_save_company_stores_user_input(self) -> None:
        profile = _make_profile("Acme Corp", "Retail")
        profile.user_input = "A mid-size retail chain in the Midwest"
        doc_id = document_query.save_company(profile)
        doc = document_query.get_company(doc_id)
        assert doc is not None
        assert doc["user_input"] == "A mid-size retail chain in the Midwest"
        # No user context -> stored as None.
        other = document_query.save_company(_make_profile("Beta Inc", "Energy"))
        assert document_query.get_company(other)["user_input"] is None


class TestRead:
    def test_list_summaries_filters_and_search(self) -> None:
        document_query.save_companies(
            [
                _make_profile("Acme Corp", "Retail"),
                _make_profile("Beta Inc", "Energy"),
                # Profiles without company data are skipped in listings.
                CompanyProfile(seed=7),
            ]
        )
        items = document_query.list_companies()
        assert len(items) == 2
        first = items[0]
        assert set(first) == {
            "id",
            "name",
            "industry",
            "headquarters",
            "size",
            "num_reports",
        }
        assert first["name"] == "Acme Corp"
        assert first["num_reports"] == 1
        # Industry filter.
        assert [
            i["name"] for i in document_query.list_companies(industry="Energy")
        ] == ["Beta Inc"]
        # Case-insensitive search.
        assert [i["name"] for i in document_query.list_companies(search="acme")] == [
            "Acme Corp"
        ]

    def test_get_company_missing_returns_none(self) -> None:
        assert document_query.get_company(999999) is None


class TestDelete:
    def test_delete(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        assert document_query.get_document_types(doc_id)
        assert document_query.delete_company(doc_id) is True
        assert document_query.get_company(doc_id) is None
        assert document_query.count_companies() == 0
        # Report types cascade with the company.
        assert document_query.get_document_types(doc_id) == []
        # Deleting an unknown id is a no-op.
        assert document_query.delete_company(999999) is False


class TestUserSettings:
    def test_set_get_overwrite_and_list(self) -> None:
        document_query.set_setting("llm", {"a": 1})
        document_query.set_setting("llm", {"b": 2})
        document_query.set_setting("dashboard", {"theme": "dark"})
        assert document_query.get_setting("llm") == {"b": 2}
        entries = {entry["key"]: entry for entry in document_query.list_settings()}
        assert set(entries) == {"llm", "dashboard"}
        assert entries["dashboard"]["value"] == {"theme": "dark"}
        assert "updated_at" in entries["llm"]

    def test_get_missing_returns_none(self) -> None:
        assert document_query.get_setting("nope") is None

    def test_delete(self) -> None:
        document_query.set_setting("llm", {"a": 1})
        assert document_query.delete_setting("llm") is True
        assert document_query.get_setting("llm") is None
        assert document_query.delete_setting("nope") is False


class TestDocumentTypes:
    def test_save_and_get(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        reports = document_query.get_document_types(doc_id)
        assert len(reports) == 1
        assert reports[0]["company_id"] == doc_id
        assert reports[0]["name"] == "Onboarding Guide"
        assert "id" in reports[0]

    def test_append_replace_and_empty(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        # Append keeps existing reports.
        append_ids = document_query.append_document_types(
            doc_id, [DocumentType(name="A", category="C1", purpose="P1")]
        )
        assert len(append_ids) == 1
        assert [r["name"] for r in document_query.get_document_types(doc_id)] == [
            "Onboarding Guide",
            "A",
        ]
        # Replace-all swaps the full list.
        replace_ids = document_query.save_document_types(
            doc_id,
            [
                DocumentType(name="B", category="C2", purpose="P2"),
                DocumentType(name="C", category="C3", purpose="P3"),
            ],
        )
        assert len(replace_ids) == 2
        assert [r["name"] for r in document_query.get_document_types(doc_id)] == [
            "B",
            "C",
        ]
        # An empty list writes nothing and clears the reports.
        document_query.save_document_types(doc_id, [])
        assert document_query.get_document_types(doc_id) == []

    def test_per_company_isolation(self) -> None:
        first = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        second = document_query.save_company(_make_profile("Beta Inc", "Energy"))
        document_query.save_document_types(
            second, [DocumentType(name="X", category="C", purpose="P")]
        )
        assert [r["name"] for r in document_query.get_document_types(first)] == [
            "Onboarding Guide"
        ]
        assert [r["name"] for r in document_query.get_document_types(second)] == ["X"]

    def test_delete(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        assert document_query.delete_document_types(doc_id) is True
        assert document_query.get_document_types(doc_id) == []
        assert document_query.delete_document_types(999999) is False

    def test_delete_single(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        type_id = document_query.get_document_types(doc_id)[0]["id"]
        assert document_query.delete_document_type(doc_id, type_id) is True
        assert document_query.get_document_types(doc_id) == []
        # Unknown id: nothing deleted.
        assert document_query.delete_document_type(doc_id, 999999) is False
        # A type owned by another company is not touched.
        other = document_query.save_company(_make_profile("Beta Inc", "Energy"))
        other_type = document_query.append_document_types(
            other, [DocumentType(name="X", category="C", purpose="P")]
        )[0]
        assert document_query.delete_document_type(doc_id, other_type) is False
        assert [r["name"] for r in document_query.get_document_types(other)] == [
            "Onboarding Guide",
            "X",
        ]


class TestUpdateDocumentType:
    def test_update_fields_and_missing(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        (type_doc,) = document_query.get_document_types(doc_id)
        updated = document_query.update_document_type(
            doc_id,
            type_doc["id"],
            DocumentType(name="New Name", category="Report", purpose="New purpose"),
        )
        assert updated is not None
        assert updated["name"] == "New Name"
        assert updated["category"] == "Report"
        assert updated["purpose"] == "New purpose"
        # Unknown type id is a no-op.
        assert (
            document_query.update_document_type(
                doc_id,
                999999,
                DocumentType(name="X", category="Y", purpose="Z"),
            )
            is None
        )

    def test_update_preserves_stored_user_input(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        (type_doc,) = document_query.get_document_types(doc_id)
        type_id = type_doc["id"]
        # Seed a generation-time user input on the stored record.
        document_query.update_document_type(
            doc_id,
            type_id,
            DocumentType(
                name="Onboarding Guide",
                category="Guide",
                purpose="Orientation",
                user_input="Guides for new hires",
            ),
        )
        stored = document_query.get_document_types(doc_id)[0]
        assert stored["user_input"] == "Guides for new hires"
        # A plain edit (no user_input) must not wipe it.
        updated = document_query.update_document_type(
            doc_id,
            type_id,
            DocumentType(name="Renamed Guide", category="Guide", purpose="Orientation"),
        )
        assert updated["name"] == "Renamed Guide"
        assert updated["user_input"] == "Guides for new hires"


class TestUpdateCompany:
    def test_update_profile(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        updated = document_query.update_company(
            doc_id,
            SyntheticCompany(
                name="Acme Corporation",
                industry="Energy",
                description="Updated",
                headquarters="Austin",
                size="large",
            ),
        )
        assert updated is not None
        assert updated["profile"]["name"] == "Acme Corporation"
        assert updated["profile"]["industry"] == "Energy"
        # Unknown company id is a no-op.
        assert (
            document_query.update_company(
                999999,
                SyntheticCompany(
                    name="X",
                    industry="Y",
                    description="Z",
                    headquarters="H",
                    size="small",
                ),
            )
            is None
        )

    def test_update_preserves_user_input(self) -> None:
        profile = _make_profile("Acme Corp", "Retail")
        profile.user_input = "Requested industry: Retail"
        doc_id = document_query.save_company(profile)
        updated = document_query.update_company(
            doc_id,
            SyntheticCompany(
                name="Acme Corporation",
                industry="Retail",
                description="Updated",
                headquarters="Austin",
                size="mid",
            ),
        )
        assert updated is not None
        assert updated["profile"]["name"] == "Acme Corporation"
        assert updated["user_input"] == "Requested industry: Retail"


class TestReportDocuments:
    def _company_with_report(self, name: str = "Acme Corp") -> tuple[int, int]:
        company_id = document_query.save_company(_make_profile(name, "Retail"))
        report_id = document_query.get_document_types(company_id)[0]["id"]
        return company_id, report_id

    @pytest.mark.parametrize("filename", ["acme_guide.pdf", "quarterly_data.csv"])
    def test_save_get_and_list_roundtrip(self, tmp_path, filename: str) -> None:
        company_id, report_id = self._company_with_report()
        file_path = tmp_path / filename
        # Recording is filetype-agnostic: the extension drives the type.
        if filename.endswith(".csv"):
            file_path.write_text("a,b\n1,2\n", encoding="utf-8")
        else:
            file_path.write_bytes(b"x" * 2048)

        doc_id = document_query.save_document(company_id, report_id, file_path)
        assert isinstance(doc_id, int)

        record = document_query.get_document(doc_id)
        assert record is not None
        assert record["id"] == doc_id
        # gen_tracing is omitted unless provided.
        assert "gen_tracing" not in record
        assert document_query.get_document(999999) is None

        item = document_query.list_documents()[0]
        assert item["id"] == doc_id
        assert item["company_id"] == company_id
        assert item["document_type_id"] == report_id
        assert item["filename"] == filename
        assert item["filetype"] == filename.rsplit(".", 1)[1]
        assert item["filepath"] == str(file_path.resolve())
        assert item["size_kb"] > 0
        assert item["company_name"] == "Acme Corp"
        assert item["report_name"] == "Onboarding Guide"
        assert "created_at" in item

    def test_save_with_gen_tracing(self, tmp_path) -> None:
        company_id, report_id = self._company_with_report()
        file_path = tmp_path / "acme_guide.pdf"
        file_path.write_bytes(b"x")
        trace = {"stages": {"markdown": {"output": "# T"}}, "total_elapsed_s": 1.2}

        doc_id = document_query.save_document(
            company_id, report_id, file_path, gen_tracing=trace
        )

        record = document_query.get_document(doc_id)
        assert record is not None
        assert record["gen_tracing"] == trace
        # Also visible through the list endpoint (plain-dict copy).
        listed = document_query.list_documents()[0]
        assert listed["gen_tracing"] == trace

    def test_list_filters_and_order(self, tmp_path) -> None:
        first, first_report = self._company_with_report("Acme Corp")
        second, second_report = self._company_with_report("Beta Inc")
        first_file = tmp_path / "first.pdf"
        first_file.write_bytes(b"x")
        second_file = tmp_path / "second.pdf"
        second_file.write_bytes(b"y")
        document_query.save_document(first, first_report, first_file)
        document_query.save_document(second, second_report, second_file)

        # Newest first.
        assert [i["filename"] for i in document_query.list_documents()] == [
            "second.pdf",
            "first.pdf",
        ]
        by_company = document_query.list_documents(company_id=second)
        assert [item["company_id"] for item in by_company] == [second]
        by_report = document_query.list_documents(document_type_id=first_report)
        assert [item["document_type_id"] for item in by_report] == [first_report]

    def test_get_document_type_id(self) -> None:
        company_id, report_id = self._company_with_report()
        assert (
            document_query.get_document_type_id(company_id, "Onboarding Guide")
            == report_id
        )
        assert (
            document_query.get_document_type_id(company_id, " onboarding guide ")
            == report_id
        )
        assert document_query.get_document_type_id(company_id, "0") == report_id
        assert document_query.get_document_type_id(company_id, "nope") is None
        assert document_query.get_document_type_id(company_id, "5") is None

    def test_report_types_expose_documents(self, tmp_path) -> None:
        company_id, report_id = self._company_with_report()
        # Before any document is recorded.
        reports = document_query.get_document_types(company_id)
        assert reports[0]["num_documents"] == 0
        assert reports[0]["documents"] == []

        file_path = tmp_path / "acme_guide.pdf"
        file_path.write_bytes(b"x" * 1024)
        doc_id = document_query.save_document(company_id, report_id, file_path)

        reports = document_query.get_document_types(company_id)
        assert reports[0]["num_documents"] == 1
        assert reports[0]["documents"] == [
            {"id": doc_id, "filename": "acme_guide.pdf", "filetype": "pdf"}
        ]

        company = document_query.get_company(company_id)
        assert company is not None
        assert company["reports"][0]["num_documents"] == 1

    def test_delete_documents(self, tmp_path) -> None:
        first, first_report = self._company_with_report("Acme Corp")
        second, second_report = self._company_with_report("Beta Inc")
        file_path = tmp_path / "doc.pdf"
        file_path.write_bytes(b"x")
        first_doc = document_query.save_document(first, first_report, file_path)
        document_query.save_document(second, second_report, file_path)

        # Single delete; deleting an unknown id is a no-op.
        assert document_query.delete_document(first_doc) is True
        assert document_query.get_document(first_doc) is None
        assert document_query.delete_document(first_doc) is False
        assert [item["company_id"] for item in document_query.list_documents()] == [
            second
        ]
        # Delete by report type.
        assert document_query.delete_documents(document_type_ids=[second_report]) == 1
        assert document_query.list_documents() == []
        # Delete by company.
        document_query.save_document(first, first_report, file_path)
        assert document_query.delete_documents(company_id=first) == 1
        assert document_query.list_documents() == []

    def test_rename_document(self, tmp_path) -> None:
        company_id, report_id = self._company_with_report()
        file_path = tmp_path / "acme_guide.pdf"
        file_path.write_bytes(b"x")
        doc_id = document_query.save_document(company_id, report_id, file_path)

        # Renames the record and the file on disk, keeping the extension.
        updated = document_query.rename_document(doc_id, "annual report")
        assert updated is not None
        assert updated["filename"] == "annual report.pdf"
        assert Path(updated["filepath"]) == (tmp_path / "annual report.pdf").resolve()
        assert not file_path.exists()
        record = document_query.get_document(doc_id)
        assert record is not None
        assert record["filename"] == "annual report.pdf"
        # The nested document listing reflects the new name.
        reports = document_query.get_document_types(company_id)
        assert reports[0]["documents"][0]["filename"] == "annual report.pdf"

        # Renaming to the current name is a no-op (no duplicate error).
        assert (
            document_query.rename_document(doc_id, "annual report")["filename"]
            == "annual report.pdf"
        )

        # A colliding target name is rejected.
        (tmp_path / "other.pdf").write_bytes(b"y")
        other_doc = document_query.save_document(
            company_id, report_id, tmp_path / "other.pdf"
        )
        with pytest.raises(FileExistsError):
            document_query.rename_document(other_doc, "annual report")

        # Unknown id is a no-op; bad names are rejected.
        assert document_query.rename_document(999999, "x") is None
        for bad in ("", "  ", "a/b", "a\\b", ".", ".."):
            with pytest.raises(ValueError):
                document_query.rename_document(doc_id, bad)

    def test_cascades(self, tmp_path) -> None:
        file_path = tmp_path / "doc.pdf"
        file_path.write_bytes(b"x")
        # Deleting the company cascades to its documents.
        company_id, report_id = self._company_with_report()
        document_query.save_document(company_id, report_id, file_path)
        assert document_query.delete_company(company_id) is True
        assert document_query.list_documents() == []
        # Replacing the report types cascades.
        company_id, report_id = self._company_with_report("Beta Inc")
        document_query.save_document(company_id, report_id, file_path)
        document_query.save_document_types(
            company_id, [DocumentType(name="New", category="C", purpose="P")]
        )
        assert document_query.list_documents() == []
        # Deleting the report types cascades.
        company_id, report_id = self._company_with_report("Gamma Inc")
        document_query.save_document(company_id, report_id, file_path)
        assert document_query.delete_document_types(company_id) is True
        assert document_query.list_documents() == []
        # Deleting a single report type cascades to its documents.
        company_id, report_id = self._company_with_report("Delta Inc")
        document_query.save_document(company_id, report_id, file_path)
        assert document_query.delete_document_type(company_id, report_id) is True
        assert document_query.list_documents() == []


class TestLegacyMigration:
    """Company docs written before denormalization embed their reports."""

    def _insert_legacy_company(self, name: str) -> int:
        doc = {
            "profile": {
                "name": name,
                "industry": "Retail",
                "description": f"Description of {name}",
                "headquarters": "Springfield",
                "size": "mid",
            },
            "reports": [
                {
                    "name": "Onboarding Guide",
                    "category": "Guide",
                    "purpose": "Orientation",
                },
                {
                    "name": "Operations Report",
                    "category": "Guide",
                    "purpose": "Operational",
                },
            ],
            "seed": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        return document_query.get_db().insert(doc)

    def test_embedded_reports_moved_to_collection(self) -> None:
        doc_id = self._insert_legacy_company("Legacy Corp")
        document_query.reset_db()  # re-open to trigger the migration
        document_query.reset_db()  # second open must not duplicate

        raw = document_query.get_db().get(doc_id=doc_id)
        assert raw["reports"] == []

        reports = document_query.get_document_types(doc_id)
        assert [report["name"] for report in reports] == [
            "Onboarding Guide",
            "Operations Report",
        ]
        assert all(report["company_id"] == doc_id for report in reports)

        # The joined company doc and summary counts see the migrated reports.
        assert len(document_query.get_company(doc_id)["reports"]) == 2
        assert document_query.list_companies()[0]["num_reports"] == 2

    def test_new_layout_is_untouched(self) -> None:
        doc_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        document_query.reset_db()
        assert [r["name"] for r in document_query.get_document_types(doc_id)] == [
            "Onboarding Guide"
        ]


class TestLegacyCollectionMigration:
    """Databases using the pre-rename collection names are migrated on open."""

    def _insert_legacy_db(self) -> int:
        company_id = document_query.save_company(_make_profile("Acme Corp", "Retail"))
        db = document_query.get_db()
        type_id = db.table("report_types").insert(
            {
                "company_id": company_id,
                "name": "Legacy Report",
                "category": "Guide",
                "purpose": "Pre-rename type",
            }
        )
        db.table("report_documents").insert(
            {"report_type_id": type_id, "filename": "legacy.pdf", "filetype": "pdf"}
        )
        db.table("user_settings").insert(
            {"key": "reports", "value": {"quick_doc": True}}
        )
        return company_id

    def test_collections_and_settings_key_migrated(self) -> None:
        company_id = self._insert_legacy_db()
        document_query.reset_db()  # re-open to trigger the migration
        document_query.reset_db()  # second open must not duplicate

        db = document_query.get_db()
        assert len(db.table("report_types").all()) == 0
        assert len(db.table("report_documents").all()) == 0

        types = document_query.get_document_types(company_id)
        assert [t["name"] for t in types] == ["Onboarding Guide", "Legacy Report"]
        legacy_type = next(t for t in types if t["name"] == "Legacy Report")

        docs = document_query.list_documents()
        assert len(docs) == 1
        assert docs[0]["filename"] == "legacy.pdf"
        assert docs[0]["document_type_id"] == legacy_type["id"]

        assert document_query.get_setting("documents") == {"quick_doc": True}


class TestConcurrency:
    def test_concurrent_saves_do_not_corrupt(self) -> None:
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(5):
                    document_query.save_company(
                        _make_profile(f"Corp {n}-{i}", "Retail")
                    )
            except Exception as exc:  # pragma: no cover - surfaced via assert
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert document_query.count_companies() == 20
