from xueqiu.integrations.xueqiu.portfolio import _classify_rebalance_batch


def _line(symbol: str, prev: float, target: float, *, prev_adjusted: float | None = None) -> dict:
    item = {
        "stock_symbol": symbol,
        "prev_weight": prev,
        "prev_target_weight": prev,
        "target_weight": target,
    }
    if prev_adjusted is not None:
        item["prev_weight_adjusted"] = prev_adjusted
    return item


def test_sys_rebalancing_batch_not_manual_even_with_weight_change():
    batch = {
        "category": "sys_rebalancing",
        "status": "success",
        "rebalancing_histories": [
            _line("SZ002867", 50.0, 33.0, prev_adjusted=33.0),
        ],
    }
    has_manual, has_non_a = _classify_rebalance_batch(batch)
    assert has_manual is False
    assert has_non_a is False


def test_user_rebalancing_batch_counts_manual_change():
    batch = {
        "category": "user_rebalancing",
        "status": "success",
        "rebalancing_histories": [
            _line("SH600519", 10.0, 15.0),
        ],
    }
    has_manual, _ = _classify_rebalance_batch(batch)
    assert has_manual is True


def test_dividend_marker_in_batch_text_not_manual():
    batch = {
        "category": "",
        "status": "success",
        "comment": "分红送配",
        "rebalancing_histories": [
            _line("SH600519", 10.0, 10.0),
        ],
    }
    has_manual, _ = _classify_rebalance_batch(batch)
    assert has_manual is False
