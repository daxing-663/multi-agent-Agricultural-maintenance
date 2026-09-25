"""LLM 客户端工厂 + 供应商适配 + 离线占位模型。

支持的供应商：

- ``deepseek``：DeepSeek（OpenAI 兼容端点）。默认供应商。
- ``openai`` / ``openai_compatible``：OpenAI 及任何 OpenAI 兼容端点（vLLM、LM Studio、Ollama…）。
- ``stub``：离线占位模型，不联网、不花钱，用于验证框架接线。

DeepSeek 的实测行为（2026-09，deepseek-v4-pro / deepseek-flash），三条都影响接线方式：

1. thinking 模式**不支持强制 tool_choice**（返回 400），因此
   ``with_structured_output(method="function_calling")`` 必须关掉 thinking；
2. 不支持 ``response_format=json_schema``；``json_mode`` 虽可用，但模型会把
   Literal/枚举的**取值**翻成中文（``"medium"`` → ``"中"``），导致校验失败；
3. thinking 模式下，多轮工具调用要求把上一轮的 ``reasoning_content`` 原样回传，
   否则报 400「The reasoning_content in the thinking mode must be passed back」。
   而 ``langchain-openai`` 明确不解析也不保留该字段（见其 base.py 模块文档），
   所以**工具型 Agent（感知/诊断/执行）在 thinking 模式下必然中断**。

结论：本框架对 DeepSeek 默认**关闭 thinking**（``deepseek_thinking=False``），
结构化输出仍固定走 **function_calling + 单次调用关闭 thinking**（双保险）。
要把 thinking 打开，必须先解决 reasoning_content 的回传问题——
那需要绕过 langchain-openai 的转换层，属于框架外的工作。
"""

from __future__ import annotations

import enum
import os
import types
import typing

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

_STUB_TEXT = "[stub] 离线占位输出：本节点接线正常，业务内容待接入真实模型。"


# ---------------------------------------------------------------------------
# 离线占位模型
# ---------------------------------------------------------------------------


def _stub_instance(schema: type[BaseModel]) -> BaseModel:
    """按字段类型为任意 schema 生成一个合法的占位实例。"""
    return schema(**{
        name: _stub_value(field.annotation)
        for name, field in schema.model_fields.items()
    })


def _stub_value(annotation):
    if annotation is None or annotation is type(None):
        return None

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Literal:
        return args[0] if args else None
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1 and (
            (isinstance(inner[0], type) and issubclass(inner[0], enum.Enum))
            or typing.get_origin(inner[0]) is typing.Literal
        ):
            return _stub_value(inner[0])
        return None
    if origin in (list, tuple, set, frozenset):
        return []
    if origin is dict:
        return {}

    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            return list(annotation)[0]
        if issubclass(annotation, BaseModel):
            return _stub_instance(annotation)
        if annotation is str:
            return _STUB_TEXT
        if annotation is bool:
            return False
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
    return None


class _StubStructured(Runnable):
    """``with_structured_output`` 的离线替身：直接返回填好的占位实例。"""

    def __init__(self, schema: type[BaseModel]):
        super().__init__()
        self._schema = schema

    def invoke(self, input, config=None, **kwargs):
        return _stub_instance(self._schema)


class StubChatModel(Runnable):
    """离线占位模型：不联网、不花钱，只证明流水线是通的。

    继承 ``Runnable`` 是必须的——Agent 节点里用的是 ``prompt | llm.bind_tools(...)``
    这种组合写法，非 Runnable 对象无法参与管道拼接。
    """

    def __init__(self, model: str = "stub"):
        super().__init__()
        self.model = model

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return _StubStructured(schema)

    def invoke(self, input, config=None, **kwargs):
        return AIMessage(content=f"{_STUB_TEXT}（model={self.model}）")

    async def ainvoke(self, input, config=None, **kwargs):
        return self.invoke(input, config)


# ---------------------------------------------------------------------------
# DeepSeek 适配
# ---------------------------------------------------------------------------


class DeepSeekChatModel(ChatOpenAI):
    """DeepSeek 客户端。

    两处适配：

    1. ``thinking_enabled=False`` 时整条链路关闭 thinking（默认，见模块文档第 3 条）；
    2. 若 ``thinking_enabled=True``，``with_structured_output`` 仍会在**那一次调用**上
       关掉 thinking——因为强制 tool_choice 与 thinking 互斥（模块文档第 1 条）。
    """

    thinking_enabled: bool = False
    disable_thinking_for_structured: bool = True

    def with_structured_output(self, schema=None, *, method=None, **kwargs):
        target = self
        if self.disable_thinking_for_structured and self.thinking_enabled:
            target = self.model_copy(update={
                "extra_body": {
                    **(self.extra_body or {}),
                    "thinking": {"type": "disabled"},
                },
            })
        return super(DeepSeekChatModel, target).with_structured_output(
            schema, method=method or "function_calling", **kwargs
        )


def _create_deepseek(model: str, base_url: str | None = None, **kwargs):
    """创建 DeepSeek 客户端；缺 key 时立即报错，不留到第一次调用才失败。"""
    api_key = kwargs.pop("api_key", None) or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError(
            "provider=deepseek 需要 DEEPSEEK_API_KEY：请写进项目根目录的 .env，"
            "或设成环境变量。"
        )

    thinking_enabled = bool(kwargs.pop("thinking_enabled", False))
    extra_body = dict(kwargs.pop("extra_body", None) or {})
    if not thinking_enabled:
        extra_body["thinking"] = {"type": "disabled"}

    return DeepSeekChatModel(
        model=model,
        base_url=base_url or DEEPSEEK_BASE_URL,
        api_key=api_key,
        thinking_enabled=thinking_enabled,
        extra_body=extra_body or None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def create_llm(provider: str, model: str, base_url: str | None = None, **kwargs):
    """按供应商创建 LLM 客户端；未知供应商直接报错，不做隐式兜底。"""
    provider_lower = (provider or "stub").lower()

    if provider_lower == "stub":
        return StubChatModel(model)

    if provider_lower == "deepseek":
        return _create_deepseek(model, base_url, **kwargs)

    if provider_lower in ("openai", "openai_compatible"):
        from langchain_openai import ChatOpenAI

        if provider_lower == "openai_compatible" and not base_url:
            raise ValueError("provider=openai_compatible 必须显式提供 base_url")
        params: dict = {"model": model, **kwargs}
        if base_url:
            params["base_url"] = base_url
        return ChatOpenAI(**params)

    raise ValueError(f"Unsupported LLM provider: {provider}")
