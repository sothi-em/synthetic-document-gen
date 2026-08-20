"""Pydantic models for synthetic company profiles."""

from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

# List of industries we can generate a synthetic company from
industry_list: list[str] = [
    "Agriculture",
    "Construction",
    "Education",
    "Energy",
    "Entertainment",
    "Finance",
    "Food & Beverage",
    "Healthcare",
    "Hospitality",
    "Information Technology",
    "Industrial Manufacturing",
    "Logistics & Transportation",
    "Mining & Metals",
    "Oil & Gas",
    "Pharmaceuticals",
    "Real Estate",
    "Retail",
    "Telecommunications",
    "Textiles",
    "Transportation",
    "Utilities",
    "Waste Management",
    "Advertising & Marketing",
    "Biotechnology",
    "Chemicals",
    "E-commerce",
    "Legal Services",
    "Public Sector",
    "Publishing",
    "Sport & Recreation",
    "Travel & Tourism",
]

condensed_industry_list: list[str] = [
    "Agriculture",
    "Construction",
    "Education",
    "Finance",
    "Food & Beverage",
    "Healthcare",
    "Hospitality",
    "Information Technology",
    "Industrial Manufacturing",
    "Oil & Gas",
    "Pharmaceuticals",
    "Real Estate",
    "Retail",
    "Telecommunications",
    "Chemicals",
    "E-commerce",
    "Publishing",
    "Sport & Recreation",
]


class DocumentType(BaseModel):
    """A company-specific document type."""

    name: str = Field(description="Name or title of document specific to the company.")
    category: str = Field(
        description=(
            "Type of document (e.g. Guide, Report, Analysis, Onboarding, "
            "Marketing, Operational, Training)."
        )
    )
    purpose: str = Field(description="Key purpose of the document.")
    user_input: str | None = Field(
        default=None,
        description=(
            "User-provided context (free-text request) that guided the "
            "generation of this document type, if any."
        ),
    )


#: Employee-count range per company size, used to derive ``employees``.
EMPLOYEE_COUNT_RANGES: dict[str, tuple[int, int]] = {
    "small": (10, 49),
    "mid": (50, 499),
    "large": (500, 5000),
}


class SyntheticCompany(BaseModel):
    """Model representing a synthetic company."""

    name: str = Field(description="Name of the company.")
    industry: str = Field(description="The industry or field this company operates in.")
    description: str = Field(description="Complete description about the company.")
    headquarters: str = Field(
        description="U.S. city in which this company headquarters is located."
    )
    size: Literal["small", "mid", "large"] = Field(
        description="String descriptor of the size of this company."
    )
    employees: SkipJsonSchema[int] = Field(
        description="The number of employees at this company.", default=0
    )

    @model_validator(mode="after")
    def _gen_employee_count(self) -> SyntheticCompany:
        """Derive an employee count range based on company size."""
        lo, hi = EMPLOYEE_COUNT_RANGES[self.size]
        self.employees = random.randint(lo, hi)
        return self

    def format_prompt(self) -> str:
        """Return a consolidated string for prompt injection.

        Returns:
            A newline-delimited summary of all model fields.
        """
        return "\n".join(
            f"{field}: {getattr(self, field)}" for field in type(self).model_fields
        )


class CompanyProfile(BaseModel):
    """Store company profile and associated report types."""

    profile: SyntheticCompany | None = Field(
        description="Company description and high level profile.", default=None
    )
    reports: list[DocumentType] = Field(
        description="List of documents specific to the company.", default_factory=list
    )
    seed: int = Field(
        description=(
            "Seed for randomized generation. Passed to the LLM (used when "
            "deterministic mode is enabled, where the same seed reproduces "
            "the generated profile and document types) and also embedded "
            "in the prompt so different seeds yield distinct companies."
        ),
        default_factory=lambda: random.randint(0, 99_999),
    )
    user_input: str | None = Field(
        default=None,
        description=(
            "User-provided context (e.g. the requested industry) that "
            "guided the generation of this company, if any."
        ),
    )


class CompanyDataLabel(BaseModel):
    """Store a company data label (a metric the company measures/reports)."""

    shorten_description: str = Field(
        description="Concise label description of what this data is eg. (Total presale over expected sale)"
    )
    description: str = Field(
        description="Detailed description of what this data label is. Eg. (Total cash spent on company outing relating to team building)."
    )
    data_type: Literal["Text", "Integer", "Date", "Float", "Boolean"]
    tags: list[str] = Field(
        description="List of tags associated with this data label. eg. (sales, inventory, headcount, usage, growth, monthly)"
    )
