"""标准物质业务规则：状态流转、字段校验、筛选与可用性判定入口。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.standard_availability import (
    CODE_FIELD,
    MODULE,
    StandardAvailability,
    evaluate_standard_availability,
    find_standard_by_code,
    list_available_standards,
    with_availability,
)
from app.store import store

REQUIRED_FIELDS = ["标物编号", "标物名称", "证书编号"]
STATUS_ORDER = ["合格在用", "即将到期", "已过期", "已消耗"]
ACTION_RULES = {"开封启用": "合格在用", "标记到期": "即将到期", "登记消耗": "已消耗"}
NEGATIVE_ACTIONS = []


class StandardService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(CODE_FIELD, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [
            with_availability(row, today=today)
            for row in rows[start:start + size]
        ]
        return page_rows, total

    def list_available_entries(
        self,
        *,
        keyword: str | None = None,
        today: date | None = None,
    ) -> list[dict[str, Any]]:
        return [
            with_availability(row, today=today)
            for row, _availability in list_available_standards(keyword=keyword, today=today)
        ]

    def get_entry(self, entry_id: int, *, today: date | None = None) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return with_availability(entry, today=today)

    def find_by_code(self, code: str, *, today: date | None = None) -> dict[str, Any] | None:
        entry = find_standard_by_code(code)
        if entry is None:
            return None
        return with_availability(entry, today=today)

    def availability_for_code(
        self,
        code: str,
        *,
        today: date | None = None,
    ) -> StandardAvailability:
        entry = find_standard_by_code(code)
        if entry is None:
            return evaluate_standard_availability(code, today=today)
        return evaluate_standard_availability(entry, today=today)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return with_availability(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"标准物质 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于标准物质可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return with_availability(entry), f"标准物质已{action}"
