"""分红送配与手动调仓区分。"""

from xueqiu.integrations.xueqiu.portfolio import (
    _line_is_dividend_corporate,
    _line_is_manual_rebalance,
    _parse_api_stock_item,
)


def test_dividend_micro_weight_drift_not_manual():
    item = {
        "stock_symbol": "SZ002738",
        "stock_name": "中矿资源",
        "prev_weight": 0.42,
        "target_weight": 0.36,
        "price": 65.89,
    }
    assert _line_is_dividend_corporate(item) is True
    assert _line_is_manual_rebalance(item) is False
    assert _parse_api_stock_item(item) is None


def test_real_decrease_still_manual():
    item = {
        "stock_symbol": "SZ300476",
        "stock_name": "胜宏科技",
        "prev_weight": 28.16,
        "target_weight": 49.0,
        "price": 100.0,
    }
    assert _line_is_dividend_corporate(item) is False
    assert _line_is_manual_rebalance(item) is True
    assert _parse_api_stock_item(item) is not None


def test_dividend_marker_in_nested_field():
    item = {
        "stock_symbol": "SZ002738",
        "stock_name": "中矿资源",
        "prev_weight": 5.0,
        "target_weight": 5.0,
        "meta": {"action_name": "分红送配"},
        "price": 65.89,
    }
    assert _line_is_dividend_corporate(item) is True
