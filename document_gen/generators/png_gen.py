"""PNG image generator: HTML -> PNG rendering and the distress pass.

- :func:`html_to_png` renders a standalone HTML document to a single PNG
  (WeasyPrint -> PDF -> pypdfium2 raster of page 1; WeasyPrint dropped
  native PNG output in v61).
- :func:`distress_image` post-processes a rendered PNG in-place so it
  looks like a scanned, aged document, driven by
  :class:`document_gen.models.distress.DistressOptions` plus an
  ``effect_seeds`` map (one plain-number seed per effect). The pass runs
  an augraphy ``AugraphyPipeline`` (ink / paper / post phases built from
  the options) plus custom warp/blur tail stages; every augmentation and
  the warp tail stage re-seeds its PRNG stream from its own entry in
  ``effect_seeds`` (missing keys fall back to 0), so each effect's random
  output depends only on its own seed and parameters.
- :func:`distress_image_to_bytes` applies the same pass to PNG bytes in
  memory (no file I/O); :func:`distress_array` is the shared array-level
  core both entry points run.
- :func:`random_effect_seeds` creates a fresh random per-effect seed map
  over the canonical effect-name list (:data:`EFFECT_SEED_NAMES`).

Heavy dependencies (cv2, numpy, weasyprint, pypdfium2) are imported
lazily inside functions to keep package import cheap.
"""

from __future__ import annotations

import logging
import random
import secrets
import tempfile
import threading
from pathlib import Path

from document_gen.models.distress import DistressOptions

logger = logging.getLogger(__name__)

#: Rasterization resolution for HTML -> PNG (points -> pixels).
#: 192 DPI (2x the 96 DPI screen baseline) for crisp text and figures.
_RENDER_SCALE = 192 / 72


def _normalize_bgr(arr):
    """Normalize a decoded image array to 3-channel BGR.

    Grayscale images are converted to BGR; RGBA images are composited
    over white so transparent areas don't go black. Already-BGR arrays
    are returned unchanged.
    """
    import cv2
    import numpy as np

    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
        return (arr[:, :, :3].astype(np.float32) * alpha + 255.0 * (1 - alpha)).astype(
            np.uint8
        )
    return arr


#: Serializes augraphy pipeline build + augment: augraphy's
#: ``random_seed`` seeds the process-global ``random`` module, so
#: concurrent calls would otherwise interleave and break determinism.
_AUGRAPHY_LOCK = threading.Lock()

#: One-time guard for :func:`_patch_augraphy`.
_AUGRAPHY_PATCHED = False


def _patch_augraphy() -> None:
    """Apply one-time workarounds for augraphy bugs.

    - ``InkGenerator.generate_noise_clusters``: the highlighter ink path
      (``Markup`` with ``markup_ink="highlighter"``, reachable via the
      default ``"random"`` selection) builds ``std_range`` from
      ``np.ceil`` (float64) and passes it to ``random.randint``, which
      raises ``TypeError: 'numpy.float64' object cannot be interpreted
      as an integer``. Coerce the range to ints (still unpatched in
      augraphy 8.2.6, the latest 8.x release).
    - ``TextureGenerator.generate_dot_granular_texture`` /
      ``generate_rough_granular_texture`` (reachable via ``PaperFactory``
      with its default ``texture_type="random"``): the granule size
      bounds come from
      ``np.floor``/``np.ceil`` (float64) and are passed to
      ``random.randint``, the same ``TypeError``. Coerce the bounds to
      ints for the duration of the call.
    """
    global _AUGRAPHY_PATCHED
    if _AUGRAPHY_PATCHED:
        return
    from augraphy.utilities.inkgenerator import InkGenerator
    from augraphy.utilities.texturegenerator import TextureGenerator

    original = InkGenerator.generate_noise_clusters

    def generate_noise_clusters(
        self, image, n_clusters=(200, 200), n_samples=(300, 300), std_range=(5, 10)
    ):
        return original(
            self, image, n_clusters, n_samples, (int(std_range[0]), int(std_range[1]))
        )

    InkGenerator.generate_noise_clusters = generate_noise_clusters

    def guard_float_randint(method):
        """Run *method* with an int-coercing ``random.randint`` installed."""

        def wrapper(self, *args, **kwargs):
            original_randint = random.randint
            random.randint = lambda a, b: original_randint(int(a), int(b))
            try:
                return method(self, *args, **kwargs)
            finally:
                random.randint = original_randint

        return wrapper

    TextureGenerator.generate_dot_granular_texture = guard_float_randint(
        TextureGenerator.generate_dot_granular_texture
    )
    TextureGenerator.generate_rough_granular_texture = guard_float_randint(
        TextureGenerator.generate_rough_granular_texture
    )
    _AUGRAPHY_PATCHED = True


