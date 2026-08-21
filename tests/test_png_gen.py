"""Tests for the PNG generator: distress pass and HTML -> PNG rendering.

No LLM required: the distress pass runs on a synthetic numpy image and
the renderer uses WeasyPrint + pypdfium2 (hard dependencies).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from document_gen.document_png import force_page_size, sanitize_image_html
from document_gen.generators.png_gen import (
    distress_array,
    distress_image,
    distress_image_to_bytes,
    html_to_png,
)
from document_gen.models.distress import DistressOptions

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _clean_image() -> np.ndarray:
    """A clean white BGR document with dark text/lines (like a render)."""
    img = np.ones((400, 300, 3), dtype=np.uint8) * 255
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, "TEST DOCUMENT", (20, 60), font, 0.8, 0, 2, cv2.LINE_AA)
    cv2.putText(
        img, "Second line of body text", (20, 120), font, 0.6, 0, 2, cv2.LINE_AA
    )
    cv2.line(img, (20, 80), (280, 80), 0, 2)
    return img


@pytest.fixture
def png_path(tmp_path: Path) -> Path:
    """A temp PNG file holding the clean synthetic document."""
    path = tmp_path / "clean.png"
    assert cv2.imwrite(str(path), _clean_image())
    return path


def _options(**overrides) -> DistressOptions:
    """Enabled distress options with optional per-flag overrides."""
    base = dict(enabled=True, seed=42)
    base.update(overrides)
    return DistressOptions(**base)


def _read(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert img is not None
    return img


def _background(img: np.ndarray) -> np.ndarray:
    """Pixels away from the text/lines (top-right corner region)."""
    return img[150:350, 150:290]


def _clean_bytes() -> bytes:
    """PNG-encoded bytes of the clean synthetic document."""
    return cv2.imencode(".png", _clean_image())[1].tobytes()


def _stain_only_options() -> DistressOptions:
    """Stains on, every other effect off (clean determinism signal)."""
    return _options(stains=True, stain_count=10, noise=False, vignette=False)


# ---------------------------------------------------------------------------
# distress_image
# ---------------------------------------------------------------------------


class TestDistressImage:
    def test_disabled_leaves_file_untouched(self, png_path: Path) -> None:
        before = png_path.read_bytes()
        distress_image(png_path, DistressOptions(), seed=42)
        assert png_path.read_bytes() == before

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            distress_image(tmp_path / "nope.png", _options(), seed=42)

    def test_enabled_changes_image_keeps_shape(self, png_path: Path) -> None:
        before = png_path.read_bytes()
        distress_image(png_path, _options(), seed=42)
        after = _read(png_path)
        assert png_path.read_bytes() != before
        assert after.shape == _clean_image().shape

    def test_paper_aging_tints_cream(self, png_path: Path) -> None:
        # Legacy backend: encodes the old hand-rolled cream tint look.
        distress_image(png_path, _options(paper_aging=True, backend="legacy"), seed=42)
        b, g, r = _background(_read(png_path)).mean(axis=(0, 1))
        # Cream paper: red channel warmest, blue coolest.
        assert r > g > b

    def test_no_paper_aging_stays_neutral(self, png_path: Path) -> None:
        distress_image(png_path, _options(paper_aging=False, stains=False), seed=42)
        b, g, r = _background(_read(png_path)).mean(axis=(0, 1))
        assert abs(r - g) < 3 and abs(g - b) < 3

    def test_vignette_darkens_edges(self, png_path: Path) -> None:
        # Legacy backend: encodes the old hand-rolled radial vignette look.
        distress_image(
            png_path,
            _options(
                paper_aging=True,
                vignette=True,
                stains=False,
                noise=False,
                backend="legacy",
            ),
            seed=42,
        )
        img = _read(png_path).astype(np.float32)
        edge = np.concatenate(
            [img[0, :].reshape(-1, 3), img[-1, :].reshape(-1, 3)]
        ).mean()
        center = img[180:220, 130:170].reshape(-1, 3).mean()
        assert edge < center * 0.8

    def test_stains_darken_paper(self, png_path: Path) -> None:
        clean = _clean_image()
        distressed = clean.copy()
        distress_image(png_path, _options(stains=True, stain_count=10), seed=42)
        img = _read(png_path)
        # With a dozen stains, some background region must be visibly darker.
        assert _background(img).min() < _background(clean).min() - 10

    def test_zero_stain_count_is_noop_for_stains(self, png_path: Path) -> None:
        distress_image(
            png_path,
            _options(stains=True, stain_count=0, noise=False, vignette=False),
            seed=42,
        )
        img = _read(png_path)
        # Only the cream tint remains; no darkened blobs.
        assert _background(img).min() > 180

    def test_noise_raises_variance(self, png_path: Path) -> None:
        clean = _clean_image()
        distress_image(
            png_path,
            _options(noise=True, noise_strength=20, stains=False, vignette=False),
            seed=42,
        )
        bg = _background(_read(png_path)).astype(np.float32)
        assert bg.std() > _background(clean).astype(np.float32).std() * 3

    def test_paper_aging_without_ink_fade_keeps_content(self, png_path: Path) -> None:
        # Regression: the paper tint replaces the base image, so the ink
        # re-stamp must still run when ink_fade is off (crisp ink, not a
        # blank cream page).
        clean = _clean_image()
        distress_image(
            png_path,
            _options(
                paper_aging=True,
                ink_fade=False,
                stains=False,
                vignette=False,
                noise=False,
            ),
            seed=42,
        )
        img = _read(png_path)
        ink_mask = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY) < 128
        assert ink_mask.any()
        # The document's text/lines are still present (dark ink pixels).
        assert img[ink_mask].mean() < 100

    def test_ink_fade_softens_text(self, png_path: Path) -> None:
        # Legacy backend: encodes the old hand-rolled 85/15 ink blend.
        clean = _clean_image()
        distress_image(png_path, _options(ink_fade=True, backend="legacy"), seed=42)
        img = _read(png_path)
        ink_mask = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY) < 128
        assert ink_mask.any()
        # Faded ink: stamped text is dark but blended with the paper
        # (no pure-black pixels remain in the ink region).
        assert img[ink_mask].min() > 0
        assert img[ink_mask].mean() < 100

    def test_warp_changes_image_keeps_shape(self, png_path: Path) -> None:
        distress_image(
            png_path,
            _options(warp=True, warp_strength=1.0, noise=False, stains=False),
            seed=42,
        )
        img = _read(png_path)
        assert img.shape == _clean_image().shape
        assert not np.array_equal(img, _clean_image())

    def test_blur_smooths_image(self, png_path: Path) -> None:
        # Legacy backend: the augraphy paper effects add their own
        # texture, so the gradient comparison encodes the old look.
        distress_image(
            png_path,
            _options(
                blur=True, noise=False, stains=False, vignette=False, backend="legacy"
            ),
            seed=42,
        )
        img = _read(png_path).astype(np.float32)
        clean = _clean_image().astype(np.float32)
        # A 3x3 blur reduces local contrast (gradient magnitude).
        grad = lambda a: np.abs(np.diff(a, axis=1)).mean()  # noqa: E731
        assert grad(img) < grad(clean)

    def test_small_image_does_not_crash_stain_blur(self, tmp_path: Path) -> None:
        # Smaller than the 151x151 stain blur kernel.
        small = np.ones((60, 80, 3), dtype=np.uint8) * 255
        path = tmp_path / "small.png"
        assert cv2.imwrite(str(path), small)
        distress_image(path, _options(stains=True, stain_count=3), seed=42)
        assert _read(path).shape == small.shape

    def test_rgba_input_composited_over_white(self, tmp_path: Path) -> None:
        rgba = np.zeros((100, 100, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255  # opaque white
        path = tmp_path / "rgba.png"
        assert cv2.imwrite(str(path), rgba)
        distress_image(path, _options(), seed=42)
        assert _read(path).shape == (100, 100, 3)

    def test_rgba_input_composited_over_white_legacy(self, tmp_path: Path) -> None:
        rgba = np.zeros((100, 100, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255  # opaque white
        path = tmp_path / "rgba.png"
        assert cv2.imwrite(str(path), rgba)
        distress_image(path, _options(backend="legacy"), seed=42)
        assert _read(path).shape == (100, 100, 3)

    def test_seed_determinism_without_stains(self, tmp_path: Path) -> None:
        # Stains are intentionally unseeded, so byte-identity comparisons
        # disable them; noise and warp must still be seed-deterministic.
        p1, p2, p3 = (tmp_path / f"img{i}.png" for i in range(3))
        for p in (p1, p2, p3):
            assert cv2.imwrite(str(p), _clean_image())
        opts = _options(stains=False)
        distress_image(p1, opts, seed=7)
        distress_image(p2, opts, seed=7)
        distress_image(p3, opts, seed=8)
        assert p1.read_bytes() == p2.read_bytes()
        assert p1.read_bytes() != p3.read_bytes()

    def test_stain_positions_vary_per_run(self, tmp_path: Path) -> None:
        # Legacy backend only: same seed, stains enabled, the two outputs
        # must differ because stain centers/radii come from OS entropy,
        # not the seed. (On the augraphy backend stains are seeded.)
        p1, p2 = (tmp_path / f"img{i}.png" for i in range(2))
        for p in (p1, p2):
            assert cv2.imwrite(str(p), _clean_image())
        opts = _options(
            stains=True, stain_count=10, noise=False, vignette=False, backend="legacy"
        )
        distress_image(p1, opts, seed=7)
        distress_image(p2, opts, seed=7)
        assert p1.read_bytes() != p2.read_bytes()


# ---------------------------------------------------------------------------
# distress_array / distress_image_to_bytes (seedable stains, byte API)
# ---------------------------------------------------------------------------


class TestStainSeed:
    def test_stain_seed_deterministic(self) -> None:
        data = _clean_bytes()
        opts = _stain_only_options()
        out1 = distress_image_to_bytes(data, opts, seed=7, stain_seed=123)
        out2 = distress_image_to_bytes(data, opts, seed=7, stain_seed=123)
        assert out1 == out2

    def test_different_stain_seed_changes_output(self) -> None:
        data = _clean_bytes()
        opts = _stain_only_options()
        out1 = distress_image_to_bytes(data, opts, seed=7, stain_seed=123)
        out2 = distress_image_to_bytes(data, opts, seed=7, stain_seed=456)
        assert out1 != out2

    def test_unseeded_stains_still_vary(self) -> None:
        # Legacy backend only: stain_seed=None keeps the SystemRandom
        # path, so two runs with the same (image, options, seed) must
        # still differ. (On the augraphy backend stains are seeded.)
        data = _clean_bytes()
        opts = _options(
            stains=True, stain_count=10, noise=False, vignette=False, backend="legacy"
        )
        out1 = distress_image_to_bytes(data, opts, seed=7)
        out2 = distress_image_to_bytes(data, opts, seed=7)
        assert out1 != out2

    def test_distress_array_stain_seed_deterministic(self) -> None:
        clean = _clean_image()
        opts = _stain_only_options()
        out1 = distress_array(clean, opts, seed=7, stain_seed=9)
        out2 = distress_array(clean, opts, seed=7, stain_seed=9)
        assert np.array_equal(out1, out2)
        # The input array is never mutated.
        assert np.array_equal(clean, _clean_image())


class TestDistressImageToBytes:
    def test_matches_in_place_for_same_inputs(self, tmp_path: Path) -> None:
        # The byte API and the in-place API must produce byte-identical
        # PNGs for the same (image, options, seed, stain_seed) inputs.
        path = tmp_path / "img.png"
        data = _clean_bytes()
        assert cv2.imwrite(str(path), _clean_image())
        opts = _options(stains=False)
        distressed_bytes = distress_image_to_bytes(data, opts, seed=7)
        distress_image(path, opts, seed=7)
        assert distressed_bytes == path.read_bytes()

    def test_disabled_returns_input_unchanged(self) -> None:
        data = _clean_bytes()
        out = distress_image_to_bytes(data, DistressOptions(), seed=1)
        assert out is data

    def test_undecodable_input_raises(self) -> None:
        with pytest.raises(ValueError, match="decode"):
            distress_image_to_bytes(b"not a png", _options(), seed=1)

    def test_rgba_bytes_composited_over_white(self) -> None:
        rgba = np.zeros((100, 100, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255  # opaque white
        data = cv2.imencode(".png", rgba)[1].tobytes()
        out = distress_image_to_bytes(data, _options(), seed=1)
        arr = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_UNCHANGED)
        assert arr is not None and arr.shape == (100, 100, 3)


# ---------------------------------------------------------------------------
# Augraphy backend (default) behavior
# ---------------------------------------------------------------------------


class TestAugraphyBackend:
    """Behavior-level assertions for the default augraphy backend.

    These intentionally do not encode the legacy hand-rolled look:
    ``vignette`` is a native light-strip gradient (not a radial
    dark-edge vignette) and ``ink_fade`` has no visible effect on the
    8.2.6 ``ink_to_paper`` blend.
    """

    def test_all_effects_off_returns_clean_copy(self) -> None:
        clean = _clean_image()
        opts = _options(
            paper_aging=False,
            vignette=False,
            stains=False,
            noise=False,
            ink_fade=False,
            blur=False,
            warp=False,
        )
        out = distress_array(clean, opts, seed=42)
        assert out.shape == clean.shape
        assert np.array_equal(out, clean)

    def test_paper_aging_shifts_off_white_keeps_shape(self) -> None:
        clean = _clean_image()
        opts = _options(paper_aging=True, vignette=False, stains=False, noise=False)
        out = distress_array(clean, opts, seed=42)
        assert out.shape == clean.shape
        # The paper tint moves the background off pure white.
        assert _background(out).mean() < 250

    def test_vignette_changes_image_keeps_shape(self) -> None:
        clean = _clean_image()
        opts = _options(paper_aging=False, vignette=True, stains=False, noise=False)
        out = distress_array(clean, opts, seed=42)
        assert out.shape == clean.shape
        assert not np.array_equal(out, clean)

    def test_stains_change_image(self) -> None:
        clean = _clean_image()
        opts = _options(
            paper_aging=False,
            vignette=False,
            stains=True,
            stain_count=10,
            noise=False,
        )
        out = distress_array(clean, opts, seed=42)
        assert out.shape == clean.shape
        assert not np.array_equal(out, clean)

    def test_zero_stain_count_no_stain_contribution(self) -> None:
        clean = _clean_image()
        opts = _options(
            paper_aging=False,
            vignette=False,
            stains=False,
            stain_count=0,
            noise=False,
            ink_fade=False,
            blur=False,
        )
        out = distress_array(clean, opts, seed=42)
        assert np.array_equal(out, clean)

    def test_noise_raises_variance(self) -> None:
        clean = _clean_image()
        opts = _options(
            paper_aging=False,
            vignette=False,
            stains=False,
            noise=True,
            noise_strength=20,
        )
        out = distress_array(clean, opts, seed=42)
        bg = _background(out).astype(np.float32)
        assert bg.std() > _background(clean).astype(np.float32).std() * 3

    def test_paper_aging_keeps_ink_legible(self) -> None:
        # The ink_to_paper overlay must keep the document content dark
        # over the tinted paper.
        clean = _clean_image()
        opts = _options(paper_aging=True, vignette=False, stains=False, noise=False)
        out = distress_array(clean, opts, seed=42)
        ink_mask = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY) < 128
        assert ink_mask.any()
        assert out[ink_mask].mean() < 100

    def test_ink_fade_accepted_both_settings(self) -> None:
        # The 8.2.6 ink_to_paper blend ignores overlay_alpha, so this
        # only asserts the toggle is accepted without error.
        clean = _clean_image()
        for fade in (True, False):
            opts = _options(ink_fade=fade)
            out = distress_array(clean, opts, seed=42)
            assert out.shape == clean.shape

    def test_determinism_same_seed_byte_identical(self, tmp_path: Path) -> None:
        # Stains included: the augraphy pipeline is fully seeded, so the
        # same (image, options, seed, stain_seed) tuple is byte-stable.
        p1, p2, p3 = (tmp_path / f"img{i}.png" for i in range(3))
        for p in (p1, p2, p3):
            assert cv2.imwrite(str(p), _clean_image())
        opts = _options()
        distress_image(p1, opts, seed=7)
        distress_image(p2, opts, seed=7)
        distress_image(p3, opts, seed=8)
        assert p1.read_bytes() == p2.read_bytes()
        assert p1.read_bytes() != p3.read_bytes()

    def test_stain_seed_determinism(self) -> None:
        data = _clean_bytes()
        opts = _stain_only_options()
        out1 = distress_image_to_bytes(data, opts, seed=7, stain_seed=123)
        out2 = distress_image_to_bytes(data, opts, seed=7, stain_seed=123)
        out3 = distress_image_to_bytes(data, opts, seed=7, stain_seed=456)
        assert out1 == out2
        assert out1 != out3

    def test_small_image_below_minimum_returns_without_raising(self) -> None:
        small = np.ones((20, 20, 3), dtype=np.uint8) * 255
        out = distress_array(small, _options(), seed=42)
        assert out.shape == small.shape
        assert out.dtype == np.uint8

    def test_backend_isolation_augraphy_toggles_noop_on_legacy(self) -> None:
        # Augraphy-only toggles must be no-ops on the legacy backend:
        # legacy output with the toggles on is identical to the legacy
        # output with them off (same seed, stains off for determinism).
        clean = _clean_image()
        common = dict(
            backend="legacy",
            paper_aging=True,
            vignette=False,
            stains=False,
            noise=False,
        )
        with_toggle = distress_array(clean, _options(ink_bleed=True, **common), seed=42)
        without_toggle = distress_array(clean, _options(**common), seed=42)
        assert np.array_equal(with_toggle, without_toggle)


# ---------------------------------------------------------------------------
# Per-augmentation smoke tests (augraphy backend)
#
# One effect at a time on a synthetic document: guards against augraphy
# API drift (renamed/removed parameters raise loudly at pipeline build).
# ---------------------------------------------------------------------------

NEW_INK_TOGGLES = [
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
]

NEW_PAPER_TOGGLES = [
    "noise_texturize",
    "brightness_texturize",
    "watermark",
    "pattern_generator",
    "voronoi_tessellation",
    "delaunay_tessellation",
    "paper_factory",
]

NEW_POST_TOGGLES = [
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
    "jpeg_artifacts",
    "double_exposure",
    "folding",
    "bindings",
    "markup",
    "scribbles",
]

ALL_NEW_TOGGLES = NEW_INK_TOGGLES + NEW_PAPER_TOGGLES + NEW_POST_TOGGLES

#: Heavier augmentations (texture/tessellation/shadow generation).
SLOW_TOGGLES = {
    "paper_factory",
    "voronoi_tessellation",
    "delaunay_tessellation",
    "shadow_cast",
}


class TestAugraphySmoke:
    @pytest.mark.parametrize("toggle", sorted(SLOW_TOGGLES))
    @pytest.mark.slow
    def test_single_augmentation_slow(self, toggle: str) -> None:
        self._run_single(toggle)

    @pytest.mark.parametrize(
        "toggle", [t for t in ALL_NEW_TOGGLES if t not in SLOW_TOGGLES]
    )
    def test_single_augmentation_fast(self, toggle: str) -> None:
        self._run_single(toggle)

    def _run_single(self, toggle: str) -> None:
        clean = _clean_image()
        opts = _options(
            paper_aging=False,
            vignette=False,
            stains=False,
            noise=False,
            ink_fade=False,
            blur=False,
            warp=False,
            **{toggle: True},
        )
        out = distress_array(clean, opts, seed=42)
        assert out.shape == clean.shape
        assert out.dtype == np.uint8
        assert out.ndim == 3 and out.shape[2] == 3
        assert not np.array_equal(out, clean)

    def test_full_defaults_plus_bad_photo_copy_combo_fresh_process(self) -> None:
        # numba 0.67 crash guard: BadPhotoCopy's JIT compilation must not
        # hit the broken worley kernel (noise_type=3 is pinned in
        # _build_augraphy_pipeline). Kernel JIT state is process-global,
        # so the check runs in a *fresh* subprocess. Uses the full
        # default options (paper_aging, vignette, stains, noise,
        # ink_fade, blur) plus bad_photo_copy.
        script = "\n".join(
            [
                "import numpy as np",
                "from document_gen.generators.png_gen import distress_array",
                "from document_gen.models.distress import DistressOptions",
                "img = np.ones((300, 400, 3), dtype=np.uint8) * 255",
                "opts = DistressOptions(enabled=True, bad_photo_copy=True)",
                "out = distress_array(img, opts, seed=1, stain_seed=2)",
                "assert out.shape == img.shape and out.dtype == np.uint8",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"fresh-process combo run failed (rc={result.returncode}): "
            f"{result.stderr[-2000:] or result.stdout[-2000:]}."
        )


# ---------------------------------------------------------------------------
# sanitize_image_html
# ---------------------------------------------------------------------------


class TestSanitizeImageHtml:
    def test_a4_page_rule_injected(self) -> None:
        doc = sanitize_image_html(
            "<html><head></head><body><p>hi</p></body></html>", a4=True
        )
        assert "@page { size: A4 portrait; margin: 2cm; }" in doc

    def test_auto_page_rule_injected(self) -> None:
        doc = sanitize_image_html(
            "<html><head></head><body><p>hi</p></body></html>", a4=False
        )
        assert "@page { size: auto; margin: 2cm; }" in doc

    def test_overrides_existing_page_rule(self) -> None:
        raw = (
            "<html><head><style>@page { size: letter landscape; margin: 1cm; }</style>"
            "</head><body></body></html>"
        )
        doc = sanitize_image_html(raw, a4=True)
        assert "size: A4 portrait" in doc
        assert "letter landscape" not in doc
        assert "margin: 1cm" not in doc

    def test_no_style_block_inserts_one(self) -> None:
        doc = sanitize_image_html("<html><body><p>hi</p></body></html>", a4=False)
        assert "<style>@page { size: auto; margin: 2cm; }</style>" in doc

    def test_extracts_from_code_fences(self) -> None:
        raw = (
            "Here you go:\n```html\n<html><head></head><body>x</body></html>\n```\nbye"
        )
        doc = sanitize_image_html(raw, a4=True)
        assert doc.startswith("<html>")
        assert "```" not in doc

    def test_force_page_size_replaces_rule(self) -> None:
        doc = sanitize_image_html(
            "<html><head></head><body><p>hi</p></body></html>", a4=False
        )
        forced = force_page_size(doc, "210mm", "123.4mm")
        assert "@page { size: 210mm 123.4mm; margin: 2cm; }" in forced
        assert "size: auto" not in forced


# ---------------------------------------------------------------------------
# html_to_png
# ---------------------------------------------------------------------------


class TestHtmlToPng:
    def test_a4_render_size(self, tmp_path: Path) -> None:
        from PIL import Image

        path = tmp_path / "a4.png"
        result = html_to_png("<html><body><h1>Hello</h1></body></html>", path, a4=True)
        assert result == path
        with Image.open(path) as img:
            w, h = img.size
        # A4 at 96dpi: 794 x 1123 (allow raster rounding).
        assert 790 <= w <= 798
        assert 1118 <= h <= 1128

    def test_auto_render_is_content_sized(self, tmp_path: Path) -> None:
        from PIL import Image

        path = tmp_path / "auto.png"
        html_to_png("<html><body><h1>Hello</h1></body></html>", path, a4=False)
        with Image.open(path) as img:
            w, h = img.size
        # Content-sized: far shorter than an A4 page.
        assert h < 800
        assert w > 0

    def test_multi_page_keeps_page_one_and_warns(self, tmp_path: Path) -> None:
        from PIL import Image

        # 10 full A4 sections force multiple pages.
        sections = "".join(
            f"<div style='height: 25cm'><h1>Section {i}</h1></div>" for i in range(10)
        )
        path = tmp_path / "multi.png"
        html_to_png(f"<html><body>{sections}</body></html>", path, a4=True)
        with Image.open(path) as img:
            w, h = img.size
        assert 790 <= w <= 798
        assert 1118 <= h <= 1128

    def test_multi_page_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sections = "".join(
            f"<div style='height: 25cm'><h1>Section {i}</h1></div>" for i in range(5)
        )
        with caplog.at_level(logging.WARNING, logger="document_gen.generators.png_gen"):
            html_to_png(
                f"<html><body>{sections}</body></html>", tmp_path / "w.png", a4=True
            )
        assert any("keeping page 1" in rec.message for rec in caplog.records)

    def test_sanitizes_conflicting_page_rule(self, tmp_path: Path) -> None:
        from PIL import Image

        raw = (
            "<html><head><style>@page { size: letter landscape; margin: 1cm; }</style>"
            "</head><body><h1>Hello</h1></body></html>"
        )
        path = tmp_path / "override.png"
        html_to_png(raw, path, a4=True)
        with Image.open(path) as img:
            w, h = img.size
        # A4 portrait forced despite the LLM's letter landscape rule.
        assert 790 <= w <= 798
        assert 1118 <= h <= 1128

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "out.png"
        html_to_png("<html><body><h1>Hello</h1></body></html>", path, a4=True)
        assert path.is_file()
