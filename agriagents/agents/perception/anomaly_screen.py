"""异常初筛：把感知报告收敛成一个可路由的判断。

这一步回答的是"要不要继续往下走"，而不是"是什么病"。
把它独立成节点（而不是塞进感知 Agent）有两个好处：

1. 路由条件读结构化字段，不依赖对散文的解析；
2. 无异常时可以低成本收口关单，不必启动诊断与工单流程。

回退语义：结构化解析失败时等级记为 ``unknown``，并**按需要跟进处理**——
一次解析失败绝不能变成一次「正常」的判定。
"""

import logging

from agriagents.agents.context import get_language_instruction, get_site_context_from_state
from agriagents.agents.schemas import AnomalyScreenResult, render_anomaly_screen
from agriagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_with_optional_structured,
    parse_level_from_text,
)

logger = logging.getLogger(__name__)

_LEVELS = ("none", "low", "medium", "high")


def create_anomaly_screen(llm):
    structured_llm = bind_structured(llm, AnomalyScreenResult, "异常初筛")

    def anomaly_screen_node(state) -> dict:
        perception_state = state["perception_state"]
        report = (perception_state.get("perception_report") or "").strip()
        site_context = get_site_context_from_state(state)

        prompt = f"""你是农业维护流程中的异常初筛员。下面是本轮巡检的感知报告，请判断是否存在需要跟进的异常。

{site_context}

---

**异常等级**（必须选一个）：
- none：各通道均在正常范围，无需跟进
- low：存在偏离但可继续观察
- medium：存在明确异常，需要进入诊断
- high：存在紧急异常，需要尽快处置

**感知报告：**
{report or "（本轮没有感知报告：它是缺失，不是「没有异常」。）"}

## 输出要求

- 结论必须来自报告中的证据；报告没覆盖的通道要写进「待确认」，不要默认正常。
- 报告缺失时，异常等级不得填 none。

{NO_EXTERNAL_TOOLS}""" + get_language_instruction()

        result, rendered = invoke_with_optional_structured(
            structured_llm, llm, prompt, render_anomaly_screen, "异常初筛"
        )

        if result is not None:
            level = result.anomaly_level
        else:
            level = parse_level_from_text(rendered, _LEVELS, "异常等级")
            logger.warning("异常初筛走回退路径，等级解析结果=%s", level)

        new_perception_state = {
            **perception_state,
            "anomaly_screen": rendered,
            "anomaly_level": level,
            # unknown 视为需要跟进：宁可多走一轮诊断，也不要把未知当正常
            "has_anomaly": level != "none",
            "perception_rounds": perception_state.get("perception_rounds", 0) + 1,
            "pending_requests": "",
        }
        return {"perception_state": new_perception_state}

    return anomaly_screen_node