#: Canonical list of every effect that draws from a PRNG stream: one
#: entry per augraphy augmentation in
#: :func:`_build_augraphy_pipeline` (tagged by its ``DistressOptions``
#: flag name) plus the random warp tail stage. This is the contract for
#: ``effect_seeds`` maps: the trace/frontend share these names.
#:
#: Excluded deterministic effects: ``ink_fade`` (only scales the
#: pipeline ``overlay_alpha``), ``blur`` (Gaussian kernel), and
#: ``dithering`` (the floyd-steinberg path makes no random draws;
#: augraphy only uses the PRNG for the "random"/"ordered" dither
#: types, which we do not use).
EFFECT_SEED_NAMES: tuple[str, ...] = (
    # ink phase
    "ink_bleed",
    "bleed_through",
    "letterpress",
    "ink_mottling",
    "ink_color_swap",
    "hollow",
    "dot_matrix",
    "low_ink_periodic_lines",
    "low_ink_random_lines",
    "lines_degradation",
    # paper phase
    "paper_aging",
    "vignette",
    "stains",
    "noise_texturize",
    "brightness_texturize",
    "watermark",
    "pattern_generator",
    "voronoi_tessellation",
    "delaunay_tessellation",
    "paper_factory",
    # post phase
    "bad_photo_copy",
    "noise",
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
    "jpeg_artifacts",
    "double_exposure",
    "folding",
    "bindings",
    "markup",
    "scribbles",
    # tail stage
    "warp",
)


def random_effect_seeds() -> dict[str, int]:
    """Create a fresh random per-effect seed map.

    One plain random number per effect in :data:`EFFECT_SEED_NAMES`
    (not derived from anything else), so each effect's random output is
    independent of the others.

    Returns:
        A dict mapping every canonical effect name to a random seed in
        ``[0, 0x7FFFFFFF)`` (safe for ``cv2.setRNGSeed``).
    """
    return {name: secrets.randbelow(0x7FFFFFFF) for name in EFFECT_SEED_NAMES}


#: Fixed pipeline-level augraphy seed. Each effect re-seeds its own PRNG
#: stream from its ``effect_seeds`` entry before running, so this value
#: only covers the rare pipeline-internal randomness no effect owns.
_PIPELINE_SEED = 0


class _SeededAugmentation:
    """Wrap an augraphy augmentation in its own per-effect PRNG stream.

    augraphy seeds one set of process-global PRNGs per pipeline run, so
    every effect draws from a single shared stream in pipeline order; an
    upstream effect's draws (and how many it consumes) shift where
    downstream effects draw theirs, and changing one effect's parameters
    re-randomizes the others. This wrapper re-seeds ``random``,
    ``np.random``, and cv2's RNG with the effect's own seed before
    delegating, so each effect's random output depends only on its own
    seed and parameters.

    Relies on augraphy's ``apply_phase`` call pattern: ``should_run()``
    then ``__call__(image=..., layer=..., mask=..., keypoints=...,
    bounding_boxes=..., force=True)`` per object in the phase list. The
    pipeline never passes a mask/keypoints/bounding_boxes into our
    effects, so the inner augmentation always returns a bare image
    array (never a result tuple), which ``apply_phase`` handles for
    non-:class:`Augmentation` objects.
    """

    def __init__(self, augmentation, effect_name: str, seed: int) -> None:
        self.augmentation = augmentation
        self.effect_name = effect_name
        self.seed = seed

    def _reseed(self) -> None:
        """Seed the three global PRNGs with this effect's seed."""
        import cv2
        import numpy as np

        random.seed(self.seed)
        np.random.seed(self.seed)
        cv2.setRNGSeed(self.seed)

    def should_run(self) -> bool:
        """Re-seed this effect's stream, then delegate to the inner check."""
        self._reseed()
        return self.augmentation.should_run()

    def __call__(self, *args, **kwargs):
        """Delegate to the inner augmentation (stream already seeded)."""
        return self.augmentation(*args, **kwargs)


