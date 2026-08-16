"""EVAL_START_DATE parsing — one cutoff, one meaning.

The scorecard and the realized ledger both scope themselves with this value.
If they disagree about what it means (naive vs UTC, or "unparseable → no
window"), they end up describing different books while looking like one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingagents_us.eval_window import eval_start_utc, parse_eval_start


class TestParseEvalStart:
    def test_plain_date_is_utc_midnight(self) -> None:
        assert parse_eval_start("2026-06-24") == datetime(2026, 6, 24, tzinfo=timezone.utc)

    def test_naive_timestamp_is_read_as_utc(self) -> None:
        # Broker fill timestamps are UTC; anchoring the boundary to local time
        # would reclassify trades on the cutoff day itself.
        assert parse_eval_start("2026-06-24T13:30:00") == datetime(
            2026, 6, 24, 13, 30, tzinfo=timezone.utc
        )

    def test_offset_timestamp_is_converted_to_utc(self) -> None:
        assert parse_eval_start("2026-06-24T16:30:00+03:00") == datetime(
            2026, 6, 24, 13, 30, tzinfo=timezone.utc
        )

    def test_zulu_suffix_parses(self) -> None:
        assert parse_eval_start("2026-06-24T00:00:00Z") == datetime(
            2026, 6, 24, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_unset_means_no_window(self, raw: str | None) -> None:
        assert parse_eval_start(raw) is None

    @pytest.mark.parametrize("raw", ["24-06-2026", "june 24", "2026-13-01", "yes"])
    def test_malformed_raises_instead_of_widening_the_window(self, raw: str) -> None:
        # Falling back to "no cutoff" would report all-time numbers under an
        # eval-window label — the exact self-flattery the ledger guards against.
        with pytest.raises(ValueError, match="EVAL_START_DATE"):
            parse_eval_start(raw)


class TestEvalStartUtc:
    def test_reads_the_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_START_DATE", "2026-06-24")
        assert eval_start_utc() == datetime(2026, 6, 24, tzinfo=timezone.utc)

    def test_missing_env_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_START_DATE", raising=False)
        assert eval_start_utc() is None

    def test_explicit_mapping_overrides_the_process_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_START_DATE", "2026-06-24")
        assert eval_start_utc({}) is None
