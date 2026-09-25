"""条件路由：图的每一条分支都由这里决定。

集中在一处的理由：路由是这套架构里最容易出错、也最需要被审计的部分。
把"什么条件下走哪条边"全部收进一个类，可以用单元测试覆盖每一条分支，
而不必把整张图跑起来。
"""

from __future__ import annotations

from agriagents.graph.execution_plan import (
    APPROVAL_GATE,
    CLOSE_CASE,
    COORDINATOR,
    DIAGNOSIS_AGENT,
    EXECUTOR,
    FINALIZE,
    PERCEPTION_AGENT,
    ROLLBACK_PLANNER,
    WORK_ORDER_PLANNER,
)


class ConditionalLogic:
    """所有条件边的判断逻辑。每个方法的返回值都是图中的节点名。"""

    def __init__(
        self,
        max_perception_rounds: int = 1,
        max_diagnosis_rounds: int = 1,
        max_coordination_rounds: int = 3,
        max_safety_rounds: int = 2,
        max_rollback_rounds: int = 1,
        auto_close_when_no_anomaly: bool = True,
    ):
        self.max_perception_rounds = max_perception_rounds
        self.max_diagnosis_rounds = max_diagnosis_rounds
        self.max_coordination_rounds = max_coordination_rounds
        self.max_safety_rounds = max_safety_rounds
        self.max_rollback_rounds = max_rollback_rounds
        self.auto_close_when_no_anomaly = auto_close_when_no_anomaly

    # ---- 感知之后 ----
    def after_anomaly_screen(self, state) -> str:
        """初筛之后：无异常且允许自动关单则收口，否则进入诊断。"""
        perception = state["perception_state"]
        if self.auto_close_when_no_anomaly and not perception.get("has_anomaly", True):
            return CLOSE_CASE
        return DIAGNOSIS_AGENT

    # ---- 中枢分派 ----
    def coordinator_next(self, state) -> str:
        """按中枢给出的 next_action 路由，同时执行两道硬闸门。

        中枢的提示词里已经写明轮次上限，但路由层必须独立再判一次：
        模型可以无视提示词，路由不行。
        """
        coordination = state["coordination_state"]
        perception_rounds = state["perception_state"].get("perception_rounds", 0)
        diagnosis_rounds = state["diagnosis_state"].get("diagnosis_rounds", 0)
        coordinator_rounds = coordination.get("coordinator_rounds", 0)

        # 闸门一：中枢轮次耗尽 → 强制收口
        if coordinator_rounds > self.max_coordination_rounds:
            return CLOSE_CASE

        action = (coordination.get("next_action") or "").strip()

        # 闸门二：补充采集/诊断的次数不得超过预算，超了就当收口处理
        if action == "perceive_more" and perception_rounds < self.max_perception_rounds:
            return PERCEPTION_AGENT
        if action == "diagnose_more" and diagnosis_rounds < self.max_diagnosis_rounds:
            return DIAGNOSIS_AGENT
        if action == "close":
            return CLOSE_CASE
        return WORK_ORDER_PLANNER

    # ---- 安全检查之后 ----
    def after_safety_review(self, state) -> str:
        """裁决路由。block 是终局：只能交回中枢，绝不放行。"""
        coordination = state["coordination_state"]
        verdict = (coordination.get("safety_verdict") or "").strip()
        if verdict == "pass":
            return APPROVAL_GATE
        if verdict == "revise" and coordination.get("safety_rounds", 0) < self.max_safety_rounds:
            return WORK_ORDER_PLANNER
        return COORDINATOR

    # ---- 人工审批之后 ----
    def after_approval(self, state) -> str:
        return EXECUTOR if state["coordination_state"].get("approved") else COORDINATOR

    # ---- 执行之后 ----
    def after_execution(self, state) -> str:
        """执行失败且还有回滚预算 → 先出回滚预案；否则收口（失败也要留档）。"""
        coordination = state["coordination_state"]
        execution = state["execution_state"]
        status = (execution.get("execution_status") or "").strip()

        if status == "failed" and coordination.get("rollback_rounds", 0) < self.max_rollback_rounds:
            return ROLLBACK_PLANNER
        return FINALIZE
