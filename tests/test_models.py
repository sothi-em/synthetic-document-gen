"""Tests for the Pydantic models in ``document_gen.models``."""

from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from document_gen.models import (
    EXCEL_FAKER_FIELDS,
    BorderStyle,
    Cell,
    CellStyle,
    Column,
    CompanyProfile,
    DocumentType,
    DistressOptions,
    ExcelDoc,
    ExcelPlan,
    FigurePlacement,
    Sheet,
    SyntheticCompany,
    Table,
    industry_list,
)
from document_gen.models.company import condensed_industry_list

SIZE_RANGES = {
    "small": (10, 49),
    "mid": (50, 499),
    "large": (500, 5000),
}


def _make_company(size: str = "mid") -> SyntheticCompany:
    return SyntheticCompany(
        name="Acme Corp",
        industry="Retail",
        description="A fictional retailer.",
        headquarters="Springfield, Illinois",
        size=size,
    )


class TestSyntheticCompany:
    @pytest.mark.parametrize("size", ["small", "mid", "large"])
    def test_employee_count(self, size: str) -> None:
        company = _make_company(size)
        low, high = SIZE_RANGES[size]
        assert low <= company.employees <= high

    def test_invalid_size_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_company("gigantic")

    def test_serialization_round_trip(self) -> None:
        company = _make_company()
        restored = SyntheticCompany.model_validate_json(company.model_dump_json())
        assert restored.name == company.name
        assert restored.size == company.size
        # employees is SkipJsonSchema, so it is excluded from the JSON schema
        assert "employees" not in company.model_json_schema()["properties"]

    def test_format_prompt_contains_all_fields(self) -> None:
        company = _make_company()
        formatted = company.format_prompt()
        for field in SyntheticCompany.model_fields:
            assert f"{field}:" in formatted


class TestCompanyProfile:
    def test_defaults(self) -> None:
        profile = CompanyProfile()
        assert profile.profile is None
        assert profile.reports == []
        assert 0 <= profile.seed <= 99_999

    def test_serialization_round_trip(self) -> None:
        profile = CompanyProfile(
            profile=_make_company(),
            reports=[
                DocumentType(
                    name="New-Hire Onboarding Guide",
                    category="Onboarding",
                    purpose="Annual disclosure",
                )
            ],
        )
        restored = CompanyProfile.model_validate_json(profile.model_dump_json())
        assert restored.profile.name == profile.profile.name
        assert restored.reports[0].name == "New-Hire Onboarding Guide"
        assert restored.seed == profile.seed


class TestIndustryLists:
    def test_industry_list_non_empty_and_unique(self) -> None:
        assert len(industry_list) > 0
        assert len(industry_list) == len(set(industry_list))

    def test_condensed_list_is_subset(self) -> None:
        assert set(condensed_industry_list) <= set(industry_list)


class TestExcelModels:
    def test_column_headers_default_empty(self) -> None:
        column = Column(headers=[])
        assert column.cells == []
        assert column.headers == []
        assert column.data_type == "str"
        assert column.not_null is False

    def test_full_document_round_trip(self) -> None:
        doc = ExcelDoc(
            doc_schema={"seed": 42, "seed_prompt": "p", "sheets": ["Overview"]},
            sheets=[
                Sheet(
                    name="Overview",
                    tables=[
                        Table(
                            columns=[Column(headers=[])],
                            num_row=1,
                            table_label="T1",
                        )
                    ],
                    cells=[Cell(value=1)],
                )
            ],
            created="2024-01-01T00:00:00",
        )
        restored = ExcelDoc.model_validate_json(doc.model_dump_json())
        assert restored.sheets[0].name == "Overview"
        assert restored.sheets[0].tables[0].table_label == "T1"
        assert restored.sheets[0].cells[0].value == 1


class TestBorderStyle:
    def test_defaults(self) -> None:
        border = BorderStyle()
        assert border.style == "thin"
        assert border.color is None
        assert border.sides == ["top", "bottom", "left", "right"]

    @pytest.mark.parametrize(
        "style", ["thin", "medium", "thick", "hair", "dashed", "dotted"]
    )
    def test_valid_styles(self, style: str) -> None:
        assert BorderStyle(style=style).style == style

    def test_invalid_style_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BorderStyle(style="double")

    @pytest.mark.parametrize("color", ["#999999", "999999", "#ABC", "abc"])
    def test_valid_colors(self, color: str) -> None:
        assert BorderStyle(color=color).color == color

    def test_invalid_color_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BorderStyle(color="red")

    def test_partial_sides(self) -> None:
        border = BorderStyle(sides=["top", "bottom"])
        assert border.sides == ["top", "bottom"]

    def test_unknown_side_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BorderStyle(sides=["top", "middle"])

    def test_empty_sides_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BorderStyle(sides=[])


