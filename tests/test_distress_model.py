"""Tests for the DistressOptions model: intensity resolution + validation."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from document_gen.models.distress import (
    _INTENSITY_EFFECTS,
    DistressOptions,
)


class TestIntensityResolution:
    def test_flag_on_absent_intensity_resolves_to_one(self) -> None:
        opts = DistressOptions(ink_bleed=True)
        assert opts.ink_bleed_intensity == 1.0

    def test_flag_off_absent_intensity_resolves_to_zero(self) -> None:
        opts = DistressOptions()
        assert opts.ink_bleed_intensity == 0.0
        assert opts.shadow_cast_intensity == 0.0

    def test_defaults_resolve_from_default_flags(self) -> None:
        # paper_aging / vignette / stains / noise / ink_fade / blur are
        # on by default; warp and all augraphy toggles are off.
        opts = DistressOptions()
        assert opts.paper_aging_intensity == 1.0
        assert opts.ink_fade_intensity == 1.0
        assert opts.blur_intensity == 1.0
        assert opts.ink_bleed_intensity == 0.0

    def test_explicit_intensity_preserved(self) -> None:
        on = DistressOptions(ink_bleed=True, ink_bleed_intensity=0.4)
        assert on.ink_bleed_intensity == 0.4
        off = DistressOptions(ink_bleed=False, ink_bleed_intensity=0.7)
        assert off.ink_bleed_intensity == 0.7

    @pytest.mark.parametrize("name", _INTENSITY_EFFECTS)
    def test_every_effect_flag_on_resolves_to_full(self, name: str) -> None:
        # markup/scribbles use 0-10 counts (full = 3), the rest 0-1.
        full = 3.0 if name in ("markup", "scribbles") else 1.0
        opts = DistressOptions(**{name: True})
        assert getattr(opts, f"{name}_intensity") == full

    @pytest.mark.parametrize("name", _INTENSITY_EFFECTS)
    def test_every_effect_flag_off_resolves_to_zero(self, name: str) -> None:
        opts = DistressOptions(**{name: False})
        assert getattr(opts, f"{name}_intensity") == 0.0


class TestIntensityValidation:
    def test_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DistressOptions(ink_bleed=True, ink_bleed_intensity=1.5)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DistressOptions(ink_bleed=False, ink_bleed_intensity=-0.1)

    def test_boundaries_accepted(self) -> None:
        assert (
            DistressOptions(ink_bleed=True, ink_bleed_intensity=0.0).ink_bleed_intensity
            == 0.0
        )
        assert (
            DistressOptions(ink_bleed=True, ink_bleed_intensity=1.0).ink_bleed_intensity
            == 1.0
        )


class TestSerialization:
    def test_default_dump_is_json_serializable(self) -> None:
        json.dumps(DistressOptions().model_dump())

    def test_old_boolean_only_payload_round_trips(self) -> None:
        # Simulate a pre-intensity saved payload: flags only, no
        # *_intensity keys. Must load and resolve to the old look.
        payload = {
            "enabled": True,
            "ink_bleed": True,
            "shadow_cast": False,
        }
        opts = DistressOptions.model_validate(payload)
        assert opts.ink_bleed_intensity == 1.0
        assert opts.shadow_cast_intensity == 0.0
        # Re-validating a dumped (now explicit) payload is stable.
        again = DistressOptions.model_validate(opts.model_dump())
        assert again.ink_bleed_intensity == 1.0
        assert again.shadow_cast_intensity == 0.0


class TestSeedField:
    def test_seed_defaults_to_none(self) -> None:
        assert DistressOptions().seed is None

    def test_override_seed_recorded(self) -> None:
        assert DistressOptions(enabled=True, seed=42).seed == 42

    def test_legacy_backend_key_ignored(self) -> None:
        # Pre-rework payloads carry a `backend` key; it is no longer a
        # field and must be ignored, not rejected.
        opts = DistressOptions.model_validate({"enabled": True, "backend": "legacy"})
        assert opts.enabled is True
        assert "backend" not in opts.model_dump()
