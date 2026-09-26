"""标准物质可用性的唯一判定口径。

台账、详情和检测任务引用标物都调用这里，避免各自按有效期、状态或存放条件
得出不同结论。判定结果只附加在接口返回值上，不回写既有台账数据。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from app.store import store

MODULE = "standard"
CODE_FIELD = "标物编号"
EXPIRY_FIELD = "有效期至"
STATUS_FIELD = "标物状态"
STORAGE_FIELD = "存放条件"

EXPIRING_WARNING_DAYS = 30

AVAILABLE = "可用"
CONDITIONALLY_AVAILABLE = "有条件可用"
UNAVAILABLE = "不可用"

ACTIVE_STATUS = "合格在用"
EXPIRING_STATUS = "即将到期"
EXPIRED_STATUS = "已过期"
CONSUMED_STATUS = "已消耗"

ACTIVE_MARKERS = ("合格在用", "在用", "合格", "有效", "正常", "无异常", "未见异常")
EXPIRING_MARKERS = ("即将到期", "临近到期", "临期", "将到期")
EXPIRED_MARKERS = ("已过期", "过期", "失效")
CONSUMED_MARKERS = ("已消耗", "已用完", "耗尽", "用完")
UNAVAILABLE_STATUS_MARKERS = (
    "停用",
    "禁用",
    "报废",
    "不合格",
    "异常",
    "损坏",
    "变质",
    "污染",
    "冻结",
    "扣留",
)
INVALID_STORAGE_EXACT_MARKERS = (
    "异常",
    "不符合",
    "不合格",
    "失控",
)
INVALID_STORAGE_PARTIAL_MARKERS = (
    "存放异常",
    "储存异常",
    "存储异常",
    "条件异常",
    "环境异常",
    "温度异常",
    "湿度异常",
    "条件不符合",
    "条件不符",
    "已变质",
    "已污染",
    "已损坏",
    "存放错误",
    "储存错误",
    "存储错误",
    "存放不当",
    "储存不当",
    "存储不当",
    "违规存放",
)


@dataclass(frozen=True)
class StandardAvailability:
    """一份标物能否被检测任务引用的判定结果。"""

    code: str
    available: bool
    availability: str
    lifecycle_status: str | None
    reason: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    expiry_date: date | None
    expiry_text: str | None
    standard_status: str | None
    storage_condition: str | None
    exists: bool = True
    checked_on: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "available": self.available,
            "availability": self.availability,
            "lifecycleStatus": self.lifecycle_status,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "expiryDate": self.expiry_date.isoformat() if self.expiry_date else None,
            "expiryText": self.expiry_text,
            "standardStatus": self.standard_status,
            "storageCondition": self.storage_condition,
            "exists": self.exists,
            "checkedOn": self.checked_on.isoformat() if self.checked_on else None,
        }


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def parse_expiry_date(value: Any) -> date | None:
    """解析有效期至；无法识别的历史文本保留在 warnings 中，不擅自改值。"""
    text = _text(value)
    if not text:
        return None

    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _storage_condition_invalid(text: str) -> bool:
    return text in INVALID_STORAGE_EXACT_MARKERS or _contains_any(
        text, INVALID_STORAGE_PARTIAL_MARKERS
    )


def _classify_status(text: str) -> tuple[str | None, bool, bool]:
    """识别状态，返回（生命周期状态，是否明确禁用，是否为可识别状态）。"""
    if not text:
        return None, False, False

    if text in (ACTIVE_STATUS, EXPIRING_STATUS, EXPIRED_STATUS, CONSUMED_STATUS):
        return text, False, True
    if _contains_any(text, CONSUMED_MARKERS):
        return CONSUMED_STATUS, True, True
    if _contains_any(text, UNAVAILABLE_STATUS_MARKERS):
        return text, True, True
    if _contains_any(text, EXPIRED_MARKERS):
        return EXPIRED_STATUS, True, True
    if _contains_any(text, EXPIRING_MARKERS):
        return EXPIRING_STATUS, False, True
    if _contains_any(text, ACTIVE_MARKERS):
        return ACTIVE_STATUS, False, True
    return None, False, False


def _normalize_status(raw_status: Any, workflow_status: Any) -> tuple[str | None, bool]:
    """合并台账展示状态与消耗动作留下的工作流状态，按更严重的状态结论。"""
    candidates = []
    for text in (_text(raw_status), _text(workflow_status)):
        lifecycle, blocked, recognized = _classify_status(text)
        if recognized:
            candidates.append((lifecycle or text, blocked))

    if not candidates:
        return None, False

    def severity(item: tuple[str, bool]) -> int:
        status, blocked = item
        if status == CONSUMED_STATUS:
            return 4
        if blocked:
            return 3
        if status == EXPIRED_STATUS:
            return 2
        if status == EXPIRING_STATUS:
            return 1
        return 0

    return max(candidates, key=severity)


def evaluate_standard_availability(
    entry: dict[str, Any] | str,
    *,
    today: date | None = None,
) -> StandardAvailability:
    """按有效期至、标物状态和存放条件给出唯一可用性结论。"""
    checked_on = today or date.today()
    if isinstance(entry, str):
        code = entry.strip()
        row = find_standard_by_code(code)
        if row is None:
            return StandardAvailability(
                code=code,
                available=False,
                availability=UNAVAILABLE,
                lifecycle_status=None,
                reason=f"标物编号 {code} 不存在",
                reasons=(f"标物编号 {code} 不存在",),
                warnings=(),
                expiry_date=None,
                expiry_text=None,
                standard_status=None,
                storage_condition=None,
                exists=False,
                checked_on=checked_on,
            )
    else:
        row = entry
        code = _text(row.get(CODE_FIELD))

    expiry_text = _text(row.get(EXPIRY_FIELD)) or None
    status_text = _text(row.get(STATUS_FIELD)) or None
    storage_text = _text(row.get(STORAGE_FIELD)) or None
    expiry_date = parse_expiry_date(expiry_text)

    reasons: list[str] = []
    warnings: list[str] = []
    lifecycle_status, status_blocked = _normalize_status(status_text, row.get("status"))

    if lifecycle_status == CONSUMED_STATUS:
        reasons.append("标物已消耗")
    elif lifecycle_status == EXPIRED_STATUS:
        reasons.append("标物状态为已过期")
    elif status_blocked:
        reasons.append(f"标物状态「{lifecycle_status}」不允许引用")

    if expiry_date and expiry_date < checked_on:
        reasons.append(f"有效期至 {expiry_date.isoformat()}，已过期")
    elif expiry_date and (
        checked_on <= expiry_date <= checked_on + timedelta(days=EXPIRING_WARNING_DAYS)
    ):
        warnings.append(
            f"有效期至 {expiry_date.isoformat()}，将在 {EXPIRING_WARNING_DAYS} 天内到期"
        )

    if not expiry_text:
        warnings.append("缺少有效期至")
    elif expiry_date is None:
        warnings.append(f"有效期至「{expiry_text}」无法识别")

    if storage_text:
        if _storage_condition_invalid(storage_text):
            reasons.append(f"存放条件「{storage_text}」不符合要求")
    else:
        warnings.append("缺少存放条件")

    if lifecycle_status is None and status_text:
        warnings.append(f"标物状态「{status_text}」无法识别")
    elif lifecycle_status is None and not status_text:
        warnings.append("缺少标物状态")

    available = not reasons
    if available:
        availability = AVAILABLE if not warnings else CONDITIONALLY_AVAILABLE
    else:
        availability = UNAVAILABLE
    if available:
        reason = "；".join(warnings) if warnings else "标物在有效期内，状态和存放条件允许引用"
    else:
        reason = "；".join(reasons)

    return StandardAvailability(
        code=code,
        available=available,
        availability=availability,
        lifecycle_status=lifecycle_status,
        reason=reason,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        expiry_date=expiry_date,
        expiry_text=expiry_text,
        standard_status=status_text,
        storage_condition=storage_text,
        exists=True,
        checked_on=checked_on,
    )


def find_standard_by_code(code: str | int | None) -> dict[str, Any] | None:
    """按标物编号取当前台账记录。"""
    normalized = _text(code)
    if not normalized:
        return None
    for row in store.rows(MODULE):
        if _text(row.get(CODE_FIELD)) == normalized:
            return row
    return None


def get_standard_availability(
    code: str | int | None,
    *,
    today: date | None = None,
) -> StandardAvailability:
    return evaluate_standard_availability(_text(code), today=today)


def is_standard_available(code: str | int | None, *, today: date | None = None) -> bool:
    return get_standard_availability(code, today=today).available


def list_available_standards(
    *,
    keyword: str | None = None,
    today: date | None = None,
) -> list[tuple[dict[str, Any], StandardAvailability]]:
    """返回可供检测任务选择的标物；调用方仍取原台账字段做展示。"""
    rows = store.rows(MODULE)
    if keyword:
        rows = [row for row in rows if keyword in _text(row.get(CODE_FIELD))]
    result: list[tuple[dict[str, Any], StandardAvailability]] = []
    for row in rows:
        availability = evaluate_standard_availability(row, today=today)
        if availability.available:
            result.append((row, availability))
    return result


def with_availability(
    entry: dict[str, Any],
    *,
    today: date | None = None,
    today_factory: Callable[[], date] | None = None,
) -> dict[str, Any]:
    """复制一条记录并附加统一结论，避免回写或改动原始数据。"""
    effective_today = today or (today_factory() if today_factory else None)
    result = dict(entry)
    result["availability"] = evaluate_standard_availability(entry, today=effective_today).to_dict()
    return result
