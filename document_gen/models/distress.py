"""Pydantic model for PNG document distress (scanned/aged look) options.

Each boolean flag maps to one effect in the distress pipeline
(``document_gen.generators.png_gen.distress_image``). Values are clamped
to their documented ranges rather than rejected, so over-specified
inputs still produce a valid render.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class DistressOptions(BaseModel):
    """Per-effect controls for the PNG distress pass.

    With ``enabled=False`` the pipeline skips the pass entirely and the
    PNG is left as a perfect render. ``seed`` pins the noise and warp
    stages (stain positions are intentionally random on every run); when
    ``None`` the pipeline falls back to the company seed.
    """

    enabled: bool = Field(
        description="Master switch; False = perfect (undistressed) image.",
        default=False,
    )
    paper_aging: bool = Field(description="Cream/beige paper tint.", default=True)
    vignette: bool = Field(
        description="Uneven lighting / darkened edges.", default=True
    )
    vignette_strength: float = Field(
        description="Vignette factor (0 = none, 1 = heavy dark edges).",
        default=0.3,
    )
    stains: bool = Field(description="Coffee/dirt blobs.", default=True)
    stain_count: int = Field(
        description="Number of stain centers (0 = no stains).", default=4
    )
    noise: bool = Field(description="Scanner grain (Gaussian noise).", default=True)
    noise_strength: float = Field(
        description="Gaussian noise sigma in 0-255 units.", default=12.0
    )
    ink_fade: bool = Field(
        description="Blend text into the paper for a faded-ink look.",
        default=True,
    )
    blur: bool = Field(
        description="Subtle global blur mimicking scanner focus loss.",
        default=True,
    )
    warp: bool = Field(
        description="Subtle feed/lens warp via mesh displacement.",
        default=False,
    )
    warp_strength: float = Field(
        description="Warp displacement magnitude (0 = none).", default=0.5
    )
    seed: int | None = Field(
        description=(
            "Random seed for the noise and warp stages (stain positions "
            "are intentionally unseeded); None = company seed."
        ),
        default=None,
    )

    @field_validator("vignette_strength", "warp_strength")
    @classmethod
    def _clamp_unit(cls, value: float) -> float:
        """Clamp 0-1 strength values into range."""
        return min(max(value, 0.0), 1.0)

    @field_validator("stain_count")
    @classmethod
    def _clamp_stain_count(cls, value: int) -> int:
        """Clamp stain count into 0-20."""
        return min(max(value, 0), 20)

    @field_validator("noise_strength")
    @classmethod
    def _clamp_noise_strength(cls, value: float) -> float:
        """Clamp noise sigma into 0-50."""
        return min(max(value, 0.0), 50.0)
