from __future__ import annotations

from xueqiu.domain.copy_backtest import BacktestConfig
from xueqiu.domain.copy_strategies import StrategyId, run_strategy


def test_legacy_backtest_config_disables_heavy_rotate_guard() -> None:
    cfg = BacktestConfig(max_positions=5, forbid_rotate_heavy=False)

    assert cfg.max_positions == 5
    assert cfg.forbid_rotate_heavy is False


def test_run_strategy_clears_global_copy_backtest_config(monkeypatch) -> None:
    import xueqiu.domain.copy_backtest as cb
    import xueqiu.domain.copy_strategies as cs

    captured: dict[str, object] = {}

    def fake_run_backtest(cfg: BacktestConfig) -> dict:
        captured["max_positions"] = cfg.max_positions
        captured["forbid_rotate_heavy"] = cfg.forbid_rotate_heavy
        return {
            "initial_capital": 100000.0,
            "final_nav": 100000.0,
            "final_nav_hfq": 100000.0,
            "profit": 0.0,
            "profit_hfq": 0.0,
            "return_pct": 0.0,
            "return_pct_raw": 0.0,
            "cash": 100000.0,
            "cash_pct": 100.0,
            "portfolio_count": 0,
            "start_time": "",
            "end_time": "",
            "blocked_688": 0,
            "cap_triggers": 0,
            "rotate_triggers": 0,
            "rebalance_triggers": 0,
            "skipped_lot": 0,
            "skipped_small": 0,
            "trade_log_count": 0,
            "star_unlocked": False,
            "max_stock_pct": 20.0,
            "min_new_position_pct": 1.0,
            "max_positions": cfg.max_positions,
            "overview_win_rate": 0.0,
            "trade_logs": [],
            "source_stats": {},
            "positions": [],
            "equity_curve": [],
            "grouped_stats": [],
        }

    monkeypatch.setattr(cs, "run_backtest", fake_run_backtest)
    cb._RUN_CFG = BacktestConfig(max_positions=20, forbid_rotate_heavy=True)

    result = run_strategy(StrategyId.LEGACY_K5)

    assert result["max_positions"] == 5
    assert captured == {"max_positions": 5, "forbid_rotate_heavy": False}
    assert cb._RUN_CFG is None
