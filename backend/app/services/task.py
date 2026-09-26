"""检测任务业务规则：状态流转、字段校验、筛选与引用标物校验。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.standard_availability import (
    find_standard_by_code,
    get_standard_availability,
)
from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
STATUS_ORDER = ["待分配", "待检测", "检测中", "已完成"]
ACTION_RULES = {"分配任务": "待检测", "开始检测": "检测中", "提交结果": "已完成"}
NEGATIVE_ACTIONS = []
REFERENCE_CODE_FIELDS = ("标物编号", "引用标物编号")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def find_reference_code(values: dict[str, Any]) -> tuple[str | None, str | None]:
    """从任务字段中找出实际指向标物台账的编号，避免误判检测方法的标准编号。"""
    explicit_fields = REFERENCE_CODE_FIELDS + tuple(
        field
        for field in values
        if "标物" in field and field.endswith("编号") and field not in REFERENCE_CODE_FIELDS
    )
    for field in explicit_fields:
        code = _text(values.get(field))
        if code:
            return field, code

    standard_code = _text(values.get("标准编号"))
    if standard_code and find_standard_by_code(standard_code) is not None:
        return "标准编号", standard_code
    return None, None


def annotate_reference_availability(
    entry: dict[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """复制任务记录并附加引用标物的统一可用性结论。"""
    result = dict(entry)
    code_field, code = find_reference_code(entry)
    if code_field and code:
        result["referenceAvailability"] = {
            "codeField": code_field,
            **get_standard_availability(code, today=today).to_dict(),
        }
    return result


class TaskService:
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
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [
            annotate_reference_availability(row, today=today)
            for row in rows[start:start + size]
        ]
        return page_rows, total

    def get_entry(self, entry_id: int, *, today: date | None = None) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return annotate_reference_availability(entry, today=today)

    def validate_reference(
        self,
        values: dict[str, Any],
        *,
        today: date | None = None,
    ) -> str | None:
        """任务引用标物时复用标物台账的唯一可用性口径。"""
        _code_field, code = find_reference_code(values)
        if not code:
            return None
        availability = get_standard_availability(code, today=today)
        if not availability.available:
            return f"引用标物 {code} 不可用：{availability.reason}"
        return None

    def create_entry(
        self,
        values: dict[str, Any],
        *,
        today: date | None = None,
    ) -> tuple[dict[str, Any] | None, list[str], str | None]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, None

        reference_error = self.validate_reference(values, today=today)
        if reference_error:
            return None, [], reference_error

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        reference_field, _reference_code = find_reference_code(values)
        if reference_field:
            entry[reference_field] = values[reference_field]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return annotate_reference_availability(entry, today=today), [], None

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        *,
        today: date | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测任务可执行范围"

        reference_values = dict(entry)
        selected_field: str | None = None
        selected_code: str | None = None
        if values:
            selected_field, selected_code = find_reference_code(values)
            if selected_field and selected_code:
                reference_values[selected_field] = selected_code
        reference_error = self.validate_reference(reference_values, today=today)
        if reference_error:
            return None, reference_error
        if selected_field and selected_code:
            entry[selected_field] = selected_code

        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return annotate_reference_availability(entry, today=today), f"检测任务已{action}"
