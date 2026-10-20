from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from bizviz.api.deps import RuntimeDep, require_csrf, require_plaid, require_session
from bizviz.plaid_types import LinkMode
from bizviz.sync import linking

router = APIRouter(
    dependencies=[Depends(require_session), Depends(require_csrf), Depends(require_plaid)]
)


class LinkTokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["bank", "investment", "loan", "update"]
    item_id: str | None = None


class ExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_token: str
    institution_id: str | None = None
    institution_name: str | None = None
    mode: str


@router.post("/api/plaid/link-token")
def link_token(body: LinkTokenBody, runtime: RuntimeDep) -> dict[str, str]:
    mode: LinkMode = body.mode
    return {"link_token": linking.create_link_token(runtime.ctx, mode, body.item_id)}


@router.post("/api/plaid/exchange")
def exchange(body: ExchangeBody, runtime: RuntimeDep) -> dict[str, object]:
    result = linking.exchange_public_token(
        runtime.ctx,
        body.public_token,
        body.institution_id,
        body.institution_name,
        body.mode,  # type: ignore[arg-type]
    )
    return {"item_id": result.item_id, "duplicate": result.duplicate, "pending": result.pending}


@router.post("/api/plaid/items/{item_id}/repaired", status_code=204)
def repaired(item_id: str, runtime: RuntimeDep) -> Response:
    linking.mark_repaired(runtime.ctx, item_id)
    return Response(status_code=204)


@router.delete("/api/plaid/items/{item_id}", status_code=204)
def disconnect(item_id: str, runtime: RuntimeDep) -> Response:
    linking.disconnect_item(runtime.ctx, item_id)
    return Response(status_code=204)
