"""结构化输出 schema。

框架的**主要载体仍然是自然语言**：各 Agent 的推理正文是用户看到的报告，
也是下游 Agent 读到的上下文。结构化输出只加在三个"下结论"的节点上
（协调决策中枢、工单规划、安全检查），目的是：

- 让关键字段（等级 / 裁决 / 状态）跨模型、跨轮次保持稳定，便于路由判断；
- 字段描述同时充当模型的输出指令，提示词正文只留上下文与判断原则；
- 渲染函数把 Pydantic 实例还原成 markdown，显示层 / 台账 / 报告树无需改动。

注意：``Disposition`` 的规范值用于代码判断，中文展示用 ``rating.label()``。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from agriagents.agents.rating import DISPOSITION_LABELS


class Disposition(str, Enum):
    """五档处置等级（全系统共同词汇）。"""

    IMMEDIATE = "Immediate"
    SOON = "Soon"
    SCHEDULED = "Scheduled"
    MONITOR = "Monitor"
    NO_ACTION = "NoAction"


# ---------------------------------------------------------------------------
# 感知 Agent
# ---------------------------------------------------------------------------


class AnomalyScreenResult(BaseModel):
    """异常初筛结论：先判断"有没有事"，再决定要不要进诊断。"""

    has_anomaly: bool = Field(description="是否发现需要跟进的异常。无明显异常填 false。")
    anomaly_level: Literal["none", "low", "medium", "high"] = Field(
        description="异常等级。none=全部通道正常；high=出现需要立即处置的异常信号。"
    )
    channels: list[str] = Field(
        description="出现异常的通道名，例如 sensor / vision / device；无异常填空列表。"
    )
    summary: str = Field(description="一段话说明初筛结论与其证据来源。")
    evidence: str = Field(
        description="支撑结论的具体证据：通道、时间、读数或画面描述。禁止编造未采集到的数据。"
    )
    uncertainty: str = Field(description="初筛不确定的地方，以及需要补充采集才能确认的部分。")


def render_anomaly_screen(result: AnomalyScreenResult) -> str:
    """渲染初筛结论（首行机器可读，正文给人看）。"""
    return "\n".join([
        f"**异常等级:** {result.anomaly_level}",
        f"**是否需跟进:** {'是' if result.has_anomaly else '否'}",
        f"**异常通道:** {', '.join(result.channels) if result.channels else '无'}",
        "",
        result.summary,
        "",
        f"**证据:** {result.evidence}",
        f"**待确认:** {result.uncertainty}",
    ])


# ---------------------------------------------------------------------------
# 诊断 Agent
# ---------------------------------------------------------------------------


class SeverityAssessment(BaseModel):
    """严重度评估：诊断结论的收敛点，也是中枢分配资源的依据。"""

    severity: Literal["none", "low", "medium", "high", "critical"] = Field(
        description="严重度。critical=若不处置将在短期内造成不可逆损失。"
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="结论置信度。证据不足或通道缺失时如实填 low。"
    )
    affected_scope: str = Field(description="影响范围：涉及的面积/株数/设备范围。")
    rationale: str = Field(description="证据链：哪些现象支持这个严重度。")
    recommended_checks: str = Field(description="还需补做的检查项，用于印证或排除该诊断。")


def render_severity(assessment: SeverityAssessment) -> str:
    return "\n".join([
        f"**严重度:** {assessment.severity}",
        f"**置信度:** {assessment.confidence}",
        f"**影响范围:** {assessment.affected_scope}",
        "",
        f"**依据:** {assessment.rationale}",
        f"**建议补检:** {assessment.recommended_checks}",
    ])


# ---------------------------------------------------------------------------
# 协调决策中枢
# ---------------------------------------------------------------------------


class CoordinationDecision(BaseModel):
    """中枢分派决策：下一步调用哪个 Agent，或者收口关单。"""

    next_action: Literal["perceive_more", "diagnose_more", "plan_work_order", "close"] = Field(
        description=(
            "下一步动作。perceive_more=证据不足需重新采集；diagnose_more=需补充诊断；"
            "plan_work_order=事实已足够，进入工单规划；close=无需处置，直接关单。"
        )
    )
    reason: str = Field(description="做出该分派的依据，须引用上游报告中的具体证据。")
    request: str = Field(description="当 next_action 为 perceive_more / diagnose_more 时，列出具体要求。")
    disposition_hint: Disposition = Field(description="对最终处置等级的预判，供后续节点参考。")
    summary: str = Field(description="给人工读者的一段话小结：当前判断与下一步。")

    @property
    def disposition_label(self) -> str:
        return DISPOSITION_LABELS.get(self.disposition_hint.value, self.disposition_hint.value)


def render_coordination_decision(decision: CoordinationDecision) -> str:
    return "\n".join([
        f"**下一步:** {decision.next_action}",
        f"**处置预判:** {DISPOSITION_LABELS.get(decision.disposition_hint.value, decision.disposition_hint.value)}",
        "",
        decision.summary,
        "",
        f"**依据:** {decision.reason}",
        f"**补充要求:** {decision.request or '无'}",
    ])


# ---------------------------------------------------------------------------
# 工单与安全检查
# ---------------------------------------------------------------------------


class WorkOrder(BaseModel):
    """维护工单草案：说清做什么、用什么、什么时候做、怎么算做成。"""

    work_order_id: str = Field(description="工单编号，格式 WO-<对象编号>-<日期>。")
    site_id: str = Field(description="维护对象编号，必须与案件编号一致。")
    disposition: Disposition = Field(description="本工单对应的处置等级。")
    tasks: str = Field(description="任务分解：按顺序列出具体作业项，每项写明对象与判定标准。")
    resources: str = Field(description="所需人力、机具、农资及数量；库存未知时写明按实际折算。")
    schedule: str = Field(description="作业时间窗与顺序约束，含天气/农时窗口要求。")
    success_criteria: str = Field(description="验收标准：作业后用什么指标判断做成了。")
    rollback_hint: str = Field(description="失败或效果不达标的回退线索，供回滚预案展开。")
    risk_notes: str = Field(description="执行中可能出现的风险与现场注意事项。")


def render_work_order(order: WorkOrder) -> str:
    return "\n".join([
        f"**工单号:** {order.work_order_id}",
        f"**对象:** {order.site_id}",
        f"**处置等级:** {DISPOSITION_LABELS.get(order.disposition.value, order.disposition.value)}",
        "",
        f"**任务分解:**\n{order.tasks}",
        "",
        f"**资源需求:** {order.resources}",
        f"**时间窗口:** {order.schedule}",
        f"**验收标准:** {order.success_criteria}",
        f"**回退线索:** {order.rollback_hint}",
        f"**风险提示:** {order.risk_notes}",
    ])


class SafetyReviewResult(BaseModel):
    """安全检查结论：执行前的最后一道闸门。"""

    verdict: Literal["pass", "revise", "block"] = Field(
        description=(
            "裁决。pass=可以进入人工审批；revise=工单需修改后重审；"
            "block=存在禁止性冲突（如安全间隔期、禁限用物资、人员安全），不得执行。"
        )
    )
    blocking_issues: str = Field(description="阻断性问题清单；无则填「无」。")
    required_controls: str = Field(description="放行必须落实的控制措施：防护、隔离、监测、告知等。")
    notes: str = Field(description="其他安全提示与需要人工确认的疑点。")


def render_safety_review(review: SafetyReviewResult) -> str:
    return "\n".join([
        f"**安全裁决:** {review.verdict}",
        "",
        f"**阻断性问题:** {review.blocking_issues}",
        f"**必须落实的控制措施:** {review.required_controls}",
        f"**备注:** {review.notes}",
    ])


# ---------------------------------------------------------------------------
# 执行与交付物
# ---------------------------------------------------------------------------


class ExecutionOutcome(BaseModel):
    """执行后反馈：真实发生了什么，与计划差在哪里。"""

    status: Literal["success", "partial", "failed"] = Field(description="执行结果。")
    actions_taken: str = Field(description="实际执行的动作清单，逐条对应工单任务。")
    deviations: str = Field(description="与工单的偏差：未做、多做、参数不同的部分。")
    feedback: str = Field(description="现场观察到的现象与设备回执，供复盘使用。")
    needs_followup: bool = Field(description="是否需要后续跟进或复检。")


def render_execution_outcome(outcome: ExecutionOutcome) -> str:
    return "\n".join([
        f"**执行状态:** {outcome.status}",
        f"**需要跟进:** {'是' if outcome.needs_followup else '否'}",
        "",
        f"**实际动作:**\n{outcome.actions_taken}",
        "",
        f"**偏差:** {outcome.deviations}",
        f"**现场反馈:** {outcome.feedback}",
    ])


class MaintenanceDecision(BaseModel):
    """《维护决策单》：整条流水线唯一的对外交付物。"""

    disposition: Disposition = Field(description="最终处置等级，必须是五档之一。")
    site_id: str = Field(description="维护对象编号。")
    executive_summary: str = Field(description="一段话结论：判断是什么、建议做什么、什么时候做。")
    basis: str = Field(description="决策依据：引用上游诊断与安全检查的具体结论。")
    work_order_ref: str = Field(description="关联工单号；无工单填「无」。")
    approval_note: str = Field(description="审批情况：审批人、结论、附加条件。")
    what_would_change: str = Field(description="出现什么新证据会改变本结论。")


def render_maintenance_decision(decision: MaintenanceDecision) -> str:
    """渲染决策单。首行的「处置等级：」是台账解析器依赖的机器可读行。"""
    return "\n".join([
        f"**处置等级：** {DISPOSITION_LABELS.get(decision.disposition.value, decision.disposition.value)}",
        f"**对象：** {decision.site_id}",
        "",
        f"**结论：** {decision.executive_summary}",
        "",
        f"**依据：** {decision.basis}",
        f"**关联工单：** {decision.work_order_ref}",
        f"**审批：** {decision.approval_note}",
        f"**改变条件：** {decision.what_would_change}",
    ])
