"""Tests for per-effect distress seeding.

Every effect draws from its own entry in the per-effect seed map, so
the same (image, options, effect_seeds) tuple always produces the same
output, and changing one effect's seed only moves that effect's random
output.
"""

import numpy as np
import pytest

from document_gen.generators.png_gen import (
    EFFECT_SEED_NAMES,
    distress_array,
    distress_image_to_bytes,
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


class TestPerEffectSeeding:
    def test_seed_reproducible(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(
            paper_aging=True, stains=True, noise_texturize=True, warp=True
        )
        a = distress_array(clean, options, _seeds())
        b = distress_array(clean, options, _seeds())
        assert np.array_equal(a, b)

    def test_seed_changes_output(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(paper_aging=True, stains=True)
        a = distress_array(clean, options, _seeds(stains=1, noise_texturize=1))
        b = distress_array(clean, options, _seeds(stains=2, noise_texturize=2))
        assert not np.array_equal(a, b)

    def test_stains_seed_reproducible(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        a = distress_array(clean, _options(stains=True), _seeds(stains=7))
        b = distress_array(clean, _options(stains=True), _seeds(stains=7))
        assert np.array_equal(a, b)

    def test_stains_seed_changes_output(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        a = distress_array(clean, _options(stains=True), _seeds(stains=7))
        b = distress_array(clean, _options(stains=True), _seeds(stains=8))
        assert not np.array_equal(a, b)

    def test_combined_seeds_reproducible(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(stains=True, noise_texturize=True)
        a = distress_array(clean, options, _seeds(stains=7, noise_texturize=3))
        b = distress_array(clean, options, _seeds(stains=7, noise_texturize=3))
        assert np.array_equal(a, b)

    def test_missing_seed_falls_back_to_zero(self):
        clean = np.full((60, 60, 3), 255, dtype=np.uint8)
        options = _options(stains=True, warp=True)
        a = distress_array(clean, options, {})
        b = distress_array(clean, options, {})
        assert np.array_equal(a, b)


class TestRandomEffectSeeds:
    def test_covers_all_effect_names(self):
        seeds = random_effect_seeds()
        assert set(seeds) == set(EFFECT_SEED_NAMES)
        assert all(isinstance(v, int) for v in seeds.values())
        assert all(0 <= v < 0x7FFFFFFF for v in seeds.values())

    def test_fresh_each_call(self):
        a = random_effect_seeds()
        b = random_effect_seeds()
        assert a != b


class TestDistressImageToBytesSeeding:
    @staticmethod
    def _data():
        import cv2

        ok, encoded = cv2.imencode(".png", np.full((60, 60, 3), 255, dtype=np.uint8))
        assert ok
        return encoded.tobytes()

    def test_stains_seed_reproducible(self):
        data = self._data()
        a = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=7))
        b = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=7))
        assert a == b

    def test_stains_seed_changes_output(self):
        data = self._data()
        a = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=7))
        b = distress_image_to_bytes(data, _options(stains=True), _seeds(stains=8))
        assert a != b
