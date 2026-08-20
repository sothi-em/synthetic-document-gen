"""Tests for the report plan model and its prompt slots."""

from __future__ import annotations

import pytest

from document_gen.models import DocumentPlan
from document_gen.prompts import (
    document_content_prompt,
    document_html_prompt,
    document_plan_prompt,
)


class TestDocumentPlanModel:
    def test_valid_plan(self) -> None:
        plan = DocumentPlan(
            include_toc=True,
            toc_reason="Long guide.",
            design_direction="Classic regulatory look.",
            palette=["#1F3A5F", "#333333", "#FFFFFF"],
            typography="serif headings, sans-serif body",
            layout_style="corporate",
        )
        assert plan.include_toc is True
        assert len(plan.palette) == 3

    @pytest.mark.parametrize(
        "palette",
        [
            [],
            ["not-a-color"],
            ["#GGGGGG"],
            ["#12345"],  # wrong length
            ["1F3A5F"],  # missing #
        ],
    )
    def test_invalid_palette_raises(self, palette: list[str]) -> None:
        with pytest.raises(Exception):
            DocumentPlan(
                include_toc=False,
                toc_reason="r",
                design_direction="d",
                palette=palette,
                typography="t",
                layout_style="l",
            )

    @pytest.mark.parametrize("color", ["#123", "#1234", "#112233", "#11223344"])
    def test_hex_palette_lengths_accepted(self, color: str) -> None:
        plan = DocumentPlan(
            include_toc=False,
            toc_reason="r",
            design_direction="d",
            palette=[color, "#111111", "#222222"],
            typography="t",
            layout_style="l",
        )
        assert color in plan.palette


def _check_slots(template: str, values: dict[str, str]) -> None:
    """Assert every slot is present and substitution leaves none behind."""
    for slot in values:
        assert slot in template
    rendered = template
    for slot, value in values.items():
        rendered = rendered.replace(slot, value)
    for slot in values:
        assert slot not in rendered


class TestDocumentPlanPrompt:
    def test_slots_and_substitution(self) -> None:
        _check_slots(
            document_plan_prompt,
            {
                "<company_profile>": "Acme Corp",
                "<document_type>": "name: Onboarding Guide",
                "<quick_doc>": "no",
                "<figures>": "bar, line",
                "<user_input>": "None.",
            },
        )


class TestTocSlot:
    @pytest.mark.parametrize(
        ("include", "text"),
        [
            (True, "Start with a **table of contents**"),
            (False, "Do **not** include a table of contents"),
        ],
    )
    def test_toc_variants_substitute(self, include: bool, text: str) -> None:
        assert "<toc>" in document_content_prompt
        rendered = document_content_prompt.replace("<toc>", text)
        assert "<toc>" not in rendered
        assert text in rendered


class TestHtmlPromptDesignBriefSlot:
    def test_design_brief_slot_and_substitution(self) -> None:
        brief = (
            "Design direction: Modern minimal.\n"
            "Color palette: #1F3A5F, #FFFFFF\n"
            "Typography: sans-serif headings, serif body\n"
            "Layout style: modern minimal"
        )
        _check_slots(document_html_prompt, {"<design_brief>": brief})
