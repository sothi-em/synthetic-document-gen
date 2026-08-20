"""Company generation pipeline built on top of the LLM backends."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import Field, create_model
from tqdm import tqdm


from document_gen.models.company import (
    EMPLOYEE_COUNT_RANGES,
    CompanyProfile,
    DocumentType,
    SyntheticCompany,
    industry_list,
)
from document_gen import document_query
from document_gen.llm import get_chat_backend
from document_gen.prompts import generate_document_types, synthetic_company_prompt

DEFAULT_THREADS: int = 20


DEFAULT_DOCUMENT_REQUEST = (
    "Any appropriate documents or reports for the company (guides, reports, "
    "analyses, flyers, etc.), grounded in its operations and data."
)


def _generate_document_types(
    profile: CompanyProfile,
    model_name: str | None,
    document_request: str | None = None,
    num_documents: int = 5,
) -> list[DocumentType]:
    """Generate document types for *profile* from the LLM.

    The company profile and the user's document request are passed to the
    prompt together. The generated list is returned without mutating the
    profile, so callers decide whether to store it (e.g. append it to the
    company's existing documents).

    Args:
        profile: A ``CompanyProfile`` whose ``profile`` field is already set.
        model_name: Optional model ID override for the LLM query.
        document_request: Free-text description of the document type(s) the
            user wants. A generic default is used when ``None``.
        num_documents: Number of document types to ask the model for.

    Returns:
        The newly generated document types.
    """
    ListDocumentModel = create_model(
        "DynamicModel",
        documents=(
            list[DocumentType],
            Field(
                description="List of document types for the company.",
                min_length=max(1, num_documents),
            ),
        ),
    )
    prompt = (
        generate_document_types.replace(
            "<document_request>", document_request or DEFAULT_DOCUMENT_REQUEST
        )
        .replace("<num_documents>", str(num_documents))
        .replace("<user_input>", profile.profile.format_prompt())
    )
    generated_document_list = get_chat_backend().query(
        prompt=prompt,
        model=ListDocumentModel,
        seed=profile.seed,
        deterministic=False,
        model_name=model_name,
    )
    # Record the user-provided context that guided this generation.
    for document in generated_document_list.documents:
        document.user_input = document_request
    return generated_document_list.documents


def generate_company_profile(
    company: CompanyProfile | None = None,
    target_industry: str | None = None,
    log_output: bool = True,
    model_name: str | None = None,
    user_input: str | None = None,
) -> CompanyProfile:
    """Generate a synthetic company profile and its document types.

    If an existing ``CompanyProfile`` is passed in, generation resumes from
    where it left off (missing profile or documents are filled in).

    If *target_industry* is ``None`` or the string ``"random"``, a random
    industry is chosen from :data:`industry_list`.

    Args:
        company: An existing ``CompanyProfile`` to extend. A new one is
            created when ``None``.
        target_industry: Industry to seed the generation with. Only used
            when the profile's company data is missing.
        log_output: When ``True``, print generated data to stdout.
        model_name: Optional model ID override for the LLM query.
        user_input: Optional free-text instruction from the user (e.g.
            "a startup focused on solar storage for rural clinics").
            Appended to the prompt as extra guidance and recorded on the
            profile.

    Returns:
        A populated ``CompanyProfile`` with profile and document types.
    """
    profile = company or CompanyProfile()

    if profile.profile is None:
        if target_industry is None or target_industry == "random":
            target_industry = random.choice(industry_list)
        prompt = synthetic_company_prompt.replace("<user_input>", target_industry)
        instruction = (user_input or "").strip()
        if instruction:
            prompt += (
                "\n**Additional user instructions**\n"
                "The company must also satisfy the following guidance "
                "from the user (in addition to the industry above):\n"
                f"{instruction}\n"
            )
        # Include the seed in the prompt so different companies always get
        # distinct prompts: with identical prompts (same industry +
        # instructions) a low-temperature or deterministic server run could
        # still return identical companies. It also keeps generation
        # reproducible when ``deterministic`` mode is enabled (same seed ->
        # same prompt -> same company).
        prompt += (
            "\n**Variation**\n"
            f"This company is generation seed {profile.seed}. Vary the "
            "company's name, founding year, headquarters, and specific "
            "details so that different seeds produce clearly distinct "
            "companies."
        )
        generated_profile = get_chat_backend().query(
            prompt=prompt,
            model=SyntheticCompany,
            seed=profile.seed,
            deterministic=False,
            model_name=model_name,
        )
        # The model validator derived ``employees`` from the unseeded
        # global RNG; re-derive it from the profile seed so the whole
        # record is reproducible.
        lo, hi = EMPLOYEE_COUNT_RANGES[generated_profile.size]
        generated_profile.employees = random.Random(profile.seed).randint(lo, hi)
        if log_output:
            print(generated_profile.format_prompt())
        profile.profile = generated_profile
        # Record the user-provided context that guided this generation
        # (``None``/``"random"`` means the user gave no context).
        profile.user_input = instruction or (
            target_industry if target_industry not in (None, "random") else None
        )

    if not profile.reports:
        profile.reports = _generate_document_types(profile, model_name)

    if log_output and profile.reports:
        print(profile.model_dump_json(indent=4))

    return profile


def generate_documents_for_company(
    company_id: int,
    document_request: str | None = None,
    model_name: str | None = None,
    force: bool = False,
    num_documents: int = 5,
) -> list[DocumentType]:
    """Generate document types for a stored company, without saving them.

    The company's existing profile is combined with *document_request* as
    prompt input. The generated list is returned for review only — it is
    **not** persisted, so the caller can append it to the company's existing
    documents (generation never replaces stored documents).

    Args:
        company_id: TinyDB ``doc_id`` of the company.
        document_request: Free-text description of the document type(s) the
            user wants. When given, generation always runs. When ``None``,
            generation is skipped if the company already has documents
            (unless *force* is ``True``), and the existing documents are
            returned.
        model_name: Optional model ID override for the LLM query.
        force: When ``True`` and no *document_request* is given, regenerate
            even if the company already has documents.
        num_documents: Number of document types to ask the model for.

    Returns:
        The newly generated document types (or the existing ones when
        generation is skipped).

    Raises:
        ValueError: When no company with *company_id* exists.
    """
    doc = document_query.get_company(company_id)
    if doc is None:
        raise ValueError(f"Company {company_id} not found")
    profile = CompanyProfile(
        profile=SyntheticCompany.model_validate(doc["profile"]),
        reports=[DocumentType.model_validate(r) for r in doc.get("reports") or []],
        seed=doc.get("seed", 0),
    )
    if document_request is None and profile.reports and not force:
        return profile.reports
    return _generate_document_types(
        profile,
        model_name,
        document_request=document_request,
        num_documents=num_documents,
    )


def generate_companies(
    num_company: int = 1,
    num_thread: int = DEFAULT_THREADS,
    target_industry: str | None = None,
    model_name: str | None = None,
) -> list[CompanyProfile]:
    """Generate multiple companies using a thread pool executor.

    Args:
        num_company: Number of companies to generate.
        num_thread: Number of concurrent threads.
        target_industry: Optional industry shared by all generated companies.
        model_name: Optional model ID override for the LLM query.

    Returns:
        A list of generated ``CompanyProfile`` instances.
    """
    results: list[CompanyProfile] = []
    with ThreadPoolExecutor(max_workers=num_thread) as executor:
        futures = {
            executor.submit(
                generate_company_profile,
                target_industry=target_industry,
                log_output=False,
                model_name=model_name,
            ): i
            for i in range(num_company)
        }
        for future in tqdm(as_completed(futures), total=len(futures)):
            results.append(future.result())
    return results
