from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from bizviz.analytics.dataset import build_dataset, compute_status
from bizviz.api.deps import ConnDep, RuntimeDep, require_csrf, require_session
from bizviz.api.schema import StatusResponse

router = APIRouter(dependencies=[Depends(require_session), Depends(require_csrf)])


@router.get("/api/status")
def status(runtime: RuntimeDep, conn: ConnDep) -> StatusResponse:
    data = compute_status(conn)
    return {
        "app_version": runtime.app_version,
        "data_version": data["data_version"],
        "newest_data_at": data["newest_data_at"],
        "items": data["items"],
        "syncing": runtime.scheduler.is_busy,
    }


@router.get("/api/data")
def data(runtime: RuntimeDep, conn: ConnDep) -> JSONResponse:
    # Returned as-is: the payload is large and already JSON-shaped, so skip response validation.
    return JSONResponse(content=build_dataset(conn, runtime.ctx.today()))


@router.post("/api/sync", status_code=202)
def sync(runtime: RuntimeDep) -> dict[str, Any]:
    runtime.scheduler.request_sync(None)
    return {}
