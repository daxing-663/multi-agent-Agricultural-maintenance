"""图装配：把 Agent 节点与它们之间的边接成 LangGraph 状态图。

拓扑（与 TradingAgents 的"分析师 → 辩论 → 裁决"结构对应）：

    感知 Agent ⇄ 感知工具
        ↓
    异常初筛 ──无异常──→ 收口关单
        ↓ 有异常
    诊断 Agent ⇄ 诊断工具
        ↓
    严重度评估
        ↓
    ┌─→ 协调决策中枢（枢纽，深思考模型）─┐
    │      ├─ 补采集 → 感知 Agent        │
    │      ├─ 补诊断 → 诊断 Agent        │
    │      ├─ 出工单 → 工单规划          │
    │      └─ 收口   → 关单              │
    │                                    │
    │   工单规划 → 安全检查 ─pass→ 人工审批 ─通过→ 执行 Agent ⇄ 执行工具
    │        ↑         │revise        │驳回             ↓
    │        └─────────┘              │            执行反馈
    │            │block               │                │失败
    │            └────────→ 中枢 ←────┘            回滚预案 ──→ 中枢
    │                                                        │
    └──────────────────────────────────────────────→ 最终决策单
"""

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agriagents.agents import (
    create_anomaly_screen,
    create_approval_gate,
    create_close_case_node,
    create_coordinator,
    create_diagnosis_agent,
    create_executor,
    create_feedback_collect,
    create_finalize_node,
    create_msg_delete,
    create_perception_agent,
    create_rollback_planner,
    create_safety_review,
    create_severity_assess,
    create_work_order_planner,
)
from agriagents.agents.state import AgriState

from .conditional_logic import ConditionalLogic
from .execution_plan import (
    ANOMALY_SCREEN,
    APPROVAL_GATE,
    CLOSE_CASE,
    COORDINATOR,
    DIAGNOSIS_AGENT,
    DIAGNOSIS_CLEAR,
    DIAGNOSIS_TOOLS,
    EXECUTION_TOOLS,
    EXECUTOR,
    FEEDBACK_COLLECT,
    FINALIZE,
    PERCEPTION_AGENT,
    PERCEPTION_CLEAR,
    PERCEPTION_TOOLS,
    ROLLBACK_PLANNER,
    SAFETY_REVIEW,
    SEVERITY_ASSESS,
    WORK_ORDER_PLANNER,
)

# 每条条件边都把**所有可能的返回值**映射齐全。
# LangGraph 在路由函数返回未登记的路径时会在运行中崩溃，
# 而崩溃点往往离真正的原因很远——路径表写全是最省事的防御。
ANOMALY_PATH_MAP = {
    DIAGNOSIS_AGENT: DIAGNOSIS_AGENT,
    CLOSE_CASE: CLOSE_CASE,
}
COORDINATOR_PATH_MAP = {
    PERCEPTION_AGENT: PERCEPTION_AGENT,
    DIAGNOSIS_AGENT: DIAGNOSIS_AGENT,
    WORK_ORDER_PLANNER: WORK_ORDER_PLANNER,
    CLOSE_CASE: CLOSE_CASE,
}
SAFETY_PATH_MAP = {
    APPROVAL_GATE: APPROVAL_GATE,
    WORK_ORDER_PLANNER: WORK_ORDER_PLANNER,
    COORDINATOR: COORDINATOR,
}
APPROVAL_PATH_MAP = {
    EXECUTOR: EXECUTOR,
    COORDINATOR: COORDINATOR,
}
EXECUTION_PATH_MAP = {
    ROLLBACK_PLANNER: ROLLBACK_PLANNER,
    FINALIZE: FINALIZE,
}


def _tools_or_clear(tool_node: str, clear_node: str):
    """路由一个 Agent 的一轮：有工具调用就去执行工具，否则本轮报告已完成。"""

    def route(state) -> str:
        return tool_node if state["messages"][-1].tool_calls else clear_node

    return route


