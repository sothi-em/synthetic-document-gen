"""PNG image generator: HTML -> PNG rendering and the distress pass.

- :func:`html_to_png` renders a standalone HTML document to a single PNG
  (WeasyPrint -> PDF -> pypdfium2 raster of page 1; WeasyPrint dropped
  native PNG output in v61).
- :func:`distress_image` post-processes a rendered PNG in-place so it
  looks like a scanned, aged document, driven by
  :class:`document_gen.models.distress.DistressOptions`. The default
  ``"augraphy"`` backend runs an augraphy ``AugraphyPipeline`` (ink /
  paper / post phases built from the options) plus custom warp/blur tail
  stages; the ``"legacy"`` backend runs the preserved pre-augraphy
  hand-rolled stage sequence (paper tint, vignette, stains, scanner
  noise, faded ink, optional warp and blur) via
  :func:`distress_array_legacy`.
- :func:`distress_image_to_bytes` applies the same pass to PNG bytes in
  memory (no file I/O); :func:`distress_array` is the shared array-level
  core both entry points run.

Heavy dependencies (cv2, numpy, weasyprint, pypdfium2) are imported
lazily inside functions to keep package import cheap.
"""

from __future__ import annotations

import logging
import random
import tempfile
import threading
import zlib
from pathlib import Path

from document_gen.models.distress import DistressOptions

logger = logging.getLogger(__name__)

#: Soft cream paper color (BGR).
_PAPER_BGR = (215, 235, 245)

#: Per-channel stain darkening factors (differential darkening -> brown tint).
_STAIN_FACTORS = (0.75, 0.82, 0.88)

#: Rasterization resolution for HTML -> PNG (points -> pixels).
_RENDER_SCALE = 96 / 72


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


