"""核对诊断报告的文档引用身份；不代替原文事实和结论的农艺核验。"""

import json
import re


def retrieved_doc_ids(messages) -> set[str]:
    """Only successful knowledge ToolMessages can authorize a citation."""
    ids = set()
    for message in messages:
        if getattr(message, "type", "") != "tool" or getattr(message, "status", "success") != "success":
            continue
        name = getattr(message, "name", "")
        text = str(message.content)
        if name in {"search_knowledge_base", "get_knowledge_document"}:
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                continue
            if payload.get("found") and name == "search_knowledge_base":
                ids.update(item["doc_id"] for item in payload.get("evidence", []) if item.get("doc_id"))
            elif payload.get("found") and payload.get("doc_id"):
                ids.add(payload["doc_id"])
        elif name in {"query_agronomy_knowledge", "query_pest_disease_library", "query_soil_reference",
                      "query_equipment_manual", "get_treatment_options"}:
            ids.update(re.findall(r"doc_id=([^；\s]+)", text))
    return ids


def check_citations(answer: str, retrieved: set[str], *, require_citation: bool = True) -> dict:
    prefixes = r"(?:seed|cropdp|plantinquiry|qa_en|qa_zh|curated|local):"
    quoted = set(re.findall(r"`(" + prefixes + r"[^`\n]+)`", answer))
    prose = re.sub(r"`[^`\n]*`", "", answer)
    unformatted = re.findall(prefixes + r"[^\s，；。`]+", prose)
    placeholders = [value for value in re.findall(r"`([^`\n]+)`", answer)
                    if ("..." in value or "…" in value) and ":" in value]
    unknown = sorted(quoted - retrieved)
    return {"cited_doc_ids": sorted(quoted), "unretrieved_citations": unknown,
            "unformatted_citations": unformatted, "placeholder_citations": placeholders,
            "answer_citations_valid": (bool(quoted) or not require_citation)
            and not unknown and not unformatted and not placeholders}
