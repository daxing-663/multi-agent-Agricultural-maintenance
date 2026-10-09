"""语料源注册表。

加一个知识源 = 加一个文件 + 在这里登记一行。build 脚本只认这张表。
"""

from __future__ import annotations

from agriagents.rag.sources import cropdp, curated, local, plantinquiry, qa_en, qa_zh, seed
from agriagents.rag.sources.base import IngestContext, SourceResult

# 键是 ``--sources`` 命令行能用的名字
SOURCES = {
    seed.NAME: seed,
    curated.NAME: curated,
    cropdp.NAME: cropdp,
    plantinquiry.NAME: plantinquiry,
    qa_en.NAME: qa_en,
    qa_zh.NAME: qa_zh,
    local.NAME: local,
}

# 全量构建顺序。cropdp 与 plantinquiry 先跑：它们的产物（图谱、卡片）
# 是实体标准化的输入，后面的源依赖标准化结果做跨语言改写。
FULL_ORDER = (seed.NAME, curated.NAME, cropdp.NAME, plantinquiry.NAME, qa_en.NAME, qa_zh.NAME)
# 默认生产构建包含全部已接入公开源；快速演示请显式 --sources seed。
# 配置自有资料时 build 脚本自动追加 local。
DEFAULT_ORDER = FULL_ORDER

__all__ = ["SOURCES", "DEFAULT_ORDER", "FULL_ORDER", "IngestContext", "SourceResult", "describe_all", "resolve"]


def resolve(names) -> list:
    """把名字列表解析为模块列表，未知名字直接报错。"""
    if not names:
        names = DEFAULT_ORDER
    out = []
    for name in names:
        module = SOURCES.get(name)
        if module is None:
            raise KeyError(f"未知语料源 {name!r}；可选：{', '.join(SOURCES)}")
        if module not in out:
            out.append(module)
    return out


def describe_all() -> list[dict]:
    return [
        {
            "name": module.NAME,
            "description": module.DESCRIPTION,
            "homepage": module.HOMEPAGE,
            "license": module.LICENSE,
        }
        for module in SOURCES.values()
    ]
