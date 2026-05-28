"""
后端 API 启动入口。

在 IDE 中直接运行本文件即可（工作目录请设为 backend/）：
  - Cursor / VS Code：右键 main.py → Run Python File
  - PyCharm：Run Configuration 脚本路径选 backend/main.py

等价于：uvicorn xueqiu.api.main:app --reload --host 0.0.0.0 --port 8011
"""

from __future__ import annotations

import sys
from pathlib import Path

# 未 pip install -e . 时，保证能 import xueqiu 包
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def main() -> None:
    import uvicorn

    from xueqiu.config import HOST, PORT

    url = f"http://127.0.0.1:{PORT}"
    print(f"启动后端: {url}")
    print(f"健康检查: {url}/health")
    print("按 Ctrl+C 停止")

    uvicorn.run(
        "xueqiu.api.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        reload_dirs=[str(_BACKEND_DIR / "xueqiu")],
    )


if __name__ == "__main__":
    main()