def _build_augraphy_pipeline(
    options: DistressOptions, effect_seeds: dict[str, int], h: int, w: int
):
    """Build a fresh augraphy :class:`AugraphyPipeline` from *options*.

    One augmentation is appended per effect gated on
    ``flag and intensity > 0``, with each effect's augraphy parameters
    scaled by its resolved 0-1 intensity (``i``; an explicit 0 turns an
    on-flagged effect off, and ``i=1`` reproduces the original fixed
    ranges). Scalable effects scale their strength/count/alpha ranges;
    non-scalable effects (no continuous parameter) use ``p=i``
    (deterministic under the pipeline seed). Each augmentation is wrapped
    in a :class:`_SeededAugmentation` that re-seeds the global PRNGs with
    a per-effect derived seed before running, so changing one effect's
    parameters never moves another effect's random output. Phase lists:

    - ink: ink bleed, bleed-through, letterpress, ink mottling, ink
      color swap, hollow, dithering, dot matrix, low-ink lines
      (periodic/random), line degradation.
    - paper: paper aging tint, vignette (lighting gradient), stains,
      noise/brightness texturize, watermark, pattern, Voronoi/Delaunay
      tessellations, paper factory.
    - post: photo-copy (perlin noise type, see below), scanner grain, fax / drum
      / roller / screen artifacts, shadow, lens flare, reflected light,
      brightness / gamma / color shift, depth blur, moire, LCD pattern,
      JPEG artifacts, double exposure, folding, bindings, markup,
      scribbles.

    ``ink_fade`` is not an augmentation: it lowers the pipeline-level
    ``overlay_alpha`` (ink-to-paper blend) from 1.0 toward 0.85 by its
    intensity.

    Args:
        options: Distress options.
        effect_seeds: Per-effect seed map (see :data:`EFFECT_SEED_NAMES`);
            each augmentation is wrapped in a
            :class:`_SeededAugmentation` re-seeded from its own entry
            (missing keys fall back to 0).
        h: Image height in pixels (vignette light position).
        w: Image width in pixels (vignette light position).

    Returns:
        A fresh ``AugraphyPipeline`` (deterministic under the
        ``effect_seeds`` map; a fresh instance is required per call), or
        ``None`` when no augmentation is enabled in any phase.
    """
    import augraphy as ag

    _patch_augraphy()

    def _i(name: str) -> float:
        """Resolved intensity for *name* (0-1; 0-10 count for
        markup and scribbles)."""
        return getattr(options, f"{name}_intensity")

    def _add(phase, name: str, aug) -> None:
        """Append *aug* to *phase* tagged with its effect flag name."""
        phase.append((name, aug))

    ink_phase: list[tuple[str, object]] = []
    if options.ink_bleed and (i := _i("ink_bleed")) > 0:
        _add(
            ink_phase,
            "ink_bleed",
            ag.InkBleed(intensity_range=(0.1 * i, 0.4 * i), kernel_size=(5, 5), p=1.0),
        )
    if options.bleed_through and (i := _i("bleed_through")) > 0:
        _add(
            ink_phase,
            "bleed_through",
            ag.BleedThrough(intensity_range=(0.1 * i, 0.4 * i), alpha=0.3 * i, p=1.0),
        )
    if options.letterpress and (i := _i("letterpress")) > 0:
        _add(
            ink_phase,
            "letterpress",
            ag.Letterpress(
                n_samples=(round(20 * i), round(60 * i)),
                n_clusters=(round(20 * i), round(60 * i)),
                std_range=(1500, 5000),
                value_range=(200, 255),
                p=1.0,
            ),
        )
    if options.ink_mottling and (i := _i("ink_mottling")) > 0:
        _add(
            ink_phase,
            "ink_mottling",
            ag.InkMottling(ink_mottling_alpha_range=(0.1 * i, 0.3 * i), p=1.0),
        )
    if options.ink_color_swap and (i := _i("ink_color_swap")) > 0:
        _add(ink_phase, "ink_color_swap", ag.InkColorSwap(ink_swap_color="random", p=i))
    if options.hollow and (i := _i("hollow")) > 0:
        _add(
            ink_phase,
            "hollow",
            ag.Hollow(hollow_median_kernel_value_range=(71, 101), p=i),
        )
    if options.dithering and (i := _i("dithering")) > 0:
        _add(
            ink_phase,
            "dithering",
            ag.Dithering(dither="floyd-steinberg", order=(2, 4), p=i),
        )
    if options.dot_matrix and (i := _i("dot_matrix")) > 0:
        _add(
            ink_phase,
            "dot_matrix",
            ag.DotMatrix(
                dot_matrix_shape="random",
                dot_matrix_dot_width_range=(round(3 * i), round(8 * i)),
                dot_matrix_dot_height_range=(round(3 * i), round(8 * i)),
                p=1.0,
            ),
        )
    if options.low_ink_periodic_lines and (i := _i("low_ink_periodic_lines")) > 0:
        _add(
            ink_phase,
            "low_ink_periodic_lines",
            ag.LowInkPeriodicLines(
                count_range=(round(2 * i), round(5 * i)),
                period_range=(10, 30),
                p=1.0,
            ),
        )
    if options.low_ink_random_lines and (i := _i("low_ink_random_lines")) > 0:
        _add(
            ink_phase,
            "low_ink_random_lines",
            ag.LowInkRandomLines(count_range=(round(5 * i), round(10 * i)), p=1.0),
        )
    if options.lines_degradation and (i := _i("lines_degradation")) > 0:
        _add(
            ink_phase,
            "lines_degradation",
            ag.LinesDegradation(line_split_probability=(0.2 * i, 0.4 * i), p=1.0),
        )

    paper_phase: list[tuple[str, object]] = []
    if options.paper_aging and (i := _i("paper_aging")) > 0:
        _add(
            paper_phase,
            "paper_aging",
            ag.ColorPaper(
                hue_range=(28, 45),
                saturation_range=(round(10 * i), round(40 * i)),
                p=1.0,
            ),
        )
    if options.vignette:
        _add(
            paper_phase,
            "vignette",
            ag.LightingGradient(
                light_position=(w // 2, h // 2),
                max_brightness=255,
                min_brightness=round(255 * (1 - options.vignette_strength)),
                mode="gaussian",
                p=1.0,
            ),
        )
    if options.stains and options.stain_count > 0:
        _add(
            paper_phase,
            "stains",
            ag.Stains(
                stains_type="random",
                stains_blend_method="darken",
                stains_blend_alpha=min(0.2 + 0.04 * options.stain_count, 0.9),
                p=1.0,
            ),
        )
    if options.noise_texturize and (i := _i("noise_texturize")) > 0:
        _add(
            paper_phase,
            "noise_texturize",
            ag.NoiseTexturize(
                sigma_range=(round(3 * i), round(10 * i)),
                turbulence_range=(round(2 * i), round(5 * i)),
                p=1.0,
            ),
        )
    if options.brightness_texturize and (i := _i("brightness_texturize")) > 0:
        _add(
            paper_phase,
            "brightness_texturize",
            ag.BrightnessTexturize(
                texturize_range=(1.0 - 0.15 * i, 0.99), deviation=0.08, p=1.0
            ),
        )
    if options.watermark and (i := _i("watermark")) > 0:
        _add(
            paper_phase,
            "watermark",
            ag.WaterMark(
                watermark_word=options.watermark_word or "random",
                watermark_font_size=(round(10 * i), round(15 * i)),
                watermark_rotation=(0, 360),
                watermark_method="darken",
                p=1.0,
            ),
        )
    if options.pattern_generator and (i := _i("pattern_generator")) > 0:
        _add(
            paper_phase,
            "pattern_generator",
            ag.PatternGenerator(color="random", alpha_range=(0.25 * i, 0.4 * i), p=1.0),
        )
    if options.voronoi_tessellation and (i := _i("voronoi_tessellation")) > 0:
        _add(
            paper_phase,
            "voronoi_tessellation",
            ag.VoronoiTessellation(
                mult_range=(50, 80),
                num_cells_range=(round(500 * i), round(1000 * i)),
                p=1.0,
            ),
        )
    if options.delaunay_tessellation and (i := _i("delaunay_tessellation")) > 0:
        _add(
            paper_phase,
            "delaunay_tessellation",
            ag.DelaunayTessellation(
                n_points_range=(round(500 * i), round(800 * i)), p=1.0
            ),
        )
    if options.paper_factory and (i := _i("paper_factory")) > 0:
        _add(paper_phase, "paper_factory", ag.PaperFactory(generate_texture=1, p=i))

    post_phase: list[tuple[str, object]] = []
    if options.bad_photo_copy and (i := _i("bad_photo_copy")) > 0:
        # noise_type=3 (perlin): the default -1 picks randomly from
        # 1-4, and the worley kernel (type 4) crashes numba 0.67's
        # compiler with a bare AssertionError at JIT time in this
        # environment (reproduces in a fresh process, independent of
        # augmentation order). Types 1-3 compile and run fine.
        _add(
            post_phase,
            "bad_photo_copy",
            ag.BadPhotoCopy(
                noise_type=3,
                noise_side="random",
                noise_sparsity=(0.1 * i, 0.4 * i),
                noise_concentration=(0.1 * i, 0.4 * i),
                p=1.0,
            ),
        )
    if options.noise:
        _add(
            post_phase,
            "noise",
            ag.SubtleNoise(subtle_range=max(1, int(options.noise_strength)), p=1.0),
        )
    if options.faxify and (i := _i("faxify")) > 0:
        _add(post_phase, "faxify", ag.Faxify(scale_range=(1.0, 1.0 + 0.25 * i), p=1.0))
    if options.dirty_drum and (i := _i("dirty_drum")) > 0:
        _add(
            post_phase,
            "dirty_drum",
            ag.DirtyDrum(line_concentration=0.1 * i, line_width_range=(1, 4), p=1.0),
        )
    if options.dirty_rollers and (i := _i("dirty_rollers")) > 0:
        _add(
            post_phase,
            "dirty_rollers",
            ag.DirtyRollers(line_width_range=(round(8 * i), round(12 * i)), p=1.0),
        )
    if options.dirty_screen and (i := _i("dirty_screen")) > 0:
        _add(
            post_phase,
            "dirty_screen",
            ag.DirtyScreen(
                n_clusters=(round(50 * i), round(100 * i)),
                n_samples=(round(2 * i), round(20 * i)),
                p=1.0,
            ),
        )
    if options.shadow_cast and (i := _i("shadow_cast")) > 0:
        _add(
            post_phase,
            "shadow_cast",
            ag.ShadowCast(
                shadow_side="random",
                shadow_opacity_range=(0.2 * i, 0.5 * i),
                shadow_blur_kernel_range=(101, 201),
                p=1.0,
            ),
        )
    if options.lens_flare and (i := _i("lens_flare")) > 0:
        _add(
            post_phase,
            "lens_flare",
            ag.LensFlare(
                lens_flare_location="random", lens_flare_size=(0.5 * i, 3 * i), p=1.0
            ),
        )
    if options.reflected_light and (i := _i("reflected_light")) > 0:
        _add(
            post_phase,
            "reflected_light",
            ag.ReflectedLight(
                reflected_light_internal_max_brightness_range=(1.0 - 0.1 * i, 1.0),
                p=1.0,
            ),
        )
    if options.brightness and (i := _i("brightness")) > 0:
        _add(
            post_phase,
            "brightness",
            ag.Brightness(brightness_range=(1.0 - 0.1 * i, 1.0 + 0.1 * i), p=1.0),
        )
    if options.gamma and (i := _i("gamma")) > 0:
        _add(
            post_phase,
            "gamma",
            ag.Gamma(gamma_range=(1.0 - 0.2 * i, 1.0 + 0.2 * i), p=1.0),
        )
    if options.color_shift and (i := _i("color_shift")) > 0:
        _add(
            post_phase,
            "color_shift",
            ag.ColorShift(
                color_shift_offset_x_range=(round(3 * i), round(5 * i)),
                color_shift_offset_y_range=(round(3 * i), round(5 * i)),
                p=1.0,
            ),
        )
    if options.depth_blur and (i := _i("depth_blur")) > 0:
        _add(
            post_phase,
            "depth_blur",
            ag.DepthSimulatedBlur(
                blur_major_axes_length_range=(round(120 * i), round(200 * i)),
                blur_minor_axes_length_range=(round(120 * i), round(200 * i)),
                p=1.0,
            ),
        )
    if options.moire and (i := _i("moire")) > 0:
        _add(
            post_phase,
            "moire",
            ag.Moire(moire_density=(15, 20), moire_blend_alpha=0.1 * i, p=1.0),
        )
    if options.lcd_pattern and (i := _i("lcd_pattern")) > 0:
        _add(
            post_phase,
            "lcd_pattern",
            ag.LCDScreenPattern(
                pattern_type="random", pattern_overlay_alpha=0.3 * i, p=1.0
            ),
        )
    # Quality 100 is the off point: the round-trip would be near-
    # invisible, so skip the stage (also covers unvalidated copies where
    # the model validator did not clear the flag).
    if options.jpeg_artifacts and options.jpeg_quality < 100:
        _add(
            post_phase,
            "jpeg_artifacts",
            ag.Jpeg(
                quality_range=(
                    max(10, options.jpeg_quality - 15),
                    options.jpeg_quality,
                ),
                p=1.0,
            ),
        )
    if options.double_exposure and (i := _i("double_exposure")) > 0:
        _add(
            post_phase,
            "double_exposure",
            ag.DoubleExposure(
                gaussian_kernel_range=(9, 12),
                offset_range=(round(18 * i), round(25 * i)),
                p=1.0,
            ),
        )
    if options.folding:
        _add(
            post_phase,
            "folding",
            ag.Folding(fold_count=options.fold_count, fold_angle_range=(0, 0), p=1.0),
        )
    if options.bindings and (i := _i("bindings")) > 0:
        _add(
            post_phase,
            "bindings",
            ag.BindingsAndFasteners(
                overlay_types="random",
                ntimes=(2, 4),
                use_figshare_library=0,
                p=i,
            ),
        )
    if options.markup and (i := _i("markup")) > 0:
        _add(
            post_phase,
            "markup",
            ag.Markup(
                num_lines_range=(round(i), round(i)),
                markup_type="random",
                p=1.0,
            ),
        )
    if options.scribbles and (i := _i("scribbles")) > 0:
        _add(
            post_phase,
            "scribbles",
            ag.Scribbles(
                scribbles_type="random",
                scribbles_count_range=(round(i), round(i)),
                p=1.0,
            ),
        )

    if not (ink_phase or paper_phase or post_phase):
        return None

    # Wrap each effect in its own per-effect PRNG stream (its own entry
    # in the effect_seeds map) so one effect's seed or parameters never
    # move another effect's random output. The pipeline-level seed is a
    # fixed constant (harmless; the wrappers override per effect).
    ink_phase = [
        _SeededAugmentation(aug, name, effect_seeds.get(name, 0))
        for name, aug in ink_phase
    ]
    paper_phase = [
        _SeededAugmentation(aug, name, effect_seeds.get(name, 0))
        for name, aug in paper_phase
    ]
    post_phase = [
        _SeededAugmentation(aug, name, effect_seeds.get(name, 0))
        for name, aug in post_phase
    ]
    # ink_fade lowers the pipeline-level overlay alpha (i=1 -> 0.85, the
    # original faded-ink blend).
    fade = options.ink_fade_intensity if options.ink_fade else 0.0
    overlay_alpha = 1.0 - 0.15 * fade if fade > 0 else 1.0
    return ag.AugraphyPipeline(
        ink_phase=ink_phase,
        paper_phase=paper_phase,
        post_phase=post_phase,
        overlay_alpha=overlay_alpha,
        random_seed=_PIPELINE_SEED,
    )


def distress_array(
    clean: np.ndarray,
    options: DistressOptions,
    effect_seeds: dict[str, int],
) -> np.ndarray:
    """Run the distress (scanned/aged look) pass on an in-memory array.

    The pass runs an augraphy ``AugraphyPipeline`` built from the
    options (see :func:`_build_augraphy_pipeline`), where every
    augmentation re-seeds its PRNG stream from its own entry in
    *effect_seeds*, so the same (image, options, effect_seeds) tuple
    always produces the same output and changing one effect's seed never
    moves another effect's random output.

    The pipeline is followed by the custom tail stages, which have no
    augraphy equivalent: warp (low-frequency remap, magnitude from
    ``warp_strength``, seeded from the ``"warp"`` entry of
    *effect_seeds*) then blur (Gaussian kernel sized by the blur
    intensity), each gated by its flag and intensity.

    Args:
        clean: Normalized 3-channel BGR source image (uint8).
        options: Per-effect controls. When ``options.enabled`` is
            ``False`` the input is returned unchanged (perfect image).
        effect_seeds: Per-effect seed map (see :data:`EFFECT_SEED_NAMES`);
            missing keys fall back to 0.

    Returns:
        The distressed image as a new uint8 BGR array (the input is
        never mutated).
    """
    import numpy as np

    if not options.enabled:
        return clean

    out = clean.copy()
    h, w = out.shape[:2]
    if min(h, w) < 30:
        logger.warning(
            "Image %dx%d is below the 30x30 augraphy minimum; "
            "skipping the augraphy pipeline (warp/blur only)",
            w,
            h,
        )
    else:
        with _AUGRAPHY_LOCK:
            pipeline = _build_augraphy_pipeline(options, effect_seeds, h, w)
            out = pipeline.augment(out, return_dict=0) if pipeline is not None else out
        # Defensively re-normalize: augraphy should return uint8 BGR at
        # the input size, but edge cases must not leak out (e.g. Faxify
        # resamples and returns a different size).
        out = np.ascontiguousarray(out)
        if out.dtype != np.uint8:
            out = out.astype(np.uint8)
        if out.ndim == 2:
            import cv2

            out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
        elif out.shape[2] == 4:
            out = _normalize_bgr(out)
        if out.shape[:2] != (h, w):
            import cv2

            out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)

    # Warp: subtle feed/lens warp via a low-frequency remap mesh.
    if options.warp:
        import cv2

        warp_rng = np.random.default_rng(effect_seeds.get("warp", 0))
        grid = 8
        dx = warp_rng.uniform(-1.0, 1.0, size=(grid, grid)).astype(np.float32)
        dy = warp_rng.uniform(-1.0, 1.0, size=(grid, grid)).astype(np.float32)
        dx = cv2.resize(dx, (w, h), interpolation=cv2.INTER_LINEAR)
        dy = cv2.resize(dy, (w, h), interpolation=cv2.INTER_LINEAR)
        amp = options.warp_strength * 3.0
        map_x = np.tile(np.arange(w, dtype=np.float32), (h, 1)) + dx * amp
        map_y = np.tile(np.arange(h, dtype=np.float32)[:, None], (1, w)) + dy * amp
        out = cv2.remap(
            out, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )

    # Blur: scanner focus loss. Kernel size scales with the resolved
    # blur intensity (3/5/7/9 at i ~ 0.1/0.35/0.7/1.0); 0 = no-op.
    if options.blur:
        import cv2

        i = options.blur_intensity
        if i > 0:
            k = 3 + 2 * round(3 * i)
            out = cv2.GaussianBlur(out, (k, k), 0)

    return out


