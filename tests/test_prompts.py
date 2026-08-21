"""Tests for the prompt templates in ``document_gen.prompts``."""

from __future__ import annotations

from document_gen.prompts import (
    excel_content_prompt,
    excel_plan_prompt,
    excel_styling_prompt,
    generate_data_label,
    generate_document_types,
    quick_document_content_prompt,
    quick_document_figures_prompt,
    quick_document_html_prompt,
    quick_document_html_system_prompt,
    quick_document_plan_prompt,
    document_content_prompt,
    document_figures_prompt,
    document_plan_prompt,
    document_html_prompt,
    document_html_system_prompt,
    image_content_prompt,
    image_html_prompt,
    image_html_system_prompt,
    synthetic_company_prompt,
)


def _check_slots(template: str, values: dict[str, str]) -> None:
    """Assert every slot is present and substitution leaves none behind."""
    for slot in values:
        assert slot in template
    rendered = template
    for slot, value in values.items():
        rendered = rendered.replace(slot, value)
    for slot in values:
        assert slot not in rendered


def _check_slots_once(template: str, values: dict[str, str]) -> None:
    """Assert every slot appears exactly once and renders cleanly."""
    for slot in values:
        assert template.count(slot) == 1, f"slot {slot!r} must appear exactly once"
    rendered = template
    for slot, value in values.items():
        rendered = rendered.replace(slot, value)
    for slot in values:
        assert slot not in rendered


class TestPromptTemplates:
    def test_synthetic_company_prompt(self) -> None:
        _check_slots(synthetic_company_prompt, {"<user_input>": "Biotechnology"})

    def test_generate_document_types(self) -> None:
        _check_slots(
            generate_document_types,
            {
                "<user_input>": "profile text",
                "<document_request>": "ESG reports",
                "<num_documents>": "5",
            },
        )

    def test_generate_data_label(self) -> None:
        _check_slots(generate_data_label, {"<user_input>": "Healthcare"})

    def test_document_content_prompt(self) -> None:
        _check_slots(
            document_content_prompt,
            {
                "<company_profile>": "profile text",
                "<document_type>": "Onboarding Guide",
                "<user_input>": "focus on Q3",
                "<figures>": "None. Do not include any figures.",
            },
        )

    def test_document_figures_prompt(self) -> None:
        _check_slots(
            document_figures_prompt,
            {"<markdown>": "# Doc\n| a | b |", "<figure_types>": "bar, line"},
        )

    def test_document_html_prompt(self) -> None:
        _check_slots(
            document_html_prompt,
            {"<company_profile>": "profile text", "<markdown>": "# Doc"},
        )

    def test_document_plan_prompt(self) -> None:
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

    def test_document_html_system_prompt_rules(self) -> None:
        # The hardcoded WeasyPrint rules must be present in the system prompt.
        assert "@page" in document_html_system_prompt
        assert "A4 portrait" in document_html_system_prompt
        assert "pixel" in document_html_system_prompt.lower()
        # Figures arrive pre-rendered: the LLM only places placeholders.
        assert "{{FIGURE_n}}" in document_html_system_prompt
        assert "no\n   inline SVG" in document_html_system_prompt
        assert "draw figures yourself" in document_html_system_prompt


class TestExcelPromptTemplates:
    """The Excel prompt set: plan, content, and styling."""

    def test_excel_plan_prompt(self) -> None:
        _check_slots_once(
            excel_plan_prompt,
            {
                "<company_profile>": "Acme Corp",
                "<document_type>": "name: Quarterly Sales Workbook",
                "<simple_sheets>": "no",
                "<glossary>": "no",
                "<figures>": "bar, line",
                "<user_input>": "None.",
            },
        )
        # The plan prompt hardcodes the simple-sheets sheet-count rule and
        # offers the optional Glossary sheet.
        assert "at most 4 sheets" in excel_plan_prompt
        assert "Glossary" in excel_plan_prompt

    def test_excel_content_prompt(self) -> None:
        _check_slots_once(
            excel_content_prompt,
            {
                "<company_profile>": "profile text",
                "<document_type>": "Quarterly Sales Workbook",
                "<user_input>": "focus on Q3",
                "<figures>": "None. Do not include any figures.",
                "<mode>": "default: cover + data-dictionary + data sheets",
            },
        )
        # The draft is data-table focused: each markdown table -> one Excel table.
        assert "becomes one Excel table" in excel_content_prompt
        # Repeated headers/line items prefer concise abbreviated terms.
        assert "abbreviated terms" in excel_content_prompt

    def test_excel_styling_prompt(self) -> None:
        _check_slots_once(
            excel_styling_prompt,
            {
                "<company_profile>": "profile text",
                "<document_type>": "Quarterly Sales Workbook",
                "<design_brief>": "Modern minimal; palette #1F3A5F, #FFFFFF.",
                "<markdown>": "# Doc\n| a | b |",
                "<figures>": "1. bar: Units by region",
                "<mode>": "default: cover + data-dictionary + data sheets",
                "<faker_fields>": "name, company, pyint, pyfloat, date_this_year",
            },
        )
        # Domain values are transcribed from the markdown; only generic
        # personal/contact columns are left for the downstream Faker step.
        assert "do NOT invent new data" in excel_styling_prompt
        assert "copy the markdown's data-row values VERBATIM" in excel_styling_prompt
        assert "Leave each column's" in excel_styling_prompt
        assert "`cells` as `[]`" in excel_styling_prompt
        # The optional Glossary sheet is a lookup of workbook abbreviations.
        assert "TOTAL_SV" in excel_styling_prompt
        assert "Glossary" in excel_styling_prompt
        # Default mode carries a cover sheet with real prose.
        assert "Cover" in excel_styling_prompt


