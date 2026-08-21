"""LLM prompt templates used across the generation pipeline."""

synthetic_company_prompt = """\
**Task**

**Create a fictional (synthetic) company** that belongs to the industry '<user_input>'.

Your output must include the following items in the order listed below.

1. **Comprehensive description** -- 3-5 sentences giving a full overview of
   the company's core business, the products or services it offers, and its
   age or legacy (e.g., "Founded in 1998, the company has 25 years of
   experience in delivering...").
2. **Headquarters** -- a U.S. city (state optional but recommended).
3. **Industry** -- the industry you provided '<user_input>'.
4. **Size** -- a single word that characterises the company's scale:
   "small", "mid", or "large". The size must be inferred from the
   description and should reflect the company's employee count, market
   reach, and overall footprint.

**Formatting**
- Use clear headings for each item ('description:', 'headquarters:',
  'size:', 'industry:').
- All details must be fictional; do not reference real companies or real
  data.
- Maintain a professional, concise tone.

**Example JSON Output**
```json
{
    "description": "Aurora BioTech is a cutting-edge provider of personalized
    genomic therapies, offering precision-driven diagnostics, gene editing
    solutions, and post-treatment monitoring to hospitals and research
    institutions worldwide. Founded in 2012, the company has cultivated a
    reputation for speed and accuracy, having launched over 150 clinical
    trials across 30 countries. With a team of leading scientists and a
    proprietary AI platform, Aurora BioTech continues to innovate at the
    intersection of biology and technology.",
    "headquarters": "Boston, Massachusetts",
    "size": "mid",
    "industry": "Biotechnology"
}
```
"""

generate_document_types = """\
You are a document planning assistant.

Task: From the company profile below, list the documents and reports the
company could produce to communicate about its operations, products,
services, or data (e.g. guides, reports, analyses, onboarding material,
flyers, case studies, training manuals, KPI/metrics reports).

The user has requested the following kind of document(s):
<document_request>

Ground the list in the company profile and focus it on the requested
document type(s). The user's request defines the kind of document; the
user's instructions take priority over any default preference in this
prompt. Never substitute a different, more data-oriented document for
what was requested (e.g. do not turn a menu into a sales report). Where
natural, prefer documents that can include numbers, metrics, or tables
of figures about the company's operations, but only if that does not
distort the requested document type.

Input
<user_input>

Output
A bullet-point list of exactly <num_documents> possible documents (e.g.,
new-employee onboarding guide, quarterly operations report, market
analysis, product flyer, customer case study, training manual, annual
impact report, KPI dashboard report, etc.) that the company might
produce.

Example
- New-employee onboarding guide
- Quarterly operations & KPI report
- Market & competitor analysis
- Product/service flyer
- Customer case study
- Training manual
"""


generate_data_label = """
For companies that are in <user_input>,
Generate me a sample list of data labels with a detailed description describing them along with data type.  Also include
a short verbose description as well. These data labels are what the company uses to measure and report on its operations, e.g. product inventory, sales, usage, headcount, growth, customer health etc..
"""

document_content_prompt = """\
You are a professional document writer for a fictional (synthetic)
company.

Company profile
<company_profile>

Document to draft
<document_type>

Additional user instructions
<user_input>

Task
Draft the full content of the requested document for this company, in
**markdown**.

Requirements
1. <toc>
2. Write the sections and subsections appropriate to the document type
   (for example, an onboarding guide would cover company overview,
   first-week schedule, tools and accounts, and culture; a market
   analysis would cover landscape, market sizing, competitor comparison,
   and outlook; a flyer would be a short, punchy one-page summary of a
   product, service, or event).
3. Ground every quantitative claim in **sample data tables** (markdown
   tables). The sample data must be:
   - internally consistent (totals add up, percentages are coherent,
     periods align),
   - plausible for the company's industry, size, and description.
4. Follow the additional user instructions when given; they take
   priority over everything else in this prompt, including the
   document type if the user requests a different kind of document.
   Never substitute a different, more data-oriented document for what
   was requested.
5. All data is fictional; do not reference real companies or real data.

Figures
<figures>

Formatting
- Markdown only. Use `#` for the document title, `##` for sections and
  `###` for subsections. No code fences around the document.
- The document title (the single `#` heading on the first line) must be
  a **concise document name**: 3-6 words that capture what this
  specific document is (e.g. "Q3 Guest Departure Summary"), not the full
  document type name and not a generic word like "Document".
- Professional, concise tone.
"""