class GraphSetup:
    """负责节点注册与边连接。"""

    def __init__(self, quick_thinking_llm, deep_thinking_llm,
                 conditional_logic: ConditionalLogic):
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.conditional_logic = conditional_logic

    def setup_graph(self):
        """构建并返回未编译的 StateGraph。"""
        quick, deep = self.quick_thinking_llm, self.deep_thinking_llm

        # 各 Agent 的工具集来自其模块内的 TOOLS 元组，图与实际可用工具不会漂移
        from agriagents.agents.diagnosis.diagnosis_agent import TOOLS as DIAGNOSIS_TOOLS_LIST
        from agriagents.agents.execution.executor import TOOLS as EXECUTION_TOOLS_LIST
        from agriagents.agents.perception.perception_agent import TOOLS as PERCEPTION_TOOLS_LIST

        workflow = StateGraph(AgriState)

        # ---- 感知组 ----
        workflow.add_node(PERCEPTION_AGENT, create_perception_agent(quick))
        workflow.add_node(PERCEPTION_TOOLS, ToolNode(list(PERCEPTION_TOOLS_LIST)))
        workflow.add_node(PERCEPTION_CLEAR, create_msg_delete())
        workflow.add_node(ANOMALY_SCREEN, create_anomaly_screen(quick))

        # ---- 诊断组 ----
        workflow.add_node(DIAGNOSIS_AGENT, create_diagnosis_agent(quick))
        workflow.add_node(DIAGNOSIS_TOOLS, ToolNode(list(DIAGNOSIS_TOOLS_LIST)))
        workflow.add_node(DIAGNOSIS_CLEAR, create_msg_delete())
        workflow.add_node(SEVERITY_ASSESS, create_severity_assess(quick))

        # ---- 协调决策组（判断类节点用深思考模型）----
        workflow.add_node(COORDINATOR, create_coordinator(deep))
        workflow.add_node(WORK_ORDER_PLANNER, create_work_order_planner(deep))
        workflow.add_node(SAFETY_REVIEW, create_safety_review(quick))
        workflow.add_node(APPROVAL_GATE, create_approval_gate())
        workflow.add_node(ROLLBACK_PLANNER, create_rollback_planner(deep))
        workflow.add_node(CLOSE_CASE, create_close_case_node())
        workflow.add_node(FINALIZE, create_finalize_node())

        # ---- 执行组 ----
        workflow.add_node(EXECUTOR, create_executor(quick))
        workflow.add_node(EXECUTION_TOOLS, ToolNode(list(EXECUTION_TOOLS_LIST)))
        workflow.add_node(FEEDBACK_COLLECT, create_feedback_collect(quick))

        # ---- 边：感知 ----
        workflow.add_edge(START, PERCEPTION_AGENT)
        workflow.add_conditional_edges(
            PERCEPTION_AGENT,
            _tools_or_clear(PERCEPTION_TOOLS, PERCEPTION_CLEAR),
            [PERCEPTION_TOOLS, PERCEPTION_CLEAR],
        )
        workflow.add_edge(PERCEPTION_TOOLS, PERCEPTION_AGENT)
        workflow.add_edge(PERCEPTION_CLEAR, ANOMALY_SCREEN)
        workflow.add_conditional_edges(
            ANOMALY_SCREEN,
            self.conditional_logic.after_anomaly_screen,
            ANOMALY_PATH_MAP,
        )

        # ---- 边：诊断 ----
        workflow.add_conditional_edges(
            DIAGNOSIS_AGENT,
            _tools_or_clear(DIAGNOSIS_TOOLS, DIAGNOSIS_CLEAR),
            [DIAGNOSIS_TOOLS, DIAGNOSIS_CLEAR],
        )
        workflow.add_edge(DIAGNOSIS_TOOLS, DIAGNOSIS_AGENT)
        workflow.add_edge(DIAGNOSIS_CLEAR, SEVERITY_ASSESS)
        workflow.add_edge(SEVERITY_ASSESS, COORDINATOR)

        # ---- 边：协调决策中枢（枢纽）----
        workflow.add_conditional_edges(
            COORDINATOR,
            self.conditional_logic.coordinator_next,
            COORDINATOR_PATH_MAP,
        )
        workflow.add_edge(WORK_ORDER_PLANNER, SAFETY_REVIEW)
        workflow.add_conditional_edges(
            SAFETY_REVIEW,
            self.conditional_logic.after_safety_review,
            SAFETY_PATH_MAP,
        )
        workflow.add_conditional_edges(
            APPROVAL_GATE,
            self.conditional_logic.after_approval,
            APPROVAL_PATH_MAP,
        )
        workflow.add_edge(ROLLBACK_PLANNER, COORDINATOR)
        workflow.add_edge(CLOSE_CASE, END)

        # ---- 边：执行 ----
        workflow.add_conditional_edges(
            EXECUTOR,
            _tools_or_clear(EXECUTION_TOOLS, FEEDBACK_COLLECT),
            [EXECUTION_TOOLS, FEEDBACK_COLLECT],
        )
        workflow.add_edge(EXECUTION_TOOLS, EXECUTOR)
        workflow.add_conditional_edges(
            FEEDBACK_COLLECT,
            self.conditional_logic.after_execution,
            EXECUTION_PATH_MAP,
        )
        workflow.add_edge(FINALIZE, END)

        return workflow
