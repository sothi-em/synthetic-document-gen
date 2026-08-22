"""Pydantic model for PNG document distress (scanned/aged look) options.

Each boolean flag maps to one effect in the distress pipeline
(``document_gen.generators.png_gen.distress_image``). Effects without a
dedicated numeric parameter additionally carry a 0-1 ``*_intensity``
field that scales the effect's augraphy parameters; a ``None``
intensity resolves from the flag (on -> 1.0, off -> 0.0), so payloads
with only booleans (old saved options) render exactly as before.
Values are clamped to their documented ranges rather than rejected, so
over-specified inputs still produce a valid render (intensity fields
are the exception: out-of-range 0-1 values are rejected).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _intensity_field() -> Field:
    """Field factory for the 0-1 per-effect intensity fields."""
    return Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "0-1 strength scaling the effect's augraphy parameters; "
            "None resolves to 1.0 when the flag is on, 0.0 when off."
        ),
    )


#: Default watermark word (an empty ``watermark_word`` means "random").
_WATERMARK_DEFAULT_WORD = "CONFIDENTIAL"

#: Effects that carry a 0-1 ``*_intensity`` field (i.e. every effect
#: without a dedicated numeric parameter). Used by
#: :meth:`DistressOptions._resolve_intensities`.
_INTENSITY_EFFECTS: tuple[str, ...] = (
    # ink phase
    "ink_fade",
    "ink_bleed",
    "bleed_through",
    "letterpress",
    "ink_mottling",
    "ink_color_swap",
    "hollow",
    "dithering",
    "dot_matrix",
    "low_ink_periodic_lines",
    "low_ink_random_lines",
    "lines_degradation",
    # paper phase
    "paper_aging",
    "noise_texturize",
    "brightness_texturize",
    "watermark",
    "pattern_generator",
    "voronoi_tessellation",
    "delaunay_tessellation",
    "paper_factory",
    # post phase + tail stages
    "bad_photo_copy",
    "faxify",
    "dirty_drum",
    "dirty_rollers",
    "dirty_screen",
    "shadow_cast",
    "lens_flare",
    "reflected_light",
    "brightness",
    "gamma",
    "color_shift",
    "depth_blur",
    "moire",
    "lcd_pattern",
    "double_exposure",
    "bindings",
    "markup",
    "scribbles",
    "blur",
)


class DistressOptions(BaseModel):
    """Per-effect controls for the PNG distress pass.

    With ``enabled=False`` the pipeline skips the pass entirely and the
    PNG is left as a perfect render. ``backend`` selects the rendering
    engine: ``"augraphy"`` (default) runs the augraphy augmentation
    pipeline, where ``seed`` drives the whole pipeline (so stain
    positions are reproducible); ``"legacy"`` runs the preserved
    pre-augraphy hand-rolled stage sequence, where stain positions are
    intentionally random on every run unless a stain seed is given and
    the augraphy-only toggles are ignored. When ``seed`` is ``None``
    the pipeline falls back to the company seed.

    Each augraphy effect is gated on ``flag and intensity > 0`` and its
    augraphy parameters are scaled by the resolved 0-1 intensity, so an
    explicit 0 turns an on-flagged effect off without changing the
    boolean. In the web UI each effect is a single slider that writes the
    intensity (or the dedicated numeric parameter) and derives the flag
    from it (0 = off).
    """

    enabled: bool = Field(
        description="Master switch; False = perfect (undistressed) image.",
        default=False,
    )
    backend: Literal["augraphy", "legacy"] = Field(
        description=(
            'Rendering engine: "augraphy" (default) runs the augraphy '
            'augmentation pipeline; "legacy" runs the preserved '
            "pre-augraphy hand-rolled stages (augraphy-only toggles are "
            "no-ops there)."
        ),
        default="augraphy",
    )
    paper_aging: bool = Field(description="Cream/beige paper tint.", default=True)
    paper_aging_intensity: float | None = _intensity_field()
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
    ink_fade_intensity: float | None = _intensity_field()
    blur: bool = Field(
        description="Subtle global blur mimicking scanner focus loss.",
        default=True,
    )
    blur_intensity: float | None = _intensity_field()
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

    # --- ink phase (augraphy backend) ---
    ink_bleed: bool = Field(
        description="Ink bleeding into the paper fibers.", default=False
    )
    ink_bleed_intensity: float | None = _intensity_field()
    bleed_through: bool = Field(
        description="Faint show-through of the back side.", default=False
    )
    bleed_through_intensity: float | None = _intensity_field()
    letterpress: bool = Field(
        description="Subtle letterpress-style texture in the ink.", default=False
    )
    letterpress_intensity: float | None = _intensity_field()
    ink_mottling: bool = Field(
        description="Patchy, mottled ink density (low-ink cartridge).", default=False
    )
    ink_mottling_intensity: float | None = _intensity_field()
    ink_color_swap: bool = Field(
        description="Swap the ink to a different color.", default=False
    )
    ink_color_swap_intensity: float | None = _intensity_field()
    hollow: bool = Field(
        description="Hollowed-out (eroded) strokes in the ink.", default=False
    )
    hollow_intensity: float | None = _intensity_field()
    dithering: bool = Field(
        description="Floyd-Steinberg dithering of the ink.", default=False
    )
    dithering_intensity: float | None = _intensity_field()
    dot_matrix: bool = Field(
        description="Dot-matrix printer rendering of the ink.", default=False
    )
    dot_matrix_intensity: float | None = _intensity_field()
    low_ink_periodic_lines: bool = Field(
        description="Periodic vertical low-ink streaks.", default=False
    )
    low_ink_periodic_lines_intensity: float | None = _intensity_field()
    low_ink_random_lines: bool = Field(
        description="Random low-ink streaks.", default=False
    )
    low_ink_random_lines_intensity: float | None = _intensity_field()
    lines_degradation: bool = Field(
        description="Broken/degraded horizontal lines in the ink.", default=False
    )
    lines_degradation_intensity: float | None = _intensity_field()

    # --- paper phase (augraphy backend) ---
    noise_texturize: bool = Field(
        description="Turbulent paper grain texture.", default=False
    )
    noise_texturize_intensity: float | None = _intensity_field()
    brightness_texturize: bool = Field(
        description="Low-frequency paper brightness variation.", default=False
    )
    brightness_texturize_intensity: float | None = _intensity_field()
    watermark: bool = Field(
        description=f"Diagonal watermark word (default '{_WATERMARK_DEFAULT_WORD}').",
        default=False,
    )
    watermark_intensity: float | None = _intensity_field()
    watermark_word: str = Field(
        description="Watermark word (stripped, max 40 chars; empty = random).",
        default=_WATERMARK_DEFAULT_WORD,
    )
    pattern_generator: bool = Field(
        description="Faint geometric background pattern.", default=False
    )
    pattern_generator_intensity: float | None = _intensity_field()
    voronoi_tessellation: bool = Field(
        description="Voronoi tessellation texture on the paper.", default=False
    )
    voronoi_tessellation_intensity: float | None = _intensity_field()
    delaunay_tessellation: bool = Field(
        description="Delaunay tessellation texture on the paper.", default=False
    )
    delaunay_tessellation_intensity: float | None = _intensity_field()
    paper_factory: bool = Field(
        description="Generated paper texture (slowest augmentation).", default=False
    )
    paper_factory_intensity: float | None = _intensity_field()

    # --- post phase (augraphy backend) ---
    bad_photo_copy: bool = Field(
        description="Noisy photo-of-a-copy artifacts.", default=False
    )
    bad_photo_copy_intensity: float | None = _intensity_field()
    faxify: bool = Field(
        description="Fax machine resampling/halftone artifacts.", default=False
    )
    faxify_intensity: float | None = _intensity_field()
    dirty_drum: bool = Field(
        description="Vertical streaks from a dirty printer drum.", default=False
    )
    dirty_drum_intensity: float | None = _intensity_field()
    dirty_rollers: bool = Field(
        description="Broad bands from dirty printer rollers.", default=False
    )
    dirty_rollers_intensity: float | None = _intensity_field()
    dirty_screen: bool = Field(
        description="Speckle pattern from a dirty print screen.", default=False
    )
    dirty_screen_intensity: float | None = _intensity_field()
    shadow_cast: bool = Field(
        description="Soft shadow cast over part of the page.", default=False
    )
    shadow_cast_intensity: float | None = _intensity_field()
    lens_flare: bool = Field(
        description="Lens flare from photographing the document.", default=False
    )
    lens_flare_intensity: float | None = _intensity_field()
    reflected_light: bool = Field(
        description="Elliptical reflected-light highlight.", default=False
    )
    reflected_light_intensity: float | None = _intensity_field()
    brightness: bool = Field(description="Global brightness shift.", default=False)
    brightness_intensity: float | None = _intensity_field()
    gamma: bool = Field(description="Global gamma (contrast) shift.", default=False)
    gamma_intensity: float | None = _intensity_field()
    color_shift: bool = Field(
        description="Channel misregistration (RGB shift).", default=False
    )
    color_shift_intensity: float | None = _intensity_field()
    depth_blur: bool = Field(
        description="Simulated depth-of-field blur.", default=False
    )
    depth_blur_intensity: float | None = _intensity_field()
    moire: bool = Field(description="Moire interference pattern.", default=False)
    moire_intensity: float | None = _intensity_field()
    lcd_pattern: bool = Field(
        description="LCD screen pixel pattern (photo of a screen).", default=False
    )
    lcd_pattern_intensity: float | None = _intensity_field()
    jpeg_artifacts: bool = Field(
        description="JPEG recompression artifacts.", default=False
    )
    jpeg_quality: int = Field(
        description="Target JPEG quality for the artifacts (10-95).",
        default=50,
    )
    double_exposure: bool = Field(description="Ghosted double exposure.", default=False)
    double_exposure_intensity: float | None = _intensity_field()
    folding: bool = Field(description="Paper fold creases.", default=False)
    fold_count: int = Field(description="Number of fold creases (1-6).", default=2)
    bindings: bool = Field(
        description="Binding/fastener overlays (staples, holes, clips).",
        default=False,
    )
    bindings_intensity: float | None = _intensity_field()
    markup: bool = Field(
        description="Handwritten-style markup lines over the document.",
        default=False,
    )
    markup_intensity: float | None = _intensity_field()
    scribbles: bool = Field(
        description="Scribbles/doodles over the document.", default=False
    )
    scribbles_intensity: float | None = _intensity_field()

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

    @field_validator("jpeg_quality")
    @classmethod
    def _clamp_jpeg_quality(cls, value: int) -> int:
        """Clamp JPEG quality into 10-95."""
        return min(max(value, 10), 95)

    @field_validator("fold_count")
    @classmethod
    def _clamp_fold_count(cls, value: int) -> int:
        """Clamp fold count into 1-6."""
        return min(max(value, 1), 6)

    @field_validator("watermark_word")
    @classmethod
    def _clean_watermark_word(cls, value: str) -> str:
        """Strip the watermark word and cap it at 40 characters."""
        return value.strip()[:40]

    @model_validator(mode="after")
    def _resolve_intensities(self) -> DistressOptions:
        """Resolve ``None`` intensities from their flags.

        Old saved payloads carry only the booleans: an on flag resolves
        to 1.0 (today's look) and an off flag to 0.0, so they render
        exactly as before. Explicit intensities are preserved as given.
        """
        for name in _INTENSITY_EFFECTS:
            if getattr(self, f"{name}_intensity") is None:
                setattr(self, f"{name}_intensity", 1.0 if getattr(self, name) else 0.0)
        return self
