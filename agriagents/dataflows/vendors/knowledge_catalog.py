"""Discover loaded knowledge sources and retrieve traceable evidence by source."""

from __future__ import annotations

import json
from collections import Counter

from agriagents.rag import get_knowledge_base
from agriagents.rag.text import chunk, normalize, query_terms, snippet

VENDOR_NAME = "local_rag"
SEARCH_SNIPPET_CHARS = 320
SEARCH_SNIPPETS_PER_DOC = 1


def _is_demo(doc) -> bool:
    return doc.source == "seed" or bool(doc.meta.get("is_demo") or doc.meta.get("demo")) or doc.meta.get("evidence_status") == "demo"


def _required_tool(kind: str) -> str:
    if kind in {"management", "treatment", "disease_card"}:
        return "get_treatment_options(diagnosis, crop)"
    if kind == "soil_reference":
        return "query_soil_reference(site_id)"
    if kind == "equipment_manual":
        return "query_equipment_manual(device_id, fault_code)"
    return ""


def _dataset_provenance(doc) -> list[dict]:
    """Keep one traceable original record per dataset after content deduplication."""
    extra = doc.meta.get("also_seen_in")
    originals = [{"dataset": doc.meta.get("dataset"), "doc_id": doc.doc_id,
                  "source_ref": doc.meta.get("source_ref") or doc.meta.get("citation") or ""},
                 *(extra if isinstance(extra, (list, tuple)) else [])]
    by_dataset = {}
    for original in originals:
        if isinstance(original, dict) and isinstance(original.get("dataset"), str) and original["dataset"]:
            by_dataset.setdefault(normalize(original["dataset"]), {
                "dataset": original["dataset"], "original_doc_id": original.get("doc_id", ""),
                "source_ref": original.get("source_ref", ""),
            })
    return list(by_dataset.values())


def _matched_dataset(doc, requested: str) -> dict:
    return next((item for item in _dataset_provenance(doc)
                 if normalize(item["dataset"]) == normalize(requested)), {}) if requested else {}


def _metadata(doc) -> dict:
    return {key: doc.meta[key] for key in (
        "crop", "crops", "crops_en", "disease", "disease_id", "entity", "part",
        "site_id", "site_ids", "device_id", "device_ids", "device_aliases",
        "model", "model_id", "device_model", "fault_code", "fault_codes",
        "dataset", "source_ref", "verified_by", "project_human_verified", "evidence_status",
        "publisher", "retrieved_at", "applicability", "is_template", "crop_scope",
        "crop_metadata_basis", "crop_metadata_inferred",
    ) if key in doc.meta}


def _citation(doc, dataset: str = "") -> str:
    return (_matched_dataset(doc, dataset).get("source_ref") or doc.meta.get("source_ref")
            or doc.meta.get("citation") or doc.meta.get("source_url")
            or doc.meta.get("dataset") or doc.source)


def _query_snippets(text: str, query: str) -> list[str]:
    """Return small, query-focused excerpts instead of placing the full document in tool messages."""
    pieces = chunk(text, max_chars=SEARCH_SNIPPET_CHARS, overlap=40)
    if not pieces:
        return []
    wanted = query_terms(query)
    ranked = sorted(
        enumerate(pieces),
        key=lambda item: (-len(wanted & query_terms(item[1])), item[0]),
    )
    selected = sorted(index for index, _ in ranked[:SEARCH_SNIPPETS_PER_DOC])
    return [snippet(pieces[index], SEARCH_SNIPPET_CHARS) for index in selected]


def _search_evidence(hit, query: str, dataset: str) -> dict:
    """Build the compact payload consumed on every search turn."""
    doc = hit.doc
    provenance = _dataset_provenance(doc)
    matched = _matched_dataset(doc, dataset)
    item = {
        "doc_id": doc.doc_id,
        "source": doc.source,
        "kind": doc.kind,
        "title": doc.title,
        "snippets": _query_snippets(doc.text, query),
        "full_text_chars": len(doc.text),
        "citation": _citation(doc, dataset),
        "is_demo": _is_demo(doc),
        "applicability_validated": False,
        "metadata": _metadata(doc),
        "match_method": hit.how,
    }
    # Keep detailed provenance only when it adds information beyond citation.
    if dataset or len(provenance) > 1:
        item["dataset_provenance"] = provenance
    if matched:
        item["matched_dataset"] = matched
    required = _required_tool(doc.kind)
    if required:
        item["required_tool"] = required
    return item


