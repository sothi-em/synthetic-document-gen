"""Tests for the FastAPI web server in ``document_gen.server``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from document_gen import document_query, llm, server  # noqa: E402
from document_gen.models import CompanyProfile, DocumentType  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeBackend:
    """Stub LLM backend for server-route tests."""

    def __init__(self, models: list[str] | None = None, error: Exception | None = None):
        self._models = models or []
        self._error = error
        self.list_timeouts: list[float | None] = []

    def list_models(self, timeout: float | None = None) -> list[str]:
        self.list_timeouts.append(timeout)
        if self._error:
            raise self._error
        return self._models

    def embed(self, texts, model=None, options=None):
        if self._error:
            raise self._error
        return [[0.0, 0.1] for _ in texts]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.app)


@pytest.fixture()
def sample_profile() -> CompanyProfile:
    with open(FIXTURES_DIR / "companies.json", encoding="utf-8") as filereader:
        return CompanyProfile.model_validate(json.load(filereader)[0])


@pytest.fixture()
def company_db(tmp_path, monkeypatch):
    """Point the server at a temp TinyDB preloaded with the fixture companies."""
    monkeypatch.setenv("TINYDB_PATH", str(tmp_path / "companies.db"))
    document_query.reset_db()
    with open(FIXTURES_DIR / "companies.json", encoding="utf-8") as filereader:
        profiles = [
            CompanyProfile.model_validate(entry) for entry in json.load(filereader)
        ]
    doc_ids = document_query.save_companies(profiles)
    yield doc_ids
    document_query.reset_db()


class TestHealthAndMeta:
    @pytest.mark.parametrize("up", [True, False])
    def test_health_and_models(self, client, monkeypatch, clean_settings, up) -> None:
        if up:
            chat = FakeBackend(["chat-model"])
            embed = FakeBackend(["embed-model"])
        else:
            chat = FakeBackend(error=ConnectionError("no server"))
            embed = chat
        monkeypatch.setattr(llm, "get_chat_backend", lambda: chat)
        monkeypatch.setattr(llm, "get_embed_backend", lambda: embed)
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        status = "up" if up else "down"
        assert body["chat"]["status"] == status
        assert body["embed"]["status"] == status
        assert body["chat"]["backend"] == "ollama"
        # /api/models follows the purpose parameter; empty when down.
        assert client.get("/api/models").json() == (["chat-model"] if up else [])
        if up:
            assert client.get("/api/models", params={"purpose": "embed"}).json() == [
                "embed-model"
            ]

    def test_health_uses_probe_timeout(
        self, client, monkeypatch, clean_settings
    ) -> None:
        """Health/model probes must not wait out the 120 s client timeout."""
        up = FakeBackend(models=["model-a:latest"])
        monkeypatch.setattr(llm, "get_chat_backend", lambda: up)
        monkeypatch.setattr(llm, "get_embed_backend", lambda: up)
        client.get("/api/health")
        client.get("/api/models")
        assert up.list_timeouts
        assert all(timeout == server.PROBE_TIMEOUT for timeout in up.list_timeouts)

    def test_industries(self, client) -> None:
        industries = client.get("/api/industries").json()
        assert "Biotechnology" in industries
        assert len(industries) > 10

    def test_app_version_matches_package(self, client) -> None:
        import importlib.metadata

        assert server.app.version == importlib.metadata.version("document-gen")


class TestLLMSettings:
    def test_get_defaults(self, client, clean_settings) -> None:
        body = client.get("/api/settings").json()
        assert body["chat"] == {
            "backend": "ollama",
            "host": None,
            "model": None,
            "api_key": None,
            "has_api_key": False,
        }
        assert body["embed"]["backend"] == "ollama"

    def test_put_get_delete_roundtrip(self, client, clean_settings) -> None:
        payload = {
            "chat": {
                "backend": "openai",
                "host": "http://localhost:8080/v1",
                "api_key": "secret-key",
                "model": "qwen2.5-7b",
            },
            "embed": {
                "backend": "ollama",
                "host": "http://localhost:11434",
                "model": "nomic",
            },
        }
        response = client.put("/api/settings", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["chat"]["api_key"] == "****"
        assert body["chat"]["has_api_key"] is True
        assert body["chat"]["backend"] == "openai"

        # The stored settings keep the real key; GET masks it.
        stored = document_query.get_setting("llm")
        assert stored["chat"]["api_key"] == "secret-key"
        assert client.get("/api/settings").json()["chat"]["api_key"] == "****"

        # DELETE falls back to the defaults.
        response = client.delete("/api/settings")
        assert response.status_code == 200
        assert response.json()["chat"]["model"] is None
        assert document_query.get_setting("llm") is None

    def test_put_masked_key_keeps_stored_value(self, client, clean_settings) -> None:
        client.put(
            "/api/settings",
            json={"chat": {"backend": "openai", "api_key": "real-key"}, "embed": {}},
        )
        client.put(
            "/api/settings",
            json={"chat": {"backend": "openai", "api_key": "****"}, "embed": {}},
        )
        stored = document_query.get_setting("llm")
        assert stored["chat"]["api_key"] == "real-key"

    @pytest.mark.parametrize("case", ["success", "embed", "failure"])
    def test_test_endpoint(self, client, monkeypatch, clean_settings, case) -> None:
        if case == "success":
            monkeypatch.setattr(
                llm, "build_backend", lambda config: FakeBackend(["m1", "m2"])
            )
            payload = {"purpose": "chat", "endpoint": {"backend": "ollama"}}
        elif case == "embed":
            # The embed purpose must also run an embedding round-trip.
            monkeypatch.setattr(
                llm,
                "build_backend",
                lambda config: FakeBackend(["nomic-embed-text:latest"]),
            )
            payload = {"purpose": "embed", "endpoint": {"backend": "ollama"}}
        else:
            monkeypatch.setattr(
                llm,
                "build_backend",
                lambda config: FakeBackend(error=ConnectionError("refused")),
            )
            payload = {"purpose": "chat", "endpoint": {"backend": "openai"}}
        body = client.post("/api/settings/test", json=payload).json()
        if case == "failure":
            assert body["ok"] is False
            assert "refused" in body["error"]
        else:
            assert body["ok"] is True
        if case == "success":
            assert body["model_count"] == 2
            assert body["models"] == ["m1", "m2"]


def _poll_job(client: TestClient, job_id: str) -> dict:
    """Poll a background job until it finishes (worker is a daemon thread)."""
    for _ in range(100):
        status = client.get(f"/api/companies/jobs/{job_id}").json()
        if status["status"] != "running":
            return status
    raise AssertionError("job did not finish in time")


class TestJobs:
    def test_validation_errors(self, client) -> None:
        assert client.get("/api/companies/jobs/nope").status_code == 404
        assert client.get("/api/companies/jobs/nope/events").status_code == 404
        assert (
            client.post("/api/companies/generate", json={"num": 0}).status_code == 422
        )

    def test_generate_job_completes(
        self, client, monkeypatch, company_db, sample_profile
    ) -> None:
        monkeypatch.setattr(
            server, "generate_company_profile", lambda **kwargs: sample_profile
        )
        response = client.post(
            "/api/companies/generate",
            json={"num": 2, "user_input": "a green logistics firm"},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        status = _poll_job(client, job_id)
        assert status["status"] == "done"
        assert status["completed"] == 2

        # The two generated companies are attached to the job for review;
        # nothing is stored until the user adds them.
        assert len(status["result"]) == 2
        assert status["result"][0]["profile"]["name"] == sample_profile.profile.name
        assert document_query.count_companies() == len(company_db)

        # Progress log lines were captured for the UI subtext.
        assert any("2/2" in line for line in status["logs"])
        assert any("Generating 2 companies" in line for line in status["logs"])

    def test_generate_job_passes_user_input(
        self, client, monkeypatch, company_db
    ) -> None:
        """The free-text instruction is forwarded to the pipeline."""
        seen: dict = {}
        monkeypatch.setattr(
            server,
            "generate_company_profile",
            lambda **kwargs: seen.update(kwargs) or CompanyProfile(),
        )
        job_id = client.post(
            "/api/companies/generate",
            json={"num": 1, "user_input": "solar storage startup"},
        ).json()["id"]
        _poll_job(client, job_id)
        assert seen["user_input"] == "solar storage startup"

    def test_generate_partial_failures_return_subset(
        self, client, monkeypatch, company_db, sample_profile
    ) -> None:
        """One failing company must not discard the successful ones."""
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise RuntimeError("boom")
            return sample_profile

        monkeypatch.setattr(server, "generate_company_profile", flaky)
        job_id = client.post("/api/companies/generate", json={"num": 3}).json()["id"]
        status = _poll_job(client, job_id)
        assert status["status"] == "done"
        assert status["completed"] == 3
        # 2 of the 3 succeeded and are returned for review; the failure is
        # summarized. Nothing is persisted automatically.
        assert len(status["result"]) == 2
        assert document_query.count_companies() == len(company_db)
        assert status["error"] == "1 of 3 failed: boom"

    def test_generate_all_failures_marks_error(
        self, client, monkeypatch, company_db
    ) -> None:
        def broken(**kwargs):
            raise RuntimeError("always down")

        monkeypatch.setattr(server, "generate_company_profile", broken)
        job_id = client.post("/api/companies/generate", json={"num": 2}).json()["id"]
        status = _poll_job(client, job_id)
        assert status["status"] == "error"
        assert "always down" in status["error"]
        assert status["result"] is None
        assert document_query.count_companies() == len(company_db)

    def test_save_companies_endpoint(self, client, company_db, sample_profile) -> None:
        """POST /api/companies persists the selected generated companies."""
        # Empty list is rejected.
        assert client.post("/api/companies", json=[]).status_code == 422

        response = client.post("/api/companies", json=[sample_profile.model_dump()])
        assert response.status_code == 201
        doc_ids = response.json()
        assert len(doc_ids) == 1
        assert document_query.count_companies() == len(company_db) + 1
        stored = document_query.get_company(doc_ids[0])
        assert stored["profile"]["name"] == sample_profile.profile.name

    def test_save_companies_rejects_invalid_profile(self, client, company_db) -> None:
        assert (
            client.post("/api/companies", json=[{"seed": "not-an-int"}]).status_code
            == 422
        )

    def test_late_sse_subscriber_gets_final_snapshot_and_closes(
        self, client, monkeypatch, company_db, sample_profile
    ) -> None:
        """Regression: subscribing after a job finished used to hang forever."""
        monkeypatch.setattr(
            server, "generate_company_profile", lambda **kwargs: sample_profile
        )
        job_id = client.post("/api/companies/generate", json={"num": 1}).json()["id"]
        status = _poll_job(client, job_id)
        assert status["status"] == "done"

        with client.stream("GET", f"/api/companies/jobs/{job_id}/events") as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
        events = [
            json.loads(line.removeprefix("data: "))
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        assert events, "expected at least the final snapshot event"
        assert events[-1]["status"] == "done"
        assert events[-1]["completed"] == 1


class TestCompanyBrowse:
    def test_list_with_filters(self, client, company_db) -> None:
        response = client.get("/api/companies")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == len(company_db)
        assert items[0]["name"] == "LuxeStays Hospitality Group"
        assert "id" in items[0]
        # Industry filter.
        filtered = client.get(
            "/api/companies", params={"industry": "Hospitality"}
        ).json()
        assert filtered
        assert all(item["industry"] == "Hospitality" for item in filtered)
        # Free-text search.
        searched = client.get("/api/companies", params={"search": "LuxeStays"}).json()
        assert searched
        assert all("luxestays" in json.dumps(item).lower() for item in searched)

    def test_list_empty_db_returns_empty(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("TINYDB_PATH", str(tmp_path / "empty.db"))
        document_query.reset_db()
        assert client.get("/api/companies").json() == []

    def test_detail(self, client, company_db) -> None:
        response = client.get(f"/api/companies/{company_db[0]}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == company_db[0]
        assert body["profile"]["name"] == "LuxeStays Hospitality Group"
        assert "reports" in body
        assert client.get("/api/companies/999999").status_code == 404

    def test_patch_updates_profile(self, client, company_db) -> None:
        company_id = company_db[0]
        response = client.patch(
            f"/api/companies/{company_id}",
            json={
                "name": "Renamed Stays",
                "industry": "Hospitality",
                "description": "Updated description",
                "headquarters": "Austin",
                "size": "large",
            },
        )
        assert response.status_code == 200
        profile = response.json()["profile"]
        assert profile["name"] == "Renamed Stays"
        assert profile["headquarters"] == "Austin"
        assert profile["size"] == "large"
        # The listing reflects the update too.
        listed = client.get("/api/companies").json()
        assert (
            next(i for i in listed if i["id"] == company_id)["name"] == "Renamed Stays"
        )
        # Unknown company: 404.
        assert (
            client.patch(
                "/api/companies/999999",
                json={
                    "name": "X",
                    "industry": "Y",
                    "description": "Z",
                    "headquarters": "H",
                    "size": "small",
                },
            ).status_code
            == 404
        )


class TestCompanyReports:
    def test_list(self, client, company_db) -> None:
        company_id = company_db[0]
        response = client.get(f"/api/companies/{company_id}/document-types")
        assert response.status_code == 200
        body = response.json()
        assert body
        assert all(report["company_id"] == company_id for report in body)
        assert {"id", "name", "category", "purpose"} <= set(body[0])

    def test_put_replaces_all(self, client, company_db) -> None:
        company_id = company_db[0]
        response = client.put(
            f"/api/companies/{company_id}/document-types",
            json=[{"name": "New Report", "category": "C", "purpose": "P"}],
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "New Report"
        # The joined company detail reflects the replacement.
        detail = client.get(f"/api/companies/{company_id}").json()
        assert [report["name"] for report in detail["reports"]] == ["New Report"]
        # An empty list clears all reports.
        assert (
            client.put(
                f"/api/companies/{company_id}/document-types", json=[]
            ).status_code
            == 200
        )
        assert client.get(f"/api/companies/{company_id}/document-types").json() == []

    def test_post_appends(self, client, company_db) -> None:
        company_id = company_db[0]
        before = client.get(f"/api/companies/{company_id}/document-types").json()
        response = client.post(
            f"/api/companies/{company_id}/document-types",
            json=[{"name": "Appended Report", "category": "C", "purpose": "P"}],
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == len(before) + 1
        assert body[-1]["name"] == "Appended Report"
        # Existing reports are untouched.
        assert [r["name"] for r in body[:-1]] == [r["name"] for r in before]

    def test_delete(self, client, company_db) -> None:
        company_id = company_db[0]
        response = client.delete(f"/api/companies/{company_id}/document-types")
        assert response.status_code == 200
        assert response.json() == {"deleted": True}
        assert client.get(f"/api/companies/{company_id}/document-types").json() == []

    def test_delete_single(self, client, company_db) -> None:
        company_id = company_db[0]
        before = client.get(f"/api/companies/{company_id}/document-types").json()
        assert len(before) >= 2
        target = before[0]
        response = client.delete(
            f"/api/companies/{company_id}/document-types/{target['id']}"
        )
        assert response.status_code == 200
        assert response.json() == {"deleted": True}
        after = client.get(f"/api/companies/{company_id}/document-types").json()
        assert [r["name"] for r in after] == [r["name"] for r in before[1:]]
        # Unknown document type: 404, list unchanged.
        assert (
            client.delete(
                f"/api/companies/{company_id}/document-types/999999"
            ).status_code
            == 404
        )
        assert client.get(f"/api/companies/{company_id}/document-types").json() == after

    def test_patch_updates_single(self, client, company_db) -> None:
        company_id = company_db[0]
        before = client.get(f"/api/companies/{company_id}/document-types").json()
        target = before[0]
        response = client.patch(
            f"/api/companies/{company_id}/document-types/{target['id']}",
            json={"name": "Renamed Report", "category": "Analysis", "purpose": "New"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Renamed Report"
        assert body["category"] == "Analysis"
        assert body["purpose"] == "New"
        after = client.get(f"/api/companies/{company_id}/document-types").json()
        assert [r["name"] for r in after] == [
            "Renamed Report" if r["id"] == target["id"] else r["name"] for r in before
        ]
        # Unknown document type: 404.
        assert (
            client.patch(
                f"/api/companies/{company_id}/document-types/999999",
                json={"name": "X", "category": "Y", "purpose": "Z"},
            ).status_code
            == 404
        )

    def test_patch_preserves_user_input(self, client, company_db) -> None:
        company_id = company_db[0]
        target, *_rest = client.get(
            f"/api/companies/{company_id}/document-types"
        ).json()
        # Seed a generation-time user input.
        client.patch(
            f"/api/companies/{company_id}/document-types/{target['id']}",
            json={
                "name": target["name"],
                "category": target["category"],
                "purpose": target["purpose"],
                "user_input": "Guides for new hires",
            },
        )
        # A plain edit (no user_input) keeps the stored value.
        response = client.patch(
            f"/api/companies/{company_id}/document-types/{target['id']}",
            json={"name": "Renamed", "category": target["category"], "purpose": "P"},
        )
        assert response.status_code == 200
        assert response.json()["user_input"] == "Guides for new hires"

    def test_missing_company_404(self, client, company_db) -> None:
        assert client.get("/api/companies/999999/document-types").status_code == 404
        assert (
            client.put("/api/companies/999999/document-types", json=[]).status_code
            == 404
        )
        assert (
            client.post(
                "/api/companies/999999/document-types",
                json=[{"name": "X", "category": "C", "purpose": "P"}],
            ).status_code
            == 404
        )
        assert client.delete("/api/companies/999999/document-types").status_code == 404
        assert (
            client.delete("/api/companies/999999/document-types/1").status_code == 404
        )

    def test_generate_reports_job_completes(
        self, client, monkeypatch, company_db
    ) -> None:
        company_id = company_db[0]
        calls: dict = {}

        def fake_generate(
            company_id,
            document_request=None,
            model_name=None,
            force=False,
            num_documents=5,
        ):
            calls["company_id"] = company_id
            calls["document_request"] = document_request
            calls["model_name"] = model_name
            calls["num_documents"] = num_documents
            return [DocumentType(name="Generated Document", category="C", purpose="P")]

        monkeypatch.setattr(server, "generate_documents_for_company", fake_generate)
        response = client.post(
            f"/api/companies/{company_id}/document-types/generate",
            json={
                "model": "m1",
                "num": 7,
                "document_request": "Onboarding guides and operations reports.",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        status = _poll_job(client, job_id)
        assert status["status"] == "done"
        assert status["company_ids"] == [company_id]
        # The generated reports are attached to the job for review.
        assert status["result"] == [
            {
                "name": "Generated Document",
                "category": "C",
                "purpose": "P",
                "user_input": None,
            }
        ]
        assert calls == {
            "company_id": company_id,
            "document_request": "Onboarding guides and operations reports.",
            "model_name": "m1",
            "num_documents": 7,
        }

    def test_generate_reports_validation_errors(self, client, company_db) -> None:
        base = f"/api/companies/{company_db[0]}/document-types/generate"
        assert (
            client.post(base, json={"document_request": "too short"}).status_code == 422
        )
        assert client.post(base, json={}).status_code == 422
        assert (
            client.post(
                "/api/companies/999999/document-types/generate",
                json={"document_request": "Onboarding guides and operations reports."},
            ).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# Report output directory settings
# ---------------------------------------------------------------------------


class TestReportSettings:
    @pytest.fixture(autouse=True)
    def fresh_db(self, tmp_path, monkeypatch):
        """Isolate the TinyDB (settings live in the shared store)."""
        monkeypatch.setenv("TINYDB_PATH", str(tmp_path / "companies.db"))
        document_query.reset_db()
        yield
        document_query.reset_db()

    def test_get_sources(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
        response = client.get("/api/settings/documents")
        assert response.status_code == 200
        assert response.json() == {
            "output_dir": None,
            "default": None,
            "source": "none",
        }
        # The env var provides the default.
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path / "reports"))
        data = client.get("/api/settings/documents").json()
        assert data["source"] == "env"
        assert data["output_dir"] == str(tmp_path / "reports")
        assert data["default"] == str(tmp_path / "reports")

    def test_put_requires_existing_dir(self, client, tmp_path) -> None:
        response = client.put(
            "/api/settings/documents",
            json={"output_dir": str(tmp_path / "missing")},
        )
        assert response.status_code == 400

    def test_put_get_roundtrip(self, client, tmp_path) -> None:
        target = tmp_path / "reports"
        target.mkdir()
        response = client.put(
            "/api/settings/documents", json={"output_dir": str(target)}
        )
        assert response.status_code == 200
        assert response.json()["source"] == "saved"
        data = client.get("/api/settings/documents").json()
        assert data["source"] == "saved"
        assert data["output_dir"] == str(target)

    def test_clearing_saved_value(self, client, tmp_path, monkeypatch) -> None:
        target = tmp_path / "reports"
        target.mkdir()
        # DELETE clears the saved value entirely.
        client.put("/api/settings/documents", json={"output_dir": str(target)})
        response = client.delete("/api/settings/documents")
        assert response.status_code == 200
        assert response.json()["source"] == "none"
        # A null PUT also clears, falling back to the env default.
        client.put("/api/settings/documents", json={"output_dir": str(target)})
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path / "env"))
        data = client.put("/api/settings/documents", json={"output_dir": None}).json()
        assert data["source"] == "env"
        assert data["output_dir"] == str(tmp_path / "env")


class TestDocumentRename:
    @pytest.fixture(autouse=True)
    def fresh_db(self, tmp_path, monkeypatch):
        """Isolate the TinyDB and seed one document record with a real file."""
        monkeypatch.setenv("TINYDB_PATH", str(tmp_path / "companies.db"))
        document_query.reset_db()
        self.file_path = tmp_path / "acme_guide.pdf"
        self.file_path.write_bytes(b"x")
        self.doc_id = document_query.save_document(1, 1, self.file_path)
        yield
        document_query.reset_db()

    def test_rename_updates_record_and_file(self, client) -> None:
        response = client.patch(
            f"/api/documents/{self.doc_id}", json={"filename": "annual report"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["filename"] == "annual report.pdf"
        assert Path(body["filepath"]).name == "annual report.pdf"
        assert not self.file_path.exists()
        assert Path(body["filepath"]).is_file()

    def test_rename_unknown_document(self, client) -> None:
        response = client.patch("/api/documents/999999", json={"filename": "x"})
        assert response.status_code == 404

    def test_rename_rejects_bad_names(self, client) -> None:
        for name in ("a/b", "a\\b", "."):
            response = client.patch(
                f"/api/documents/{self.doc_id}", json={"filename": name}
            )
            assert response.status_code == 400

    def test_rename_collision(self, client, tmp_path) -> None:
        (tmp_path / "other.pdf").write_bytes(b"y")
        other_id = document_query.save_document(1, 1, tmp_path / "other.pdf")
        response = client.patch(
            f"/api/documents/{other_id}", json={"filename": "acme_guide"}
        )
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Directory browsing
# ---------------------------------------------------------------------------


class TestFsBrowse:
    def test_lists_subdirectories_sorted(self, client, tmp_path) -> None:
        (tmp_path / "b").mkdir()
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "nested").mkdir()
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")
        data = client.get("/api/fs/browse", params={"path": str(tmp_path)}).json()
        assert Path(data["path"]) == tmp_path.resolve()
        assert data["parent"] is not None
        assert [entry["name"] for entry in data["entries"]] == ["a", "b"]
        assert all(Path(entry["path"]).is_dir() for entry in data["entries"])

    def test_invalid_path_400(self, client, tmp_path) -> None:
        file_path = tmp_path / "f.txt"
        file_path.write_text("x", encoding="utf-8")
        assert (
            client.get(
                "/api/fs/browse", params={"path": str(tmp_path / "nope")}
            ).status_code
            == 400
        )
        assert (
            client.get("/api/fs/browse", params={"path": str(file_path)}).status_code
            == 400
        )


# ---------------------------------------------------------------------------
# PDF report generation
# ---------------------------------------------------------------------------


class TestCompanyPdf:
    def test_validation_errors(self, client, company_db, monkeypatch, tmp_path) -> None:
        # No output directory configured.
        monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/pdf", json={"report": "x"}
            ).status_code
            == 400
        )
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
        # Unknown company.
        assert (
            client.post("/api/companies/999999/pdf", json={"report": "x"}).status_code
            == 404
        )
        # Unknown figure kind.
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/pdf",
                json={"report": "Onboarding Guide", "figure_kinds": ["donut"]},
            ).status_code
            == 422
        )

    def test_job_completes(self, client, company_db, monkeypatch, tmp_path) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(
            company_id,
            report,
            user_input=None,
            model_name=None,
            output_dir=None,
            figure_kinds=None,
            quick_doc=False,
            gen_tracing=False,
        ):
            calls.update(
                company_id=company_id,
                report=report,
                user_input=user_input,
                model_name=model_name,
                figure_kinds=figure_kinds,
                quick_doc=quick_doc,
                gen_tracing=gen_tracing,
            )
            return SimpleNamespace(
                pdf_path=out / "acme_report.pdf", report_name="Onboarding Guide"
            )

        monkeypatch.setattr(server.document_pdf, "generate_document_pdf", fake_generate)
        response = client.post(
            f"/api/companies/{company_db[0]}/pdf",
            json={
                "report": "Onboarding Guide",
                "user_input": "focus on Q3",
                "model": "m1",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        status = _poll_job(client, job_id)
        assert status["status"] == "done"
        assert status["company_ids"] == [company_db[0]]
        assert status["result"] == {
            "pdf": "acme_report.pdf",
            "report": "Onboarding Guide",
        }
        # Stage log lines from the worker thread were captured.
        assert any("starting" in line for line in status["logs"])
        assert any("done in" in line for line in status["logs"])
        assert calls == {
            "company_id": company_db[0],
            "report": "Onboarding Guide",
            "user_input": "focus on Q3",
            "model_name": "m1",
            "figure_kinds": [],
            "quick_doc": False,
            "gen_tracing": False,
        }

    def test_options_pass_through(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(
            company_id, report, user_input=None, model_name=None, **kwargs
        ):
            calls.update(user_input=user_input, model_name=model_name)
            calls.update(kwargs)
            return SimpleNamespace(
                pdf_path=out / "acme_report.pdf", report_name="Onboarding Guide"
            )

        monkeypatch.setattr(server.document_pdf, "generate_document_pdf", fake_generate)
        response = client.post(
            f"/api/companies/{company_db[0]}/pdf",
            json={
                "report": "Onboarding Guide",
                "user_input": "focus on Q3",
                "model": "m1",
                "figure_kinds": ["bar", "line"],
                "quick_doc": True,
                "gen_tracing": True,
            },
        )
        assert response.status_code == 202
        _poll_job(client, response.json()["id"])
        assert calls == {
            "user_input": "focus on Q3",
            "model_name": "m1",
            "figure_kinds": ["bar", "line"],
            "quick_doc": True,
            "gen_tracing": True,
        }

    def test_job_error(self, client, company_db, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))

        def fake_generate(company_id, report, **kwargs):
            raise ValueError("Document type 'x' not found for company")

        monkeypatch.setattr(server.document_pdf, "generate_document_pdf", fake_generate)
        response = client.post(
            f"/api/companies/{company_db[0]}/pdf", json={"report": "x"}
        )
        assert response.status_code == 202
        status = _poll_job(client, response.json()["id"])
        assert status["status"] == "error"
        assert "not found" in (status["error"] or "")

    def test_download_serves_file(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        out = tmp_path / "reports"
        out.mkdir()
        (out / "acme_report.pdf").write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        response = client.get(f"/api/companies/{company_db[0]}/pdf/acme_report.pdf")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 fake"
        assert response.headers["content-type"] == "application/pdf"

    def test_download_errors(self, client, company_db, monkeypatch, tmp_path) -> None:
        out = tmp_path / "reports"
        out.mkdir()
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        assert (
            client.get(f"/api/companies/{company_db[0]}/pdf/nope.pdf").status_code
            == 404
        )
        # Non-PDF file names are rejected.
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
        assert (
            client.get(f"/api/companies/{company_db[0]}/pdf/notes.txt").status_code
            == 400
        )


# ---------------------------------------------------------------------------
# Excel workbook generation
# ---------------------------------------------------------------------------


class TestCompanyExcel:
    def test_validation_errors(self, client, company_db, monkeypatch, tmp_path) -> None:
        # No output directory configured.
        monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/excel", json={"report": "x"}
            ).status_code
            == 400
        )
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
        # Unknown company.
        assert (
            client.post("/api/companies/999999/excel", json={"report": "x"}).status_code
            == 404
        )
        # Unknown figure kind.
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/excel",
                json={"report": "Onboarding Guide", "figure_kinds": ["donut"]},
            ).status_code
            == 422
        )

    def test_job_completes(self, client, company_db, monkeypatch, tmp_path) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(
            company_id,
            report,
            user_input=None,
            model_name=None,
            output_dir=None,
            figure_kinds=None,
            quick_doc=False,
            simple_sheets=False,
            glossary=False,
            gen_tracing=False,
        ):
            calls.update(
                company_id=company_id,
                report=report,
                user_input=user_input,
                model_name=model_name,
                figure_kinds=figure_kinds,
                quick_doc=quick_doc,
                simple_sheets=simple_sheets,
                glossary=glossary,
                gen_tracing=gen_tracing,
            )
            return SimpleNamespace(
                xlsx_path=out / "acme_report.xlsx", report_name="Onboarding Guide"
            )

        monkeypatch.setattr(
            server.document_excel, "generate_document_excel", fake_generate
        )
        response = client.post(
            f"/api/companies/{company_db[0]}/excel",
            json={
                "report": "Onboarding Guide",
                "user_input": "focus on Q3",
                "model": "m1",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        status = _poll_job(client, job_id)
        assert status["status"] == "done"
        assert status["company_ids"] == [company_db[0]]
        assert status["result"] == {
            "xlsx": "acme_report.xlsx",
            "report": "Onboarding Guide",
        }
        # Stage log lines from the worker thread were captured.
        assert any("starting" in line for line in status["logs"])
        assert any("done in" in line for line in status["logs"])
        assert calls == {
            "company_id": company_db[0],
            "report": "Onboarding Guide",
            "user_input": "focus on Q3",
            "model_name": "m1",
            "figure_kinds": [],
            "quick_doc": False,
            "simple_sheets": False,
            "glossary": False,
            "gen_tracing": False,
        }

    def test_options_pass_through(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(
            company_id, report, user_input=None, model_name=None, **kwargs
        ):
            calls.update(user_input=user_input, model_name=model_name)
            calls.update(kwargs)
            return SimpleNamespace(
                xlsx_path=out / "acme_report.xlsx", report_name="Onboarding Guide"
            )

        monkeypatch.setattr(
            server.document_excel, "generate_document_excel", fake_generate
        )
        response = client.post(
            f"/api/companies/{company_db[0]}/excel",
            json={
                "report": "Onboarding Guide",
                "user_input": "focus on Q3",
                "model": "m1",
                "figure_kinds": ["bar", "line"],
                "quick_doc": True,
                "simple_sheets": True,
                "glossary": True,
                "gen_tracing": True,
            },
        )
        assert response.status_code == 202
        _poll_job(client, response.json()["id"])
        assert calls == {
            "user_input": "focus on Q3",
            "model_name": "m1",
            "figure_kinds": ["bar", "line"],
            "quick_doc": True,
            "simple_sheets": True,
            "glossary": True,
            "gen_tracing": True,
        }

    def test_job_error(self, client, company_db, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))

        def fake_generate(company_id, report, **kwargs):
            raise ValueError("Document type 'x' not found for company")

        monkeypatch.setattr(
            server.document_excel, "generate_document_excel", fake_generate
        )
        response = client.post(
            f"/api/companies/{company_db[0]}/excel", json={"report": "x"}
        )
        assert response.status_code == 202
        status = _poll_job(client, response.json()["id"])
        assert status["status"] == "error"
        assert "not found" in (status["error"] or "")

    def test_download_serves_file(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        out = tmp_path / "reports"
        out.mkdir()
        (out / "acme_report.xlsx").write_bytes(b"PK fake xlsx")
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        response = client.get(f"/api/companies/{company_db[0]}/excel/acme_report.xlsx")
        assert response.status_code == 200
        assert response.content == b"PK fake xlsx"
        assert response.headers["content-type"] == server.XLSX_MEDIA_TYPE

    def test_download_errors(self, client, company_db, monkeypatch, tmp_path) -> None:
        out = tmp_path / "reports"
        out.mkdir()
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        assert (
            client.get(f"/api/companies/{company_db[0]}/excel/nope.xlsx").status_code
            == 404
        )
        # Non-xlsx file names are rejected.
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
        assert (
            client.get(f"/api/companies/{company_db[0]}/excel/notes.txt").status_code
            == 400
        )
        # Path traversal is rejected.
        assert client.get(
            f"/api/companies/{company_db[0]}/excel/..%2Fevil.xlsx"
        ).status_code in (400, 404)


# ---------------------------------------------------------------------------
# PNG image document generation
# ---------------------------------------------------------------------------


class TestCompanyImage:
    def test_validation_errors(self, client, company_db, monkeypatch, tmp_path) -> None:
        # No output directory configured.
        monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/image", json={"report": "x"}
            ).status_code
            == 400
        )
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
        # Unknown company.
        assert (
            client.post("/api/companies/999999/image", json={"report": "x"}).status_code
            == 404
        )
        # Unknown figure kind.
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/image",
                json={"report": "Onboarding Guide", "figure_kinds": ["donut"]},
            ).status_code
            == 422
        )
        # Malformed distress options are rejected.
        assert (
            client.post(
                f"/api/companies/{company_db[0]}/image",
                json={"report": "x", "distress": {"stain_count": "not-a-number"}},
            ).status_code
            == 422
        )

    def test_job_completes(self, client, company_db, monkeypatch, tmp_path) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(
            company_id,
            report,
            user_input=None,
            model_name=None,
            output_dir=None,
            figure_kinds=None,
            a4_aspect=True,
            distress=None,
            gen_tracing=False,
        ):
            calls.update(
                company_id=company_id,
                report=report,
                user_input=user_input,
                model_name=model_name,
                figure_kinds=figure_kinds,
                a4_aspect=a4_aspect,
                distress=distress,
                gen_tracing=gen_tracing,
            )
            return SimpleNamespace(
                png_path=out / "acme_report.png", report_name="Onboarding Guide"
            )

        monkeypatch.setattr(
            server.document_png, "generate_document_image", fake_generate
        )
        response = client.post(
            f"/api/companies/{company_db[0]}/image",
            json={
                "report": "Onboarding Guide",
                "user_input": "focus on Q3",
                "model": "m1",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        status = _poll_job(client, job_id)
        assert status["status"] == "done"
        assert status["company_ids"] == [company_db[0]]
        assert status["result"] == {
            "png": "acme_report.png",
            "report": "Onboarding Guide",
        }
        # Stage log lines from the worker thread were captured.
        assert any("starting" in line for line in status["logs"])
        assert any("done in" in line for line in status["logs"])
        assert calls["company_id"] == company_db[0]
        assert calls["report"] == "Onboarding Guide"
        assert calls["user_input"] == "focus on Q3"
        assert calls["model_name"] == "m1"
        assert calls["figure_kinds"] == []
        assert calls["a4_aspect"] is True
        assert calls["distress"].enabled is False
        assert calls["gen_tracing"] is False

    def test_options_pass_through(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(
            company_id, report, user_input=None, model_name=None, **kwargs
        ):
            calls.update(user_input=user_input, model_name=model_name)
            calls.update(kwargs)
            return SimpleNamespace(
                png_path=out / "acme_report.png", report_name="Onboarding Guide"
            )

        monkeypatch.setattr(
            server.document_png, "generate_document_image", fake_generate
        )
        response = client.post(
            f"/api/companies/{company_db[0]}/image",
            json={
                "report": "Onboarding Guide",
                "user_input": "focus on Q3",
                "model": "m1",
                "figure_kinds": ["bar", "line"],
                "a4_aspect": False,
                "distress": {
                    "enabled": True,
                    "stains": True,
                    "stain_count": 7,
                    "warp": True,
                },
                "gen_tracing": True,
            },
        )
        assert response.status_code == 202
        _poll_job(client, response.json()["id"])
        assert calls["user_input"] == "focus on Q3"
        assert calls["model_name"] == "m1"
        assert calls["figure_kinds"] == ["bar", "line"]
        assert calls["a4_aspect"] is False
        assert calls["gen_tracing"] is True
        assert calls["distress"].enabled is True
        assert calls["distress"].stain_count == 7
        assert calls["distress"].warp is True

    def test_distress_new_fields_accepted(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        from types import SimpleNamespace

        out = tmp_path / "reports"
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        calls: dict = {}

        def fake_generate(company_id, report, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                png_path=out / "acme_report.png", report_name="Guide"
            )

        monkeypatch.setattr(
            server.document_png, "generate_document_image", fake_generate
        )
        # A body with only augraphy-only fields (no legacy keys) is valid:
        # the legacy fields keep their defaults and backend defaults to
        # "augraphy".
        response = client.post(
            f"/api/companies/{company_db[0]}/image",
            json={
                "report": "Guide",
                "distress": {"enabled": True, "ink_bleed": True},
            },
        )
        assert response.status_code == 202
        _poll_job(client, response.json()["id"])
        assert calls["distress"].ink_bleed is True
        assert calls["distress"].backend == "augraphy"
        assert calls["distress"].paper_aging is True  # model default

    def test_job_error(self, client, company_db, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))

        def fake_generate(company_id, report, **kwargs):
            raise ValueError("Document type 'x' not found for company")

        monkeypatch.setattr(
            server.document_png, "generate_document_image", fake_generate
        )
        response = client.post(
            f"/api/companies/{company_db[0]}/image", json={"report": "x"}
        )
        assert response.status_code == 202
        status = _poll_job(client, response.json()["id"])
        assert status["status"] == "error"
        assert "not found" in (status["error"] or "")

    def test_download_serves_file(
        self, client, company_db, monkeypatch, tmp_path
    ) -> None:
        out = tmp_path / "reports"
        out.mkdir()
        (out / "acme_report.png").write_bytes(b"\x89PNG fake")
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        response = client.get(f"/api/companies/{company_db[0]}/image/acme_report.png")
        assert response.status_code == 200
        assert response.content == b"\x89PNG fake"
        assert response.headers["content-type"] == "image/png"

    def test_download_errors(self, client, company_db, monkeypatch, tmp_path) -> None:
        out = tmp_path / "reports"
        out.mkdir()
        monkeypatch.setenv("DOCUMENTS_DIR", str(out))
        assert (
            client.get(f"/api/companies/{company_db[0]}/image/nope.png").status_code
            == 404
        )
        # Non-PNG file names are rejected.
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
        assert (
            client.get(f"/api/companies/{company_db[0]}/image/notes.txt").status_code
            == 400
        )
        # Path traversal is rejected.
        assert client.get(
            f"/api/companies/{company_db[0]}/image/..%2Fevil.png"
        ).status_code in (400, 404)


# ---------------------------------------------------------------------------
# Report documents
# ---------------------------------------------------------------------------


class TestDocuments:
    def _record(
        self, company_id: int, tmp_path: Path, name: str = "acme_report.pdf"
    ) -> int:
        """Write a temp file and record it for the company's first report."""
        file_path = tmp_path / name
        file_path.write_bytes(b"%PDF-1.4 fake")
        report_id = document_query.get_document_types(company_id)[0]["id"]
        return document_query.save_document(company_id, report_id, file_path)

    def test_list(self, client, company_db, tmp_path) -> None:
        assert client.get("/api/documents").json() == []
        doc_id = self._record(company_db[0], tmp_path)
        report_name = document_query.get_document_types(company_db[0])[0]["name"]

        items = client.get("/api/documents").json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == doc_id
        assert item["filename"] == "acme_report.pdf"
        assert item["filetype"] == "pdf"
        assert item["company_id"] == company_db[0]
        assert item["company_name"] == "LuxeStays Hospitality Group"
        assert item["report_name"] == report_name
        assert "filepath" in item and "size_kb" in item and "created_at" in item

    def test_list_filters(self, client, company_db, tmp_path) -> None:
        first = self._record(company_db[0], tmp_path, name="first.pdf")
        second = self._record(company_db[1], tmp_path, name="second.pdf")

        by_company = client.get(
            "/api/documents", params={"company_id": company_db[1]}
        ).json()
        assert [item["id"] for item in by_company] == [second]

        report_id = document_query.get_document_types(company_db[0])[0]["id"]
        by_report = client.get(
            "/api/documents", params={"document_type_id": report_id}
        ).json()
        assert [item["id"] for item in by_report] == [first]

    def test_download_serves_file(self, client, company_db, tmp_path) -> None:
        doc_id = self._record(company_db[0], tmp_path)
        response = client.get(f"/api/documents/{doc_id}/download")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 fake"
        assert response.headers["content-type"] == "application/pdf"

    def test_download_errors(self, client, company_db, tmp_path) -> None:
        assert client.get("/api/documents/999999/download").status_code == 404
        doc_id = self._record(company_db[0], tmp_path)
        (tmp_path / "acme_report.pdf").unlink()
        assert client.get(f"/api/documents/{doc_id}/download").status_code == 404

    def test_preview_serves_inline(self, client, company_db, tmp_path) -> None:
        doc_id = self._record(company_db[0], tmp_path)
        response = client.get(f"/api/documents/{doc_id}/preview")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 fake"
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == (
            'inline; filename="acme_report.pdf"'
        )

    def test_preview_errors(self, client, company_db, tmp_path) -> None:
        assert client.get("/api/documents/999999/preview").status_code == 404
        doc_id = self._record(company_db[0], tmp_path)
        (tmp_path / "acme_report.pdf").unlink()
        assert client.get(f"/api/documents/{doc_id}/preview").status_code == 404

    def test_delete_removes_record_and_file(self, client, company_db, tmp_path) -> None:
        doc_id = self._record(company_db[0], tmp_path)
        response = client.delete(f"/api/documents/{doc_id}")
        assert response.status_code == 200
        assert response.json() == {"deleted": True}
        assert client.get("/api/documents").json() == []
        assert not (tmp_path / "acme_report.pdf").exists()

    def test_delete_missing(self, client, company_db, tmp_path) -> None:
        assert client.delete("/api/documents/999999").status_code == 404
        # A missing file still removes the record.
        doc_id = self._record(company_db[0], tmp_path)
        (tmp_path / "acme_report.pdf").unlink()
        response = client.delete(f"/api/documents/{doc_id}")
        assert response.status_code == 200
        assert client.get("/api/documents").json() == []


