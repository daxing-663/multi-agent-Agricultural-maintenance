"""执行反馈：把"实际发生了什么"固化成结构化结论。

反馈是闭环的起点：没有它，决策台账里的条目永远停在 pending，
下一轮也就学不到任何教训。框架只做两件事——收集反馈、判定状态；
**写入台账与复盘由 memory 层负责**（见 ``memory/settlement.py``）。
"""

import logging

from agriagents.agents.context import get_site_context_from_state
from agriagents.agents.schemas import ExecutionOutcome, render_execution_outcome
from agriagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_with_optional_structured,
)

logger = logging.getLogger(__name__)


def create_feedback_collect(llm):
    structured_llm = bind_structured(llm, ExecutionOutcome, "执行反馈")

    def feedback_collect_node(state) -> dict:
        execution_state = state["execution_state"]
        coordination_state = state["coordination_state"]
        site_context = get_site_context_from_state(state)
        dry_run = state.get("_dry_run", None)

        prompt = f"""你是农业维护流程中的执行反馈员。请根据执行记录，总结实际发生了什么、与工单差在哪里。

{site_context}

**工单：**
{coordination_state.get("work_order") or "（无）"}

**执行记录：**
{execution_state.get("dispatch_log") or "（没有下发记录。）"}

## 输出要求

- 逐条对应工单任务说明"做了 / 未做 / 参数不同"，不要笼统地说"基本完成"。
- 记录只有 dry-run（演练）标记时，状态必须如实反映这是演练，不得写成已执行。
- 现场反馈要写观察到的事实；没有拿到回执就写"未取得回执"。

{NO_EXTERNAL_TOOLS}"""

        result, rendered = invoke_with_optional_structured(
            structured_llm, llm, prompt, render_execution_outcome, "执行反馈"
        )

        if result is not None:
            status = result.status
            needs_followup = result.needs_followup
        else:
            status = "unknown"
            needs_followup = True
            logger.warning("执行反馈走回退路径，状态按未知处理")

        return {
            "execution_state": {
                **execution_state,
                "execution_status": status,
                "execution_report": rendered,
                "needs_followup": needs_followup,
            }
        }

    return feedback_collect_node
