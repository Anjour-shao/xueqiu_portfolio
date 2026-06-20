"""信念分级 + 师傅信用：抄作业策略辅助逻辑。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from xueqiu.domain.nav_engine import TradeInput, VirtualFund, resolve_price_hfq

HEAVY_HOLDER_PCT = 20.0
TIER_TRIAL_CAP = 0.15
TIER_BELIEF_CAP = 0.30
TIER_STRONG_CAP = 0.38
CONSENSUS_BONUS_PCT = 0.05
TRUST_FLOOR = 0.75
TRUST_MIN_LEGS = 3


@dataclass(frozen=True)
class HeavyLegEvent:
    trade_time: str
    account_code: str
    ts_code: str
    from_weight: float
    leg_return_pct: float


def _holders_for_code(mirror: dict[tuple[str, str], float], code: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for (portfolio, ts_code), weight in mirror.items():
        if ts_code == code and weight > 1e-9:
            out[portfolio] = weight
    return out


def build_heavy_leg_events(
    all_trades: list[tuple[str, str, TradeInput]],
    adj_map: dict[tuple[str, str], float],
    *,
    threshold: float = HEAVY_HOLDER_PCT,
) -> list[HeavyLegEvent]:
    """回放各账户调仓，收集卖出/减仓时的重仓 leg（无未来数据，供 trust_at 过滤）。"""
    by_acct: dict[str, list[tuple[str, str, TradeInput]]] = defaultdict(list)
    for acct, name, trade in all_trades:
        by_acct[acct].append((acct, name, trade))

    events: list[HeavyLegEvent] = []
    for acct, rows in by_acct.items():
        fund = VirtualFund()
        for acct_code, _name, trade in rows:
            hfq = resolve_price_hfq(trade, adj_map)
            if not hfq or hfq <= 0:
                continue
            raw = float(trade.price) if trade.price and trade.price > 0 else None
            nav_pre = fund.nav({trade.ts_code: hfq})
            leg, _, _ = fund.apply_trade(
                trade.ts_code,
                trade.stock_name,
                float(trade.from_weight),
                float(trade.to_weight),
                hfq,
                nav_pre,
                raw_price=raw,
            )
            fund.last_weights[trade.ts_code] = float(trade.to_weight)
            fund.last_marks[trade.ts_code] = hfq
            if trade.to_weight + 1e-9 < trade.from_weight and trade.from_weight >= threshold:
                if leg is not None:
                    events.append(
                        HeavyLegEvent(
                            trade_time=trade.trade_time,
                            account_code=acct_code,
                            ts_code=trade.ts_code,
                            from_weight=float(trade.from_weight),
                            leg_return_pct=float(leg),
                        )
                    )
    events.sort(key=lambda e: (e.trade_time, e.account_code, e.ts_code))
    return events


def portfolio_trust_at(
    events: list[HeavyLegEvent],
    account_code: str,
    as_of_time: str,
    *,
    min_legs: int = TRUST_MIN_LEGS,
    threshold: float = HEAVY_HOLDER_PCT,
) -> float:
    """按 as_of_time 之前的历史重仓 leg 胜率，返回 [0.75, 1.0] 信用乘数。"""
    legs = [
        e
        for e in events
        if e.account_code == account_code
        and e.trade_time < as_of_time
        and e.from_weight >= threshold
    ]
    if len(legs) < min_legs:
        return 1.0
    win_rate = sum(1 for e in legs if e.leg_return_pct >= 0) / len(legs)
    if win_rate >= 0.85:
        return 1.0
    if win_rate >= 0.75:
        return 0.92
    if win_rate >= 0.65:
        return 0.85
    return TRUST_FLOOR


def conviction_cap_pct(
    master_to_weight: float,
    mirror: dict[tuple[str, str], float],
    code: str,
    trust: float,
    *,
    hard_cap: float = 0.40,
    heavy_pct: float = HEAVY_HOLDER_PCT,
) -> float:
    """信念分级目标上限（占 NAV 比例），含共识加成与师傅信用。"""
    holders = _holders_for_code(mirror, code)
    weights = set(holders.values())
    weights.add(float(master_to_weight))
    max_master = max(weights, default=0.0)
    heavy_count = sum(1 for w in weights if w >= heavy_pct)

    if max_master < heavy_pct:
        return 0.0

    if max_master >= 50.0 or heavy_count >= 3:
        base = TIER_STRONG_CAP
    elif max_master >= 35.0 or heavy_count >= 2:
        base = TIER_BELIEF_CAP
    else:
        base = TIER_TRIAL_CAP

    if heavy_count >= 2:
        base *= 1.0 + CONSENSUS_BONUS_PCT * (heavy_count - 1)

    base *= max(TRUST_FLOOR, min(1.0, trust))
    return min(base, hard_cap)


def consensus_trust_for_code(
    events: list[HeavyLegEvent],
    mirror: dict[tuple[str, str], float],
    code: str,
    as_of_time: str,
    *,
    min_legs: int = TRUST_MIN_LEGS,
    heavy_pct: float = HEAVY_HOLDER_PCT,
) -> float:
    """多师傅共识时取最低信用（保守）。"""
    holders = _holders_for_code(mirror, code)
    heavy_accounts = [p for p, w in holders.items() if w >= heavy_pct]
    if not heavy_accounts:
        return 1.0
    trusts = [
        portfolio_trust_at(events, acct, as_of_time, min_legs=min_legs, threshold=heavy_pct)
        for acct in heavy_accounts
    ]
    return min(trusts)
