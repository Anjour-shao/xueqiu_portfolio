"""挖组合扩展：静态单测（不访问网络）。"""
from __future__ import annotations

import unittest

from xueqiu.domain.codes import is_cn_a_share
from xueqiu.domain.discovery_hot_symbols import (
    VOLUME_TOP100_SYMBOLS,
    VOLUME_TOP100_TRADE_DATE,
)
from xueqiu.integrations.xueqiu.social import XueQiuUserBrief, _parse_user


class DiscoveryHotSymbolsTest(unittest.TestCase):
    def test_trade_date(self) -> None:
        self.assertEqual(VOLUME_TOP100_TRADE_DATE, "20260605")

    def test_symbol_count(self) -> None:
        self.assertEqual(len(VOLUME_TOP100_SYMBOLS), 100)

    def test_all_cn_a_share(self) -> None:
        for sym in VOLUME_TOP100_SYMBOLS:
            self.assertTrue(is_cn_a_share(sym), sym)

    def test_no_duplicates(self) -> None:
        self.assertEqual(len(VOLUME_TOP100_SYMBOLS), len(set(VOLUME_TOP100_SYMBOLS)))

    def test_includes_emerging_examples(self) -> None:
        self.assertIn("SZ300308", VOLUME_TOP100_SYMBOLS)


class SocialParseTest(unittest.TestCase):
    def test_parse_user(self) -> None:
        u = _parse_user(
            {
                "id": 123,
                "screen_name": "tester",
                "followers_count": 1000,
                "friends_count": 50,
                "verified_type": 0,
                "allow_all_stock": True,
                "description": "demo",
            }
        )
        self.assertIsNotNone(u)
        assert u is not None
        self.assertIsInstance(u, XueQiuUserBrief)
        self.assertEqual(u.uid, 123)
        self.assertEqual(u.followers_count, 1000)
        self.assertTrue(u.allow_all_stock)


if __name__ == "__main__":
    unittest.main()
