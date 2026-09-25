"""状态初始化：把一次 run 所需的全部上下文装配成初始 state。

与 TradingAgents 的 ``propagator`` 同构。要点：

- 子状态必须**完整初始化**（嵌套 TypedDict 没有 reducer，缺键会导致覆盖）；
- 案件上下文（对象档案、资源账本、历史教训）在 run 开始时一次性注入，
  之后所有节点都读同一份，保证整条流水线的"事实基线"一致。
"""

from typing import Any

from agriagents.agents.state import (
    CoordinationState,
    DiagnosisState,
    ExecutionState,
    PerceptionState,
)


class Propagator:
    """负责初始状态构造与调用参数。"""

    def __init__(self, max_recur_limit: int = 100):
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        site_id: str,
        as_of: str,
        site_type: str = "field",
        site_context: str = "",
        resource_context: str = "",
        past_context: str = "",
    ) -> dict[str, Any]:
        return {
            "messages": [("human", site_id)],
            "site_of_interest": site_id,
            "site_type": site_type,
            "as_of": str(as_of),
            "site_context": site_context,
            "resource_context": resource_context,
            "past_context": past_context,
            "perception_state": PerceptionState(
                perception_report="",
                anomaly_screen="",
                anomaly_level="",
                has_anomaly=False,
                perception_rounds=0,
                pending_requests="",
            ),
            "diagnosis_state": DiagnosisState(
                diagnosis_report="",
                severity="",
                confidence="",
                rationale="",
                recommended_checks="",
                diagnosis_history="",
                diagnosis_rounds=0,
                pending_request="",
            ),
            "coordination_state": CoordinationState(
                next_action="",
                decision_reason="",
                coordination_notes="",
                work_order="",
                safety_review="",
                safety_verdict="",
                approval="",
                approved=False,
                rollback_plan="",
                coordination_history="",
                coordinator_rounds=0,
                safety_rounds=0,
                rollback_rounds=0,
            ),
            "execution_state": ExecutionState(
                dispatch_log="",
                execution_status="",
                execution_report="",
                needs_followup=False,
            ),
            "final_decision": "",
            "case_status": "",
        }

    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """图调用参数：递归上限是防死循环的最后一道保险丝。"""
        config: dict[str, Any] = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {"stream_mode": "values", "config": config}
