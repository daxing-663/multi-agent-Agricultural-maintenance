"""图状态契约：所有节点读写的数据结构集中定义于此。

与 TradingAgents 的 ``agents/state.py`` 同构：

- 顶层 ``AgriState`` 继承 ``MessagesState``，承载跨阶段共享的上下文；
- 四个 Agent 各自有一个嵌套子状态（感知 / 诊断 / 协调决策 / 执行）。

⚠️ 框架级注意事项：嵌套 TypedDict 在 LangGraph 中**没有 reducer**，
节点更新时必须返回**完整**的子状态字典；只返回部分键会把其余键整体覆盖掉。
"""

from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import NotRequired, TypedDict


class PerceptionState(TypedDict):
    """感知 Agent 的子状态：多通道采集 + 异常初筛。

    多通道（传感器 / 影像 / 设备状态 / 气象）由同一个 Agent 通过工具调用覆盖，
    因此只保留一份综合报告；缺失的通道要在报告里写明缺失，而不是留空。
    """

    perception_report: Annotated[str, "多通道综合感知报告"]
    anomaly_screen: Annotated[str, "异常初筛结论（结构化结果渲染后的 markdown）"]
    anomaly_level: Annotated[str, "初筛等级：none / low / medium / high"]
    has_anomaly: Annotated[bool, "是否发现需要跟进的异常"]
    perception_rounds: Annotated[int, "已执行的采集轮次，用于预算控制"]
    pending_requests: Annotated[str, "中枢要求补采的通道清单（空表示常规采集）"]


class DiagnosisState(TypedDict):
    """诊断 Agent 的子状态：判断病虫害 / 土壤问题 / 设备故障。"""

    diagnosis_report: Annotated[str, "诊断报告：候选病因、鉴别过程与证据"]
    citation_check: NotRequired[dict]
    severity: Annotated[str, "严重度：none / low / medium / high / critical"]
    confidence: Annotated[str, "结论置信度：low / medium / high"]
    rationale: Annotated[str, "证据链与推理依据"]
    recommended_checks: Annotated[str, "建议补做的检查项"]
    diagnosis_history: Annotated[str, "历次诊断记录（供中枢复核与追问）"]
    diagnosis_rounds: Annotated[int, "已执行的诊断轮次"]
    pending_request: Annotated[str, "中枢要求补充诊断的具体问题（空表示常规诊断）"]


class CoordinationState(TypedDict):
    """协调决策 Agent 的子状态：分解、分配、工单、安检、审批、回滚。"""

    next_action: Annotated[str, "中枢决策：perceive_more / diagnose_more / plan_work_order / close"]
    decision_reason: Annotated[str, "本次分派或收口的理由"]
    coordination_notes: Annotated[str, "中枢决策正文（结构化结果渲染后的 markdown）"]
    work_order: Annotated[str, "维护工单（结构化结果渲染后的 markdown）"]
    safety_review: Annotated[str, "安全检查正文"]
    safety_verdict: Annotated[str, "安全检查裁决：pass / revise / block"]
    approval: Annotated[str, "人工审批记录（审批人 / 结论 / 意见）"]
    approved: Annotated[bool, "是否已获批准执行"]
    rollback_plan: Annotated[str, "失败回滚预案"]
    coordination_history: Annotated[str, "中枢历次决策记录"]
    coordinator_rounds: Annotated[int, "中枢决策轮次"]
    safety_rounds: Annotated[int, "安全检查打回重做的轮次"]
    rollback_rounds: Annotated[int, "回滚重试轮次"]


class ExecutionState(TypedDict):
    """执行 Agent 的子状态：下发指令 + 执行后反馈。"""

    dispatch_log: Annotated[str, "指令下发记录（含干跑标记）"]
    execution_status: Annotated[str, "执行结果：success / partial / failed"]
    execution_report: Annotated[str, "执行后反馈（结构化结果渲染后的 markdown）"]
    needs_followup: Annotated[bool, "是否需要后续复检"]


class AgriState(MessagesState):
    """整图的顶层状态。"""

    # ---- 案件上下文（run 开始时一次性确定，之后只读）----
    site_of_interest: Annotated[str, "维护对象：地块 / 大棚 / 果园 / 机具编号"]
    site_type: Annotated[str, "对象类型：field / greenhouse / orchard / machine"]
    as_of: Annotated[str, "本次维护决策的基准日期 YYYY-MM-DD，所有取数不得晚于它"]
    site_context: Annotated[str, "确定性解析出的对象档案，锚定所有 Agent 的认知"]
    resource_context: Annotated[str, "可用人力 / 机具 / 农资；未提供时明确标注为未知"]
    past_context: Annotated[str, "历史决策与复盘教训（run 开始时注入）"]

    # ---- 四个 Agent 的子状态 ----
    perception_state: Annotated[PerceptionState, "感知组状态"]
    diagnosis_state: Annotated[DiagnosisState, "诊断组状态"]
    coordination_state: Annotated[CoordinationState, "协调决策组状态"]
    execution_state: Annotated[ExecutionState, "执行组状态"]

    # ---- 交付物 ----
    final_decision: Annotated[str, "《维护决策单》：最终结论与依据"]
    case_status: Annotated[str, "案件状态：closed_no_action / planned / executed / rolled_back"]
