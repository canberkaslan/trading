"""WCAG contrast for the web dashboard's colour tokens.

The mobile app has had a contrast test since its own muted grey was caught
failing at 3.4:1; the web dashboard had none, and shipped the same mistake
independently — `--ink3` measured 3.39:1 on the page background, the same
failure to two decimals, on the token used for eyebrow labels, table headers,
timestamps, the footer and unselected period tabs.

The rule these encode: a token is only usable on a surface it clears AA on, and
`--ink3` has to clear on the darkest AND the lightest surface, because the same
token is used on all four.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DASHBOARD = Path(__file__).resolve().parents[1] / "api" / "static" / "dashboard.html"

# WCAG 2.1 AA for text below 18pt / 14pt bold.
AA_NORMAL = 4.5


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    r, g, b = linear
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens() -> dict[str, str]:
    """Parse the `--name:#hex` custom properties out of the :root block."""
    css = _DASHBOARD.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert root, "could not find the :root block in dashboard.html"
    pattern = r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})"
    return {m.group(1): m.group(2) for m in re.finditer(pattern, root.group(1))}


# Every surface a text token can land on, darkest first.
SURFACES = ("bg", "s1", "s2", "s3")
# Tokens that carry readable text and therefore owe AA on all four.
TEXT_TOKENS = ("ink", "ink2", "ink3")


def test_the_expected_tokens_are_present() -> None:
    """Guards the parse itself: a renamed token must fail loudly here rather
    than silently drop out of every assertion below."""
    tokens = _tokens()
    for name in SURFACES + TEXT_TOKENS:
        assert name in tokens, f"--{name} missing from :root"


@pytest.mark.parametrize("token", TEXT_TOKENS)
@pytest.mark.parametrize("surface", SURFACES)
def test_text_tokens_meet_aa_on_every_surface(token: str, surface: str) -> None:
    tokens = _tokens()
    ratio = contrast_ratio(tokens[token], tokens[surface])
    assert ratio >= AA_NORMAL, (
        f"--{token} {tokens[token]} on --{surface} {tokens[surface]} is {ratio:.2f}:1, "
        f"below AA {AA_NORMAL}:1"
    )


def test_the_grey_that_shipped_is_a_real_failure_not_a_near_miss() -> None:
    """Regression guard, mirroring the mobile suite's own. #5d6673 was the
    muted token for months; pinning it here means reverting to it fails a test
    that names the reason rather than just going quiet."""
    tokens = _tokens()
    assert contrast_ratio("#5d6673", tokens["bg"]) < AA_NORMAL
    assert contrast_ratio("#5d6673", tokens["s3"]) < 3.0


def test_the_benchmark_label_is_readable_where_it_is_actually_drawn() -> None:
    """--bench carries text in exactly one place: the SPY end-of-line label in
    the chart, an 11px string drawn on the page ground. It also appears on --s3
    (the tooltip) but only as an 8px swatch, so that pairing is decoration and
    not held to a text ratio — asserting it there would be measuring a surface
    the colour never carries a glyph on.
    """
    tokens = _tokens()
    ratio = contrast_ratio(tokens["bench"], tokens["bg"])
    assert ratio >= AA_NORMAL, f"--bench on --bg is {ratio:.2f}:1"


@pytest.mark.parametrize("token", ("pos", "neg", "warn"))
def test_semantic_colours_are_readable_on_the_page_ground(token: str) -> None:
    """Gain/loss/warning are read as text, not just as fills."""
    tokens = _tokens()
    ratio = contrast_ratio(tokens[token], tokens["bg"])
    assert ratio >= AA_NORMAL, f"--{token} {tokens[token]} on --bg is {ratio:.2f}:1"