document_plan_prompt = """\
You are planning the layout and design of a company document before it
is drafted.

Company profile
<company_profile>

Document to plan
<document_type>

Generation options
- Quick doc (short, fast document): <quick_doc>
- Figures to include: <figures>
- Additional user instructions: <user_input>

Task
Decide two things:

1. **Table of contents**: should the document start with a TOC?
   - Quick docs and short documents (roughly one or two main sections,
     e.g. a one-page flyer or a single-metric report) do NOT
     need a TOC.
   - Long, multi-section documents (e.g. a multi-chapter guide, a
     training manual, or an annual report with many sections and
     subsections) DO need a TOC.
   - Figures make a document longer, so a document with several figures
     leans toward including a TOC.

2. **Design brief**: a distinctive visual identity for this specific
   document, derived from the company's industry, size, and the
   document's category and purpose. Choose:
   - `design_direction`: 1-2 sentences describing the visual identity
     (mood, header/letterhead treatment, overall feel),
   - `palette`: 3-5 hex colors (e.g. "#1F3A5F") that fit the company
     and document type. Do not default to plain black and white unless
     it genuinely fits,
   - `typography`: a font pairing using only web-safe / system font
     families (e.g. "serif headings, sans-serif body"),
   - `layout_style`: a short label such as "corporate", "modern
     minimal", "editorial", or "playful".

The design must be appropriate for a professional printed document and
should differ from document to document.
"""

document_figures_prompt = """\
Document content (markdown)
<markdown>

Allowed figure types
<figure_types>

Task
Extract 1-2 figures that should illustrate the sample data tables in the
markdown above. Use only values that appear verbatim in the markdown
tables; do not invent, round, or recompute numbers.

For each figure provide:
- `kind`: one of the allowed figure types,
- `title`: a short caption (e.g. "Units sold by region, 2021-2024"),
- `labels`: the row labels of the underlying table,
- `series`: one entry per numeric column, each with a `name` and the
  column's `values` in row order,
- optional `x_label` / `y_label` axis labels.

Keep `labels` short so chart axis labels do not overlap: prefer at most
12-15 characters and abbreviate long names (e.g. "Mental/Emotional" not
"Mental and Emotional Wellbeing").

Output only the figures; if the tables do not support any of the allowed
figure types, output an empty list.
"""

