"""个人实盘账户：持仓、现金、抄作业策略调仓方案。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, text

from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.domain.copy_backtest import _is_star_market, _lot_size
from xueqiu.domain.copy_strategies import StrategyId, run_strategy, strategy_to_backtest_response
from xueqiu.integrations.sina.spot import fetch_spot_prices
from xueqiu.storage.db import get_conn, personal_accounts_table, personal_holdings_table, personal_trades_table

DEFAULT_STRATEGY_ID = StrategyId.ROUTE_G_CONVICTION_TRUST.value
DEFAULT_ACCOUNT_NAME = "股票账户1"


def _norm_code(raw: str) -> str:
    code = str(raw or "").strip().upper()
    if not code:
        raise ValueError("空股票代码")
    if len(code) >= 8 and code[:2] in {"SH", "SZ", "BJ"}:
        return code
    digits = code.split(".")[0]
    if digits.isdigit() and len(digits) == 6:
        if digits.startswith(("5", "6", "9")):
            return f"SH{digits}"
        return f"SZ{digits}"
    return to_xueqiu_code(code)


def _now() -> datetime:
    return datetime.now()


def _ensure_default_account(conn) -> int:
    row = conn.execute(select(personal_accounts_table).limit(1)).mappings().first()
    if row:
        return int(row["id"])
    conn.execute(
        personal_accounts_table.insert().values(
            name=DEFAULT_ACCOUNT_NAME,
            cash=0.0,
            strategy_id=DEFAULT_STRATEGY_ID,
            updated_at=_now(),
        )
    )
    row = conn.execute(select(personal_accounts_table).limit(1)).mappings().first()
    return int(row["id"])


def _holding_days(opened_at: str | None) -> int | None:
    if not opened_at:
        return None
    try:
        opened_date = datetime.strptime(opened_at[:10], "%Y-%m-%d").date()
        return max(0, (_now().date() - opened_date).days)
    except ValueError:
        return None


def _round_lot_delta(code: str, delta: float) -> int:
    """将股数差规整到整手（卖出不超过持仓）。"""
    lot = _lot_size(code)
    if abs(delta) < lot:
        return 0
    sign = 1 if delta > 0 else -1
    lots = int(abs(delta) // lot)
    if lots <= 0:
        return 0
    return sign * lots * lot


def get_personal_account_raw() -> dict[str, Any] | None:
    with get_conn() as conn:
        account = conn.execute(select(personal_accounts_table).limit(1)).mappings().first()
        if not account:
            return None
        account_id = int(account["id"])
        holdings = conn.execute(
            select(personal_holdings_table).where(personal_holdings_table.c.account_id == account_id)
        ).mappings().all()
        return {
            "id": account_id,
            "name": str(account["name"]),
            "cash": float(account["cash"] or 0),
            "strategy_id": str(account["strategy_id"] or DEFAULT_STRATEGY_ID),
            "updated_at": account["updated_at"],
            "holdings": [dict(h) for h in holdings],
        }


def build_personal_account_view() -> dict[str, Any]:
    raw = get_personal_account_raw()
    if not raw:
        with get_conn() as conn:
            account_id = _ensure_default_account(conn)
            raw = {
                "id": account_id,
                "name": DEFAULT_ACCOUNT_NAME,
                "cash": 0.0,
                "strategy_id": DEFAULT_STRATEGY_ID,
                "updated_at": _now(),
                "holdings": [],
            }

    codes = [str(h["ts_code"]) for h in raw["holdings"]]
    prices = fetch_spot_prices(codes)

    holdings_out: list[dict[str, Any]] = []
    market_value = 0.0
    daily_pnl = 0.0
    holding_pnl = 0.0
    cost_basis = 0.0

    for h in raw["holdings"]:
        code = str(h["ts_code"])
        shares = int(h["shares"] or 0)
        cost = float(h["cost_price"] or 0)
        price = prices.get(code)
        mkt = round(price * shares, 2) if price and shares > 0 else 0.0
        market_value += mkt
        if price and shares > 0 and cost > 0:
            holding_pnl += round((price - cost) * shares, 2)
            cost_basis += cost * shares
        opened_at = h.get("opened_at")
        holdings_out.append(
            {
                "ts_code": code,
                "stock_name": str(h["stock_name"]),
                "shares": shares,
                "cost_price": cost,
                "opened_at": opened_at,
                "holding_days": _holding_days(str(opened_at) if opened_at else None),
                "price": round(price, 4) if price else None,
                "market_value": mkt,
                "unrealized_pnl_pct": round((price - cost) / cost * 100, 2) if price and cost > 0 else None,
                "unrealized_pnl_amount": round((price - cost) * shares, 2) if price and cost > 0 else None,
            }
        )

    cash = float(raw["cash"])
    total_assets = round(market_value + cash, 2)
    for item in holdings_out:
        if total_assets > 0 and item["market_value"]:
            item["weight_pct"] = round(item["market_value"] / total_assets * 100, 2)

    holdings_out.sort(key=lambda x: -(x.get("weight_pct") or 0))

    holding_pnl_pct = round(holding_pnl / cost_basis * 100, 2) if cost_basis > 0 else None

    return {
        "name": raw["name"],
        "cash": cash,
        "strategy_id": raw["strategy_id"],
        "market_value": round(market_value, 2),
        "total_assets": total_assets,
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": None,
        "holding_pnl": round(holding_pnl, 2),
        "holding_pnl_pct": holding_pnl_pct,
        "holdings": holdings_out,
        "updated_at": raw["updated_at"].isoformat(sep=" ", timespec="seconds") if raw.get("updated_at") else None,
    }


def update_personal_cash(cash: float) -> dict[str, Any]:
    if cash < 0:
        raise ValueError("现金不能为负")
    with get_conn() as conn:
        account_id = _ensure_default_account(conn)
        conn.execute(
            personal_accounts_table.update()
            .where(personal_accounts_table.c.id == account_id)
            .values(cash=round(cash, 2), updated_at=_now())
        )
    from xueqiu.domain.digest_holdings_export import sync_digest_holdings_after_change

    sync_digest_holdings_after_change()
    return build_personal_account_view()


def update_personal_strategy(strategy_id: str) -> dict[str, Any]:
    sid = strategy_id.strip()
    if not sid:
        raise ValueError("策略 ID 不能为空")
    try:
        StrategyId(sid)
    except ValueError as exc:
        raise ValueError(f"未知策略: {sid}") from exc
    with get_conn() as conn:
        account_id = _ensure_default_account(conn)
        conn.execute(
            personal_accounts_table.update()
            .where(personal_accounts_table.c.id == account_id)
            .values(strategy_id=sid, updated_at=_now())
        )
    from xueqiu.domain.digest_holdings_export import sync_digest_holdings_after_change

    sync_digest_holdings_after_change()
    return build_personal_account_view()


def execute_personal_trade(
    *,
    action: str,
    ts_code: str,
    shares: int,
    price: float | None = None,
    stock_name: str | None = None,
) -> dict[str, Any]:
    action = action.strip()
    if action not in ("买入", "卖出"):
        raise ValueError("action 须为 买入 或 卖出")
    code = _norm_code(ts_code)
    if not code:
        raise ValueError("股票代码不能为空")
    if shares <= 0:
        raise ValueError("股数须为正整数")
    lot = _lot_size(code)
    if shares % lot != 0:
        raise ValueError(f"须为整手交易（{lot} 股/手）")

    spot = fetch_spot_prices([code])
    trade_price = price if price and price > 0 else spot.get(code)
    if not trade_price or trade_price <= 0:
        raise ValueError("无法获取成交价，请手动填写价格")

    amount = round(trade_price * shares, 2)
    now = _now()

    with get_conn() as conn:
        account_id = _ensure_default_account(conn)
        account = conn.execute(
            select(personal_accounts_table).where(personal_accounts_table.c.id == account_id)
        ).mappings().first()
        cash = float(account["cash"] or 0)

        holding = conn.execute(
            select(personal_holdings_table).where(
                personal_holdings_table.c.account_id == account_id,
                personal_holdings_table.c.ts_code == code,
            )
        ).mappings().first()

        if action == "买入":
            if cash < amount:
                raise ValueError(f"现金不足（需 {amount:.2f}，可用 {cash:.2f}）")
            cash = round(cash - amount, 2)
            cur_shares = int(holding["shares"]) if holding else 0
            cur_cost = float(holding["cost_price"]) if holding else 0.0
            new_shares = cur_shares + shares
            new_cost = round((cur_cost * cur_shares + trade_price * shares) / new_shares, 4) if new_shares > 0 else trade_price
            name = stock_name or (holding and str(holding["stock_name"])) or code
            opened_at = holding and holding.get("opened_at")
            if not opened_at:
                opened_at = now.strftime("%Y-%m-%d")
            if holding:
                conn.execute(
                    personal_holdings_table.update()
                    .where(
                        personal_holdings_table.c.account_id == account_id,
                        personal_holdings_table.c.ts_code == code,
                    )
                    .values(
                        shares=new_shares,
                        cost_price=new_cost,
                        stock_name=name,
                        opened_at=opened_at,
                        updated_at=now,
                    )
                )
            else:
                conn.execute(
                    personal_holdings_table.insert().values(
                        account_id=account_id,
                        ts_code=code,
                        stock_name=name,
                        shares=new_shares,
                        cost_price=new_cost,
                        opened_at=opened_at,
                        updated_at=now,
                    )
                )
        else:
            if not holding or int(holding["shares"]) < shares:
                held = int(holding["shares"]) if holding else 0
                raise ValueError(f"持仓不足（持有 {held} 股）")
            cash = round(cash + amount, 2)
            new_shares = int(holding["shares"]) - shares
            if new_shares <= 0:
                conn.execute(
                    personal_holdings_table.delete().where(
                        personal_holdings_table.c.account_id == account_id,
                        personal_holdings_table.c.ts_code == code,
                    )
                )
            else:
                conn.execute(
                    personal_holdings_table.update()
                    .where(
                        personal_holdings_table.c.account_id == account_id,
                        personal_holdings_table.c.ts_code == code,
                    )
                    .values(shares=new_shares, updated_at=now)
                )

        conn.execute(
            personal_accounts_table.update()
            .where(personal_accounts_table.c.id == account_id)
            .values(cash=cash, updated_at=now)
        )
        conn.execute(
            personal_trades_table.insert().values(
                account_id=account_id,
                trade_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                ts_code=code,
                stock_name=stock_name or (holding and str(holding["stock_name"])) or code,
                action=action,
                shares=shares,
                price=round(trade_price, 4),
                amount=amount,
                created_at=now,
            )
        )

    from xueqiu.domain.digest_holdings_export import sync_digest_holdings_after_change

    sync_digest_holdings_after_change()
    return build_personal_account_view()


def _strategy_label(sid: str) -> str:
    from xueqiu.domain.copy_strategies import STRATEGY_CATALOG

    for spec in STRATEGY_CATALOG:
        if spec.id.value == sid:
            return spec.label
    return sid


def _norm_rebalance_time(value: str) -> str:
    return str(value or "").strip()[:19]


INCREMENTAL_PLAN_WAIT_NOTE = (
    "调仓方案仅在「关注组合有新调仓并已推送」时生成，针对本次新信号给出参考，"
    "不要求追历史仓位，也不会因策略目标变化而建议你改动现有持仓。请等待下次组合更新通知。"
)


def compute_copy_rebalance_plan_from_digest_updates(
    updates: list[Any],
    *,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """根据「今晚推送的调仓批次」与抄作业策略，给出建议仓位（非追历史）。"""
    from xueqiu.domain.copy_conviction import HEAVY_HOLDER_PCT, conviction_cap_pct

    view = build_personal_account_view()
    sid = strategy_id or view["strategy_id"] or DEFAULT_STRATEGY_ID
    label = _strategy_label(sid)
    total_assets = float(view["total_assets"] or 0)

    if not updates:
        return {
            "strategy_id": sid,
            "strategy_label": label,
            "total_assets": total_assets,
            "plan_mode": "incremental",
            "actions": [],
            "note": INCREMENTAL_PLAN_WAIT_NOTE,
        }

    if total_assets <= 0:
        return {
            "strategy_id": sid,
            "strategy_label": label,
            "total_assets": total_assets,
            "plan_mode": "incremental",
            "actions": [],
            "note": "未配置个人持仓/现金，无法计算建议仓位（请在前端「我的持仓」维护或配置 MY_HOLDINGS）",
        }

    mirror: dict[tuple[str, str], float] = {}
    sell_codes: set[str] = set()
    master_weight: dict[str, float] = {}
    names: dict[str, str] = {}

    for upd in updates:
        pid = str(getattr(upd, "portfolio_id", "") or "")
        for batch in getattr(upd, "batches", []) or []:
            for record in batch.records or []:
                action = str(record.get("action") or "")
                try:
                    code = _norm_code(str(record.get("code") or ""))
                except ValueError:
                    continue
                names[code] = str(record.get("name") or names.get(code) or code)
                to_w = float(record.get("to_weight") or 0)
                from_w = float(record.get("from_weight") or 0)
                if action == "买入" and to_w >= HEAVY_HOLDER_PCT:
                    mirror[(pid, code)] = max(mirror.get((pid, code), 0.0), to_w)
                    master_weight[code] = max(master_weight.get(code, 0.0), to_w)
                elif action == "卖出" and from_w > 1.0 and to_w <= 1.0:
                    sell_codes.add(code)

    target_codes = set(master_weight.keys()) | sell_codes
    if not target_codes:
        return {
            "strategy_id": sid,
            "strategy_label": label,
            "total_assets": total_assets,
            "plan_mode": "incremental",
            "actions": [],
            "note": "本次调仓无 ≥20% 重仓跟单信号（或均为卖出轻仓），策略建议保持现有持仓。",
        }

    current_map = {h["ts_code"]: h for h in view["holdings"]}
    prices = fetch_spot_prices(list(target_codes))

    actions: list[dict[str, Any]] = []
    for code in sorted(target_codes):
        cur = current_map.get(code)
        cur_shares = int(cur["shares"]) if cur else 0
        cur_weight = float(cur.get("weight_pct") or 0) if cur else 0.0
        name = names.get(code) or (cur and cur["stock_name"]) or code

        if code in master_weight:
            cap = conviction_cap_pct(master_weight[code], mirror, code, trust=1.0)
            target_pct = round(cap * 100, 2)
        else:
            target_pct = 0.0

        price = prices.get(code)
        if price and total_assets > 0:
            target_shares = int(round(total_assets * (target_pct / 100) / price))
            target_shares = (target_shares // _lot_size(code)) * _lot_size(code)
        else:
            target_shares = 0

        delta = target_shares - cur_shares
        rounded_delta = _round_lot_delta(code, delta)
        if rounded_delta == 0:
            continue

        action = "买入" if rounded_delta > 0 else "卖出"
        shares_abs = abs(rounded_delta)
        tgt_shares = cur_shares + rounded_delta
        amount = round(shares_abs * price, 2) if price else None

        actions.append(
            {
                "action": action,
                "ts_code": code,
                "stock_name": name,
                "shares_delta": shares_abs,
                "current_shares": cur_shares,
                "target_shares": tgt_shares,
                "current_weight_pct": round(cur_weight, 2),
                "target_weight_pct": target_pct,
                "price": round(price, 4) if price else None,
                "amount": amount,
            }
        )

    actions.sort(key=lambda x: (-(x.get("amount") or 0), x["ts_code"]))

    if actions:
        note = (
            f"以下根据今晚调仓信号与「{label}」规则（≥{int(HEAVY_HOLDER_PCT)}% 才跟）"
            f"给出的建议仓位，供参考。"
        )
    else:
        note = "本次调仓信号与您的持仓已基本一致（整手口径），无需调整。"

    return {
        "strategy_id": sid,
        "strategy_label": label,
        "total_assets": total_assets,
        "plan_mode": "incremental",
        "actions": actions,
        "note": note,
    }


def compute_copy_rebalance_plan(
    *,
    strategy_id: str | None = None,
    initial_capital: float | None = None,
    rebalance_times: list[str] | None = None,
    trigger_codes: list[str] | None = None,
) -> dict[str, Any]:
    """增量调仓方案：仅在有新组合调仓批次时，按策略对该批信号的跟单动作给出建议。"""
    view = build_personal_account_view()
    sid = strategy_id or view["strategy_id"] or DEFAULT_STRATEGY_ID
    label = _strategy_label(sid)
    total_assets = float(view["total_assets"] or 0)
    if total_assets <= 0:
        return {
            "strategy_id": sid,
            "strategy_label": label,
            "total_assets": total_assets,
            "plan_mode": "incremental",
            "actions": [],
            "note": "账户总资产为 0，无法生成调仓方案",
        }

    if not rebalance_times:
        return {
            "strategy_id": sid,
            "strategy_label": label,
            "total_assets": total_assets,
            "plan_mode": "incremental",
            "actions": [],
            "note": INCREMENTAL_PLAN_WAIT_NOTE,
        }

    capital = initial_capital if initial_capital and initial_capital > 0 else total_assets
    time_keys = {_norm_rebalance_time(t) for t in rebalance_times if t}
    code_keys: set[str] = set()
    for raw_code in trigger_codes or []:
        try:
            code_keys.add(_norm_code(raw_code))
        except ValueError:
            continue

    try:
        raw = run_strategy(StrategyId(sid), initial_capital=capital)
        result = strategy_to_backtest_response(raw)
    except Exception as exc:
        return {
            "strategy_id": sid,
            "strategy_label": label,
            "total_assets": total_assets,
            "plan_mode": "incremental",
            "actions": [],
            "note": f"策略计算失败: {exc}",
        }

    current_map = {h["ts_code"]: h for h in view["holdings"]}
    trade_logs = result.get("trade_logs") or []

    matched_logs: list[dict[str, Any]] = []
    for log in trade_logs:
        log_time = _norm_rebalance_time(str(log.get("trade_time") or ""))
        code = str(log.get("ts_code") or "")
        if log_time not in time_keys:
            continue
        if code_keys and code not in code_keys:
            continue
        action = str(log.get("action") or "")
        if action not in ("买入", "卖出"):
            continue
        matched_logs.append(log)

    codes_for_price = {str(log.get("ts_code") or "") for log in matched_logs} | set(current_map.keys())
    prices = fetch_spot_prices([c for c in codes_for_price if c])

    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for log in matched_logs:
        code = str(log.get("ts_code") or "")
        action = str(log.get("action") or "")
        key = (code, action)
        if key in seen:
            continue
        seen.add(key)

        cur = current_map.get(code)
        cur_shares = int(cur["shares"]) if cur else 0
        cur_weight = float(cur.get("weight_pct") or 0) if cur else 0.0
        qty_delta = abs(float(log.get("qty_delta") or 0))
        log_nav = float(log.get("nav_after") or capital)
        if log_nav <= 0 or qty_delta <= 0:
            continue

        scaled = qty_delta * (total_assets / log_nav)
        price = prices.get(code) or (float(log["price"]) if log.get("price") else None)
        name = (cur and cur["stock_name"]) or str(log.get("stock_name") or code)

        if action == "卖出":
            if cur_shares <= 0:
                continue
            scaled = min(scaled, cur_shares)
            rounded = _round_lot_delta(code, -scaled)
            if rounded >= 0:
                continue
            shares_abs = abs(rounded)
            target_shares = max(0, cur_shares - shares_abs)
        else:
            rounded = _round_lot_delta(code, scaled)
            if rounded <= 0:
                continue
            shares_abs = rounded
            target_shares = cur_shares + shares_abs

        tgt_weight = float(log.get("our_weight_pct") or 0)
        amount = round(shares_abs * price, 2) if price else None
        actions.append(
            {
                "action": action,
                "ts_code": code,
                "stock_name": name,
                "shares_delta": shares_abs,
                "current_shares": cur_shares,
                "target_shares": target_shares,
                "current_weight_pct": round(cur_weight, 2),
                "target_weight_pct": round(tgt_weight, 2),
                "price": round(price, 4) if price else None,
                "amount": amount,
            }
        )

    actions.sort(key=lambda x: (-(x.get("amount") or 0), x["ts_code"]))

    if actions:
        batch_hint = "、".join(sorted(time_keys)[:2])
        if len(time_keys) > 2:
            batch_hint += f" 等{len(time_keys)}批"
        note = f"以下仅针对本次新调仓（{batch_hint}）按策略跟单规则给出的参考，非追历史仓位。"
    else:
        note = (
            "本次组合调仓信号经策略过滤后（如未达重仓门槛）无跟单建议，"
            "可保持现有持仓，等待下次更新。"
        )

    return {
        "strategy_id": sid,
        "strategy_label": label,
        "total_assets": total_assets,
        "sim_capital": capital,
        "plan_mode": "incremental",
        "actions": actions,
        "note": note,
    }


def seed_personal_account_from_config(
    holdings: list[dict[str, Any]],
    *,
    name: str = DEFAULT_ACCOUNT_NAME,
    cash: float | None = None,
    total_assets: float | None = None,
) -> bool:
    """若库内无持仓，用配置初始化（供 digest 迁移 MY_HOLDINGS）。"""
    if not holdings:
        return False
    with get_conn() as conn:
        account_id = _ensure_default_account(conn)
        existing = conn.execute(
            select(personal_holdings_table).where(personal_holdings_table.c.account_id == account_id).limit(1)
        ).first()
        if existing:
            return False

        now = _now()
        if name:
            conn.execute(
                personal_accounts_table.update()
                .where(personal_accounts_table.c.id == account_id)
                .values(name=name, updated_at=now)
            )

        market_value_est = 0.0
        for item in holdings:
            code = _norm_code(str(item.get("code", "")))
            if not code:
                continue
            shares = int(item.get("shares") or 0)
            cost = float(item.get("cost_price") or 0)
            name_h = str(item.get("name") or code)
            opened = item.get("opened_at")
            if not opened and item.get("holding_days"):
                try:
                    days = int(item["holding_days"])
                    from datetime import timedelta

                    opened = (now.date() - timedelta(days=days)).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    opened = None
            conn.execute(
                personal_holdings_table.insert().values(
                    account_id=account_id,
                    ts_code=code,
                    stock_name=name_h,
                    shares=shares,
                    cost_price=cost,
                    opened_at=opened,
                    updated_at=now,
                )
            )
            market_value_est += cost * shares

        if cash is not None:
            conn.execute(
                personal_accounts_table.update()
                .where(personal_accounts_table.c.id == account_id)
                .values(cash=round(cash, 2), updated_at=now)
            )
        elif total_assets is not None and total_assets >= market_value_est:
            conn.execute(
                personal_accounts_table.update()
                .where(personal_accounts_table.c.id == account_id)
                .values(cash=round(total_assets - market_value_est, 2), updated_at=now)
            )
        return True


def holdings_for_digest() -> list[dict[str, Any]]:
    """供 daily_digest 使用的持仓列表（code/name/shares/cost_price 格式）。"""
    raw = get_personal_account_raw()
    if not raw or not raw["holdings"]:
        return []
    out: list[dict[str, Any]] = []
    for h in raw["holdings"]:
        opened = h.get("opened_at")
        item: dict[str, Any] = {
            "code": str(h["ts_code"]),
            "name": str(h["stock_name"]),
            "shares": int(h["shares"]),
            "cost_price": float(h["cost_price"]),
        }
        if opened:
            item["opened_at"] = str(opened)
        days = _holding_days(str(opened) if opened else None)
        if days is not None:
            item["holding_days"] = days
        out.append(item)
    return out


def account_meta_for_digest() -> dict[str, Any]:
    raw = get_personal_account_raw()
    if not raw:
        return {"name": DEFAULT_ACCOUNT_NAME}
    return {
        "name": raw["name"],
        "cash": float(raw["cash"] or 0),
        "total_assets": None,
    }
