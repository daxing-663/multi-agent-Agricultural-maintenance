"""协调决策中枢：整张图的路由器与唯一决策者。

它是三个执行型 Agent（感知 / 诊断 / 执行）的调度方：

- 证据不足 → 分派补充采集 / 补充诊断；
- 事实充分 → 进入工单规划，再走安全检查与人工审批；
- 无需处置 → 直接收口关单。

**它自己不做专业判断**：诊断结论来自诊断 Agent，安全结论来自安全检查节点。
中枢只负责"下一步调用谁"，以及把各方的结论合成一个可执行的决定。
这样出错时能定位到具体环节，而不是全部归咎于"AI 想错了"。

提示词分两层：``SYSTEM_PROMPT`` 是稳定的角色与规则，``build_prompt`` 注入本轮证据、
历史决策与轮次预算——预算提醒必须逐轮出现，否则模型会一直要求补数据。
"""

import logging

from agriagents.agents.context import (
    get_language_instruction,
    get_resource_context_from_state,
    get_site_context_from_state,
    report_or_absent,
)
from agriagents.agents.schemas import CoordinationDecision, render_coordination_decision
from agriagents.skills import load_skill
from agriagents.dataflows.config import get_config
from agriagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_with_optional_structured,
)

logger = logging.getLogger(__name__)

VALID_ACTIONS = ("perceive_more", "diagnose_more", "plan_work_order", "close")

SYSTEM_PROMPT = """\
你是「农业维护多智能体流程」中的**协调决策中枢**（Coordinator）：整条流水线唯一的路由与决策节点，\
也是本次维护结论的最终责任人。你调度三个执行型 Agent —— 感知、诊断、执行，\
把它们的产出组织成一个可执行、可审计的决定。

**你不亲自做专业判断**：诊断结论来自诊断 Agent，安全结论来自安全检查节点。\
你只决定"下一步调用谁"，以及"这次到底要不要动"。

## 一、可选动作（每轮必须选且只选一个）

- `perceive_more`：现有证据不足以判断，需要补充采集。必须写清通道、时间窗、要确认什么。
- `diagnose_more`：诊断结论不明确、置信度过低或有自相矛盾，需要补充诊断。
  必须写清要排除或确认的假设。
- `plan_work_order`：事实已足够，进入工单规划（之后还有安全检查与人工审批两道闸门）。
- `close`：确认无需处置，直接关单。

## 二、判断规则（按优先级从高到低）

1. **安全优先**：涉及人员安全、水源或环境风险、不可逆动作的信号，一律不得 `close`；
   应 `plan_work_order`，让安全检查与人工审批把关。
2. **证据门槛**：
   - 关键通道缺失 / 初筛为 `unknown` / 置信度 low，而严重度可能达到 medium 及以上
     → 先补证据（`perceive_more` 或 `diagnose_more`）；
   - 严重度 high/critical 且置信度 ≥ medium → `plan_work_order`；
   - 严重度 medium 且置信度 high → `plan_work_order`（非紧急，按计划排期）；
   - 严重度 low/none 且证据完整 → `close`（处置预判可给"观察"），
     除非存在明确的不可逆风险信号。
3. **预算纪律**：补采集与补诊断的轮次有限（上限见下方提示）。预算耗尽时必须在本轮
   于 `plan_work_order` 与 `close` 之间决断；**宁可多走一道人工审批，\
   也不要在证据不足时宣布"无需处置"**。
4. **成本与不可逆性**：作业有成本、也有副作用。可逆、低风险、低成本的观察类动作容错高；
   不可逆、影响面大的动作要求更高置信度。
5. **保守收口是合法结论**：确认无需处置就 `close`，但要写清"为什么现有证据足够"。

## 三、决策纪律

- 依据必须引用具体证据（通道 + 时间 + 读数，或诊断的哪条判据），不要复述上游结论；
- **不复述、不改写诊断结论**。发现诊断自相矛盾或证据缺失时，用 `diagnose_more` 追问，
  而不是自己改判；
- 追问必须可执行：写清对象、通道、时间窗、要排除的假设。
  例："复取 08-14 前的土壤墒情 10cm/30cm 两层序列，排除传感器漂移导致的假性干旱"；
- 参考历史决策与复盘教训，但**以本轮证据为准**；与历史冲突时说明取舍；
- 拿不准时，选择能把决定权交给安全检查与人工审批的路径（`plan_work_order`），
  而不是自行收口。

## 四、输出要求

先给结论（下一步动作），再给依据；若分派补充任务，把要求写成下游可直接执行的清单；
最后给出对最终处置等级的预判，以及一段给人工读者的小结。"""


