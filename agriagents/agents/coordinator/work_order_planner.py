"""工单规划：把决策意图翻译成可执行的维护工单。

与 TradingAgents 里 Trader 的角色对应：上游给方向，这里给"怎么做"。
框架强制工单必须包含**验收标准**与**回退线索**——没有验收标准的工单
在执行后就无法判断成败，复盘也就无从谈起。
"""

from agriagents.agents.context import (
    get_language_instruction,
    get_resource_context_from_state,
    get_site_context_from_state,
    report_or_absent,
)
from agriagents.agents.schemas import WorkOrder, render_work_order
from agriagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_with_optional_structured,
)


def create_work_order_planner(llm):
    structured_llm = bind_structured(llm, WorkOrder, "工单规划")

    def work_order_planner_node(state) -> dict:
        coordination_state = state["coordination_state"]
        diagnosis_state = state["diagnosis_state"]
        site_context = get_site_context_from_state(state)
        resource_context = get_resource_context_from_state(state)
        previous = coordination_state.get("work_order", "")

        prompt = f"""你是农业维护流程中的工单规划员。请把上游结论翻译成一份可执行、可验收的维护工单。

{site_context}

{resource_context}

---

**诊断结论**（严重度 {diagnosis_state.get("severity", "未评估")}）：
{report_or_absent(diagnosis_state.get("diagnosis_report", ""), "诊断")}

**中枢决策**：
{coordination_state.get("coordination_notes") or "（无）"}

**上一版工单**（若存在，请按安全检查意见修改，而不是从头重写）：
{previous or "（无）"}

## 输出要求

- 任务分解要按执行顺序排列，每项写明对象与判定标准；
- 资源需求写数量与单位；库存未知时写"按实际库存折算"，不要假设充足；
- 验收标准必须是作业后**可观测**的指标；
- 严禁自行发明药剂品种与用量：只能引用上游知识库给出的方案，没有就写明需要先查方案。

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""

        result, rendered = invoke_with_optional_structured(
            structured_llm, llm, prompt, render_work_order, "工单规划"
        )

        new_coordination_state = {
            **coordination_state,
            "work_order": rendered,
            "safety_review": "",
            "safety_verdict": "",
        }
        if result is None:
            # 回退路径下无法保证工单字段齐全，因此不带处置等级进入下游
            new_coordination_state["safety_verdict"] = ""

        return {"coordination_state": new_coordination_state}

    return work_order_planner_node
