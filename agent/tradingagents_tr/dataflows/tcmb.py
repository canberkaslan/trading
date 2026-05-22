"""TCMB EVDS — macro indicators (faiz, döviz rezervi, enflasyon).

Free API. Stub for Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class MacroSeries:
    series_id: str
    series_name_tr: str
    series_name_en: str
    values: dict[date, float]


def fetch_series(series_id: str, start: date, end: date) -> MacroSeries:
    """Fetch a TCMB EVDS series.

    Common series IDs:
      - TP.AB.A1: 1-week repo policy rate
      - TP.YOOC.YOOCY: USD/TRY
      - TP.RK.USD1.A: CBRT FX reserves USD
      - TP.FE.OKTG01: CPI YoY
    """
    raise NotImplementedError("tcmb.fetch_series — phase 1 stub")