def _build_augraphy_pipeline(
    options: DistressOptions, seed: int, stain_seed: int | None, h: int, w: int
):
    """Build a fresh augraphy :class:`AugraphyPipeline` from *options*.

    One augmentation is appended per enabled flag (all with ``p=1.0``
    and fixed conservative ranges, so defaults look like a mildly
    degraded office document). Phase lists:

    - ink: ink bleed, bleed-through, letterpress, ink mottling, ink
      color swap, hollow, dithering, dot matrix, low-ink lines
      (periodic/random), line degradation.
    - paper: paper aging tint, vignette (lighting gradient), stains,
      noise/brightness texturize, watermark, pattern, Voronoi/Delaunay
      tessellations, paper factory.
    - post: photo-copy (kept first, see below), scanner grain, fax / drum
      / roller / screen artifacts, shadow, lens flare, reflected light,
      brightness / gamma / color shift, depth blur, moire, LCD pattern,
      JPEG artifacts, double exposure, folding, bindings, markup,
      scribbles.

    ``ink_fade`` is not an augmentation: it lowers the pipeline-level
    ``overlay_alpha`` (ink-to-paper blend) from 1.0 to 0.85.

    Args:
        options: Distress options (field values are backend-agnostic).
        seed: Base random seed.
        stain_seed: Optional second seed; augraphy has one pipeline-level
            seed, so when given it is combined with *seed* via CRC32.
        h: Image height in pixels (vignette light position).
        w: Image width in pixels (vignette light position).

    Returns:
        A fresh ``AugraphyPipeline`` (deterministic under its seed; a
        fresh instance is required per call), or ``None`` when no
        augmentation is enabled in any phase.
    """
    import augraphy as ag

    ink_phase = []
    if options.ink_bleed:
        ink_phase.append(
            ag.InkBleed(intensity_range=(0.1, 0.4), kernel_size=(5, 5), p=1.0)
        )
    if options.bleed_through:
        ink_phase.append(ag.BleedThrough(intensity_range=(0.1, 0.4), alpha=0.3, p=1.0))
    if options.letterpress:
        ink_phase.append(
            ag.Letterpress(
                n_samples=(20, 60),
                n_clusters=(20, 60),
                std_range=(1500, 5000),
                value_range=(200, 255),
                p=1.0,
            )
        )
    if options.ink_mottling:
        ink_phase.append(ag.InkMottling(ink_mottling_alpha_range=(0.1, 0.3), p=1.0))
    if options.ink_color_swap:
        ink_phase.append(ag.InkColorSwap(ink_swap_color="random", p=1.0))
    if options.hollow:
        ink_phase.append(ag.Hollow(hollow_median_kernel_value_range=(71, 101), p=1.0))
    if options.dithering:
        ink_phase.append(ag.Dithering(dither="floyd-steinberg", order=(2, 4), p=1.0))
    if options.dot_matrix:
        ink_phase.append(
            ag.DotMatrix(
                dot_matrix_shape="random",
                dot_matrix_dot_width_range=(3, 8),
                dot_matrix_dot_height_range=(3, 8),
                p=1.0,
            )
        )
    if options.low_ink_periodic_lines:
        ink_phase.append(
            ag.LowInkPeriodicLines(count_range=(2, 5), period_range=(10, 30), p=1.0)
        )
    if options.low_ink_random_lines:
        ink_phase.append(ag.LowInkRandomLines(count_range=(5, 10), p=1.0))
    if options.lines_degradation:
        ink_phase.append(ag.LinesDegradation(line_split_probability=(0.2, 0.4), p=1.0))

    paper_phase = []
    if options.paper_aging:
        paper_phase.append(
            ag.ColorPaper(hue_range=(28, 45), saturation_range=(10, 40), p=1.0)
        )
    if options.vignette:
        paper_phase.append(
            ag.LightingGradient(
                light_position=(w // 2, h // 2),
                max_brightness=255,
                min_brightness=round(255 * (1 - options.vignette_strength)),
                mode="gaussian",
                p=1.0,
            )
        )
    if options.stains and options.stain_count > 0:
        paper_phase.append(
            ag.Stains(
                stains_type="random",
                stains_blend_method="darken",
                stains_blend_alpha=min(0.2 + 0.04 * options.stain_count, 0.9),
                p=1.0,
            )
        )
    if options.noise_texturize:
        paper_phase.append(
            ag.NoiseTexturize(sigma_range=(3, 10), turbulence_range=(2, 5), p=1.0)
        )
    if options.brightness_texturize:
        paper_phase.append(
            ag.BrightnessTexturize(texturize_range=(0.85, 0.99), deviation=0.08, p=1.0)
        )
    if options.watermark:
        paper_phase.append(
            ag.WaterMark(
                watermark_word=options.watermark_word or "random",
                watermark_font_size=(10, 15),
                watermark_rotation=(0, 360),
                watermark_method="darken",
                p=1.0,
            )
        )
    if options.pattern_generator:
        paper_phase.append(
            ag.PatternGenerator(color="random", alpha_range=(0.25, 0.4), p=1.0)
        )
    if options.voronoi_tessellation:
        paper_phase.append(
            ag.VoronoiTessellation(
                mult_range=(50, 80), num_cells_range=(500, 1000), p=1.0
            )
        )
    if options.delaunay_tessellation:
        paper_phase.append(ag.DelaunayTessellation(n_points_range=(500, 800), p=1.0))
    if options.paper_factory:
        paper_phase.append(ag.PaperFactory(generate_texture=1, p=1.0))

    post_phase = []
    # BadPhotoCopy must be appended (and thus JIT-compiled) before
    # SubtleNoise: with numba 0.67, compiling SubtleNoise's prange kernel
    # first makes BadPhotoCopy's worley-noise parfor compilation crash
    # with an AssertionError inside the numba compiler.
    if options.bad_photo_copy:
        post_phase.append(
            ag.BadPhotoCopy(
                noise_side="random",
                noise_sparsity=(0.1, 0.4),
                noise_concentration=(0.1, 0.4),
                p=1.0,
            )
        )
    if options.noise:
        post_phase.append(
            ag.SubtleNoise(subtle_range=max(1, int(options.noise_strength)), p=1.0)
        )
    if options.faxify:
        post_phase.append(ag.Faxify(scale_range=(1.0, 1.25), p=1.0))
    if options.dirty_drum:
        post_phase.append(
            ag.DirtyDrum(line_concentration=0.1, line_width_range=(1, 4), p=1.0)
        )
    if options.dirty_rollers:
        post_phase.append(ag.DirtyRollers(line_width_range=(8, 12), p=1.0))
    if options.dirty_screen:
        post_phase.append(
            ag.DirtyScreen(n_clusters=(50, 100), n_samples=(2, 20), p=1.0)
        )
    if options.shadow_cast:
        post_phase.append(
            ag.ShadowCast(
                shadow_side="random",
                shadow_opacity_range=(0.2, 0.5),
                shadow_blur_kernel_range=(101, 201),
                p=1.0,
            )
        )
    if options.lens_flare:
        post_phase.append(
            ag.LensFlare(lens_flare_location="random", lens_flare_size=(0.5, 3), p=1.0)
        )
    if options.reflected_light:
        post_phase.append(
            ag.ReflectedLight(
                reflected_light_internal_max_brightness_range=(0.9, 1.0), p=1.0
            )
        )
    if options.brightness:
        post_phase.append(ag.Brightness(brightness_range=(0.9, 1.1), p=1.0))
    if options.gamma:
        post_phase.append(ag.Gamma(gamma_range=(0.8, 1.2), p=1.0))
    if options.color_shift:
        post_phase.append(
            ag.ColorShift(
                color_shift_offset_x_range=(3, 5),
                color_shift_offset_y_range=(3, 5),
                p=1.0,
            )
        )
    if options.depth_blur:
        post_phase.append(
            ag.DepthSimulatedBlur(
                blur_major_axes_length_range=(120, 200),
                blur_minor_axes_length_range=(120, 200),
                p=1.0,
            )
        )
    if options.moire:
        post_phase.append(
            ag.Moire(moire_density=(15, 20), moire_blend_alpha=0.1, p=1.0)
        )
    if options.lcd_pattern:
        post_phase.append(
            ag.LCDScreenPattern(pattern_type="random", pattern_overlay_alpha=0.3, p=1.0)
        )
    if options.jpeg_artifacts:
        post_phase.append(
            ag.Jpeg(
                quality_range=(
                    max(10, options.jpeg_quality - 15),
                    options.jpeg_quality,
                ),
                p=1.0,
            )
        )
    if options.double_exposure:
        post_phase.append(
            ag.DoubleExposure(
                gaussian_kernel_range=(9, 12), offset_range=(18, 25), p=1.0
            )
        )
    if options.folding:
        post_phase.append(
            ag.Folding(fold_count=options.fold_count, fold_angle_range=(0, 0), p=1.0)
        )
    if options.bindings:
        post_phase.append(
            ag.BindingsAndFasteners(
                overlay_types="random",
                ntimes=(2, 4),
                use_figshare_library=0,
                p=1.0,
            )
        )
    if options.markup:
        post_phase.append(
            ag.Markup(num_lines_range=(2, 5), markup_type="random", p=1.0)
        )
    if options.scribbles:
        post_phase.append(
            ag.Scribbles(scribbles_type="random", scribbles_count_range=(1, 4), p=1.0)
        )

    if not (ink_phase or paper_phase or post_phase):
        return None

    # Mask to signed 32-bit: augraphy passes the seed to cv2.setRNGSeed,
    # which overflows on the unsigned 32-bit values zlib.crc32 returns.
    random_seed = (
        zlib.crc32(f"{seed}:{stain_seed}".encode()) if stain_seed is not None else seed
    ) & 0x7FFFFFFF
    return ag.AugraphyPipeline(
        ink_phase=ink_phase,
        paper_phase=paper_phase,
        post_phase=post_phase,
        overlay_alpha=0.85 if options.ink_fade else 1.0,
        random_seed=random_seed,
    )


def distress_array(
    clean: np.ndarray,
    options: DistressOptions,
    seed: int,
    stain_seed: int | None = None,
) -> np.ndarray:
    """Run the distress (scanned/aged look) pass on an in-memory array.

    Two backends are available via ``options.backend``:

    - ``"augraphy"`` (default): an augraphy ``AugraphyPipeline`` built
      from the options (see :func:`_build_augraphy_pipeline`) replaces
      the paper tint / vignette / stains / noise / ink re-stamp stages;
      the whole pipeline is seeded from *seed* (combined with
      *stain_seed* via CRC32 when given), so the same
      (image, options, seed, stain_seed) tuple always produces the same
      output.
    - ``"legacy"``: the preserved pre-augraphy hand-rolled stage
      sequence (:func:`distress_array_legacy`); stain positions are
      unseeded unless *stain_seed* is given, and augraphy-only toggles
      are ignored.

    Both backends finish with the same custom tail stages, which have no
    augraphy equivalent: warp (low-frequency remap) then blur (3x3
    Gaussian), each gated by its flag.

    Args:
        clean: Normalized 3-channel BGR source image (uint8).
        options: Per-effect controls. When ``options.enabled`` is
            ``False`` the input is returned unchanged (perfect image).
        seed: Random seed driving the random stages (the whole augraphy
            pipeline on the default backend; noise and warp on legacy).
        stain_seed: Optional second seed. On the augraphy backend it is
            combined with *seed* to seed the pipeline (stain positions
            are reproducible); on the legacy backend it seeds only the
            stain stage (``None`` keeps the unseeded OS-entropy
            behavior).

    Returns:
        The distressed image as a new uint8 BGR array (the input is
        never mutated).
    """
    import numpy as np

    if not options.enabled:
        return clean

    if options.backend == "legacy":
        return distress_array_legacy(clean, options, seed, stain_seed)

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
            pipeline = _build_augraphy_pipeline(options, seed, stain_seed, h, w)
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

        warp_rng = np.random.default_rng(seed)
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

    # Blur: scanner focus loss.
    if options.blur:
        import cv2

        out = cv2.GaussianBlur(out, (3, 3), 0)

    return out


def distress_array_legacy(
    clean: np.ndarray,
    options: DistressOptions,
    seed: int,
    stain_seed: int | None = None,
) -> np.ndarray:
    """Legacy reference implementation of the pre-augraphy distress pass.

    Kept verbatim for exact reproduction of old renders and as a
    fallback; new work goes to the augraphy path
    (:func:`distress_array` with ``options.backend == "augraphy"``).

    Stage pipeline (each stage gated by its flag, in this order):
    paper tint -> vignette -> stains -> noise -> ink re-stamp (soft-alpha
    blend) -> warp -> blur. The ink re-stamp always runs when the paper
    tint is on (it restores the document over the solid paper base);
    ``ink_fade`` alone controls only the faded-ink tint.

    The noise and warp stages are driven by *seed*. Stain positions and
    radii are drawn from a non-seeded OS-entropy RNG and intentionally
    vary on every run, unless *stain_seed* is given, in which case a
    seeded RNG is used and the same (image, options, seed, stain_seed)
    tuple always produces the same output.

    Args:
        clean: Normalized 3-channel BGR source image (uint8).
        options: Per-effect controls. When ``options.enabled`` is
            ``False`` the input is returned unchanged (perfect image).
        seed: Random seed for the noise and warp stages.
        stain_seed: Optional seed for the stain stage. ``None`` keeps
            the unseeded OS-entropy behavior.

    Returns:
        The distressed image as a new uint8 BGR array (the input is
        never mutated).
    """
    import cv2
    import numpy as np

    if not options.enabled:
        return clean

    h, w = clean.shape[:2]

    # 1. Paper tint (or the clean render itself as an untouched base).
    if options.paper_aging:
        paper = np.full(clean.shape, _PAPER_BGR, dtype=np.uint8)
    else:
        paper = clean.copy()

    # 2. Vignette: uneven lighting / darkened edges.
    if options.vignette:
        x = np.linspace(-1.0, 1.0, w, dtype=np.float32)
        y = np.linspace(-1.0, 1.0, h, dtype=np.float32)
        xx, yy = np.meshgrid(x, y)
        factor = np.clip(
            1.0 - options.vignette_strength * (xx * xx + yy * yy), 0.0, 1.0
        )
        paper = (paper.astype(np.float32) * factor[:, :, None]).astype(np.uint8)

    # 3. Stains: low-frequency coffee/dirt blobs with differential
    #    darkening. Centers/radii default to OS entropy (every run places
    #    the stains differently); a *stain_seed* makes them reproducible.
    if options.stains and options.stain_count > 0:
        stain_rng = (
            random.Random(stain_seed)
            if stain_seed is not None
            else random.SystemRandom()
        )
        mask = np.zeros((h, w), dtype=np.uint8)
        for _ in range(options.stain_count):
            cx = stain_rng.randint(0, w - 1)
            cy = stain_rng.randint(0, h - 1)
            radius = stain_rng.randint(40, 120)
            cv2.circle(mask, (cx, cy), radius, 255, -1)
        # Kernel must be odd and no larger than the image.
        k = min(151, 2 * (min(h, w) // 2) + 1)
        mask = cv2.GaussianBlur(mask, (max(k, 1), max(k, 1)), 0)
        mask_norm = mask.astype(np.float32) / 255.0
        for i, f in enumerate(_STAIN_FACTORS):
            paper[:, :, i] = (
                paper[:, :, i].astype(np.float32) * (1.0 - 0.35 * mask_norm * f)
            ).astype(np.uint8)

    # 4. Noise: high-frequency scanner grain (cv2.randn draws from the
    #    global cv2 RNG, which is seeded for reproducibility).
    if options.noise:
        cv2.setRNGSeed(seed)
        noise = np.zeros((h, w, 3), dtype=np.int16)
        cv2.randn(noise, 0, options.noise_strength)
        paper = np.clip(paper.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # 5. Ink re-stamp: blend the clean render's ink back over the dirty
    #    paper using a luminance-based soft alpha (no hard threshold, so
    #    it works on colored renders too). Always runs when the paper
    #    tint replaced the base (otherwise the page would be blank);
    #    ``ink_fade`` only controls the faded-ink tint.
    if options.paper_aging or options.ink_fade:
        luminance = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY).astype(np.float32)
        alpha = ((255.0 - luminance) / 255.0)[:, :, None]
        if options.ink_fade:
            ink_px = 30.0 * 0.85 + paper.astype(np.float32) * 0.15
        else:
            ink_px = np.full_like(paper, 30.0, dtype=np.float32)
        paper = (alpha * ink_px + (1.0 - alpha) * paper.astype(np.float32)).astype(
            np.uint8
        )

    out = paper

    # 6. Warp: subtle feed/lens warp via a low-frequency remap mesh.
    if options.warp:
        warp_rng = np.random.default_rng(seed)
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

    # 7. Blur: scanner focus loss.
    if options.blur:
        out = cv2.GaussianBlur(out, (3, 3), 0)

    return out


def distress_image(path: Path, options: DistressOptions, seed: int) -> None:
    """Apply the distress (scanned/aged look) pass in-place to a PNG.

    Runs :func:`distress_array` (augraphy pipeline by default, or the
    legacy hand-rolled stage sequence when
    ``options.backend == "legacy"``) plus the warp/blur tail stages. The
    PNG at *path* is overwritten.

    On the default augraphy backend *seed* drives the whole pipeline, so
    the same (image, options, seed) triple always produces the same
    output. On the legacy backend stain positions and radii are drawn
    from a non-seeded OS-entropy RNG and intentionally vary on every
    run; the noise and warp stages are driven by *seed*.

    Args:
        path: Path to the source PNG; overwritten with the distressed image.
        options: Per-effect controls. When ``options.enabled`` is ``False``
            the pass is skipped entirely and the file is left untouched
            (perfect image).
        seed: Random seed for the noise and warp stages (stain positions
            are intentionally unseeded).

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
    out = distress_array(clean, options, seed, stain_seed=None)
    cv2.imwrite(str(path), out)


def distress_image_to_bytes(
    data: bytes,
    options: DistressOptions,
    seed: int,
    stain_seed: int | None = None,
) -> bytes:
    """Apply the distress pass to PNG bytes and return the result as bytes.

    Same stage pipeline as :func:`distress_image`, but entirely in
    memory: the input PNG bytes are decoded, distressed via
    :func:`distress_array`, and re-encoded to PNG.

    Args:
        data: PNG-encoded source image bytes.
        options: Per-effect controls. When ``options.enabled`` is
            ``False`` the input is returned unchanged (perfect image).
        seed: Random seed for the noise and warp stages.
        stain_seed: Optional seed for the stain stage. ``None`` keeps
            the unseeded OS-entropy behavior.

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
    out = distress_array(clean, options, seed, stain_seed=stain_seed)
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

#: Pixel value at or above which a pixel counts as "paper" (white).
_PAPER_PIXEL = 250


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
    small safety buffer.

    Args:
        image: Rasterized page 1 (PIL image).
        scale: Raster scale used (pixels per PDF point).

    Returns:
        Content height in mm (at least :data:`_MIN_PAGE_HEIGHT_MM`).
    """
    import numpy as np

    gray = np.frombuffer(image.convert("L").tobytes(), dtype=np.uint8).reshape(
        image.size[1], image.size[0]
    )
    ink_rows = np.nonzero((gray < _PAPER_PIXEL).any(axis=1))[0]
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
