"""Pydantic models for Excel document structures.

Nested hierarchy:

ExcelDoc
    Sheet
        Table
            Column
                Cell
        Cell (standalone)
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator

#: Whitelisted Faker field names that may drive column value generation.
#:
#: Plain strings (no faker import in the model); verified against Faker
#: 40.11.1's flat API (note Faker 40 exposes ``f.word()``, not
#: ``f.lorem.word()``). ``bank`` was dropped: it raises in the default
#: locale.
EXCEL_FAKER_FIELDS: frozenset[str] = frozenset(
    {
        # People (15)
        "name",
        "first_name",
        "last_name",
        "prefix",
        "suffix",
        "job",
        "passport_gender",
        "phone_number",
        "email",
        "address",
        "street_address",
        "city",
        "postcode",
        "country",
        "date_of_birth",
        # Company (5)
        "company",
        "company_email",
        "company_suffix",
        "catch_phrase",
        "bs",
        # Finance (8)
        "credit_card_number",
        "credit_card_expire",
        "pricetag",
        "iban",
        "swift11",
        "currency_name",
        "currency_code",
        "ein",
        # Codes & IDs (10)
        "isbn13",
        "upc_a",
        "ean13",
        "license_plate",
        "uuid4",
        "user_name",
        "mac_address",
        "ipv4",
        "pyint",
        "pyfloat",
        # Dates & times (8)
        "date",
        "date_this_year",
        "date_this_decade",
        "date_time",
        "date_time_this_year",
        "time",
        "day_of_week",
        "month_name",
        # Text (4)
        "word",
        "words",
        "sentence",
        "user_agent",
    }
)

_HEX_DIGITS = set("0123456789abcdefABCDEF")
_BORDER_SIDES = {"top", "bottom", "left", "right"}


def _is_hex_color(value: str) -> bool:
    """Return True if ``value`` is a hex color (``#`` optional)."""
    body = value[1:] if value.startswith("#") else value
    return len(body) in (3, 4, 6, 8) and set(body) <= _HEX_DIGITS


class ExcelCell(BaseModel):
    """Base cell for all cell types."""

    position: str | None = Field(
        description="Position of the cell in Excel format (A1, B3, etc.).",
        default=None,
    )


class BorderStyle(BaseModel):
    """Border configuration for a cell.

    ``color`` accepts hex with or without a leading ``#``; ``sides`` is a
    subset of ``top``/``bottom``/``left``/``right`` (default: all four).
    """

    style: Literal["thin", "medium", "thick", "hair", "dashed", "dotted"] = Field(
        description="Border line style.", default="thin"
    )
    color: str | None = Field(
        description="Border color as hex (e.g. '999999' or '#999999').",
        default=None,
    )
    sides: list[str] = Field(
        description="Border sides to apply (subset of top/bottom/left/right).",
        default_factory=lambda: ["top", "bottom", "left", "right"],
    )

    @field_validator("color")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        """Ensure the border color is a hex string when present."""
        if value is not None and not _is_hex_color(value):
            raise ValueError(f"not a hex color: {value!r}")
        return value

    @field_validator("sides")
    @classmethod
    def _validate_sides(cls, value: list[str]) -> list[str]:
        """Ensure sides is a non-empty subset of the four border sides."""
        if not value or not set(value) <= _BORDER_SIDES:
            raise ValueError(
                f"sides must be a non-empty subset of {sorted(_BORDER_SIDES)}"
            )
        return value


class CellStyle(BaseModel):
    """Styling for a cell (font, fill, alignment, border)."""

    font_color: str | None = Field(
        description="Font color as hex (e.g. '#FFFFFF').", default=None
    )
    fill_color: str | None = Field(
        description="Cell fill color as hex (e.g. '#1F3A5F').", default=None
    )
    bold: bool | None = Field(description="Bold font.", default=None)
    italic: bool | None = Field(description="Italic font.", default=None)
    font_size: float | None = Field(description="Font size in points.", default=None)
    number_format: str | None = Field(
        description="Excel number format (e.g. '#,##0.00', 'yyyy-mm-dd').",
        default=None,
    )
    alignment: Literal["left", "center", "right"] | None = Field(
        description="Horizontal alignment.", default=None
    )
    wrap_text: bool | None = Field(
        description="Wrap text within the cell.", default=None
    )
    border: BorderStyle | None = Field(
        description="Border configuration.", default=None
    )

    @field_validator("font_color", "fill_color")
    @classmethod
    def _validate_colors(cls, value: str | None) -> str | None:
        """Ensure font/fill colors are hex strings when present."""
        if value is not None and not _is_hex_color(value):
            raise ValueError(f"not a hex color: {value!r}")
        return value


class FigurePlacement(BaseModel):
    """Anchor for a matplotlib figure within a sheet.

    ``index`` is 1-based and matches the order of the figure list produced
    during generation.
    """

    index: int = Field(
        description="1-based index into the document's figure list.",
        ge=1,
    )
    anchor: str = Field(
        description="Cell coordinate to anchor the figure at (e.g. 'A12').",
    )


class Cell(ExcelCell):
    """Represent a single cell in an Excel sheet."""

    value: str | int | float | datetime | date | time | bool | dict | None = Field(
        description="Cell value.", default=None
    )
    style: CellStyle | None = Field(
        description="Styling applied to this cell.", default=None
    )
    merge_range: str | None = Field(
        description="Range to merge starting at this cell (e.g. 'B5:E8').",
        default=None,
    )


class Column(ExcelCell):
    """Represent a table column containing cells ordered top to bottom."""

    cells: list[Cell] = Field(description="Cells in this column.", default_factory=list)
    headers: list[Cell] = Field(
        description="Column labeling. Could contain multiple header labels.",
        default_factory=list,
    )
    data_type: str = Field(
        description="Data type for the column (str, int, float, datetime, etc.).",
        default="str",
    )
    not_null: bool = Field(
        description="Whether the column can contain null values.", default=False
    )
    style: CellStyle | None = Field(
        description="Styling applied to the column's data cells.", default=None
    )
    faker_field: str | None = Field(
        description=(
            "Whitelisted Faker field name driving value generation "
            "(e.g. 'name', 'credit_card_number', 'date_this_year'). "
            f"Must be one of: {', '.join(sorted(EXCEL_FAKER_FIELDS))}. "
            "None = derive from data_type (str -> word, int -> pyint, "
            "float -> pyfloat, datetime -> date_time)."
        ),
        default=None,
    )

    @field_validator("faker_field")
    @classmethod
    def _validate_faker_field(cls, value: str | None) -> str | None:
        """Ensure the faker field is in the whitelist when present."""
        if value is not None and value not in EXCEL_FAKER_FIELDS:
            raise ValueError(f"unknown faker field: {value!r}")
        return value


class Table(BaseModel):
    """Model representing a data table."""

    columns: list[Column] = Field(
        description="Ordered list of columns.", default_factory=list
    )
    num_row: int = Field(description="Number of rows.", default=0)
    upper_left_position: str = Field(
        description="The upper-left corner position of the table.", default="A1"
    )
    table_label: str = Field(description="The labeling for this table.")
    header_style: CellStyle | None = Field(
        description="Styling applied to the table's header cells.", default=None
    )
    table_style: CellStyle | None = Field(
        description=(
            "Styling applied to the table's data cells. Style cascade: "
            "cell > column > table."
        ),
        default=None,
    )


class Sheet(BaseModel):
    """Represent each sheet in the Excel file."""

    name: str = Field(description="Sheet name.")
    tables: list[Table] = Field(
        description="All tables within this sheet.", default_factory=list
    )
    cells: list[Cell] = Field(
        description="Standalone cell data within the sheet.", default_factory=list
    )
    hidden: bool = Field(description="Whether this sheet is hidden.", default=False)
    sheet_descriptor: str | None = Field(
        description="Brief description of what is within the sheet.", default=None
    )
    figures: list[FigurePlacement] = Field(
        description="Figures anchored in this sheet.", default_factory=list
    )


class DocSchema(BaseModel):
    """High-level schema describing how a mock document is generated."""

    seed: int = Field(
        description="The random seed used to generate the document.", default=0
    )
    seed_prompt: str = Field(
        description="Used for LLM to generate various descriptors and metadata."
    )
    sheets: list[str] = Field(
        description="Predefined list of sheets that go into the workbook."
    )


class ExcelPlan(BaseModel):
    """LLM-determined workbook-level design plan for an Excel document."""

    design_direction: str = Field(
        description=(
            "1-2 sentence description of the workbook's visual identity "
            "(mood, header treatment, overall feel)."
        )
    )
    palette: list[str] = Field(
        description="3-5 hex colors (e.g. '#1F3A5F') for the workbook."
    )
    sheet_names: list[str] = Field(
        description="Ordered names for the workbook's sheets."
    )
    table_density: Literal["compact", "standard", "spacious"] = Field(
        description="How densely data tables should be packed.", default="standard"
    )
    notes: str = Field(
        description="Notes for the styling stage (layout, emphasis, callouts).",
        default="",
    )

    @field_validator("palette")
    @classmethod
    def _validate_palette(cls, value: list[str]) -> list[str]:
        """Ensure the palette is non-empty and holds hex color strings."""
        if not value:
            raise ValueError("palette must contain 3-5 hex colors")
        for color in value:
            if not _is_hex_color(color):
                raise ValueError(f"not a hex color: {color!r}")
        return value


class ExcelDoc(BaseModel):
    """Model representing a whole Excel document."""

    doc_schema: DocSchema = Field(
        description="Config on how the document is generated."
    )
    sheets: list[Sheet] = Field(description="Sheets.", default_factory=list)
    creator: str = Field(description="Document creator.", default="")
    title: str = Field(description="Title of the doc (e.g. 'My Report').", default="")
    created: datetime = Field(description="Time the document was originally created.")
    version: str = Field(description="Version of the document.", default="")
    keywords: list[str] = Field(
        description="Keywords associated with this doc.", default_factory=list
    )
