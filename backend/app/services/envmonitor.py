"""环境监控业务规则：状态流转、字段校验与筛选口径都收在这里。

核心约定：
- 同一监测区域当期只保留一条未归档记录，重复登记会合并到原记录而不是新增；
- 记录编号全局唯一，编号与监测区域不允许错位；
- 偏离预警只能由超范围读数触发，读数回到范围内才能纠正为在控，并保留纠正说明；
- 已归档记录只读：能查、不再触发预警、任何动作都会被拦下。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "envmonitor"
MEASURE_FIELDS = ["温度值", "湿度值", "压差值"]
EXTRA_FIELDS = ["监测时间", "记录人员"]
STATUS_ORDER = ["在控", "偏离预警", "已归档"]
ACTION_RULES = {"偏离预警": "偏离预警", "纠正记录": "在控", "归档": "已归档"}

# 各项读数的受控范围（下限, 上限）；落在范围外才允许挂偏离预警。
CONTROL_LIMITS: dict[str, tuple[float, float]] = {
    "温度值": (18.0, 26.0),
    "湿度值": (30.0, 70.0),
    "压差值": (5.0, 20.0),
}


def _parse_number(value: Any) -> float | None:
    """把读数解析成数值；空值或非数值返回 None，表示不参与范围判定。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def out_of_control_fields(entry: dict[str, Any]) -> list[str]:
    """返回超出受控范围的读数字段；无法解析的读数不纳入判定。"""
    exceeded: list[str] = []
    for field, (low, high) in CONTROL_LIMITS.items():
        number = _parse_number(entry.get(field))
        if number is not None and not low <= number <= high:
            exceeded.append(field)
    return exceeded


def sync_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """把展示字段与内部状态对齐，保证列表、详情、导出三处口径一致。"""
    status = str(entry.get("status") or STATUS_ORDER[0])
    entry["status"] = status
    entry["记录状态"] = status
    entry["pending"] = status != STATUS_ORDER[-1]
    entry["abnormal"] = status == "偏离预警"
    entry.setdefault("纠正说明", "")
    entry.setdefault("纠正历史", [])
    return entry


class EnvService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [sync_entry(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return sync_entry(entry)

    def _active_entry_of_area(self, area: str) -> dict[str, Any] | None:
        """同一监测区域当期只留一条未归档记录；归档记录不参与合并。"""
        for row in store.rows(MODULE):
            if row.get("监测区域") == area and row.get("status") != "已归档":
                return row
        return None

    def _entry_of_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("记录编号", "")) == code:
                return row
        return None

    def _next_code(self) -> str:
        used = {str(row.get("记录编号", "")) for row in store.rows(MODULE)}
        seq = len(used) + 1
        while f"ENV-{seq:04d}" in used:
            seq += 1
        return f"ENV-{seq:04d}"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str] | str]:
        """登记或合并当期记录：同区域已有未归档记录时更新原记录，避免重复数据。"""
        area = str(values.get("监测区域") or "").strip()
        if not area:
            return None, ["监测区域"]
        code = str(values.get("记录编号") or "").strip()

        entry = self._active_entry_of_area(area)
        if entry is None:
            # 新区域建档时温度值必填；合并既有记录时允许只更新部分读数。
            if not str(values.get("温度值") or "").strip():
                return None, ["温度值"]
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry["纠正历史"] = []
            entry["status"] = STATUS_ORDER[0]
            rows.append(entry)
        elif code and str(entry.get("记录编号", "")) != code:
            return None, f"监测区域「{area}」的当期记录编号是 {entry.get('记录编号')}，与提交的 {code} 不一致"

        if code:
            owner = self._entry_of_code(code)
            if owner is not None and owner is not entry:
                return None, f"记录编号 {code} 已属于监测区域「{owner.get('监测区域')}」，不能重复占用"
            entry["记录编号"] = code
        elif not entry.get("记录编号"):
            entry["记录编号"] = self._next_code()

        entry["监测区域"] = area
        for field in MEASURE_FIELDS + EXTRA_FIELDS:
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)

        # 在控记录按最新读数复评：超范围且未纠正过才挂预警；
        # 已纠正过的记录两次纠正之间不会再自动冒出偏离预警，偏离预警记录也不会被登记动作静默改掉。
        if entry.get("status") == STATUS_ORDER[0]:
            exceeded = out_of_control_fields(entry)
            if exceeded and not entry.get("纠正历史"):
                entry["status"] = "偏离预警"
                entry["预警原因"] = "、".join(f"{field}超范围" for field in exceeded)
        return sync_entry(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"环境记录 {entry_id} 不存在"
        sync_entry(entry)
        if entry["status"] == "已归档":
            return None, f"环境记录 {entry.get('记录编号')} 已归档，仅可查询，不能再执行任何动作"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于环境监控可执行范围"

        if action == "偏离预警":
            if entry["status"] != "在控":
                return None, f"记录当前为「{entry['status']}」，只有在控记录才能发起偏离预警"
            exceeded = out_of_control_fields(entry)
            if not exceeded:
                return None, "当前读数均在受控范围内，不能挂偏离预警"
            entry["status"] = "偏离预警"
            entry["预警原因"] = "、".join(f"{field}超范围" for field in exceeded)
            return sync_entry(entry), f"环境记录已偏离预警（{entry['预警原因']}）"

        if action == "纠正记录":
            if entry["status"] != "偏离预警":
                return None, f"记录当前为「{entry['status']}」，只有偏离预警记录需要纠正"
            note = str(values.get("纠正说明") or "").strip()
            if not note:
                return None, "纠正记录必须填写纠正说明"
            for field in MEASURE_FIELDS:
                if values.get(field) not in (None, ""):
                    entry[field] = values.get(field)
            exceeded = out_of_control_fields(entry)
            if exceeded:
                return None, f"{'、'.join(exceeded)}仍超出受控范围，不能恢复在控"
            entry["status"] = "在控"
            entry["纠正说明"] = note
            entry.pop("预警原因", None)
            entry["纠正历史"].append({
                "纠正时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "纠正说明": note,
                "纠正后状态": "在控",
            })
            return sync_entry(entry), "环境记录已纠正，恢复在控"

        # 归档：只有在控记录可以归档；归档后只读、不再触发预警。
        if entry["status"] != "在控":
            return None, "偏离预警记录须先纠正为在控，才能归档"
        entry["status"] = "已归档"
        return sync_entry(entry), "环境记录已归档"
