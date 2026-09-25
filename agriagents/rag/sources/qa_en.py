"""英文农业问答语料：Agriculture-QA 及其同源增强集。

三个来源，互补关系：

================================  ========  ==============================
数据集                             条数      特点
================================  ========  ==============================
``talhakk/agriculture-qa``        25410     综合农业问答，覆盖面广
``KisanVaani/agriculture-qa-…``   22615     同上，问法更口语
``manifesta/verified-agronomy``   17199     带 ``worked_solution`` 与引用来源，
                                             **经人工核验**，质量最高
================================  ========  ==============================

三个都进，但按 ``meta.verified`` 标记区别对待：核验过的答案被引用时
可信度更高。这是数据层面的分级，不是检索层面的过滤——
未核验的答案依然可能包含有用的操作细节，直接丢掉可惜。
"""

from __future__ import annotations

import hashlib

from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult, dedupe_docs, load_hf_rows

NAME = "qa_en"
DESCRIPTION = "Agriculture-QA 系列英文农业问答（含人工核验子集）"
HOMEPAGE = "https://huggingface.co/datasets/talhakk/agriculture-qa"
LICENSE = "见各数据集卡片"

# (数据集, split, 是否人工核验, 展示名)
_DATASETS = (
    ("talhakk/agriculture-qa", "train", False, "Agriculture-QA"),
    ("KisanVaani/agriculture-qa-english-only", "train", False, "KisanVaani QA"),
    ("manifesta/verified-agronomy-17k", "train", True, "Verified Agronomy 17k"),
)

# 低于这个长度的答案多是"能/不能""是的"这类无可操作信息的回复，不入索引。
_MIN_ANSWER_CHARS = 25


def build(ctx: IngestContext) -> SourceResult:
    docs: list[Doc] = []
    notes: list[str] = []

    for dataset, split, verified, label in _DATASETS:
        rows, mode = load_hf_rows(ctx, dataset, split=split, limit=ctx.limit)
        count = 0
        for row in rows:
            doc = _row_to_doc(row, dataset, label, verified)
            if doc is not None:
                docs.append(doc)
                count += 1
        notes.append(f"{label}：{count} 条（{mode}）")

    before = len(docs)
    docs = dedupe_docs(docs)
    notes.append(f"去重：{before} → {len(docs)}")
    return SourceResult(docs=docs, notes=notes)


def _row_to_doc(row: dict, dataset: str, label: str, verified: bool) -> Doc | None:
    question = (row.get("question") or row.get("prompt") or "").strip()
    answer = (row.get("answer") or row.get("answers") or row.get("response") or "").strip()
    if not question or len(answer) < _MIN_ANSWER_CHARS:
        return None

    solution = (row.get("worked_solution") or "").strip()
    citation = (row.get("citation") or "").strip()
    body = [f"问题：{question}", f"解答：{answer}"]
    if solution and solution != answer:
        body.append(f"推导过程：{solution}")
    if citation:
        body.append(f"来源：{citation}")

    return Doc(
        doc_id=f"qa_en:{dataset}:{_stable_key(question)}",
        source=NAME,
        kind="qa_pair",
        title=question[:120],
        lang="en",
        text="\n".join(body),
        meta={
            "dataset": dataset,
            "label": label,
            "verified": verified,
            "citation": citation,
            "difficulty": row.get("difficulty") or "",
            "answer_type": row.get("answer_type") or "",
        },
    )


def _stable_key(text: str) -> str:
    """稳定 doc_id。

    不能用内置 ``hash()``——它每进程加盐，重启后同一个问题会得到不同 ID，
    增量重建索引时会产生重复条目。
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()