class TestCellStyle:
    def test_defaults(self) -> None:
        style = CellStyle()
        assert style.font_color is None
        assert style.fill_color is None
        assert style.bold is None
        assert style.italic is None
        assert style.font_size is None
        assert style.number_format is None
        assert style.alignment is None
        assert style.wrap_text is None
        assert style.border is None

    def test_full_style(self) -> None:
        style = CellStyle(
            font_color="#FFFFFF",
            fill_color="1F3A5F",
            bold=True,
            italic=False,
            font_size=11.5,
            number_format="#,##0.00",
            alignment="center",
            wrap_text=True,
            border=BorderStyle(style="medium", sides=["top", "bottom"]),
        )
        assert style.bold is True
        assert style.border is not None
        assert style.border.sides == ["top", "bottom"]

    @pytest.mark.parametrize("alignment", ["left", "center", "right"])
    def test_valid_alignments(self, alignment: str) -> None:
        assert CellStyle(alignment=alignment).alignment == alignment

    def test_invalid_alignment_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CellStyle(alignment="justify")

    @pytest.mark.parametrize("field", ["font_color", "fill_color"])
    def test_invalid_color_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            CellStyle(**{field: "not-a-color"})


class TestFigurePlacement:
    def test_fields(self) -> None:
        placement = FigurePlacement(index=2, anchor="B5")
        assert placement.index == 2
        assert placement.anchor == "B5"

    def test_index_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            FigurePlacement(index=0, anchor="A1")


class TestCellStyling:
    def test_new_fields_default_none(self) -> None:
        cell = Cell(value="x")
        assert cell.style is None
        assert cell.merge_range is None

    def test_with_style_and_merge(self) -> None:
        cell = Cell(
            position="B5",
            value="Note",
            style=CellStyle(wrap_text=True),
            merge_range="B5:E8",
        )
        assert cell.merge_range == "B5:E8"
        assert cell.style is not None
        assert cell.style.wrap_text is True


class TestColumnFakerField:
    def test_defaults(self) -> None:
        column = Column(headers=[])
        assert column.style is None
        assert column.faker_field is None

    def test_whitelist_entries_accepted(self) -> None:
        for field in sorted(EXCEL_FAKER_FIELDS):
            assert Column(faker_field=field).faker_field == field

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Column(faker_field="lorem_words")

    def test_whitelist_has_expected_size(self) -> None:
        assert len(EXCEL_FAKER_FIELDS) == 50

    def test_whitelist_is_frozenset_of_strings(self) -> None:
        assert isinstance(EXCEL_FAKER_FIELDS, frozenset)
        assert all(isinstance(f, str) for f in EXCEL_FAKER_FIELDS)

    def test_whitelist_contains_representative_fields(self) -> None:
        assert {
            "name",
            "company",
            "credit_card_number",
            "date_this_year",
            "isbn13",
            "pyint",
            "pyfloat",
            "word",
        } <= EXCEL_FAKER_FIELDS


class TestTableStyling:
    def test_new_fields_default_none(self) -> None:
        table = Table(columns=[], table_label="T1")
        assert table.header_style is None
        assert table.table_style is None

    def test_with_styles(self) -> None:
        table = Table(
            columns=[Column(headers=[], faker_field="name")],
            num_row=5,
            table_label="Employees",
            header_style=CellStyle(bold=True, fill_color="1F3A5F"),
            table_style=CellStyle(number_format="#,##0"),
        )
        assert table.header_style is not None
        assert table.table_style is not None


class TestSheetFigures:
    def test_figures_default_empty(self) -> None:
        sheet = Sheet(name="Data")
        assert sheet.figures == []

    def test_with_figures(self) -> None:
        sheet = Sheet(name="Data", figures=[FigurePlacement(index=1, anchor="A12")])
        assert sheet.figures[0].anchor == "A12"


