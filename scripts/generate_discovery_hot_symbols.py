"""从 TuShare 拉取成交额 Top100 JSON（开发用）。

  python scripts/_fetch_volume_top100.py
"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("_fetch_volume_top100.py")), run_name="__main__")
