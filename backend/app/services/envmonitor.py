"""环境监控业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "envmonitor"
REQUIRED_FIELDS = ["记录编号", "监测区域", "温度值", "湿度值"]
STATUS_ORDER = ["在控", "偏离预警", "已归档"]
ACTION_RULES = {"偏离预警": "偏离预警", "纠正记录": "在控", "归档": "已归档"}

IN_CONTROL, DEVIATION, ARCHIVED = STATUS_ORDER

# 实验室环境湿度控制范围（%RH）：超出范围判偏离预警，落回范围内才允许解除
HUMIDITY_MIN = 45.0
HUMIDITY_MAX = 65.0
RANGE_LABEL = f"{HUMIDITY_MIN:g}%~{HUMIDITY_MAX:g}%RH"

# 登记与流转时维护的展示字段；记录状态始终与 status 同步，保证列表、详情、归档三处一致
ENTRY_FIELDS = ["记录编号", "监测区域", "温度值", "湿度值", "压差值", "监测时间", "记录人员"]


def parse_humidity(raw: Any) -> float | None:
    """从湿度值里提取数值，支持 52、52.3%、52.3%RH 等写法；取不到数返回 None。"""
    match = re.search(r"\d+(?:\.\d+)?", str(raw or ""))
    return float(match.group()) if match else None


def humidity_in_range(raw: Any) -> bool | None:
    """湿度是否落在控制范围内；数值无法识别时返回 None，由调用方决定提示口径。"""
    value = parse_humidity(raw)
    if value is None:
        return None
    return HUMIDITY_MIN <= value <= HUMIDITY_MAX


class EnvService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        area: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if area:
            rows = [row for row in rows if area in str(row.get("监测区域", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        # 已归档的记录同样能查到，只是不能再改动
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        code = str(values.get("记录编号", "")).strip()
        area = str(values.get("监测区域", "")).strip()
        if any(str(row.get("记录编号", "")).strip() == code for row in store.rows(MODULE)):
            return None, [f"记录编号 {code} 已存在，不能重复登记"]
        in_range = humidity_in_range(values.get("湿度值"))
        if in_range is None:
            return None, ["湿度值需为可识别的数值（如 52.3 或 52.3%）"]
        # 湿度落点直接决定记录结论：范围内在控，范围外偏离预警
        status = IN_CONTROL if in_range else DEVIATION
        for row in self._current_rows(area):
            if row.get("status") != status:
                continue
            if status == IN_CONTROL:
                return None, [f"监测区域 {area} 当期已存在在控记录 {row.get('记录编号')}，同一区域只能保留一条在控数据"]
            return None, [f"监测区域 {area} 已存在未处理的偏离预警 {row.get('记录编号')}，请先完成纠正或归档"]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ENTRY_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["纠正说明"] = ""
        self._apply_status(entry, status)
        entry["abnormal"] = status == DEVIATION
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        values: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"环境记录 {entry_id} 不存在"
        if entry.get("status") == ARCHIVED:
            return None, f"环境记录 {entry.get('记录编号')} 已归档，仅可查询，不能再改动"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于环境监控可执行范围"
        values = values or {}
        if action == "偏离预警":
            return self._mark_deviation(entry, values)
        if action == "纠正记录":
            return self._correct(entry, values, remark)
        return self._archive(entry)

    def _current_rows(self, area: str) -> list[dict[str, Any]]:
        """同一监测区域的当期（未归档）记录：在控唯一性与重复预警都按这个口径判断。"""
        return [
            row
            for row in store.rows(MODULE)
            if str(row.get("监测区域", "")).strip() == area and row.get("status") != ARCHIVED
        ]

    def _apply_status(self, entry: dict[str, Any], status: str) -> None:
        entry["status"] = status
        entry["记录状态"] = status
        entry["pending"] = status == DEVIATION

    def _refresh_humidity(self, entry: dict[str, Any], values: dict[str, Any]) -> str | None:
        """动作里带了新的实测湿度值就先更新记录；返回错误说明，没有问题返回 None。"""
        raw = values.get("湿度值")
        if raw is None or not str(raw).strip():
            return None
        if parse_humidity(raw) is None:
            return "提交的湿度值无法识别，需为数值（如 52.3 或 52.3%）"
        entry["湿度值"] = str(raw).strip()
        return None

    def _mark_deviation(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        code = entry.get("记录编号")
        if entry.get("status") != IN_CONTROL:
            return None, f"环境记录 {code} 当前状态为{entry.get('status')}，不能重复标记偏离预警"
        error = self._refresh_humidity(entry, values)
        if error:
            return None, error
        in_range = humidity_in_range(entry.get("湿度值"))
        if in_range is None:
            return None, "湿度值无法识别，请提交实测湿度值后再标记偏离预警"
        if in_range:
            return None, f"湿度值 {parse_humidity(entry.get('湿度值')):g}% 处于 {RANGE_LABEL} 范围内，不能标记偏离预警"
        self._apply_status(entry, DEVIATION)
        entry["abnormal"] = True
        return entry, f"环境记录 {code} 已标记偏离预警"

    def _correct(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        remark: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        code = entry.get("记录编号")
        if entry.get("status") != DEVIATION:
            return None, f"环境记录 {code} 当前状态为{entry.get('status')}，无需纠正"
        note = str(remark or values.get("纠正说明") or "").strip()
        if not note:
            return None, "请填写纠正说明，解除偏离预警需要留痕"
        error = self._refresh_humidity(entry, values)
        if error:
            return None, error
        in_range = humidity_in_range(entry.get("湿度值"))
        if in_range is None:
            return None, "湿度值无法识别，请提交纠正后的复测湿度值"
        if not in_range:
            return None, f"湿度值 {parse_humidity(entry.get('湿度值')):g}% 仍超出 {RANGE_LABEL} 范围，偏离预警不能解除"
        for row in self._current_rows(str(entry.get("监测区域", "")).strip()):
            if row.get("id") != entry.get("id") and row.get("status") == IN_CONTROL:
                return None, f"监测区域 {entry.get('监测区域')} 已存在在控记录 {row.get('记录编号')}，请先归档该记录再解除预警"
        self._apply_status(entry, IN_CONTROL)
        entry["abnormal"] = False
        entry["纠正说明"] = note
        return entry, f"环境记录 {code} 偏离预警已解除，记录转为在控"

    def _archive(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        # abnormal 保持原值：偏离记录归档后仍留痕，但不再参与预警与待处理统计
        self._apply_status(entry, ARCHIVED)
        return entry, f"环境记录 {entry.get('记录编号')} 已归档，归档后仅可查询"