def distress_image(
    path: Path, options: DistressOptions, effect_seeds: dict[str, int]
) -> None:
    """Apply the distress (scanned/aged look) pass in-place to a PNG.

    Runs :func:`distress_array` (augraphy pipeline plus the warp/blur
    tail stages). The PNG at *path* is overwritten.

    The pass is fully deterministic under the *effect_seeds* map: the
    same (image, options, effect_seeds) triple always produces the same
    output.

    Args:
        path: Path to the source PNG; overwritten with the distressed image.
        options: Per-effect controls. When ``options.enabled`` is ``False``
            the pass is skipped entirely and the file is left untouched
            (perfect image).
        effect_seeds: Per-effect seed map (see :data:`EFFECT_SEED_NAMES`);
            missing keys fall back to 0.

    Raises:
        FileNotFoundError: If *path* does not exist (and the pass is enabled).
        ValueError: If the file at *path* cannot be decoded as an image.
    """
    if not options.enabled:
        return

    import cv2

    if not path.is_file():
        raise FileNotFoundError(path)
    clean = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if clean is None:
        raise ValueError(f"Could not decode image: {path}")
    clean = _normalize_bgr(clean)
    out = distress_array(clean, options, effect_seeds)
    cv2.imwrite(str(path), out)


def distress_image_to_bytes(
    data: bytes, options: DistressOptions, effect_seeds: dict[str, int]
) -> bytes:
    """Apply the distress pass to PNG bytes and return the result as bytes.

    Same stage pipeline as :func:`distress_image`, but entirely in
    memory: the input PNG bytes are decoded, distressed via
    :func:`distress_array`, and re-encoded to PNG.

    Args:
        data: PNG-encoded source image bytes.
        options: Per-effect controls. When ``options.enabled`` is
            ``False`` the input is returned unchanged (perfect image).
        effect_seeds: Per-effect seed map (see :data:`EFFECT_SEED_NAMES`);
            missing keys fall back to 0.

    Returns:
        PNG-encoded bytes of the distressed image.

    Raises:
        ValueError: If *data* cannot be decoded as an image.
    """
    if not options.enabled:
        return data

    import cv2
    import numpy as np

    clean = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if clean is None:
        raise ValueError("Could not decode image from bytes")
    clean = _normalize_bgr(clean)
    out = distress_array(clean, options, effect_seeds)
    ok, encoded = cv2.imencode(".png", out)
    if not ok:
        raise ValueError("Could not encode distressed image to PNG")
    return encoded.tobytes()