class TestQuickPromptTemplates:
    """The quick-doc prompt set: smaller, content-focused variants."""

    def test_quick_plan_prompt(self) -> None:
        _check_slots(
            quick_document_plan_prompt,
            {
                "<company_profile>": "Acme Corp",
                "<document_type>": "name: Onboarding Guide",
                "<figures>": "bar",
                "<user_input>": "None.",
            },
        )
        # The quick plan prompt hardcodes the no-TOC / minimal-design
        # policy, so it has no <quick_doc> slot.
        assert "<quick_doc>" not in quick_document_plan_prompt
        assert "do NOT need a TOC" in quick_document_plan_prompt

    def test_quick_content_prompt(self) -> None:
        _check_slots(
            quick_document_content_prompt,
            {
                "<company_profile>": "profile text",
                "<document_type>": "Onboarding Guide",
                "<user_input>": "focus on Q3",
                "<figures>": "None. Do not include any figures.",
            },
        )
        # Quick docs never carry a TOC, so there is no <toc> slot.
        assert "<toc>" not in quick_document_content_prompt

    def test_quick_figures_prompt(self) -> None:
        _check_slots(
            quick_document_figures_prompt,
            {"<markdown>": "# Doc\n| a | b |", "<figure_types>": "bar, line"},
        )
        assert "at most 1 figure" in quick_document_figures_prompt

    def test_quick_html_prompt(self) -> None:
        _check_slots(
            quick_document_html_prompt,
            {
                "<company_profile>": "profile text",
                "<design_brief>": "Modern minimal.",
                "<markdown>": "# Doc",
                "<figures>": "bar",
            },
        )

    def test_quick_html_system_prompt_rules(self) -> None:
        # The hardcoded WeasyPrint rules must still be present.
        assert "@page" in quick_document_html_system_prompt
        assert "A4 portrait" in quick_document_html_system_prompt
        assert "pixel" in quick_document_html_system_prompt.lower()
        assert "{{FIGURE_n}}" in quick_document_html_system_prompt

    def test_quick_prompts_are_smaller_than_full_prompts(self) -> None:
        # The whole point of the quick set: smaller prompts.
        assert len(quick_document_plan_prompt) < len(document_plan_prompt)
        assert len(quick_document_content_prompt) < len(document_content_prompt)
        assert len(quick_document_figures_prompt) < len(document_figures_prompt)
        assert len(quick_document_html_system_prompt) < len(document_html_system_prompt)
        assert len(quick_document_html_prompt) < len(document_html_prompt)


class TestImagePromptTemplates:
    """The single-page image prompt set: content + HTML (system + user)."""

    def test_image_content_prompt(self) -> None:
        _check_slots_once(
            image_content_prompt,
            {
                "<company_profile>": "profile text",
                "<document_type>": "Product Flyer",
                "<user_input>": "focus on Q3",
                "<figures>": "None. Do not include any figures.",
            },
        )
        # Single-page contract: 3-5 short sections, one table, no TOC.
        assert "3-5 short sections" in image_content_prompt
        assert "at most one" in image_content_prompt.lower()
        assert "no table of contents" in image_content_prompt.lower()
        # No <toc> slot: image documents never carry a TOC.
        assert "<toc>" not in image_content_prompt

    def test_image_html_system_prompt(self) -> None:
        _check_slots_once(image_html_system_prompt, {"<page_size>": "A4 portrait"})
        # Everything on one page: no page furniture, compact type scale.
        assert "one page" in image_html_system_prompt
        assert "page numbers" in image_html_system_prompt
        assert "page-break" in image_html_system_prompt
        assert "Compact type scale" in image_html_system_prompt
        # Figures arrive pre-rendered: the LLM only places placeholders.
        assert "{{FIGURE_n}}" in image_html_system_prompt
        assert "draw figures yourself" in image_html_system_prompt

    def test_image_html_prompt(self) -> None:
        _check_slots_once(
            image_html_prompt,
            {
                "<company_profile>": "profile text",
                "<design_brief>": "Modern minimal.",
                "<markdown>": "# Doc",
                "<figures>": "bar",
            },
        )
        # The page size lives in the system prompt, not the user prompt.
        assert "<page_size>" not in image_html_prompt