# ---------------------------------------------------------------------------
# Distress editor (live preview + save)
# ---------------------------------------------------------------------------


class TestDistressEditor:
    @staticmethod
    def _png_bytes() -> bytes:
        import cv2
        import numpy as np

        img = np.ones((400, 300, 3), dtype=np.uint8) * 255
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(img, "TEST DOCUMENT", (20, 60), font, 0.8, 0, 2, cv2.LINE_AA)
        return cv2.imencode(".png", img)[1].tobytes()

    @staticmethod
    def _body() -> dict:
        return {
            "distress": {
                "enabled": True,
                "stains": True,
                "stain_count": 3,
                "paper_aging": False,
                "vignette": False,
                "noise": False,
                "ink_fade": False,
                "blur": False,
                "warp": False,
            },
            "seed": 42,
            "stain_seed": 123,
        }

    def _record(
        self,
        company_id: int,
        tmp_path: Path,
        *,
        with_trace: bool = True,
        name: str = "acme_report.png",
    ) -> int:
        """Write a synthetic PNG (plus its stored original) and record it."""
        file_path = tmp_path / name
        file_path.write_bytes(self._png_bytes())
        original = file_path.with_name(file_path.stem + "_original.png")
        original.write_bytes(file_path.read_bytes())
        report_id = document_query.get_document_types(company_id)[0]["id"]
        gen_tracing = (
            {
                "stages": {
                    "distress": {
                        "seed": 42,
                        "original_path": str(original),
                    }
                }
            }
            if with_trace
            else None
        )
        return document_query.save_document(
            company_id, report_id, file_path, gen_tracing=gen_tracing
        )

    def _original_path(self, tmp_path: Path, name: str = "acme_report.png") -> Path:
        return (tmp_path / name).with_name(Path(name).stem + "_original.png")

    def test_preview_returns_distressed_png(self, client, company_db, tmp_path) -> None:
        from document_gen.generators.png_gen import distress_image_to_bytes
        from document_gen.models import DistressOptions

        doc_id = self._record(company_db[0], tmp_path)
        response = client.post(
            f"/api/documents/{doc_id}/image/distress-preview", json=self._body()
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["content-disposition"] == "inline"
        # The preview is exactly the server-side distress pipeline output.
        expected = distress_image_to_bytes(
            self._original_path(tmp_path).read_bytes(),
            DistressOptions(**self._body()["distress"]),
            42,
            stain_seed=123,
        )
        assert response.content == expected
        assert response.content.startswith(b"\x89PNG")

    def test_preview_error_matrix(self, client, company_db, tmp_path) -> None:
        # Unknown document.
        assert (
            client.post(
                "/api/documents/999999/image/distress-preview", json=self._body()
            ).status_code
            == 404
        )
        # Non-PNG document.
        pdf_id = self._record(company_db[0], tmp_path, name="notes.pdf")
        (tmp_path / "notes.pdf").write_bytes(b"%PDF-1.4 fake")
        assert (
            client.post(
                f"/api/documents/{pdf_id}/image/distress-preview", json=self._body()
            ).status_code
            == 400
        )
        # No generation trace stored.
        no_trace_id = self._record(company_db[0], tmp_path, with_trace=False)
        assert (
            client.post(
                f"/api/documents/{no_trace_id}/image/distress-preview",
                json=self._body(),
            ).status_code
            == 409
        )
        # Trace present but the original file was deleted.
        doc_id = self._record(company_db[0], tmp_path, name="ghost.png")
        self._original_path(tmp_path, "ghost.png").unlink()
        response = client.post(
            f"/api/documents/{doc_id}/image/distress-preview", json=self._body()
        )
        assert response.status_code == 409
        assert "No stored original" in response.json()["detail"]

    def test_save_overwrites_document_keeps_original(
        self, client, company_db, tmp_path
    ) -> None:
        from document_gen.generators.png_gen import distress_image_to_bytes
        from document_gen.models import DistressOptions

        doc_id = self._record(company_db[0], tmp_path)
        original_path = self._original_path(tmp_path)
        original_before = original_path.read_bytes()
        document_before = (tmp_path / "acme_report.png").read_bytes()

        response = client.post(
            f"/api/documents/{doc_id}/image/distress-save", json=self._body()
        )
        assert response.status_code == 200

        # The document file now holds the distressed render.
        expected = distress_image_to_bytes(
            original_before,
            DistressOptions(**self._body()["distress"]),
            42,
            stain_seed=123,
        )
        assert (tmp_path / "acme_report.png").read_bytes() == expected
        assert (tmp_path / "acme_report.png").read_bytes() != document_before
        # The original is left untouched, so the document stays re-editable.
        assert original_path.read_bytes() == original_before

        # The returned record reflects the new file size.
        record = response.json()
        assert record["id"] == doc_id
        assert record["size_kb"] == round(len(expected) / 1024, 2)
        listed = document_query.get_document(doc_id)
        assert listed is not None
        assert listed["size_kb"] == record["size_kb"]

    def test_save_error_matrix(self, client, company_db, tmp_path) -> None:
        assert (
            client.post(
                "/api/documents/999999/image/distress-save", json=self._body()
            ).status_code
            == 404
        )
        pdf_id = self._record(company_db[0], tmp_path, name="notes.pdf")
        (tmp_path / "notes.pdf").write_bytes(b"%PDF-1.4 fake")
        assert (
            client.post(
                f"/api/documents/{pdf_id}/image/distress-save", json=self._body()
            ).status_code
            == 400
        )
        no_trace_id = self._record(company_db[0], tmp_path, with_trace=False)
        assert (
            client.post(
                f"/api/documents/{no_trace_id}/image/distress-save", json=self._body()
            ).status_code
            == 409
        )
        doc_id = self._record(company_db[0], tmp_path, name="ghost.png")
        self._original_path(tmp_path, "ghost.png").unlink()
        assert (
            client.post(
                f"/api/documents/{doc_id}/image/distress-save", json=self._body()
            ).status_code
            == 409
        )