#: Page width for content-sized (non-A4) image documents (A4 width).
_TALL_PAGE_WIDTH = "210mm"

#: Height of the measurement page used for content-sized documents.
_TALL_PAGE_HEIGHT_MM = 2000.0

#: Page margin in mm (matches the canonical ``@page`` rule).
_MARGIN_MM = 20.0

#: Minimum content-sized page height (mm), for (nearly) empty documents.
_MIN_PAGE_HEIGHT_MM = 100.0

#: Safety buffer added to the measured content height (mm) so the final
#: render cannot push the last line onto a second page.
_HEIGHT_BUFFER_MM = 1.0

#: Per-channel distance from the sampled paper color at or below which
#: a pixel still counts as "paper" (tolerates anti-aliasing and slight
#: rendering jitter around the page background color).
_PAPER_TOLERANCE = 16


def _render_page_image(doc: str):
    """Render *doc* to PDF and rasterize page 1.

    Returns:
        A ``(PIL.Image, page_count)`` tuple.
    """
    import pypdfium2 as pdfium
    from weasyprint import HTML

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "page.pdf"
        HTML(string=doc).write_pdf(str(pdf_path))
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            count = len(pdf)
            image = pdf[0].render(scale=_RENDER_SCALE).to_pil()
        finally:
            pdf.close()
    return image, count


