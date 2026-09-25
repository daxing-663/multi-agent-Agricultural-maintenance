"""结构化输出 + 优雅回退的公共实现。

三个"下结论"的节点（异常初筛、严重度评估、协调决策中枢、工单规划、安全检查）
走同一条规范路径：

1. 创建 Agent 时用 ``with_structured_output(Schema)`` 包装 LLM，让它直接产出
   类型化的 Pydantic 实例；若供应商不支持（少数本地模型），跳过包装改用自由文本。
2. 调用时执行结构化调用并把结果渲染回 markdown；结构化调用自身失败
   （弱模型输出坏 JSON、供应商抖动）则退回一次普通 ``llm.invoke``，
   保证流水线不因单次解析失败而中断。
3. **结构化调用只执行一次**：需要字段值做路由的节点用
   ``invoke_with_optional_structured`` 一次拿到「对象 + 渲染文本」，
   不要为了取字段再调一遍模型。

集中在这里，使各 Agent 工厂保持极薄，并让回退时的告警口径一致。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# 结构化输出会绑定 schema 本身作为唯一工具，模型若顺手去调外部工具，
# 会因未知工具调用导致整次结构化尝试作废。因此这条约束必须写在提示词里。
NO_EXTERNAL_TOOLS = (
    "只使用本提示词中提供的证据。不要调用外部工具或联网检索；"
    "确实缺失的信息，请直接说明缺失。"
)


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """返回 ``llm.with_structured_output(schema)``；不支持时返回 None 并告警。"""
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s：该供应商不支持 with_structured_output（%s）；将改用自由文本生成",
            agent_name, exc,
        )
        return None


def invoke_with_optional_structured(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> tuple[T | None, str]:
    """执行一次结构化调用，返回 ``(解析对象, 渲染文本)``。

    结构化不可用或失败时返回 ``(None, 自由文本)``。调用方若需要字段值，
    用返回的对象；对象为 None 时说明本次是回退路径，字段需按「未知」处理，
    **不要**再调一次模型去补齐字段。
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if result is None:
                raise ValueError("结构化输出未返回可解析结果")
            return result, render(result)
        except Exception as exc:
            logger.warning("%s：结构化调用失败（%s）；改以自由文本重试一次", agent_name, exc)

    response = plain_llm.invoke(prompt)
    text = response.content if hasattr(response, "content") else str(response)
    return None, text


def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """只要渲染文本时的便捷封装。"""
    return invoke_with_optional_structured(
        structured_llm, plain_llm, prompt, render, agent_name
    )[1]


def parse_level_from_text(text: str, levels: tuple[str, ...], marker: str) -> str:
    """回退路径专用：从渲染文本里解析等级。

    解析不出返回 ``"unknown"``。上游必须把 unknown 当成「需要跟进」，
    绝不能让一次解析失败变成一次「正常」的判定。
    """
    if not text:
        return "unknown"
    for line in text.splitlines():
        if marker in line:
            for level in levels:
                if level in line:
                    return level
    for level in levels:
        if level in text:
            return level
    return "unknown"
