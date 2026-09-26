"""检测任务业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.standard_availability import availability_by_code
from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
STATUS_ORDER = ["待分配", "待检测", "检测中", "已完成"]
ACTION_RULES = {"分配任务": "待检测", "开始检测": "检测中", "提交结果": "已完成"}
NEGATIVE_ACTIONS = []


def _check_standard_ref(code: str) -> str | None:
    """检测任务引用标物时走统一可用性口径；可以引用返回 None，否则返回拦阻原因。"""
    conclusion = availability_by_code(code)
    if conclusion is None:
        return f"标物编号 {code} 在标准物质台账中不存在"
    if not conclusion["usable"]:
        return f"标准物质 {code} 当前不可用：{'；'.join(conclusion['reasons'])}"
    return None


class TaskService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        code = str(values.get("标物编号") or "").strip()
        if code:
            problem = _check_standard_ref(code)
            if problem:
                return None, [problem]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        if code:
            entry["标物编号"] = code
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测任务可执行范围"
        if action == "开始检测":
            code = str(entry.get("标物编号") or "").strip()
            if code:
                problem = _check_standard_ref(code)
                if problem:
                    return None, problem
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检测任务已{action}"
