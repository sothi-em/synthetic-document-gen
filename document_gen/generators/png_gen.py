"""PNG image generator: HTML -> PNG rendering and the distress pass.

- :func:`html_to_png` renders a standalone HTML document to a single PNG
  (WeasyPrint -> PDF -> pypdfium2 raster of page 1; WeasyPrint dropped
  native PNG output in v61).
- :func:`distress_image` post-processes a rendered PNG in-place so it
  looks like a scanned, aged document (paper tint, vignette, stains,
  scanner noise, faded ink, optional warp and blur), driven by
  :class:`document_gen.models.distress.DistressOptions`.
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


def distress_array(
    clean: np.ndarray,
    options: DistressOptions,
    seed: int,
    stain_seed: int | None = None,
) -> np.ndarray:
    """Run the distress (scanned/aged look) stages on an in-memory array.

    Stage pipeline (each stage gated by its flag, in this order):
    paper tint -> vignette -> stains -> noise -> ink re-stamp (soft-alpha
    blend) -> warp -> blur.

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
    #    it works on colored renders too).
    if options.ink_fade:
        luminance = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY).astype(np.float32)
        alpha = ((255.0 - luminance) / 255.0)[:, :, None]
        ink_px = 30.0 * 0.85 + paper.astype(np.float32) * 0.15
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

    Stage pipeline (each stage gated by its flag, in this order):
    paper tint -> vignette -> stains -> noise -> ink re-stamp (soft-alpha
    blend) -> warp -> blur. The PNG at *path* is overwritten.

    Stain positions and radii are drawn from a non-seeded OS-entropy RNG
    and intentionally vary on every run. The noise and warp stages are
    driven by *seed*, so with stains disabled the same (image, options,
    seed) triple always produces the same output.

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
