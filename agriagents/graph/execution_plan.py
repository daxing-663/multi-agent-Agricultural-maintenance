"""节点清单：全图"有哪些节点、叫什么、属于哪个组"的单一事实来源。

仿 TradingAgents 的 ``analyst_execution.py``：把节点名集中成数据，
构图代码只消费这份数据，避免节点名在多个文件里手写导致漂移
（LangGraph 里节点名写错不会报错，只会在运行时路由失败，很难查）。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- 感知组 ----
PERCEPTION_AGENT = "Perception Agent"
PERCEPTION_TOOLS = "tools_perception"
PERCEPTION_CLEAR = "Msg Clear Perception"
ANOMALY_SCREEN = "Anomaly Screen"

# ---- 诊断组 ----
DIAGNOSIS_AGENT = "Diagnosis Agent"
DIAGNOSIS_TOOLS = "tools_diagnosis"
DIAGNOSIS_CLEAR = "Msg Clear Diagnosis"
SEVERITY_ASSESS = "Severity Assess"

# ---- 协调决策组 ----
COORDINATOR = "Coordinator"
WORK_ORDER_PLANNER = "Work Order Planner"
SAFETY_REVIEW = "Safety Review"
APPROVAL_GATE = "Approval Gate"
ROLLBACK_PLANNER = "Rollback Planner"
CLOSE_CASE = "Close Case"
FINALIZE = "Finalize"

# ---- 执行组 ----
EXECUTOR = "Executor"
EXECUTION_TOOLS = "tools_execution"
FEEDBACK_COLLECT = "Feedback Collect"


@dataclass(frozen=True)
class NodeSpec:
    key: str
    node: str
    team: str
    kind: str          # agent | tool | clear | gate | deterministic
    owns_state: str    # 该节点主要负责读写的子状态
    model: str         # quick | deep | none


NODE_SPECS: tuple[NodeSpec, ...] = (
    NodeSpec("perception_agent", PERCEPTION_AGENT, "perception", "agent", "perception_state", "quick"),
    NodeSpec("perception_tools", PERCEPTION_TOOLS, "perception", "tool", "perception_state", "none"),
    NodeSpec("perception_clear", PERCEPTION_CLEAR, "perception", "clear", "perception_state", "none"),
    NodeSpec("anomaly_screen", ANOMALY_SCREEN, "perception", "agent", "perception_state", "quick"),

    NodeSpec("diagnosis_agent", DIAGNOSIS_AGENT, "diagnosis", "agent", "diagnosis_state", "quick"),
    NodeSpec("diagnosis_tools", DIAGNOSIS_TOOLS, "diagnosis", "tool", "diagnosis_state", "none"),
    NodeSpec("diagnosis_clear", DIAGNOSIS_CLEAR, "diagnosis", "clear", "diagnosis_state", "none"),
    NodeSpec("severity_assess", SEVERITY_ASSESS, "diagnosis", "agent", "diagnosis_state", "quick"),

    NodeSpec("coordinator", COORDINATOR, "coordinator", "agent", "coordination_state", "deep"),
    NodeSpec("work_order_planner", WORK_ORDER_PLANNER, "coordinator", "agent", "coordination_state", "deep"),
    NodeSpec("safety_review", SAFETY_REVIEW, "coordinator", "agent", "coordination_state", "quick"),
    NodeSpec("approval_gate", APPROVAL_GATE, "coordinator", "gate", "coordination_state", "none"),
    NodeSpec("rollback_planner", ROLLBACK_PLANNER, "coordinator", "agent", "coordination_state", "deep"),
    NodeSpec("close_case", CLOSE_CASE, "coordinator", "deterministic", "coordination_state", "none"),
    NodeSpec("finalize", FINALIZE, "coordinator", "deterministic", "coordination_state", "none"),

    NodeSpec("executor", EXECUTOR, "execution", "agent", "execution_state", "quick"),
    NodeSpec("execution_tools", EXECUTION_TOOLS, "execution", "tool", "execution_state", "none"),
    NodeSpec("feedback_collect", FEEDBACK_COLLECT, "execution", "agent", "execution_state", "quick"),
)

# 报告树与 CLI 的展示顺序
TEAM_ORDER = ("perception", "diagnosis", "coordinator", "execution")
