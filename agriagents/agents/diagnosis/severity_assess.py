"""严重度评估：诊断结果的收敛点，也是中枢分配资源的依据。"""

import logging

from agriagents.agents.context import get_site_context_from_state
from agriagents.agents.schemas import SeverityAssessment, render_severity
from agriagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_with_optional_structured,
    parse_level_from_text,
)

logger = logging.getLogger(__name__)

_LEVELS = ("critical", "high", "medium", "low", "none")


def create_severity_assess(llm):
    structured_llm = bind_structured(llm, SeverityAssessment, "严重度评估")

    def severity_assess_node(state) -> dict:
        diagnosis_state = state["diagnosis_state"]
        perception_state = state["perception_state"]
        report = (diagnosis_state.get("diagnosis_report") or "").strip()
        site_context = get_site_context_from_state(state)

        prompt = f"""你是农业维护流程中的严重度评估员。下面是本轮诊断报告，请给出严重度、置信度与影响范围。

{site_context}

---

**严重度档位**（必须选一个）：
- none：无实质问题
- low：轻微，可观察
- medium：需要安排处置，但非紧急
- high：需要尽快处置
- critical：不处置将在短期内造成不可逆损失

**诊断报告：**
{report or "（本轮没有诊断报告：它是缺失，不是「无问题」。）"}

**感知初筛：**
{perception_state.get("anomaly_screen") or "（本轮无初筛结论）"}

## 输出要求

- 置信度要如实：证据不足、通道缺失时填 low，不要用高置信度掩盖证据缺口。
- 影响范围要可量化（面积 / 株数 / 设备台数），无法量化就说明原因。

{NO_EXTERNAL_TOOLS}"""
        result, rendered = invoke_with_optional_structured(
            structured_llm, llm, prompt, render_severity, "严重度评估"
        )

        if result is not None:
            severity, confidence = result.severity, result.confidence
            rationale, checks = result.rationale, result.recommended_checks
        else:
            severity = parse_level_from_text(rendered, _LEVELS, "严重度")
            confidence, rationale, checks = "unknown", rendered, ""
            logger.warning("严重度评估走回退路径，严重度解析结果=%s", severity)

        entry = (
            f"[第{diagnosis_state.get('diagnosis_rounds', 0) + 1}轮]\n"
            f"{report}\n\n{rendered}"
        ).strip()
        history = (diagnosis_state.get("diagnosis_history", "") + "\n\n" + entry).strip()

        new_diagnosis_state = {
            **diagnosis_state,
            "severity": severity,
            "confidence": confidence,
            "rationale": rationale,
            "recommended_checks": checks,
            "diagnosis_history": history,
            "diagnosis_rounds": diagnosis_state.get("diagnosis_rounds", 0) + 1,
            "pending_request": "",
        }
        return {"diagnosis_state": new_diagnosis_state}

    return severity_assess_node
