"""路径安全工具：把用户/外部传入的编号安全地用作文件路径片段。

案件编号会进入检查点数据库路径与报告目录路径。一个值为 ``..`` 的编号
可以把文件写到预期目录之外，因此在**每一个**拼接处统一校验。
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")


def safe_site_component(value: str) -> str:
    """把案件编号规范成可安全用作路径片段的形式。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("维护对象编号不能为空")
    text = text.replace("..", "_")
    text = _UNSAFE.sub("_", text)
    text = text.strip("._") or "unnamed"
    return text[:64]
