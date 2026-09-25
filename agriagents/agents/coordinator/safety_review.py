"""安全检查：执行前的最后一道闸门。

与 TradingAgents 的风控辩论对应，但这里不是"辩论"，而是**规则判定**：
农业作业涉及禁限用物资、安全间隔期、人员与环境安全，这些不是可以权衡的
风险偏好，而是硬约束。所以本节点的裁决是 pass / revise / block 三档，
其中 block 是终局性的——不允许被任何"收益更大"的理由覆盖。
"""

import logging

from agriagents.agents.context import get_language_instruction, get_site_context_from_state
from agriagents.agents.schemas import SafetyReviewResult, render_safety_review
from agriagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_with_optional_structured,
)

logger = logging.getLogger(__name__)

VERDICTS = ("pass", "revise", "block")


def create_safety_review(llm):
    structured_llm = bind_structured(llm, SafetyReviewResult, "安全检查")

    def safety_review_node(state) -> dict:
        coordination_state = state["coordination_state"]
        work_order = (coordination_state.get("work_order") or "").strip()
        site_context = get_site_context_from_state(state)
        rounds = coordination_state.get("safety_rounds", 0)

        prompt = f"""你是农业维护流程中的安全检查员。请对照安全与合规要求审查这份工单。

{site_context}

---

**工单：**
{work_order or "（没有工单：缺失即不允许放行。）"}

**裁决档位**（必须选一个）：
- pass：无阻断性问题，可提交人工审批
- revise：存在需修正的问题（如缺少防护措施、时间窗与安全间隔期冲突），退回重做
- block：存在禁止性冲突（禁限用物资、超出许可用量、危害人员安全），不得执行

## 输出要求

- 「没有发现问题」必须有依据：逐项核对用工、用药、用水、用电、人员防护、环境约束。
- 工单缺失或信息不足以判断时，裁决为 revise，不得为 pass。
- 你只能依据工单内容与安全规则判断，不要评估作业的农艺收益——那是上游的职责。

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""

        result, rendered = invoke_with_optional_structured(
            structured_llm, llm, prompt, render_safety_review, "安全检查"
        )

        if result is not None:
            verdict = result.verdict
        else:
            verdict = _parse_verdict(rendered)
            logger.warning("安全检查走回退路径，裁决解析结果=%s", verdict)

        new_coordination_state = {
            **coordination_state,
            "safety_review": rendered,
            "safety_verdict": verdict,
            "safety_rounds": rounds + 1 if verdict == "revise" else rounds,
        }
        return {"coordination_state": new_coordination_state}

    return safety_review_node


def _parse_verdict(rendered: str) -> str:
    """回退路径解析裁决。解析不出按 revise 处理：宁可多审一轮，不可误放行。"""
    for line in rendered.splitlines():
        if "安全裁决" in line:
            for verdict in VERDICTS:
                if verdict in line:
                    return verdict
    for verdict in VERDICTS:
        if verdict in rendered:
            return verdict
    return "revise"
