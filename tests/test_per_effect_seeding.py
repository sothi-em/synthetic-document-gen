"""Per-effect seeding tests for the augraphy distress pipeline.

Each effect must draw from its own PRNG stream derived from the base
seed, so changing one effect's parameters never moves another effect's
random output, while the same (image, options, seed, stain_seed) tuple
still produces identical output.

Requires augraphy and cv2 (available in the project venv).
"""

import numpy as np
import pytest

augraphy = pytest.importorskip("augraphy")
cv2 = pytest.importorskip("cv2")

from document_gen.generators.png_gen import distress_array
from document_gen.models.distress import DistressOptions

#: Flat white page: uniform pixels compress losslessly under JPEG, so
#: the JPEG stage is a visual no-op and any output difference between
#: two JPEG qualities is purely the downstream effects being redrawn.
_PAGE = np.full((300, 300, 3), 255, dtype=np.uint8)


def _options(**kwargs) -> DistressOptions:
    """Enabled options with all default-on effects turned off.

    Only the effects named in *kwargs* (plus enabled=True) are active,
    so renders contain exactly the effects under test.
    """
    base = dict(
        enabled=True,
        paper_aging=False,
        vignette=False,
        stains=False,
        noise=False,
        ink_fade=False,
        blur=False,
    )
    base.update(kwargs)
    return DistressOptions(**base)


def test_determinism_same_inputs_same_output():
    """Same (image, options, seed, stain_seed) rendered twice is identical."""
    options = _options(scribbles=True, scribbles_intensity=1.0, noise=True)
    out1 = distress_array(_PAGE, options, seed=42, stain_seed=7)
    out2 = distress_array(_PAGE, options, seed=42, stain_seed=7)
    assert np.array_equal(out1, out2)


def test_effect_independence_upstream_effect_does_not_move_scribbles():
    """Toggling an upstream effect must not redraw the scribbles.

    Regression test for the shared-PRNG-stream defect: with a flat page
    the JPEG stage is a visual no-op (uniform pixels compress
    losslessly), so "scribbles only" and "jpeg + scribbles" must be
    pixel-identical. Under the old shared stream the JPEG stage's random
    draws shifted where the scribbles drew theirs, landing the strokes
    in different positions.
    """
    out_scribbles = distress_array(
        _PAGE,
        _options(scribbles=True, scribbles_intensity=1.0),
        seed=42,
        stain_seed=7,
    )
    out_jpeg_scribbles = distress_array(
        _PAGE,
        _options(
            jpeg_artifacts=True,
            jpeg_quality=50,
            scribbles=True,
            scribbles_intensity=1.0,
        ),
        seed=42,
        stain_seed=7,
    )
    assert np.array_equal(out_scribbles, out_jpeg_scribbles)


def test_effect_independence_jpeg_quality_does_not_move_scribbles():
    """Changing the JPEG quality (upstream) must not redraw scribbles.

    With a flat page the JPEG stage is a visual no-op at any quality,
    so the two renders must be pixel-identical even though the JPEG
    stage draws different quality parameters at each setting.
    """
    out_a = distress_array(
        _PAGE,
        _options(
            jpeg_artifacts=True,
            jpeg_quality=50,
            scribbles=True,
            scribbles_intensity=1.0,
        ),
        seed=42,
        stain_seed=7,
    )
    out_b = distress_array(
        _PAGE,
        _options(
            jpeg_artifacts=True,
            jpeg_quality=90,
            scribbles=True,
            scribbles_intensity=1.0,
        ),
        seed=42,
        stain_seed=7,
    )
    assert np.array_equal(out_a, out_b)


def test_seed_sensitivity_different_base_seeds_differ():
    """Different base seeds produce different random output."""
    out_a = distress_array(
        _PAGE, _options(scribbles=True, scribbles_intensity=1.0), seed=42
    )
    out_b = distress_array(
        _PAGE, _options(scribbles=True, scribbles_intensity=1.0), seed=43
    )
    assert not np.array_equal(out_a, out_b)
