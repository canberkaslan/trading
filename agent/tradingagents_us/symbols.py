"""What counts as a US ticker, decided from the listings rather than guessed.

Two endpoints validated symbols independently — `isalpha()` and a length of
six — and both were wrong in the same two ways. Berkshire's B shares are
`BRK.B`, and the catalogue's longest symbols are seven characters, so the app
could search for names it then refused to analyse.

The rule here was derived by reading Polygon's active US stock list: 13,181
symbols, in which the ONLY non-letter character is a dot (140 of them) and the
longest entry is seven characters. `AKO.B`, `AAC.WS` and `AGM.A` are all real.

A test asserts this accepts every symbol in that live catalogue. A rule about
what the market contains should be checked against the market, not against
what seemed reasonable when it was written.
"""

from __future__ import annotations

import re

# Letters, optionally one dot followed by a one- or two-letter class or series
# suffix: BRK.B, AKO.B, AAC.WS. Anchored, upper-case only — the caller
# normalises first, so anything still lower-case is a caller bug worth failing.
_TICKER = re.compile(r"^[A-Z]{1,6}(\.[A-Z]{1,2})?$")

# The longest symbol observed in the live catalogue. A cap belongs here rather
# than in each route, where the two copies had already drifted apart.
MAX_TICKER_LEN = 7


def normalize_ticker(raw: str) -> str:
    """Trim and upper-case. Does not validate — see `is_valid_ticker`."""
    return (raw or "").strip().upper()


def is_valid_ticker(raw: str) -> bool:
    """Whether `raw` is shaped like a symbol the US market actually lists."""
    sym = normalize_ticker(raw)
    return len(sym) <= MAX_TICKER_LEN and bool(_TICKER.match(sym))
