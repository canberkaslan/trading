"""The shape both sources hand to extraction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

YOUTUBE = "youtube"
X = "x"


@dataclass(frozen=True)
class RawItem:
    """One video or post as fetched. `text` is read by extraction and never stored.

    `published_at` is always a UTC-aware datetime: a source that cannot date an
    item drops it rather than building one of these, because an undated item
    cannot be kept out of a backtest that predates it.
    """

    source: str
    source_id: str
    channel_id: str
    url: str
    published_at: datetime
    text: str

    @property
    def content_sha256(self) -> str:
        """Fingerprint of what was extracted, so a later edit is detectable."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()
