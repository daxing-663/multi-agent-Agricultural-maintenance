"""回滚预案：执行失败或效果不达标时的退路。

框架立场：**回滚预案必须在执行之前就有**，而不是出事之后再想。
因此本节点产出的是"预案"（写进工单留档），执行失败后由中枢决定是否启用。

与 TradingAgents 的对应关系：这里相当于风控团队里"最保守的那一位"的职责，
但落点不同——交易的风控是拒绝交易，农业的风控是"万一没做成，怎么退回来"。
"""

from agriagents.agents.context import get_language_instruction, get_site_context_from_state


def create_rollback_planner(llm):
    def rollback_planner_node(state) -> dict:
        coordination_state = state["coordination_state"]
        execution_state = state["execution_state"]
        site_context = get_site_context_from_state(state)

        # TODO(内容): 在此编写回滚预案的提示词。
        # 需要覆盖的要点（占位）：
        #   1. 按失败模式分类给出退路：未执行 / 部分执行 / 执行后反而恶化；
        #   2. 明确什么条件下必须立即停手并转人工；
        #   3. 判断可逆性——哪些动作不可逆、只能补救。
        prompt = f"""你是农业维护流程中的回滚预案员。执行环节出现了问题，请给出可操作的退路。

{site_context}

**工单：**
{coordination_state.get("work_order") or "（无）"}

**执行反馈：**
{execution_state.get("execution_report") or "（无）"}

请按顺序输出：① 问题定性；② 立即止损动作；③ 逐条退路与适用条件；④ 必须转人工的情形。
""" + get_language_instruction()

        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

        return {
            "coordination_state": {
                **coordination_state,
                "rollback_plan": text,
                "rollback_rounds": coordination_state.get("rollback_rounds", 0) + 1,
            }
        }

    return rollback_planner_node
