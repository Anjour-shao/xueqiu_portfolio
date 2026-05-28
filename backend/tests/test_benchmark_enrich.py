"""基准增量 enrich：累计收益应相对全序列首日。"""

from __future__ import annotations

import unittest

from xueqiu.domain.benchmark_series import enrich_benchmark_rows


class TestBenchmarkEnrich(unittest.TestCase):
    def test_cum_relative_to_series_start(self) -> None:
        rows = [
            {"ts_code": "000001.SH", "trade_date": "20260524", "close": 100.0},
            {"ts_code": "000001.SH", "trade_date": "20260525", "close": 101.0},
            {"ts_code": "000001.SH", "trade_date": "20260526", "close": 102.0},
        ]
        enriched = enrich_benchmark_rows(rows)
        by_date = {r["trade_date"]: r for r in enriched}
        self.assertEqual(by_date["20260524"]["cum_return_pct"], 0.0)
        self.assertEqual(by_date["20260526"]["cum_return_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