document_html_system_prompt = """\
You are a document designer. You convert document markdown into a single
standalone HTML document that reads as a **professional company
document** (a report, guide, analysis, or flyer). Each document gets
its own visual identity, driven by the design brief in the task — do
not settle for one generic template look for every document.

Hard layout rules (always obey)
1. Paper format: declare it explicitly with the CSS `@page` rule:
   `@page { size: A4 portrait; margin: 2cm; }`. The page must be A4 and
   portrait, every time.
2. No fixed pixel widths that assume a screen or a different paper ratio
   (never `width: 900px` on body, main, tables, or page containers).
   Use `100%`, `%`, `cm`, `mm`, `pt`, or `em` instead.
3. Scale proportionally: base font size in `pt` (10-11pt body), tables
   `width: 100%` (use `table-layout: fixed` where columns need control),
   `page-break-inside: avoid` on tables and figures, `page-break-before`
   on major sections.
4. Styling: use the **design brief** given in the task (design
   direction, color palette, typography, layout style) to give this
   document a distinctive visual identity. Vary colors, the
   header/letterhead treatment (company name and address should appear
   somewhere on the first page), table styling, and section headers
   from document to document — do not reuse the same generic look for
   everything. The result must still look professional, cohesive, and
   print-friendly. A document title block (title, date, and optionally
   a reference number) is expected, and a footer with the page number
   via `@page { @bottom-center { content: counter(page) " of " counter(pages); } }`
   is recommended.
5. Single standalone document: all CSS in one `<style>` block in the
   `<head>`. No external assets, no JavaScript, no image files, no
   inline SVG — any decoration must be drawn with CSS (borders, rules,
   backgrounds). Figures (charts, graphs, plots) are the only exception:
   they arrive pre-rendered. Place the given `{{FIGURE_n}}` placeholder
   tokens verbatim in the document; they are replaced with embedded
   chart images after you finish. Never draw figures yourself.

Content rules
- Preserve ALL content, headings, and tables from the given markdown.
- Do not invent, add, or remove data.
- Fenced ```chart / ```plot / ```graph blocks in the markdown are figure
  declarations, not content: do not render them as code blocks. Instead
  place the matching `{{FIGURE_n}}` placeholder (in the order listed in
  the task) near the table or section the figure illustrates.
- Output the complete HTML document only (from `<!DOCTYPE html>` to
  `</html>`), with no commentary and no code fences.
"""

# ---------------------------------------------------------------------------
# Quick-doc prompt set: smaller, more concise variants used when the user
# requests a quick doc. They focus on content generation rather than
# formatting: no TOC, at most one data table, at most two figures, and a
# minimal (single-accent-color) HTML style.
# ---------------------------------------------------------------------------

quick_document_plan_prompt = """\
You are planning a **quick** company document: short, fast, and focused
on content rather than design.

Company profile
<company_profile>

Document to plan
<document_type>

Figures to include: <figures>
Additional user instructions: <user_input>

Task
1. **Table of contents**: quick documents do NOT need a TOC; set
   `include_toc` to false.
2. **Design brief**: keep it minimal. Pick one accent color that fits
   the company's industry (plus black and white) for `palette`, a plain
   web-safe font pairing for `typography`, and "quick minimal" for
   `layout_style`.
"""

quick_document_content_prompt = """\
You are drafting a **quick** company document for a fictional company.
Focus on content, not formatting: keep it short and to the point.

Company profile
<company_profile>

Document to draft
<document_type>

Additional user instructions
<user_input>

Task
Write a short markdown document (1-3 sections, roughly one page):
1. First line: a `#` title — 3-6 words naming this specific document.
2. Cover the essentials of the document type in concise prose.
3. Include at most one small sample data table (markdown) with
   internally consistent, plausible, fictional numbers.
4. Follow the additional user instructions when given; they take
   priority over everything else in this prompt, including the
   document type if the user requests a different kind of document.
   Never substitute a different, more data-oriented document for what
   was requested.

No table of contents, no code fences around the document. All data is
fictional.

Figures
<figures>
"""

quick_document_figures_prompt = """\
Document content (markdown)
<markdown>

Allowed figure types
<figure_types>

Task
Pick at most 1 figure illustrating the tables above, using only values
that appear verbatim in the markdown. For each figure provide `kind` (one
of the allowed types), `title`, `labels` (the table's row labels), and
`series` (one entry per numeric column with `name` and `values` in row
order). Keep `labels` short (at most 12-15 characters; abbreviate long
names) so chart axis labels do not overlap. Output only the figures; an
empty list is fine.
"""

quick_document_html_system_prompt = """\
You convert document markdown into a single standalone HTML document.
Keep styling minimal and content-focused.

Rules
1. A4 portrait: `@page { size: A4 portrait; margin: 2cm; }`.
2. No fixed pixel widths (use `100%`, `%`, `cm`, or `em`); body font
   10-11pt; tables `width: 100%`.
3. Plain, professional styling: one accent color for headings, simple
   table borders. No JavaScript, no external assets, no inline SVG.
4. Preserve ALL content, headings, and tables from the markdown; do not
   invent, add, or remove data.
5. Place the given `{{FIGURE_n}}` placeholder tokens verbatim where the
   figures belong; never draw figures yourself.
6. Output the complete HTML document only (from `<!DOCTYPE html>` to
   `</html>`), with no commentary and no code fences.
"""

