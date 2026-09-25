"""内置示例知识库：随项目分发的精简语料，不依赖任何外部下载。

**为什么要有这个源**：CropDP-KG 与 PlantInquiryVQA 是真实的专业知识库，
但它们的规模让"把框架跑起来看一眼"变成一次几十分钟的下载。
内置种子库让整条 RAG 链路可以在**几秒内**构建并端到端验证——
接线对不对、Agent 能不能检索到，不需要等语料下载完才知道。

语料文件：``agriagents/rag/seed/knowledge_seed.jsonl``（18 条）。
内容覆盖五种知识工具的全部形态：病害卡片、分部位症状、防治方案、
严重度判定、鉴别诊断、农艺问答、土壤档案、设备手册、安全规则。

**内容是结构性占位**，不是可用于生产的农艺建议——涉及药剂剂量、
安全间隔期等字段的地方都显式标注了缺失。要接真实数据，
把 ``cropdp`` / ``plantinquiry`` / ``qa_en`` / ``qa_zh`` 加进构建即可，
本文件不用改。
"""

from __future__ import annotations

import json
import os

from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult

NAME = "seed"
DESCRIPTION = "内置示例知识库（18 条，覆盖全部知识工具形态，零下载）"
HOMEPAGE = "（项目内置，无外部来源）"
LICENSE = "随本项目"

_SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed")
SEED_FILE = os.path.join(_SEED_DIR, "knowledge_seed.jsonl")
TERM_FILE = os.path.join(_SEED_DIR, "term_seed.json")


def load_terms() -> list[dict]:
    """内置术语表：少量中英/拉丁对齐，让跨语言检索在内置库里也能成立。"""
    if not os.path.exists(TERM_FILE):
        return []
    with open(TERM_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build(ctx: IngestContext) -> SourceResult:
    if not os.path.exists(SEED_FILE):
        raise FileNotFoundError(f"内置知识库缺失：{SEED_FILE}")

    docs: list[Doc] = []
    with open(SEED_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            docs.append(_to_doc(json.loads(line)))
        if ctx.limit is not None:
            docs = docs[: ctx.limit]

    kinds = sorted({doc.kind for doc in docs})
    terms = load_terms()
    return SourceResult(
        docs=docs,
        artifacts={"seed_terms": terms},
        notes=[
            f"{len(docs)} 条内置条目，覆盖 {len(kinds)} 种形态：{'、'.join(kinds)}",
            f"{len(terms)} 条内置术语（中/英/拉丁对齐）",
        ],
    )


def _to_doc(row: dict) -> Doc:
    """把种子行还原成 ``Doc``。

    ``id`` / ``parent`` / ``meta`` 之外的所有字段都平铺进 ``meta``，
    这样加一个知识形态不用改这里——只要在 JSONL 里多写一行。
    """
    reserved = {"id", "kind", "title", "lang", "text", "parent", "meta"}
    meta = dict(row.get("meta") or {})
    meta.update({key: value for key, value in row.items() if key not in reserved})

    return Doc(
        doc_id=row["id"],
        source=NAME,
        kind=row.get("kind", "note"),
        title=row.get("title", ""),
        lang=row.get("lang", "zh"),
        parent_id=row.get("parent", ""),
        text=row.get("text", ""),
        meta=meta,
    )
