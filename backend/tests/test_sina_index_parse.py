"""新浪指数 K 线解析单测（不依赖外网）。"""

from __future__ import annotations

import unittest

from xueqiu.integrations.sina.index import parse_sina_kline_payload, rows_to_closes


class TestSinaIndexParse(unittest.TestCase):
    def test_direct_json_array(self) -> None:
        text = '[{"day":"2026-05-26","open":"3400","high":"3410","low":"3390","close":"3405.12","volume":"1"}]'
        rows = parse_sina_kline_payload(text)
        self.assertEqual(len(rows), 1)
        closes = rows_to_closes(rows)
        self.assertEqual(closes["20260526"], 3405.12)

    def test_jsonp_wrapper(self) -> None:
        text = 'var _sh000001_240_123=( [{"day":"2026-05-25","close":"3390.5"}] );'
        rows = parse_sina_kline_payload(text)
        self.assertEqual(len(rows), 1)
        closes = rows_to_closes(rows)
        self.assertEqual(closes["20260525"], 3390.5)

    def test_empty_payload(self) -> None:
        self.assertEqual(parse_sina_kline_payload(""), [])
        self.assertEqual(parse_sina_kline_payload("null"), [])


if __name__ == "__main__":
    unittest.main()