quick_document_html_prompt = """\
Company (for a simple header with the company name)
<company_profile>

Design brief (use only its first color as an accent)
<design_brief>

Document content (markdown)
<markdown>

Figures to include
<figures>

Convert the markdown above into a single standalone HTML document. Keep
the styling minimal and plain (A4 portrait `@page` rule, no fixed pixel
widths); use only the first color from the design brief as an accent for
headings. Preserve every section and table exactly, and place each figure
placeholder listed above verbatim near the table it illustrates.
"""

# ---------------------------------------------------------------------------
# Excel prompt set: workbook-level plan, data-table-focused markdown draft,
# and the markdown -> ExcelDoc JSON styling step. Quick docs reuse the same
# prompts (the pipeline only cuts the markdown token cap and disables
# model thinking).
# ---------------------------------------------------------------------------

excel_plan_prompt = """\
You are planning the structure and design of a company **Excel workbook**
before its content is drafted.

Company profile
<company_profile>

Workbook to plan
<document_type>

Generation options
- Simple sheets mode (no cover sheet, at most 4 sheets, 1-2 simple
  tables per sheet, no figures): <simple_sheets>
- Glossary sheet (a single lookup sheet defining abbreviated terms):
  <glossary>
- Figures to include: <figures>
- Additional user instructions: <user_input>

Task
Produce a workbook-level design plan:

1. **Sheet names**: the ordered list of worksheet names for the workbook.
   - Simple sheets mode: at most 4 sheets, each holding 1-2 simple data
     tables.
   - Otherwise: a "Cover" sheet, a "Glossary" sheet when the glossary
     option is on, then one or more data sheets named after the
     workbook's main topics.
   Names become worksheet tab names: keep them short (31 characters max)
   and free of the characters []:*?/\\.
2. **Design direction**: 1-2 sentences describing the workbook's visual
   identity (mood, header treatment, overall feel), derived from the
   company's industry, size, and the workbook's purpose.
3. **Palette**: 3-5 hex colors (e.g. "#1F3A5F") that fit the company and
   workbook type. Do not default to plain black and white unless it
   genuinely fits.
4. **Table density**: "compact", "standard", or "spacious" — how densely
   data tables should be packed on each sheet.
5. **Notes**: guidance for the styling stage (layout, emphasis, callouts,
   figure placement).
"""

excel_content_prompt = """\
You are a professional document writer for a fictional (synthetic)
company, drafting the content of an **Excel workbook**.

Company profile
<company_profile>

Workbook to draft
<document_type>

Additional user instructions
<user_input>

Mode
<mode>

Task
Draft the workbook content in **markdown**, focused on **data tables**:
each markdown table in your draft becomes one Excel table.

1. First line: a `#` title — 3-6 words naming this specific workbook
   (e.g. "Q3 Sales by Region Workbook"), not a generic word like
   "Document".
2. Write concise prose sections (`##` / `###`) that frame the data; keep
   the prose short — the tables are the substance of the workbook.
3. Ground every section in **sample data tables** (markdown tables) that
   are:
   - internally consistent (totals add up, percentages are coherent,
     periods align),
   - plausible for the company's industry, size, and description,
   - built from clear single-row header labels that will become Excel
     column headers (no compound or merged header text inside the
     markdown).
   Keep column headers and line items clear and self-explanatory; use
   abbreviated terms only when the mode instructions direct you to.
4. Follow the additional user instructions when given; they take
   priority over your default section choices.
5. All data is fictional; do not reference real companies or real data.

Figures
<figures>

Formatting
- Markdown only. No code fences around the document.
- Professional, concise tone.
"""

