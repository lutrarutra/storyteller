"""The palettes against the colour-vision checks they were designed with (see palette_checks.py)."""

import pytest
from palette_checks import categorical_problems, ordinal_problems

from storyteller.theme import palette


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_categorical_passes(mode):
    assert categorical_problems(palette.categorical(mode=mode), mode) == []


def test_brand_yellow_is_not_a_series_colour():
    """Why the categorical set darkens the brand's yellow and orange: they fail on cream."""
    brand = ["#4f46e5", "#0f9d8a", "#e8668f", "#ffcf3f", "#ed9e4b"]
    assert categorical_problems(brand, "light")


@pytest.mark.parametrize("name", palette.SEQUENTIAL_NAMES)
@pytest.mark.parametrize("n", [3, 5, 7])
def test_ordered_classes_pass(name, n):
    assert ordinal_problems(palette.sequential(n, name), "#fffaf0") == []


def test_colormaps_are_monotone_in_lightness():
    for name in palette.SEQUENTIAL_NAMES:
        lightness = [palette.to_oklab(c)[0] for c in palette.colormap_stops(name)]
        assert lightness == sorted(lightness, reverse=True)


def test_diverging_has_a_neutral_midpoint():
    stops = palette.diverging_stops(9)
    _, a, b = palette.to_oklab(stops[4])
    assert (a**2 + b**2) ** 0.5 < 0.03
    lightness = [palette.to_oklab(c)[0] for c in stops]
    assert max(lightness) == lightness[4]


def test_too_many_categories_is_an_error():
    with pytest.raises(ValueError, match="Other"):
        palette.categorical(9)


def test_css_variables_cover_tokens_and_series():
    css = palette.css_variables("light")
    assert "--cf-paper: #f6efd9;" in css and "--cf-c8:" in css and "--cf-critical-status:" in css