def _measure_content_height_mm(image, scale: float) -> float:
    """Measure the rendered content height in mm from page 1 of a render.

    Finds the last non-paper row and adds the bottom page margin plus a
    small safety buffer. The paper color is sampled from the top-left
    corner (inside the page margin, so it reflects the uniform white
    ``@page`` background) instead of assuming white.

    Args:
        image: Rasterized page 1 (PIL image).
        scale: Raster scale used (pixels per PDF point).

    Returns:
        Content height in mm (at least :data:`_MIN_PAGE_HEIGHT_MM`).
    """
    import numpy as np

    rgb = (
        np.frombuffer(image.convert("RGB").tobytes(), dtype=np.uint8)
        .reshape(image.size[1], image.size[0], 3)
        .astype(np.int16)
    )
    paper = rgb[0, 0]
    ink_rows = np.nonzero(
        (np.abs(rgb - paper).max(axis=2) > _PAPER_TOLERANCE).any(axis=1)
    )[0]
    bottom = int(ink_rows[-1]) + 1 if ink_rows.size else 0
    content_mm = bottom * 25.4 / (scale * 72.0)
    return max(_MIN_PAGE_HEIGHT_MM, content_mm + _MARGIN_MM + _HEIGHT_BUFFER_MM)


def html_to_png(html: str, path: Path, a4: bool = True) -> Path:
    """Render a standalone HTML document string to a single PNG file.

    The document is sanitized with
    :func:`document_gen.document_png.sanitize_image_html`, rendered to PDF
    with WeasyPrint, and page 1 is rasterized to PNG with pypdfium2
    (WeasyPrint dropped native PNG output in v61).

    Page sizing:

    - ``a4=True`` -> A4 portrait.
    - ``a4=False`` -> content-sized. WeasyPrint ignores ``size: auto``,
      so the document is first rendered on a tall page, the content
      height is measured, and it is re-rendered with the explicit
      measured size (width stays A4 width).

    Single-page contract: when a render produces more than one page a
    warning is logged and only page 1 is kept.

    Args:
        html: A complete HTML document string (with embedded CSS).
        path: Where to write the PNG file.
        a4: Lock the page to A4 portrait (True) or size it to the content
            (False).

    Returns:
        *path* (the written PNG file).
    """
    from document_gen.document_png import force_page_size, sanitize_image_html

    doc = sanitize_image_html(html, a4)
    if not a4:
        tall_doc = force_page_size(doc, _TALL_PAGE_WIDTH, f"{_TALL_PAGE_HEIGHT_MM}mm")
        tall_image, count = _render_page_image(tall_doc)
        if count > 1:
            logger.warning(
                "Content taller than %.0fmm; clamping page height", _TALL_PAGE_HEIGHT_MM
            )
        height_mm = _measure_content_height_mm(tall_image, _RENDER_SCALE)
        doc = force_page_size(doc, _TALL_PAGE_WIDTH, f"{height_mm:.1f}mm")

    image, count = _render_page_image(doc)
    if count > 1:
        logger.warning("PNG render produced %d pages; keeping page 1 only", count)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path
