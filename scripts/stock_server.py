"""个股详情可视化服务 — Apple 风格。

用法:
    cd backend
    python ../scripts/stock_server.py
    浏览器打开 http://127.0.0.1:8766
"""

from __future__ import annotations

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, text
from xueqiu.storage.db import engine as xq_engine

AS_DB_URL = "mysql+pymysql://root:shaojunjie0808@127.0.0.1:3306/ashare_system?charset=utf8mb4"
as_eng = create_engine(AS_DB_URL, future=True)
PORT = 8766
HTML_PATH = ROOT / "data" / "stock_detail.html"


def _to_ts(code: str) -> str:
    c = str(code).strip()[:6]
    if c.startswith(("60", "68")): return f"{c}.SH"
    if c.startswith(("00", "30")): return f"{c}.SZ"
    if c.startswith(("8", "4")): return f"{c}.BJ"
    return f"{c}.SH"


def api_stocks() -> list[dict]:
    with xq_engine.begin() as conn:
        rows = conn.execute(text(
            """SELECT es.stock_name, es.stock_code, COUNT(*) AS cnt,
                      MIN(p.created_at) AS fd, MAX(p.created_at) AS ld
               FROM extraction_stocks es
               JOIN xueqiu_extractions e ON es.extraction_id = e.id
               JOIN xueqiu_posts p ON e.post_id = p.post_id
               WHERE e.has_info = 1 AND es.stock_code IS NOT NULL AND es.stock_code != ''
               GROUP BY es.stock_name, es.stock_code
               HAVING cnt >= 1 ORDER BY cnt DESC"""
        )).fetchall()
    result = []
    for r in rows:
        code = str(r[1]).strip()[:6]
        if not code.isdigit(): continue
        result.append({"name": r[0], "code": code, "ts_code": _to_ts(code), "mentions": r[2], "first_date": str(r[3])[:10], "last_date": str(r[4])[:10]})
    return result


def api_stock_detail(code: str) -> dict | None:
    ts = _to_ts(code)
    name = ""
    with xq_engine.begin() as conn:
        r = conn.execute(text("SELECT DISTINCT stock_name FROM extraction_stocks WHERE stock_code LIKE :c LIMIT 1"), {"c": f"{code}%"}).fetchone()
        if r: name = r[0]

    with as_eng.begin() as conn:
        prices = []
        for pr in conn.execute(text("SELECT trade_date, close FROM daily_prices WHERE ts_code=:ts AND trade_date>='20250101' AND trade_date<='20260228' ORDER BY trade_date"), {"ts": ts}).fetchall():
            prices.append({"date": f"{pr[0][:4]}-{pr[0][4:6]}-{pr[0][6:]}", "close": float(pr[1])})
        bench = {}
        for br in conn.execute(text("SELECT trade_date, close FROM benchmark_index WHERE trade_date>='20250101' AND trade_date<='20260228' ORDER BY trade_date")).fetchall():
            d = f"{br[0][:4]}-{br[0][4:6]}-{br[0][6:]}"
            bench[d] = float(br[1])
    for p in prices:
        p["benchmark"] = bench.get(p["date"])

    with xq_engine.begin() as conn:
        mentions = []
        for m in conn.execute(text(
            """SELECT es.sentiment, es.time_horizon, es.key_logic, es.mention_type,
                      p.post_id, p.created_at, p.text, e.summary
               FROM extraction_stocks es
               JOIN xueqiu_extractions e ON es.extraction_id = e.id
               JOIN xueqiu_posts p ON e.post_id = p.post_id
               WHERE e.has_info = 1 AND es.stock_code LIKE :c ORDER BY p.created_at ASC"""),
            {"c": f"{code}%"}).fetchall():
            d = str(m[5])[:10] if m[5] else ""
            mentions.append({"date": d, "post_id": m[4], "text": str(m[6] or ""), "summary": str(m[7] or ""),
                             "sentiment": m[0] or "未明确", "time_horizon": m[1] or "未明确",
                             "mention_type": m[3] or "", "key_logic": str(m[2] or "")})
    if not prices: return None
    return {"name": name or code, "code": code, "ts_code": ts, "prices": prices, "mentions": mentions}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/': self._serve_file(HTML_PATH, 'text/html')
        elif p == '/api/stocks': self._json(api_stocks())
        elif p.startswith('/api/stock/'): self._json(api_stock_detail(p.split('/')[-1]) or {"error": "not found"})
        else: self.send_error(404)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', len(body)); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); self.wfile.write(body)

    def _serve_file(self, path, mime):
        try:
            body = Path(path).read_bytes()
            self.send_response(200); self.send_header('Content-Type', f'{mime}; charset=utf-8'); self.send_header('Content-Length', len(body)); self.end_headers(); self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def log_message(self, format, *args): pass


if __name__ == '__main__':
    if not HTML_PATH.exists():
        print(f"错误: 找不到前端文件 {HTML_PATH}")
        sys.exit(1)
    print(f"个股追踪: http://127.0.0.1:{PORT}")
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
