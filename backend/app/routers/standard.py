"""标准物质接口：维护标准物质，覆盖开封启用、标记到期、登记消耗等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.standard import StandardService

router = APIRouter(prefix="/api/standard", tags=["标准物质"])

service = StandardService()

LIST_FIELDS = ["标物编号", "标物名称", "证书编号", "浓度范围", "有效期至", "存放条件", "开封日期", "标物状态"]
STATUSES = ["合格在用", "即将到期", "已过期", "已消耗"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按标物编号检索"),
    status: str | None = Query(default=None, description="合格在用、即将到期、已过期、已消耗"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按标物编号与状态过滤标准物质列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/available", response_model=PageResult[dict])
def list_available_entries(
    keyword: str | None = Query(default=None, description="按标物编号检索"),
) -> PageResult[dict]:
    """供检测任务选择标物，只返回统一口径判定为可引用的记录。"""
    items = service.list_available_entries(keyword=keyword)
    return PageResult(items=items, total=len(items), page=1, size=max(len(items), 1))


@router.get("/availability", response_model=dict)
def get_availability(
    code: str = Query(description="按标物编号读取可用性结论"),
) -> dict[str, Any]:
    """按标物编号返回台账、详情和任务引用共用的可用性结论。"""
    return service.availability_for_code(code).to_dict()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出标准物质清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "standard", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条标准物质明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"标准物质 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条标准物质，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="标准物质已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条标准物质执行开封启用、标记到期、登记消耗；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
