"""资源与安全规则适配器（占位）。

TODO(内容):
- 资源：人力排班表、机具可用性台账、农资与备件库存；
- 安全规则：禁用/限用清单、安全间隔期、用水与环保约束、作业安全规程。

安全规则库是执行前的**硬约束来源**，接真实规则后应当做版本管理：
同一条工单在不同版本的规则下可能有不同裁决，留痕时必须能指回版本。
"""

from __future__ import annotations

from agriagents.dataflows.errors import NoDataError

VENDOR_NAME = "stub_ops"
_PENDING = "未接入真实数据源（框架占位）。"


def get_resource_inventory(site_id: str) -> str:
    """查询可用资源（人力 / 机具 / 农资）。"""
    return (
        f"[{VENDOR_NAME}] 资源账本 @ {site_id}\n{_PENDING}\n"
        "契约：返回人力、机具、农资库存及各自的可调度时间窗。"
    )


def check_safety_policy(action: str, materials: str) -> str:
    """对照安全与合规规则库检查作业方案。

    规则库未接入时**必须抛错**：返回一段占位文本让流程继续走下去，
    等于用"看起来审过了"替代"实际没有规则可依"，是最危险的静默降级。
    """
    raise NoDataError("check_safety_policy", VENDOR_NAME, _PENDING)
