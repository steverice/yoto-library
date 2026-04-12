"""Tests for cover art style definitions."""

from __future__ import annotations

import pytest

from yoto_lib.covers.styles import CoverStyle


class TestCoverStyleRegistration:
    def test_get_returns_registered_style(self):
        """CoverStyle.get() returns a style registered at module load."""
        style = CoverStyle.get("storybook")
        assert style.name == "storybook"

    def test_get_raises_for_unknown_style(self):
        """CoverStyle.get() raises ValueError for unregistered names."""
        with pytest.raises(ValueError, match="Unknown style"):
            CoverStyle.get("nonexistent")

    def test_default_returns_storybook(self):
        """The default style is storybook."""
        default = CoverStyle.default()
        assert default.name == "storybook"
        assert default.is_default is True

    def test_names_returns_frozenset(self):
        """CoverStyle.names() returns a frozenset of all style names."""
        names = CoverStyle.names()
        assert isinstance(names, frozenset)
        assert "cartoon" in names
        assert "storybook" in names

    def test_all_styles_have_nonempty_prompts(self):
        """Every registered style must have non-empty prompt fragments."""
        for name in CoverStyle.names():
            style = CoverStyle.get(name)
            assert style.illustration_prompt, f"{name} has empty illustration_prompt"
            assert style.title_prompt, f"{name} has empty title_prompt"
            assert style.label, f"{name} has empty label"

    def test_exactly_eight_styles(self):
        """There should be exactly 8 registered styles."""
        assert len(CoverStyle.names()) == 8

    def test_exactly_one_default(self):
        """Exactly one style should have is_default=True."""
        defaults = [CoverStyle.get(n) for n in CoverStyle.names() if CoverStyle.get(n).is_default]
        assert len(defaults) == 1

    def test_duplicate_name_raises(self):
        """Registering a style with an existing name raises ValueError."""
        with pytest.raises(ValueError, match="Duplicate style"):
            CoverStyle(
                name="cartoon",
                label="duplicate",
                illustration_prompt="duplicate",
                title_prompt="duplicate",
            )

    def test_multiple_defaults_raises(self):
        """Registering a second default style raises ValueError."""
        with pytest.raises(ValueError, match="Multiple default styles"):
            CoverStyle(
                name="_test_second_default",
                label="test",
                illustration_prompt="test",
                title_prompt="test",
                is_default=True,
            )
