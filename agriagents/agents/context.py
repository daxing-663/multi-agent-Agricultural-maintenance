"""注入到提示词里的共享上下文片段。

三条与本框架强相关的原则（都来自 TradingAgents 的踩坑经验）：

1. **对象锚定**：案件对象（地块/机具）在 run 开始时确定性解析一次，写进
   ``site_context``，之后每个 Agent 都读它，避免模型从零散读数里"猜"出一个对象。
2. **缺席显式化**：某路报告没产生 ≠ 该路结论为空。用 ``report_or_absent``
   写明"本轮无此报告"，否则下游会把缺失当成空白证据自行脑补。
3. **未知即未知**：没提供资源账本时，必须说"不知道库存与人力"，而不能
   默认成"零库存"，否则协调中枢会按一个从未被告知的仓库去做分配。
"""

from __future__ import annotations

from typing import Any, Mapping

from langchain_core.messages import HumanMessage, RemoveMessage

from agriagents.dataflows.config import get_config


def get_site_context_from_state(state: Mapping[str, Any]) -> str:
    """返回本次 run 的对象档案上下文。"""
    context = state.get("site_context")
    if isinstance(context, str) and context.strip():
        return context
    return build_site_context(
        str(state.get("site_of_interest", "")),
        state.get("site_type", "field"),
    )


def build_site_context(site_id: str, site_type: str = "field", identity: dict | None = None,
                       as_of: str | None = None) -> str:
    """构造对象档案文本：编号、类型、解析出的业务身份。

    TODO(内容): 接入真实档案来源（地块台账 / 机具台账 / 品种与播期档案），
    把确定性查到的事实填进 details，框架部分无需改动。
    """
    type_labels = {
        "field": "地块", "greenhouse": "棚室", "orchard": "果园", "machine": "机具",
    }
    label = type_labels.get(site_type, "对象")
    context = f"本次维护的{label}编号为 `{site_id}`，请在所有工具调用与结论中沿用该编号。"

    details: list[str] = []
    identity = identity or {}
    if identity.get("name"):
        details.append(f"名称：{identity['name']}")
    if identity.get("crop"):
        details.append(f"作物：{identity['crop']}")
    if identity.get("area"):
        details.append(f"规模：{identity['area']}")
    if details:
        context += " 已解析档案：" + "；".join(details) + "。"
        if as_of:
            context += f" 该档案是今天的登记状态，未必与 {as_of} 当天一致。"
    return context


# 「没给账本」的标准措辞。提成常量是为了让 state 与提示词共用同一份表述——
# 两处各写一遍，早晚会不一致。
RESOURCE_CONTEXT_ABSENT = (
    "资源账本：未提供。你不知道当前可用人力、机具与农资库存，"
    "因此不要假设库存充足或为零；请给出方向与优先级，并让执行方按实际库存折算。"
)


def get_resource_context_from_state(state: Mapping[str, Any]) -> str:
    """返回资源账本（人力/机具/农资），未提供时明确说明未知。"""
    context = state.get("resource_context")
    if isinstance(context, str) and context.strip():
        return context
    return RESOURCE_CONTEXT_ABSENT


def get_language_instruction() -> str:
    """输出语言指令。内部推理语言由模型自行决定，只约束交付物。"""
    language = get_config().get("output_language") or "Chinese"
    return (
        f"\n\n请使用 {language} 撰写给用户看的报告与结论；"
        "专有名词、编号与代码标识保留原文。"
    )


def report_or_absent(text: str, source: str) -> str:
    """某路报告正文，或"本轮缺失"的显式标记。"""
    text = (text or "").strip()
    if text:
        return text
    return f"（本轮没有{source}报告：它是缺失，不是「结论为空」。）"


def create_msg_delete():
    """清空消息并在原位放一个锚定案件上下文的占位消息。

    占位消息不能是裸的 "继续"：部分模型会把它当成新的用户任务，
    转而讨论"继续"这个词。锚定对象与基准日期可避免这种跑题。
    """

    def delete_messages(state):
        messages = state["messages"]
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        site_context = get_site_context_from_state(state)
        as_of = state.get("as_of", "指定日期")
        placeholder = HumanMessage(
            content=(
                f"请继续执行你在本流程中被分配的职责。{site_context} "
                f"决策基准日期为 {as_of}。"
            )
        )
        return {"messages": removal_operations + [placeholder]}

    return delete_messages


def render_resource_context(resources: dict | None) -> str:
    """把调用方给的人力 / 机具 / 农资渲染成提示词片段。

    ``None`` 与 ``{}`` 都返回空串，由 ``get_resource_context_from_state``
    统一转成「未提供」的表述——"没给账本"和"账本是空的"必须区分：
    前者是未知，后者是确知没有，两者会导出完全不同的资源分配方案。
    """
    if not resources:
        return ""
    lines = ["可用资源账本（本轮由调用方提供）："]
    for key, value in resources.items():
        if isinstance(value, list):
            rendered = "、".join(str(item) for item in value)
        elif isinstance(value, dict):
            rendered = "；".join(f"{k}={v}" for k, v in value.items())
        else:
            rendered = str(value)
        lines.append(f"- {key}：{rendered}")
    return "\n".join(lines)