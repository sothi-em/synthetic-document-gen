"""Tests for the PNG generator (pure functions + distress pass)."""

import numpy as np
import pytest

from document_gen.generators.png_gen import (
    EFFECT_SEED_NAMES,
    _normalize_bgr,
    _patch_augraphy,
    distress_array,
    distress_image,
    distress_image_to_bytes,
    html_to_png,
    random_effect_seeds,
)
from document_gen.models import DistressOptions


def _options(**kwargs):
    base = {"enabled": True}
    base.update(kwargs)
    return DistressOptions(**base)


def _seeds(**overrides):
    """A fixed per-effect seed map (deterministic across calls)."""
    seeds = {name: i + 1 for i, name in enumerate(EFFECT_SEED_NAMES)}
    seeds.update(overrides)
    return seeds


class TestNormalizeBgr:
    def test_rgb_passthrough(self):
        arr = np.zeros((4, 4, 3), dtype=np.uint8)
        out = _normalize_bgr(arr)
        assert out is arr

    def test_rgba_drops_alpha(self):
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        out = _normalize_bgr(arr)
        assert out.shape == (4, 4, 3)

    def test_gray_to_bgr(self):
        arr = np.zeros((4, 4), dtype=np.uint8)
        out = _normalize_bgr(arr)
        assert out.shape == (4, 4, 3)

    def test_rgba_composited_over_white(self):
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        out = _normalize_bgr(arr)
        assert out.shape == (4, 4, 3)
        # Fully transparent pixels composite to white.
        assert np.array_equal(out[0, 0], [255, 255, 255])


class TestDistressArray:
    def test_enabled_changes_image(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        out = distress_array(
            clean, _options(paper_aging=True, vignette=True, stains=True), _seeds()
        )
        assert not np.array_equal(clean, out)

    def test_disabled_returns_input(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        out = distress_array(clean, _options(enabled=False), _seeds())
        assert out is clean

    def test_does_not_mutate_input(self):
        clean = np.full((60, 60, 3), 200, dtype=np.uint8)
        before = clean.copy()
        distress_array(clean, _options(paper_aging=True), _seeds())
        assert np.array_equal(clean, before)

    def test_seed_reproducible(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(
            paper_aging=True, stains=True, noise_texturize=True, warp=True
        )
        a = distress_array(clean, options, _seeds())
        b = distress_array(clean, options, _seeds())
        assert np.array_equal(a, b)

    def test_different_seeds_differ(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(paper_aging=True, stains=True)
        a = distress_array(clean, options, _seeds(stains=1))
        b = distress_array(clean, options, _seeds(stains=2))
        assert not np.array_equal(a, b)

    def test_missing_seed_falls_back_to_zero(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(stains=True, warp=True)
        a = distress_array(clean, options, {})
        b = distress_array(clean, options, {})
        assert np.array_equal(a, b)


class TestAugraphyPipeline:
    def test_pipeline_seed_reproducible(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        a = distress_array(clean, _options(stains=True, noise_texturize=True), _seeds())
        b = distress_array(clean, _options(stains=True, noise_texturize=True), _seeds())
        assert np.array_equal(a, b)

    def test_pipeline_different_seeds_differ(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        a = distress_array(
            clean, _options(noise_texturize=True), _seeds(noise_texturize=1)
        )
        b = distress_array(
            clean, _options(noise_texturize=True), _seeds(noise_texturize=2)
        )
        assert not np.array_equal(a, b)

    def test_pipeline_min_size_guard(self):
        tiny = np.full((20, 20, 3), 255, dtype=np.uint8)
        out = distress_array(tiny, _options(stains=True), _seeds())
        assert out.shape == (20, 20, 3)


class TestRandomEffectSeeds:
    def test_covers_all_effect_names(self):
        seeds = random_effect_seeds()
        assert set(seeds) == set(EFFECT_SEED_NAMES)
        assert all(isinstance(v, int) for v in seeds.values())
        assert all(0 <= v < 0x7FFFFFFF for v in seeds.values())

    def test_effect_names_exclude_deterministic_effects(self):
        assert "ink_fade" not in EFFECT_SEED_NAMES
        assert "blur" not in EFFECT_SEED_NAMES
        # floyd-steinberg dithering makes no random draws in augraphy
        assert "dithering" not in EFFECT_SEED_NAMES


class TestDistressImage:
    def test_seed_reproducible(self, tmp_path):
        import cv2

        path = tmp_path / "doc.png"
        cv2.imwrite(str(path), np.full((60, 60, 3), 255, dtype=np.uint8))
        options = _options(paper_aging=True, warp=True)
        distress_image(path, options, _seeds())
        first = path.read_bytes()
        cv2.imwrite(str(path), np.full((60, 60, 3), 255, dtype=np.uint8))
        distress_image(path, options, _seeds())
        assert path.read_bytes() == first

    def test_disabled_leaves_file(self, tmp_path):
        import cv2

        path = tmp_path / "doc.png"
        cv2.imwrite(str(path), np.full((60, 60, 3), 255, dtype=np.uint8))
        before = path.read_bytes()
        distress_image(path, _options(enabled=False), _seeds())
        assert path.read_bytes() == before

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            distress_image(tmp_path / "nope.png", _options(paper_aging=True), _seeds())


class TestDistressImageToBytes:
    def test_roundtrip(self):
        import cv2
        import numpy as np

        data = self._png(np.full((60, 60, 3), 255, dtype=np.uint8))
        out = distress_image_to_bytes(
            data, _options(paper_aging=True, stains=True), _seeds()
        )
        assert out != data
        assert out[:4] == b"\x89PNG"

    def test_disabled_returns_input(self):
        import numpy as np

        data = self._png(np.full((60, 60, 3), 255, dtype=np.uint8))
        out = distress_image_to_bytes(data, _options(enabled=False), _seeds())
        assert out is data

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            distress_image_to_bytes(b"not a png", _options(paper_aging=True), _seeds())

    def test_stains_reproducible(self):
        import numpy as np

        data = self._png(np.full((60, 60, 3), 255, dtype=np.uint8))
        a = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=7))
        b = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=7))
        assert a == b

    def test_stains_seed_changes_output(self):
        import numpy as np

        data = self._png(np.full((60, 60, 3), 255, dtype=np.uint8))
        a = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=7))
        b = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=8))
        assert a != b

    @staticmethod
    def _png(arr):
        import cv2

        ok, encoded = cv2.imencode(".png", arr)
        assert ok
        return encoded.tobytes()


class TestHtmlToPng:
    def test_renders_png(self, tmp_path):
        html = "<html><body><h1>Hi</h1></body></html>"
        out = html_to_png(html, tmp_path / "out.png")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_content_sized_page(self, tmp_path):
        html = "<html><body><h1>Hi</h1></body></html>"
        out = html_to_png(html, tmp_path / "auto.png", a4=False)
        assert out.exists()


class TestPatchAugraphy:
    def test_idempotent(self):
        _patch_augraphy()
        _patch_augraphy()  # second call must not raise

    def test_patch_applies(self):
        from document_gen.generators import png_gen

        _patch_augraphy()
        assert png_gen._AUGRAPHY_PATCHED is True
