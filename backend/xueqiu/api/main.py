from __future__ import annotations

import asyncio
import queue
import threading
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from xueqiu.api.schemas import (
    AccountItem,
    CopyBacktestRequest,
    CopyBacktestResponse,
    StrategyCatalogItem,
    StrategyCompareRequest,
    StrategyCompareResponse,
    DashboardPayload,
    DeleteAccountResponse,
    ImportRequest,
    ImportResponse,
    DataFreshnessResponse,
    PortfoliosOverviewResponse,
    PortfoliosOverviewStatsResponse,
    RecomputeResponse,
    DiscoveryImportRequest,
    DiscoveryMineRequest,
    DiscoveryMineResponse,
    DiscoveryStatsResponse,
    ImportMinedCubeResponse,
    MinedCubeListResponse,
    UpdateMinedCubeRequest,
    SyncCubeNavAllResponse,
    SyncLatestHfqResponse,
    SyncQuotesResponse,
    SyncXueqiuAllResponse,
    SyncXueqiuResponse,
)
from xueqiu.api.services import (
    get_dashboard,
    delete_account,
    get_discovery_stats,
    import_discovery_cube,
    list_discovery_cubes,
    patch_discovery_cube,
    get_data_freshness,
    get_portfolios_overview,
    get_portfolios_overview_stats,
    import_trades,
    iter_discovery_import_stream,
    iter_discovery_mine_stream,
    iter_sync_all_stream,
    iter_sync_xueqiu_stream,
    list_accounts,
    recompute_returns,
    compare_backtest_strategies,
    list_backtest_strategies,
    run_copy_backtest,
    sync_all_from_xueqiu,
    sync_cube_nav_all,
    sync_from_xueqiu,
    sync_latest_hfq_prices,
    sync_quotes_data,
)
from xueqiu.config import DATABASE_URL, HOST, PORT
from xueqiu.domain.nav_engine import ENGINE_VERSION
from xueqiu.storage.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Xueqiu Portfolio API", version="0.5.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "api_version": app.version,
        "engine_version": ENGINE_VERSION,
        "database_url": DATABASE_URL,
    }


@app.get("/api/accounts", response_model=list[AccountItem])
def accounts() -> list[AccountItem]:
    return [AccountItem(**item) for item in list_accounts()]


@app.delete("/api/accounts/{account_key}", response_model=DeleteAccountResponse)
def remove_account(account_key: str) -> DeleteAccountResponse:
    try:
        return DeleteAccountResponse(**delete_account(account_key))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/portfolios/overview", response_model=PortfoliosOverviewResponse)
def portfolios_overview() -> PortfoliosOverviewResponse:
    return PortfoliosOverviewResponse(**get_portfolios_overview())


@app.get("/api/portfolios/overview-stats", response_model=PortfoliosOverviewStatsResponse)
def portfolios_overview_stats() -> PortfoliosOverviewStatsResponse:
    return PortfoliosOverviewStatsResponse(**get_portfolios_overview_stats())


@app.get("/api/data-freshness", response_model=DataFreshnessResponse)
def data_freshness() -> DataFreshnessResponse:
    return DataFreshnessResponse(**get_data_freshness())


@app.get("/api/dashboard/{account_key}", response_model=DashboardPayload)
def dashboard(account_key: str) -> DashboardPayload:
    try:
        return DashboardPayload(**get_dashboard(account_key))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/recompute/{account_key}", response_model=RecomputeResponse)
def recompute_account(account_key: str) -> RecomputeResponse:
    try:
        return RecomputeResponse(**recompute_returns(account_key))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/sync-latest-hfq/{account_key}", response_model=SyncLatestHfqResponse)
def sync_latest_hfq(account_key: str) -> SyncLatestHfqResponse:
    try:
        return SyncLatestHfqResponse(**sync_latest_hfq_prices(account_key))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/sync-xueqiu/{account_key}", response_model=SyncXueqiuResponse)
def sync_xueqiu(account_key: str) -> SyncXueqiuResponse:
    try:
        return SyncXueqiuResponse(**sync_from_xueqiu(account_key))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/sync-xueqiu-stream/{account_key}")
async def sync_xueqiu_stream(account_key: str, request: Request) -> StreamingResponse:
    cancel = threading.Event()

    async def generate():
        chunk_queue: queue.Queue[str | None] = queue.Queue()

        def worker() -> None:
            try:
                for chunk in iter_sync_xueqiu_stream(account_key, cancel_event=cancel):
                    if cancel.is_set():
                        break
                    chunk_queue.put(chunk)
            finally:
                chunk_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            if await request.is_disconnected():
                cancel.set()
                break
            try:
                chunk = chunk_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.15)
                continue
            if chunk is None:
                break
            yield chunk

        cancel.set()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/discovery/stats", response_model=DiscoveryStatsResponse)
def discovery_stats() -> DiscoveryStatsResponse:
    return DiscoveryStatsResponse(**get_discovery_stats())