excel_styling_prompt = """\
You are an Excel workbook designer. Convert the markdown draft below into
a complete **ExcelDoc** JSON document that a renderer will turn into an
.xlsx file.

Company (for the cover sheet and workbook properties)
<company_profile>

Workbook
<document_type>

Design plan
<design_brief>

Document content (markdown)
<markdown>

Figures to place (each entry has a 1-based index)
<figures>

Mode
<mode>

Allowed Faker fields (the ONLY valid values for a column's `faker_field`)
<faker_fields>

Task
Produce the full ExcelDoc JSON:
- `doc_schema`: `seed_prompt` (a short descriptor of the workbook) and
  `sheets` (the ordered sheet names),
- `title`, `creator` (the company name), `created` (an ISO datetime),
  `version`, `keywords`,
- `sheets`: one entry per worksheet, with `name`, `tables`, `cells`,
  `hidden`, `sheet_descriptor`, and `figures`.

Sheets
- Default mode: start with a **Cover** sheet (workbook title, company
  info, and a short description / table of contents as standalone `cells`
  with real `value`s; use `merge_range` + `wrap_text` for paragraph
  blocks). Then the data sheets.
- Glossary sheet: include it only when the mode instructions say to.
  It is a single lookup sheet (never repeated per sheet) defining the
  abbreviated terms used in the workbook — one row per term as a
  two-column table (`Term` | `Definition`) with real `value`s (e.g.
  `TOTAL_SV` — total sale value for the current year).
- Simple mode: data sheets only — no cover sheet.

Data tables (values come from the markdown — do NOT invent new data)
- Each markdown table becomes a `Table` with `upper_left_position`,
  `table_label`, `num_row` (the number of data rows), and one `Column`
  per markdown header.
- Each column carries `headers` (1-2 header cells) and `data_type`
  (str/int/float/datetime).
- Domain-specific columns — anything the markdown already specifies
  (line items, categories, amounts, ratios, periods, terms, notes, etc.):
  copy the markdown's data-row values VERBATIM into `cells` as real
  `Cell` values, typed to match `data_type` (numbers as int/float, not
  strings). Leave `faker_field` as `null`.
- Generic personal/contact columns — data that needs no domain
  specifics (person names, addresses, phone numbers, emails, account or
  ID numbers, dates of birth, and the like): Leave each column's
  `cells` as `[]` and set `faker_field` to the best-matching allowed
  Faker field above; a downstream Faker step fills those cells
  deterministically.

Styling
- Apply the design plan: header fills from the palette (with a
  contrasting `font_color`), `table_style` borders, number formats
  (`#,##0.00`, `yyyy-mm-dd`, currency), alignment, and
  `table_density`-appropriate spacing between blocks.
- Loose annotation blocks: standalone sheet `cells` with a real `value`,
  a `merge_range` (e.g. "B6:E9"), and `wrap_text` for paragraph notes.
- Figures: for each figure listed above, add a `FigurePlacement` on the
  matching sheet (`index` = the figure's 1-based index, `anchor` = a cell
  below or beside the table the figure illustrates).
"""

document_html_prompt = """\
Company (for the letterhead)
<company_profile>

Design brief
<design_brief>

Document content (markdown)
<markdown>

Figures to include
<figures>

Convert the markdown above into a single standalone HTML document for
this company, following the layout rules you were given (A4 portrait
`@page` rule, no fixed pixel widths, proportional scaling) and the
design brief above (palette, typography, layout style). Preserve every
section, subsection, and table from the markdown exactly, and place each
figure placeholder listed above verbatim near the table or section it
illustrates.
"""

# ---------------------------------------------------------------------------
# Image prompt set: single-page image documents. Same pipeline shape as
# the PDF set (plan -> markdown -> figures -> HTML+CSS) but everything
# must fit on exactly one page: no TOC ever, at most one data table, at
# most one figure, and no page furniture (headers, footers, page
# numbers). The <page_size> slot is "A4 portrait" or "content-sized
# (auto)".
# ---------------------------------------------------------------------------