class TestDistressOptions:
    def test_defaults(self) -> None:
        options = DistressOptions()
        assert options.enabled is False
        assert options.paper_aging is True
        assert options.vignette is True
        assert options.vignette_strength == 0.3
        assert options.stains is True
        assert options.stain_count == 4
        assert options.noise is True
        assert options.noise_strength == 12.0
        assert options.ink_fade is True
        assert options.blur is True
        assert options.warp is False
        assert options.warp_strength == 0.5
        assert options.seed is None

    @pytest.mark.parametrize(
        ("field", "value", "clamped"),
        [
            ("vignette_strength", -0.5, 0.0),
            ("vignette_strength", 2.0, 1.0),
            ("warp_strength", 5.0, 1.0),
            ("warp_strength", -1.0, 0.0),
            ("noise_strength", -3.0, 0.0),
            ("noise_strength", 100.0, 50.0),
            ("stain_count", -2, 0),
            ("stain_count", 99, 20),
        ],
    )
    def test_range_clamping(self, field: str, value: object, clamped: object) -> None:
        options = DistressOptions(**{field: value})
        assert getattr(options, field) == clamped

    def test_in_range_values_unchanged(self) -> None:
        options = DistressOptions(
            vignette_strength=0.7,
            warp_strength=0.25,
            noise_strength=20.0,
            stain_count=10,
            seed=42,
        )
        assert options.vignette_strength == 0.7
        assert options.warp_strength == 0.25
        assert options.noise_strength == 20.0
        assert options.stain_count == 10
        assert options.seed == 42

    def test_serialization_round_trip(self) -> None:
        options = DistressOptions(enabled=True, stain_count=7, seed=123)
        restored = DistressOptions.model_validate_json(options.model_dump_json())
        assert restored == options


class TestExcelPlan:
    def test_full_plan(self) -> None:
        plan = ExcelPlan(
            design_direction="Corporate finance workbook.",
            palette=["#1F3A5F", "#4A90D9", "#F5F5F5"],
            sheet_names=["Overview", "Sales", "Inventory"],
            table_density="compact",
            notes="Emphasize totals rows.",
        )
        assert plan.table_density == "compact"
        assert plan.sheet_names[0] == "Overview"

    def test_density_defaults_standard(self) -> None:
        plan = ExcelPlan(
            design_direction="d",
            palette=["#111111", "#222222", "#333333"],
            sheet_names=["S1"],
        )
        assert plan.table_density == "standard"
        assert plan.notes == ""

    @pytest.mark.parametrize("density", ["compact", "standard", "spacious"])
    def test_valid_densities(self, density: str) -> None:
        plan = ExcelPlan(
            design_direction="d",
            palette=["#111111", "#222222", "#333333"],
            sheet_names=["S1"],
            table_density=density,
        )
        assert plan.table_density == density

    def test_invalid_density_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExcelPlan(
                design_direction="d",
                palette=["#111111", "#222222", "#333333"],
                sheet_names=["S1"],
                table_density="cramped",
            )

    def test_empty_palette_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExcelPlan(design_direction="d", palette=[], sheet_names=["S1"])

    def test_non_hex_palette_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExcelPlan(
                design_direction="d",
                palette=["#1F3A5F", "blue", "#F5F5F5"],
                sheet_names=["S1"],
            )


class TestExcelStylingRoundTrip:
    def test_full_document_round_trip(self) -> None:
        doc = ExcelDoc(
            doc_schema={"seed": 7, "seed_prompt": "p", "sheets": ["Data"]},
            sheets=[
                Sheet(
                    name="Data",
                    tables=[
                        Table(
                            columns=[
                                Column(
                                    headers=[Cell(value="Name")],
                                    data_type="str",
                                    faker_field="name",
                                    style=CellStyle(number_format="@"),
                                ),
                                Column(
                                    headers=[Cell(value="Amount")],
                                    data_type="float",
                                    faker_field="pyfloat",
                                ),
                            ],
                            num_row=3,
                            table_label="Sales",
                            header_style=CellStyle(bold=True, fill_color="1F3A5F"),
                            table_style=CellStyle(
                                border=BorderStyle(style="thin", sides=["bottom"])
                            ),
                        )
                    ],
                    cells=[
                        Cell(
                            position="A1",
                            value="Notes",
                            style=CellStyle(wrap_text=True),
                            merge_range="A1:D3",
                        )
                    ],
                    figures=[FigurePlacement(index=1, anchor="F5")],
                )
            ],
            created="2024-01-01T00:00:00",
        )
        restored = ExcelDoc.model_validate_json(doc.model_dump_json())
        table = restored.sheets[0].tables[0]
        assert table.columns[0].faker_field == "name"
        assert table.columns[0].style is not None
        assert table.header_style is not None
        assert table.header_style.bold is True
        assert table.table_style is not None
        assert table.table_style.border is not None
        assert restored.sheets[0].cells[0].merge_range == "A1:D3"
        assert restored.sheets[0].figures[0].index == 1

    def test_plan_round_trip(self) -> None:
        plan = ExcelPlan(
            design_direction="d",
            palette=["#1F3A5F", "#4A90D9"],
            sheet_names=["S1", "S2"],
        )
        restored = ExcelPlan.model_validate_json(plan.model_dump_json())
        assert restored == plan