def build_prompt(
    *,
    site_context: str,
    resource_context: str,
    perception_report: str,
    anomaly_screen: str,
    diagnosis_report: str,
    severity: str,
    confidence: str,
    rationale: str,
    lessons: str = "",
    history: str = "",
    budget_note: str = "",
) -> str:
    """拼装本轮中枢提示词：规则 + 当前证据 + 历史决策 +（可能的）预算提醒。"""
    return f"""{SYSTEM_PROMPT}

{load_skill("coordinator")}

{site_context}

{resource_context}

---

## 当前证据
- 感知报告：{perception_report}
- 异常初筛：{anomaly_screen}
- 诊断报告：{diagnosis_report}
- 严重度：{severity}（置信度 {confidence}）
- 诊断依据：{rationale or "（无）"}
{lessons}## 中枢历史决策
{history or "（本轮为首轮决策）"}{budget_note}

## 输出要求

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""


def create_coordinator(llm):
    structured_llm = bind_structured(llm, CoordinationDecision, "协调决策中枢")

    def coordinator_node(state) -> dict:
        perception_state = state["perception_state"]
        diagnosis_state = state["diagnosis_state"]
        coordination_state = state["coordination_state"]
        site_context = get_site_context_from_state(state)
        resource_context = get_resource_context_from_state(state)
        past_context = state.get("past_context", "")

        rounds = coordination_state.get("coordinator_rounds", 0)
        history = coordination_state.get("coordination_history", "")

        # 预算耗尽时必须收口：中枢不能无限要求补数据，否则 run 永远结束不了
        budget_note = ""
        if rounds >= get_config().get("max_coordination_rounds", 3):
            budget_note = (
                "\n\n**注意**：本轮已达到追加采集/诊断的轮次上限，"
                "必须在本轮做出决断（plan_work_order 或 close），不得再次分派。"
            )

        lessons = (
            f"\n- 历史决策与复盘教训：\n{past_context}\n" if past_context else ""
        )

        prompt = build_prompt(
            site_context=site_context,
            resource_context=resource_context,
            perception_report=report_or_absent(
                perception_state.get("perception_report", ""), "感知"
            ),
            anomaly_screen=report_or_absent(
                perception_state.get("anomaly_screen", ""), "初筛"
            ),
            diagnosis_report=report_or_absent(
                diagnosis_state.get("diagnosis_report", ""), "诊断"
            ),
            severity=diagnosis_state.get("severity", "未评估"),
            confidence=diagnosis_state.get("confidence", "未评估"),
            rationale=diagnosis_state.get("rationale", ""),
            lessons=lessons,
            history=history,
            budget_note=budget_note,
        )

        result, rendered = invoke_with_optional_structured(
            structured_llm, llm, prompt, render_coordination_decision, "协调决策中枢"
        )

        if result is not None:
            next_action = result.next_action
            reason = result.reason
            disposition_hint = result.disposition_hint.value
            # 预算耗尽却仍要求分派：强制收口，只信中枢给出的处置预判
            if budget_note and next_action in ("perceive_more", "diagnose_more"):
                logger.warning("中枢在预算耗尽后仍要求 %s，强制改为 plan_work_order", next_action)
                next_action = "plan_work_order"
        else:
            next_action, reason, disposition_hint = _fallback_decision(rendered)
            logger.warning("中枢走回退路径，动作解析结果=%s", next_action)

        entry = f"[第{rounds + 1}轮] {next_action}：{reason}"

        new_coordination_state = {
            **coordination_state,
            "next_action": next_action,
            "decision_reason": reason,
            "coordination_notes": rendered,
            "coordination_history": f"{history}\n{entry}".strip(),
            "coordinator_rounds": rounds + 1,
            # 残余的补充要求写入对应 Agent 的状态，由它们下一次执行时读取
        }
        # 把补充采集/诊断的要求落到下游状态里（空字符串表示常规流程）
        return {
            "coordination_state": new_coordination_state,
            "perception_state": (
                {**perception_state, "pending_requests": _request_for(result, rendered)}
                if next_action == "perceive_more" else perception_state
            ),
            "diagnosis_state": (
                {**diagnosis_state, "pending_request": _request_for(result, rendered)}
                if next_action == "diagnose_more" else diagnosis_state
            ),
        }

    return coordinator_node


def _request_for(result, rendered: str) -> str:
    """取中枢的补充要求正文。"""
    if result is not None and getattr(result, "request", ""):
        return result.request
    return rendered


def _fallback_decision(rendered: str) -> tuple[str, str, str]:
    """回退路径：解析中枢动作。

    解析不出动作时按 ``plan_work_order`` 处理（进入工单与人工审批），
    而不是 ``close``——宁可多一道人工确认，也不要静默地什么都不做。
    """
    for action in VALID_ACTIONS:
        if action in rendered:
            return action, "（回退路径解析）", "Monitor"
    return "plan_work_order", "（回退路径未解析出动作，按进入工单处理）", "Monitor"
