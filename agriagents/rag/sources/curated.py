"""随包分发的少量机构资料释义；保留出处和适用范围，不伪称本地验证。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult

NAME = "curated"
DESCRIPTION = "大学推广机构温室水肥资料的短中文释义（保留原始出处与适用范围）"
HOMEPAGE = "https://www.uaf.edu/ces/ ; https://ask.ifas.ufl.edu/"
LICENSE = "短释义由本项目撰写；原始资料版权及使用条款见各 source_ref"
CURATED_DIR = Path(__file__).resolve().parent.parent / "curated"


def _to_doc(row: dict) -> Doc:
    if not isinstance(row, dict) or row.get("kind") != "agronomy":
        raise ValueError("机构资料释义必须是 kind=agronomy 的对象")
    for key in ("id", "title", "text"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"机构释义缺少 {key}")
    if not row["id"].startswith("curated:"):
        raise ValueError("机构释义 id 必须以 curated: 开头")
    meta = row.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("机构释义缺少 meta")
    for key in ("source_ref", "citation", "publisher", "retrieved_at", "applicability"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise ValueError(f"机构释义缺少 meta.{key}")
    if not meta["source_ref"].startswith("https://"):
        raise ValueError("机构释义必须保留 https 原文链接")
    datetime.date.fromisoformat(meta["retrieved_at"])
    if meta.get("evidence_status") != "source_summary" or meta.get("project_human_verified") is not False:
        raise ValueError("机构释义必须注明 source_summary 与未做项目人工核验")
    if meta.get("is_demo") is not False:
        raise ValueError("机构释义应明确与演示记录区分")
    crops = meta.get("crops")
    if not isinstance(crops, list) or not crops or any(not isinstance(c, str) or not c.strip() for c in crops):
        raise ValueError("机构释义须注明源文档适用作物 crops")
    return Doc(doc_id=row["id"], source=NAME, kind="agronomy", title=row["title"],
               text=row["text"], lang=row.get("lang", "zh"), meta=dict(meta))


def build(ctx: IngestContext) -> SourceResult:
    paths = sorted(CURATED_DIR.glob("*.jsonl"))
    if not paths:
        raise FileNotFoundError(f"随包机构释义缺失：{CURATED_DIR}")
    docs = []
    seen = set()
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    doc = _to_doc(json.loads(line))
                    if doc.doc_id in seen:
                        raise ValueError(f"重复 id：{doc.doc_id}")
                except (TypeError, ValueError, KeyError) as exc:
                    raise ValueError(f"{path}:{line_no}: {exc}") from exc
                seen.add(doc.doc_id)
                docs.append(doc)
    if ctx.limit is not None:
        docs = docs[:ctx.limit]
    return SourceResult(docs=docs,
                        notes=["机构原文的短中文释义；适用范围随文保留，不代表本地农艺验证或定量处方"],
                        details={"files": [p.name for p in paths],
                                 "references": [doc.meta["source_ref"] for doc in docs]})
