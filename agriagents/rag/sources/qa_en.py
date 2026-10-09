"""英文农业问答语料：Agriculture-QA 及其同源增强集。

三个来源，互补关系：

================================  ========  ==============================
数据集                             条数      特点
================================  ========  ==============================
``talhakk/agriculture-qa``        25410     综合农业问答，覆盖面广
``KisanVaani/agriculture-qa-…``   22615     同上，问法更口语
``manifesta/verified-agronomy``   17199     带 ``worked_solution`` 与引用来源，
                                             附上游验证方式（如 closed-form）
================================  ========  ==============================

保留上游验证声明及方法；这不代表项目对内容做过人工农艺核验。
"""

from __future__ import annotations

import hashlib

from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult, dedupe_docs, load_hf_rows
from agriagents.rag.sources.scope import crop_metadata, is_food_health_qa

NAME = "qa_en"
DESCRIPTION = "Agriculture-QA 系列英文农业问答（含带推导及上游验证声明的子集）"
HOMEPAGE = "https://huggingface.co/datasets/talhakk/agriculture-qa"
LICENSE = "见各数据集卡片"

# (数据集, split, 数据集是否声明验证, 展示名)
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
    datasets: dict = {}

    for dataset, split, verified, label in _DATASETS:
        rows, mode = load_hf_rows(ctx, dataset, split=split, limit=ctx.limit)
        count = 0
        scanned = 0
        food_health_rejected = 0
        for row in rows:
            scanned += 1
            if is_food_health_qa(
                _text(row.get("question") or row.get("prompt")),
                _text(row.get("answer") or row.get("answers") or row.get("response")),
            ):
                food_health_rejected += 1
                continue
            doc = _row_to_doc(row, dataset, label, verified)
            if doc is not None:
                docs.append(doc)
                count += 1
        notes.append(f"{label}：{count} 条，排除食品/人体健康问答 {food_health_rejected} 条（{mode}）")
        if count == 0:
            raise ValueError(f"{dataset} 扫描 {scanned} 行但无可用文档，可能为字段变化或空数据")
        datasets[dataset] = {"status": "success", "rows_scanned": scanned, "accepted": count,
                             "food_health_rejected": food_health_rejected, "mode": mode}

    before = len(docs)
    docs = dedupe_docs(docs)
    notes.append(f"去重：{before} → {len(docs)}")
    for dataset, info in datasets.items():
        info["documents"] = sum(doc.meta.get("dataset") == dataset for doc in docs)
    return SourceResult(docs=docs, notes=notes, details={"datasets": datasets})


def _row_to_doc(row: dict, dataset: str, label: str, verified: bool) -> Doc | None:
    question = _text(row.get("question") or row.get("prompt"))
    answer = _text(row.get("answer") or row.get("answers") or row.get("response"))
    solution = _text(row.get("worked_solution"))
    # 数值题答案常只有几个字符，worked_solution 才是知识内容。
    if not question or not answer or max(len(answer), len(solution)) < _MIN_ANSWER_CHARS:
        return None
    if is_food_health_qa(question, answer):
        return None

    citation = _text(row.get("citation"))
    record_id = _text(row.get("id")) or _stable_key(question + "\n" + answer)
    source_ref = f"https://huggingface.co/datasets/{dataset}"
    body = [f"问题：{question}", f"解答：{answer}"]
    if solution and solution != answer:
        body.append(f"推导过程：{solution}")
    if citation:
        body.append(f"来源：{citation}")

    return Doc(
        doc_id=f"qa_en:{dataset}:{record_id}",
        source=NAME,
        kind="qa_pair",
        title=question[:120],
        lang="en",
        text="\n".join(body),
        meta={
            **crop_metadata(question, answer, lang="en"),
            "dataset": dataset,
            "label": label,
            "source_ref": source_ref,
            "record_id": record_id,
            "split": "train",
            "evidence_status": "upstream_claim" if verified else "unreviewed_public_qa",
            "verified": False,
            "project_human_verified": False,
            "upstream_verification_claim": verified,
            "verified_by": _text(row.get("verified_by")),
            "verification_note": _text(row.get("verification_note")),
            "verifiable": row.get("verifiable"),
            "exactly_gradable": row.get("exactly_gradable"),
            "answer": answer,
            "worked_solution": solution,
            "answer_unit": _text(row.get("answer_unit")),
            "citation": citation,
            "difficulty": row.get("difficulty") or "",
            "answer_type": row.get("answer_type") or "",
        },
    )


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(_text(item) for item in value).strip()
    return str(value).strip()


def _stable_key(text: str) -> str:
    """稳定 doc_id。

    不能用内置 ``hash()``——它每进程加盐，重启后同一个问题会得到不同 ID，
    增量重建索引时会产生重复条目。
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()