@app.get("/api/discovery/cubes", response_model=MinedCubeListResponse)
def discovery_cubes(
    auto_pass: bool | None = None,
    selected: int | None = None,
    depth: int | None = None,
    q: str | None = None,
) -> MinedCubeListResponse:
    items = list_discovery_cubes(auto_pass=auto_pass, selected=selected, depth=depth, q=q)
    return MinedCubeListResponse(items=items)


@app.patch("/api/discovery/cubes/{account_code}")
def discovery_cube_patch(account_code: str, body: UpdateMinedCubeRequest) -> dict:
    try:
        return patch_discovery_cube(
            account_code,
            selected=body.selected,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/discovery/cubes/{account_code}/import", response_model=ImportMinedCubeResponse)
def discovery_cube_import(account_code: str) -> ImportMinedCubeResponse:
    try:
        return ImportMinedCubeResponse(**import_discovery_cube(account_code))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/discovery/import-stream")
async def discovery_import_stream(
    request: Request,
    body: DiscoveryImportRequest = Body(default_factory=DiscoveryImportRequest),
) -> StreamingResponse:
    cancel = threading.Event()
    codes = [str(c).strip().upper() for c in body.account_codes if str(c).strip()]

    async def generate():
        chunk_queue: queue.Queue[str | None] = queue.Queue()

        def worker() -> None:
            try:
                for chunk in iter_discovery_import_stream(codes, cancel_event=cancel):
                    if cancel.is_set():
                        break
                    chunk_queue.put(chunk)
            finally:
                chunk_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            if await request.is_disconnected():
                cancel.set()
                break
            try:
                chunk = chunk_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.15)
                continue
            if chunk is None:
                break
            yield chunk

        cancel.set()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/discovery/mine-stream")
async def discovery_mine_stream(
    request: Request,
    body: DiscoveryMineRequest = Body(default_factory=DiscoveryMineRequest),
) -> StreamingResponse:
    cancel = threading.Event()
    max_depth = max(1, min(int(body.max_depth), 5))

    async def generate():
        chunk_queue: queue.Queue[str | None] = queue.Queue()

        def worker() -> None:
            try:
                for chunk in iter_discovery_mine_stream(max_depth=max_depth, cancel_event=cancel):
                    if cancel.is_set():
                        break
                    chunk_queue.put(chunk)
            finally:
                chunk_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            if await request.is_disconnected():
                cancel.set()
                break
            try:
                chunk = chunk_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.15)
                continue
            if chunk is None:
                break
            yield chunk

        cancel.set()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sync-all-stream")
async def sync_all_stream(request: Request) -> StreamingResponse:
    cancel = threading.Event()

    async def generate():
        chunk_queue: queue.Queue[str | None] = queue.Queue()

        def worker() -> None:
            try:
                for chunk in iter_sync_all_stream(cancel_event=cancel):
                    if cancel.is_set():
                        break
                    chunk_queue.put(chunk)
            finally:
                chunk_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            if await request.is_disconnected():
                cancel.set()
                break
            try:
                chunk = chunk_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.15)
                continue
            if chunk is None:
                break
            yield chunk

        cancel.set()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sync-quotes", response_model=SyncQuotesResponse)
def sync_quotes() -> SyncQuotesResponse:
    try:
        return SyncQuotesResponse(**sync_quotes_data())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/sync-cube-nav-all", response_model=SyncCubeNavAllResponse)
def sync_cube_nav() -> SyncCubeNavAllResponse:
    try:
        return SyncCubeNavAllResponse(**sync_cube_nav_all())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/sync-xueqiu-all", response_model=SyncXueqiuAllResponse)
def sync_xueqiu_all() -> SyncXueqiuAllResponse:
    try:
        return SyncXueqiuAllResponse(**sync_all_from_xueqiu())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/import-logs", response_model=ImportResponse)
def import_logs(payload: ImportRequest) -> ImportResponse:
    try:
        return ImportResponse(
            **import_trades(
                account_code=payload.account_id,
                account_name=payload.account_name,
                trades=[item.model_dump() for item in payload.trades],
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/backtest-strategies", response_model=list[StrategyCatalogItem])
def backtest_strategies() -> list[StrategyCatalogItem]:
    return [StrategyCatalogItem(**item) for item in list_backtest_strategies()]


@app.post("/api/backtest-compare", response_model=StrategyCompareResponse)
def backtest_compare(payload: StrategyCompareRequest) -> StrategyCompareResponse:
    if not payload.strategy_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个策略")
    try:
        return StrategyCompareResponse(**compare_backtest_strategies(**payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtest-copy", response_model=CopyBacktestResponse)
def backtest_copy(payload: CopyBacktestRequest = Body(default_factory=CopyBacktestRequest)) -> CopyBacktestResponse:
    body = payload
    try:
        return CopyBacktestResponse(**run_copy_backtest(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    # 也可运行本文件；推荐 IDE 直接运行 backend/main.py
    from pathlib import Path
    import sys

    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    import uvicorn

    print(f"启动后端服务: http://{HOST}:{PORT}")
    print(f"健康检查: http://127.0.0.1:{PORT}/health")
    uvicorn.run("xueqiu.api.main:app", host=HOST, port=PORT, reload=True)
