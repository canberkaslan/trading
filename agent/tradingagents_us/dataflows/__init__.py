"""TR-specific dataflows. KAP, TCMB, Matriks (BIST) + Polygon, EDGAR, FRED, Reddit (US)."""

from .fred import FREDClient
from .polygon import PolygonClient
from .sec_edgar import EdgarClient

__all__ = ["PolygonClient", "EdgarClient", "FREDClient"]
