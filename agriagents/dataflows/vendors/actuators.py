"""执行机构网关适配器（占位）——**唯一会改变物理世界的一层**。

框架层面的两道硬约束：
1. 必须已通过人工审批（由审批门节点保证，本层不做判断，只认工单号）；
2. ``dry_run`` 打开时只记录指令、不下发（默认打开）。

TODO(内容): 接入真实网关（灌溉控制器、变量喷洒机、阀门、机械臂、农机调度系统），
并在本层实现超时、幂等与回执校验。
"""

from __future__ import annotations

from agriagents.dataflows.config import get_config

VENDOR_NAME = "stub_actuators"
_PENDING = "未接入真实执行网关（框架占位）。"


def dispatch_command(
    site_id: str, actuator: str, command: str, params: str, work_order_id: str
) -> str:
    """下发控制指令；``dry_run`` 时只留痕不下发。"""
    config = get_config()
    if config.get("dry_run", True):
        return (
            f"[{VENDOR_NAME}][dry-run] 已记录但**未下发**："
            f"工单={work_order_id} 对象={site_id} 机构={actuator} 指令={command} 参数={params}"
        )
    return (
        f"[{VENDOR_NAME}] 下发：工单={work_order_id} 对象={site_id} 机构={actuator} "
        f"指令={command} 参数={params}；{_PENDING}"
    )


def get_execution_status(site_id: str, task_id: str) -> str:
    """查询已下发任务的执行状态。"""
    return f"[{VENDOR_NAME}] 任务 {task_id} @ {site_id} 的执行状态\n{_PENDING}"
