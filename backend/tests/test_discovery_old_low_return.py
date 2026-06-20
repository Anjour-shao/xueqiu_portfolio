from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from xueqiu.domain.discovery_mine import (
    _apply_old_low_return_filter,
    _cube_founded_before_years,
    _MIN_CUM_RETURN_10X_PCT,
)


def _ms_years_ago(years: float) -> int:
    dt = datetime.now(timezone.utc) - timedelta(days=int(years * 365.25))
    return int(dt.timestamp() * 1000)


def test_cube_founded_before_eight_years():
    assert _cube_founded_before_years(_ms_years_ago(10), 8) is True
    assert _cube_founded_before_years(_ms_years_ago(5), 8) is False


def test_old_low_return_filter_adds_reason():
    reasons: list[str] = []
    _apply_old_low_return_filter(
        code="ZH000001",
        api=MagicMock(),
        created_at_ms=_ms_years_ago(10),
        cum_return_pct=_MIN_CUM_RETURN_10X_PCT - 1,
        reasons=reasons,
    )
    assert reasons == ["old_low_return"]


def test_old_low_return_filter_skips_young_cube():
    reasons: list[str] = []
    _apply_old_low_return_filter(
        code="ZH000001",
        api=MagicMock(),
        created_at_ms=_ms_years_ago(3),
        cum_return_pct=100.0,
        reasons=reasons,
    )
    assert reasons == []


def test_old_low_return_filter_skips_10x_return():
    reasons: list[str] = []
    _apply_old_low_return_filter(
        code="ZH000001",
        api=MagicMock(),
        created_at_ms=_ms_years_ago(12),
        cum_return_pct=_MIN_CUM_RETURN_10X_PCT,
        reasons=reasons,
    )
    assert reasons == []
