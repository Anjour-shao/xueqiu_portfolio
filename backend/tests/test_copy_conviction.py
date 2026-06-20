"""Unit tests for conviction tier + portfolio trust helpers."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.domain.copy_conviction import (
    HeavyLegEvent,
    conviction_cap_pct,
    portfolio_trust_at,
)


def test_conviction_cap_trial_tier():
    mirror = {("ZH1", "SZ000001"): 22.0}
    cap = conviction_cap_pct(22.0, mirror, "SZ000001", trust=1.0)
    assert cap == 0.15


def test_conviction_cap_belief_with_consensus():
    mirror = {
        ("ZH1", "SZ000001"): 36.0,
        ("ZH2", "SZ000001"): 25.0,
    }
    cap = conviction_cap_pct(36.0, mirror, "SZ000001", trust=1.0)
    # belief 0.30 * (1 + 0.05) = 0.315
    assert abs(cap - 0.315) < 1e-9


def test_conviction_cap_strong_tier():
    mirror = {
        ("ZH1", "SZ000001"): 55.0,
        ("ZH2", "SZ000001"): 30.0,
        ("ZH3", "SZ000001"): 22.0,
    }
    cap = conviction_cap_pct(55.0, mirror, "SZ000001", trust=1.0)
    # strong 0.38 * (1 + 0.10) = 0.418 -> capped at 0.40
    assert cap == 0.40


def test_conviction_cap_below_threshold_returns_zero():
    cap = conviction_cap_pct(15.0, {}, "SZ000001", trust=1.0)
    assert cap == 0.0


def test_portfolio_trust_neutral_until_min_legs():
    events = [
        HeavyLegEvent("2020-01-01", "ZH1", "SZ000001", 25.0, 10.0),
        HeavyLegEvent("2020-02-01", "ZH1", "SZ000002", 30.0, -5.0),
    ]
    assert portfolio_trust_at(events, "ZH1", "2021-01-01", min_legs=3) == 1.0


def test_portfolio_trust_discounts_low_win_rate():
    events = [
        HeavyLegEvent("2020-01-01", "ZH1", "SZ000001", 25.0, 10.0),
        HeavyLegEvent("2020-02-01", "ZH1", "SZ000002", 30.0, -5.0),
        HeavyLegEvent("2020-03-01", "ZH1", "SZ000003", 40.0, -8.0),
        HeavyLegEvent("2020-04-01", "ZH1", "SZ000004", 35.0, -3.0),
    ]
    # 1 win / 4 = 25% -> floor 0.75
    assert portfolio_trust_at(events, "ZH1", "2021-01-01", min_legs=3) == 0.75


def test_portfolio_trust_uses_only_past_legs():
    events = [
        HeavyLegEvent("2020-01-01", "ZH1", "SZ000001", 25.0, 10.0),
        HeavyLegEvent("2020-06-01", "ZH1", "SZ000002", 30.0, 10.0),
        HeavyLegEvent("2020-12-01", "ZH1", "SZ000003", 40.0, 10.0),
        HeavyLegEvent("2021-06-01", "ZH1", "SZ000004", 35.0, -50.0),
    ]
    # as of mid-2021 only first 3 legs count -> 100% win rate
    assert portfolio_trust_at(events, "ZH1", "2021-01-01", min_legs=3) == 1.0
