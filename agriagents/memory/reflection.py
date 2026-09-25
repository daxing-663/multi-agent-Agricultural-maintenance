"""复盘反思：结果已知后，用 2-4 句话把教训写进台账。

要求写得**短**是刻意的：这些句子会被重新注入后续 run 的提示词，
写成长篇分析会挤占上下文且掩盖重点。
"""

from __future__ import annotations

from typing import Any


class Reflector:
    """对已结束的维护决策做复盘。"""

    def __init__(self, quick_thinking_llm: Any):
        self.quick_thinking_llm = quick_thinking_llm

    def _system_prompt(self) -> str:
        return (
            "你是一名农艺/设备维护复盘员，正在回顾自己此前做出的维护决策，"
            "现在执行结果已经回来了。\n"
            "请写 2-4 句平实的文字（不要分点、不要标题、不要 markdown）。\n\n"
            "按顺序覆盖：\n"
            "1. 结果说明了当初判断的哪一部分是对的/错的（引用具体数据）；\n"
            "2. 这个结果是否足以判断当初的决策，若时间窗太短请直接说明；\n"
            "3. 一条下次遇到类似情形可以直接用的具体教训。\n\n"
            "要具体、简短。你的输出会被原样存进决策台账，并被后续的决策者读到，"
            "所以每个字都要有信息量。"
        )

    def reflect_on_outcome(
        self,
        final_decision: str,
        outcome: str,
        status: str = "",
        site_id: str = "",
        as_of: str = "",
    ) -> str:
        """对一次已回填结果的决策做复盘，返回 2-4 句教训。"""
        messages = [
            ("system", self._system_prompt()),
            (
                "human",
                (
                    f"对象：{site_id}（决策日期 {as_of}）\n"
                    f"执行状态：{status or '未知'}\n\n"
                    f"现场结果：\n{outcome}\n\n"
                    f"当初的决策单：\n{final_decision}"
                ),
            ),
        ]
        response = self.quick_thinking_llm.invoke(messages)
        return response.content if hasattr(response, "content") else str(response)
