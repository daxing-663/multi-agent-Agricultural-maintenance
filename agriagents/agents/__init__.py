"""四个 Agent 组的节点工厂汇总。

命名约定：``create_<角色>(llm)`` 返回一个 LangGraph 节点函数。
这样每个 Agent 都能被单独测试（喂一个假的 llm 就能跑），
也能在图里按需装配——不在使用中的角色不必实例化。
"""

from agriagents.agents.context import create_msg_delete
from agriagents.agents.coordinator.approval_gate import create_approval_gate
from agriagents.agents.coordinator.coordinator import create_coordinator
from agriagents.agents.coordinator.finalize import create_close_case_node, create_finalize_node
from agriagents.agents.coordinator.rollback_planner import create_rollback_planner
from agriagents.agents.coordinator.safety_review import create_safety_review
from agriagents.agents.coordinator.work_order_planner import create_work_order_planner
from agriagents.agents.diagnosis.diagnosis_agent import create_diagnosis_agent
from agriagents.agents.diagnosis.severity_assess import create_severity_assess
from agriagents.agents.execution.executor import create_executor
from agriagents.agents.execution.feedback import create_feedback_collect
from agriagents.agents.perception.anomaly_screen import create_anomaly_screen
from agriagents.agents.perception.perception_agent import create_perception_agent
from agriagents.agents.state import (
    AgriState,
    CoordinationState,
    DiagnosisState,
    ExecutionState,
    PerceptionState,
)

__all__ = [
    "AgriState",
    "PerceptionState",
    "DiagnosisState",
    "CoordinationState",
    "ExecutionState",
    "create_msg_delete",
    # 感知
    "create_perception_agent",
    "create_anomaly_screen",
    # 诊断
    "create_diagnosis_agent",
    "create_severity_assess",
    # 协调决策
    "create_coordinator",
    "create_work_order_planner",
    "create_safety_review",
    "create_approval_gate",
    "create_rollback_planner",
    "create_close_case_node",
    "create_finalize_node",
    # 执行
    "create_executor",
    "create_feedback_collect",
]
