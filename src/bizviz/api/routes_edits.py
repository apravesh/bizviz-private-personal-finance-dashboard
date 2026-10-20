from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from bizviz.analytics import edits
from bizviz.api.deps import ConnDep, RuntimeDep, require_csrf, require_session
from bizviz.api.schema import AccountOut, CategoryOut, RuleOut

router = APIRouter(dependencies=[Depends(require_session), Depends(require_csrf)])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def changes(self, *exclude: str) -> dict[str, Any]:
        # model_fields_set separates "field absent" from an explicit null (which clears it).
        return {k: getattr(self, k) for k in self.model_fields_set if k not in exclude}


class ClassifyBody(_Body):
    category_id: str | None = None
    flow_class: str | None = None
    recurring: Any = None


class RuleCreateBody(ClassifyBody):
    merchant_key: str


class CategoryCreateBody(_Body):
    name: str
    applies_to: str


class CategoryPatchBody(_Body):
    name: str | None = None
    hidden: bool | None = None


class AccountPatchBody(_Body):
    ownership: str | None = None
    included: bool | None = None
    reviewed: bool | None = None


@router.patch("/api/transactions/{transaction_id}/override")
def override(
    transaction_id: str, body: ClassifyBody, runtime: RuntimeDep, conn: ConnDep
) -> dict[str, int]:
    version = edits.set_override(conn, transaction_id, body.changes(), runtime.ctx.today())
    return {"data_version": version}


@router.post("/api/transactions/{transaction_id}/apply-to-merchant")
def apply_to_merchant(
    transaction_id: str, body: ClassifyBody, runtime: RuntimeDep, conn: ConnDep
) -> dict[str, int]:
    rule_id, version = edits.apply_to_merchant(
        conn, transaction_id, body.changes(), runtime.ctx.today()
    )
    return {"rule_id": rule_id, "data_version": version}


@router.get("/api/rules")
def list_rules(conn: ConnDep) -> list[RuleOut]:
    return edits.list_rules(conn)


@router.post("/api/rules", status_code=201)
def create_rule(body: RuleCreateBody, runtime: RuntimeDep, conn: ConnDep) -> RuleOut:
    return edits.create_rule(
        conn, body.merchant_key, body.changes("merchant_key"), runtime.ctx.today()
    )


@router.patch("/api/rules/{rule_id}")
def update_rule(rule_id: int, body: ClassifyBody, runtime: RuntimeDep, conn: ConnDep) -> RuleOut:
    return edits.update_rule(conn, rule_id, body.changes(), runtime.ctx.today())


@router.delete("/api/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, runtime: RuntimeDep, conn: ConnDep) -> Response:
    edits.delete_rule(conn, rule_id, runtime.ctx.today())
    return Response(status_code=204)


@router.post("/api/categories", status_code=201)
def create_category(body: CategoryCreateBody, runtime: RuntimeDep, conn: ConnDep) -> CategoryOut:
    return edits.create_category(conn, body.name, body.applies_to, runtime.ctx.today())


@router.patch("/api/categories/{category_id}")
def update_category(
    category_id: str, body: CategoryPatchBody, runtime: RuntimeDep, conn: ConnDep
) -> CategoryOut:
    return edits.update_category(
        conn, category_id, name=body.name, hidden=body.hidden, today=runtime.ctx.today()
    )


@router.patch("/api/accounts/{account_id}")
def patch_account(
    account_id: str, body: AccountPatchBody, runtime: RuntimeDep, conn: ConnDep
) -> AccountOut:
    return edits.patch_account(
        conn,
        account_id,
        runtime.ctx.today(),
        ownership=body.ownership,
        included=body.included,
        reviewed=body.reviewed,
    )