image_content_prompt = """\
You are a professional document writer for a fictional (synthetic)
company, drafting the content of a **single-page image document**:
the finished document must fit on exactly one printed page.

Company profile
<company_profile>

Document to draft
<document_type>

Additional user instructions
<user_input>

Task
Draft the full content of the requested document for this company, in
**markdown**.

Requirements
1. Keep it concise: a title plus **3-5 short sections** (one or two
   sentences each), no subsections and **no table of contents**.
2. Include **at most one** sample data table (markdown). The sample
   data must be:
   - internally consistent (totals add up, percentages are coherent,
     periods align),
   - plausible for the company's industry, size, and description.
3. Follow the additional user instructions when given; they take
   priority over everything else in this prompt, including the
   document type if the user requests a different kind of document.
   Never substitute a different, more data-oriented document for what
   was requested.
4. All data is fictional; do not reference real companies or real data.

Figures
<figures>

Formatting
- Markdown only. Use `#` for the document title, `##` for sections.
  No code fences around the document.
- The document title (the single `#` heading on the first line) must be
  a **concise document name**: 3-6 words that capture what this
  specific document is (e.g. "Q3 Guest Departure Summary"), not the full
  document type name and not a generic word like "Document".
- Professional, concise tone.
"""

image_html_system_prompt = """\
You are a document designer. You convert document markdown into a single
standalone HTML document for a **single-page image document**: the whole
document must fit on exactly **one page**, with no room to spare.

Hard layout rules (always obey)
1. Page size: <page_size>. The exact `@page` size is enforced after you
   finish, but design for it: everything on one page — no page breaks,
   no `page-break-*` rules, no running headers or footers,
   no page numbers, no `@page` margin-box content (page counters, etc.).
2. No fixed pixel widths that assume a screen or a different page ratio
   (never `width: 900px` on body, main, tables, or page containers).
   Use `100%`, `%`, `cm`, `mm`, `pt`, or `em` instead.
3. Compact type scale: 9-10pt body, tight (but readable) margins and
   paragraph spacing, small section headings, tables `width: 100%` with
   compact rows. The content must not overflow the page.
4. Styling: use the **design brief** given in the task (design
   direction, color palette, typography, layout style) to give this
   document a distinctive visual identity. A compact title block (title,
   date, and optionally a reference number) is expected, and the company
   name should appear somewhere (e.g. a small letterhead line). The
   result must still look professional, cohesive, and print-friendly.
5. Single standalone document: all CSS in one `<style>` block in the
   `<head>`. No external assets, no JavaScript, no image files, no
   inline SVG — any decoration must be drawn with CSS (borders, rules,
   backgrounds). Figures (charts, graphs, plots) are the only exception:
   they arrive pre-rendered. Place the given `{{FIGURE_n}}` placeholder
   tokens verbatim in the document; they are replaced with embedded
   chart images after you finish. Never draw figures yourself.

Content rules
- Preserve ALL content, headings, and tables from the given markdown.
- Do not invent, add, or remove data.
- Fenced ```chart / ```plot / ```graph blocks in the markdown are figure
  declarations, not content: do not render them as code blocks. Instead
  place the matching `{{FIGURE_n}}` placeholder (in the order listed in
  the task) near the table or section the figure illustrates.
- Output the complete HTML document only (from `<!DOCTYPE html>` to
  `</html>`), with no commentary and no code fences.
"""

image_html_prompt = """\
Company (for the letterhead)
<company_profile>

Design brief
<design_brief>

Document content (markdown)
<markdown>

Figures to include
<figures>

Convert the markdown above into a single standalone HTML document for
this company, following the layout rules you were given (everything on
one page, no page breaks or page furniture, compact type scale) and the
design brief above (palette, typography, layout style). Preserve every
section and table from the markdown exactly, and place each figure
placeholder listed above verbatim near the table it illustrates.
"""
