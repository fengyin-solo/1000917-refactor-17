"""标准物质可用性判断：标物台账、标物详情与检测任务引用标物校验共用的唯一口径。

结论只按标物编号取到的「有效期至、标物状态、存放条件」给出；
以后要调整可用口径，只改这一处写法，三个使用方自动保持一致。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "standard"

# 可用性档位：可用 / 临期可用 / 不可用
LEVEL_OK = "可用"
LEVEL_WARN = "临期可用"
LEVEL_BLOCK = "不可用"

# 标物状态里直接判定不可用的取值（已消耗、已过期是历史事实，一律不可用）
BLOCKED_STATUSES = {"已过期": "标物状态为已过期", "已消耗": "标物状态为已消耗"}
# 标物状态里提示临期的取值
WARN_STATUSES = {"即将到期": "标物状态为即将到期"}

# 距有效期至不足多少天视为临期
EXPIRY_WARN_DAYS = 30


def _parse_date(raw: Any) -> date | None:
    """把「有效期至」解析成日期；空值或格式无法识别时返回 None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def evaluate_availability(entry: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """按有效期至、标物状态、存放条件给出统一可用性结论。

    返回 {"usable": 是否可用, "level": 可用性档位, "reasons": 判定依据}；
    台账、详情与任务引用校验都只看这一份结论，不再各自判断。
    """
    today = today or date.today()
    status = str(entry.get("status") or "").strip()
    blocks: list[str] = []
    warns: list[str] = []

    if status in BLOCKED_STATUSES:
        blocks.append(BLOCKED_STATUSES[status])
    elif status in WARN_STATUSES:
        warns.append(WARN_STATUSES[status])

    expiry = _parse_date(entry.get("有效期至"))
    if expiry is None:
        warns.append("有效期至未登记或无法识别")
    elif expiry < today:
        blocks.append(f"有效期至 {expiry.isoformat()} 已过")
    elif (expiry - today).days <= EXPIRY_WARN_DAYS:
        warns.append(f"有效期至 {expiry.isoformat()} 临近")

    if not str(entry.get("存放条件") or "").strip():
        warns.append("存放条件未登记")

    if blocks:
        level = LEVEL_BLOCK
    elif warns:
        level = LEVEL_WARN
    else:
        level = LEVEL_OK
    return {"usable": not blocks, "level": level, "reasons": blocks + warns}


def find_by_code(code: str) -> dict[str, Any] | None:
    """按标物编号在台账里找到对应标物；找不到返回 None。"""
    text = str(code or "").strip()
    if not text:
        return None
    for row in store.rows(MODULE):
        if str(row.get("标物编号") or "").strip() == text:
            return row
    return None


def availability_by_code(code: str, *, today: date | None = None) -> dict[str, Any] | None:
    """按标物编号给出统一可用性结论；编号不存在时返回 None。"""
    entry = find_by_code(code)
    if entry is None:
        return None
    return evaluate_availability(entry, today=today)