def list_knowledge_bases() -> str:
    """Report actual loaded coverage, not just configured adapters."""
    kb = get_knowledge_base()
    docs = kb.index.docs if kb.index else []
    sources = []
    for source in sorted({doc.source for doc in docs}):
        items = [doc for doc in docs if doc.source == source]
        sources.append({
            "source": source,
            "documents": len(items),
            "kinds": dict(sorted(Counter(doc.kind for doc in items).items())),
            "datasets": sorted({item["dataset"] for doc in items for item in _dataset_provenance(doc)}),
            "demo_documents": sum(_is_demo(doc) for doc in items),
        })
    return json.dumps({
        "vendor": VENDOR_NAME,
        "available": kb.available,
        "documents": len(docs),
        "sources": sources,
        "graph": kb.kg.stats() if kb.kg else {},
        "tools": {
            "病虫害": "query_pest_disease_library(crop, symptom)",
            "农艺问答": "query_agronomy_knowledge(question)",
            "处置方案": "get_treatment_options(diagnosis, crop)",
            "土壤档案": "query_soil_reference(site_id)",
            "设备手册": "query_equipment_manual(device_id, fault_code)",
            "指定语料源查证": "search_knowledge_base(query, source, kind)",
            "按文档读取全文": "get_knowledge_document(doc_id)",
        },
        "note": "目录仅反映已加载内容；seed/is_demo 为演示资料，不是实测证据。",
    }, ensure_ascii=False)


def search_knowledge_base(query: str, source: str = "", kind: str = "", top_k: int = 3, crop: str = "", dataset: str = "") -> str:
    """Read-only evidence lookup with strict source/kind selection and provenance."""
    kb = get_knowledge_base()
    docs = kb.index.docs if kb.index else []
    available_sources = {doc.source for doc in docs}
    available_kinds = {doc.kind for doc in docs}
    if source and source not in available_sources:
        hits, reason = [], f"指定知识源未加载：{source}"
    elif kind and kind not in available_kinds:
        hits, reason = [], f"指定知识类型未加载：{kind}"
    else:
        hits = kb.search(
            query, sources=(source,) if source else None,
            kinds=(kind,) if kind else None, top_k=max(1, min(top_k, 10)),
            allowed_doc_ids={doc.doc_id for doc in docs if kb.crop_doc_matches(doc, crop)} if crop else None,
            meta_filters={"dataset": dataset} if dataset else None,
        )
        reason = "" if hits else "指定范围内未命中；请补充实体名称或使用专用工具查询。"
    return json.dumps({
        "vendor": VENDOR_NAME,
        "query": query,
        "source_filter": source,
        "kind_filter": kind,
        "crop_filter": crop,
        "dataset_filter": dataset,
        "found": bool(hits),
        "reason": reason,
        "evidence": [_search_evidence(hit, query, dataset) for hit in hits],
        "full_text_tool": "get_knowledge_document(doc_id)",
        "usage": "snippets 是待核对的命中片段，不是全文。仅在片段不足以完成鉴别时，才用 get_knowledge_document(doc_id) 按需读取全文。处置方案、地块档案和设备故障须再用专用工具按作物/对象核对；不得把相似度作为诊断置信度。",
    }, ensure_ascii=False)


def get_knowledge_document(doc_id: str) -> str:
    """Return one exact document in full after a search result identifies its doc_id."""
    kb = get_knowledge_base()
    docs = kb.index.docs if kb.index else []
    doc = next((item for item in docs if item.doc_id == doc_id), None)
    if doc is None:
        return json.dumps({
            "vendor": VENDOR_NAME,
            "doc_id": doc_id,
            "found": False,
            "reason": "未找到完全匹配的 doc_id；不会改用相似文档。",
            "document": None,
        }, ensure_ascii=False)
    return json.dumps({
        "vendor": VENDOR_NAME,
        "doc_id": doc.doc_id,
        "found": True,
        "reason": "",
        "document": {
            "source": doc.source,
            "kind": doc.kind,
            "title": doc.title,
            "text": doc.text,
            "citation": _citation(doc),
            "dataset_provenance": _dataset_provenance(doc),
            "is_demo": _is_demo(doc),
            "metadata": _metadata(doc),
        },
        "usage": "这是按精确 doc_id 读取的全文；仅用于命中片段不足时的鉴别和引用核对。",
    }, ensure_ascii=False)
