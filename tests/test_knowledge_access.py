"""Knowledge retrieval through the actual Agent ToolNode interface."""

import json

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agriagents.agents.diagnosis.diagnosis_agent import TOOLS
from agriagents.agents.tools import get_knowledge_document, list_knowledge_bases, search_knowledge_base
from tests.test_rag import routed_kb  # noqa: F401 - shared isolated seed fixture


def test_agent_toolnode_reaches_every_knowledge_interface(routed_kb):
    requests = [
        ("list_knowledge_bases", {}),
        ("search_knowledge_base", {"query": "晚疫病", "source": "seed", "kind": "symptom", "crop": "番茄"}),
        ("get_knowledge_document", {"doc_id": "seed:tomato-late-blight:symptom-leaf"}),
        ("query_pest_disease_library", {"crop": "番茄", "symptom": "叶片暗绿色水浸状病斑、白色霉层"}),
        ("query_agronomy_knowledge", {"question": "大棚连作障碍怎么缓解"}),
        ("query_soil_reference", {"site_id": "FIELD-07"}),
        ("query_equipment_manual", {"device_id": "irrigation_valve", "fault_code": "E04"}),
        ("get_treatment_options", {"diagnosis": "番茄晚疫病", "crop": "番茄"}),
    ]
    calls = [{"id": str(i), "name": name, "args": args, "type": "tool_call"}
             for i, (name, args) in enumerate(requests)]
    workflow = StateGraph(MessagesState)
    workflow.add_node("tools", ToolNode(list(TOOLS)))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    result = workflow.compile().invoke({"messages": [AIMessage(content="", tool_calls=calls)]})
    outputs = {message.name: message for message in result["messages"] if message.type == "tool"}
    assert set(outputs) == {name for name, _ in requests}
    for message in outputs.values():
        assert message.status == "success", message.content
        assert "local_rag" in message.content
        assert "未接入真实数据源" not in message.content
    evidence = json.loads(outputs["search_knowledge_base"].content)["evidence"]
    assert evidence and all(item["source"] == "seed" and item["kind"] == "symptom" for item in evidence)
    assert all(item["doc_id"] and item["citation"] and item["is_demo"] for item in evidence)
    assert all("text" not in item and item["snippets"] for item in evidence)
    full_document = json.loads(outputs["get_knowledge_document"].content)
    assert full_document["found"] is True
    assert full_document["doc_id"] == "seed:tomato-late-blight:symptom-leaf"
    assert "水浸状" in full_document["document"]["text"]


def test_unknown_source_never_falls_back_to_another_library(routed_kb):
    result = json.loads(search_knowledge_base.invoke({"query": "番茄晚疫病", "source": "not-loaded"}))
    assert result["found"] is False
    assert result["evidence"] == []


def test_full_document_lookup_requires_exact_doc_id(routed_kb):
    result = json.loads(get_knowledge_document.invoke({"doc_id": "seed:tomato-late-blight:symptom"}))
    assert result["found"] is False
    assert result["document"] is None
    assert "不会改用相似文档" in result["reason"]


def test_search_returns_relevant_bounded_snippets_and_full_text_is_on_demand(monkeypatch):
    from agriagents.dataflows.vendors import knowledge_catalog
    from agriagents.rag import Doc, KnowledgeBase, RagIndex

    noise = "Background material unrelated to lesions. " * 18
    evidence = "Key finding: concentric rings and a yellow halo distinguish early blight lesions."
    doc = Doc("plantinquiry:focused", "plantinquiry", "diagnosis", noise + evidence,
              title="tomato early blight", meta={"crop": "tomato", "dataset": "fixture"})
    kb = KnowledgeBase("unused", index=RagIndex.build([doc]))
    monkeypatch.setattr(knowledge_catalog, "get_knowledge_base", lambda: kb)

    result = json.loads(knowledge_catalog.search_knowledge_base(
        "concentric rings yellow halo", source="plantinquiry", kind="diagnosis", top_k=1,
    ))
    item = result["evidence"][0]
    assert "text" not in item
    assert len(item["snippets"]) <= knowledge_catalog.SEARCH_SNIPPETS_PER_DOC
    assert all(len(part) <= knowledge_catalog.SEARCH_SNIPPET_CHARS for part in item["snippets"])
    assert "concentric rings" in " ".join(item["snippets"])
    assert item["full_text_chars"] == len(doc.text)

    full = json.loads(knowledge_catalog.get_knowledge_document(doc.doc_id))
    assert full["document"]["text"] == doc.text


def test_catalog_reports_loaded_coverage_and_demo_identity(routed_kb):
    result = json.loads(list_knowledge_bases.invoke({}))
    assert result["available"] is True
    assert {source["source"] for source in result["sources"]} == {"seed"}
    assert result["documents"] == result["sources"][0]["demo_documents"]


def test_catalog_crop_filter_rejects_other_crops_before_ranking(routed_kb):
    result = json.loads(search_knowledge_base.invoke({
        "query": "番茄晚疫病", "source": "seed", "kind": "symptom", "crop": "不存在的作物XYZ",
    }))
    assert not result["found"]


def test_catalog_exact_dataset_filter_and_demo_alias(monkeypatch):
    from agriagents.dataflows.vendors import knowledge_catalog
    from agriagents.rag import Doc, KnowledgeBase, RagIndex

    docs = [
        Doc("one", "qa_en", "qa_pair", "tomato rotation tomato rotation", meta={"dataset": "one"}),
        Doc("two", "qa_en", "qa_pair", "tomato rotation", meta={"dataset": "two", "demo": True}),
    ]
    kb = KnowledgeBase("unused", index=RagIndex.build(docs))
    monkeypatch.setattr(knowledge_catalog, "get_knowledge_base", lambda: kb)
    result = json.loads(knowledge_catalog.search_knowledge_base("tomato rotation", source="qa_en", dataset="two", top_k=1))
    assert [row["doc_id"] for row in result["evidence"]] == ["two"]
    assert result["evidence"][0]["is_demo"] is True
    assert result["evidence"][0]["applicability_validated"] is False
    assert not json.loads(knowledge_catalog.search_knowledge_base("tomato rotation", dataset="missing"))["found"]


def test_catalog_research_hit_requires_specialized_tool(routed_kb):
    result = json.loads(search_knowledge_base.invoke({"query": "FIELD-07", "kind": "soil_reference"}))
    assert result["evidence"]
    assert all(row["required_tool"] == "query_soil_reference(site_id)" and not row["applicability_validated"] for row in result["evidence"])


def test_catalog_keeps_manual_identity_needed_for_exact_followup(monkeypatch):
    from agriagents.dataflows.vendors import knowledge_catalog
    from agriagents.rag import Doc, KnowledgeBase, RagIndex

    identity = {"model_id": "MODEL-X", "device_aliases": ["PUMP-X"], "fault_codes": ["E01"]}
    doc = Doc("local:manual", "local", "equipment_manual", "MODEL-X E01 no feedback",
              meta={**identity, "source_ref": "manufacturer fixture"})
    kb = KnowledgeBase("unused", index=RagIndex.build([doc]))
    monkeypatch.setattr(knowledge_catalog, "get_knowledge_base", lambda: kb)
    item = json.loads(knowledge_catalog.search_knowledge_base("MODEL-X E01"))["evidence"][0]
    assert all(item["metadata"][key] == value for key, value in identity.items())
    hits = kb.equipment_manual(item["metadata"]["device_aliases"][0], item["metadata"]["fault_codes"][0])
    assert [hit.doc.doc_id for hit in hits] == [item["doc_id"]]


def test_live_smoke_rejects_successful_calls_without_evidence():
    from tools.verify_agent_knowledge import _evidence_ids

    assert _evidence_ids("search_knowledge_base", '{"found": false, "evidence": []}') == ([], False)
    assert _evidence_ids("get_treatment_options", "local_rag：可选数据源不可用") == ([], False)
    assert _evidence_ids("query_soil_reference", "source=seed；doc_id=seed:soil:field-07:demo；出处=fixture") == (
        ["seed:soil:field-07:demo"], True,
    )


def test_live_smoke_requires_both_crop_and_object_scenarios():
    from tools.verify_agent_knowledge import _scenario_checks

    trace = [{"tool": "query_soil_reference", "arguments": {"site_id": "FIELD-07"}, "evidence_valid": True}]
    checks = _scenario_checks(trace)
    assert checks["soil:FIELD-07"]
    assert not checks["soil:GH-09"]
    assert not checks["get_treatment_options:cucumber"]
    assert not checks["equipment:FAN-03:OFFLINE"]


def test_deduplicated_dataset_remains_discoverable_with_its_own_citation(monkeypatch):
    from agriagents.dataflows.vendors import knowledge_catalog
    from agriagents.rag import Doc, KnowledgeBase, RagIndex
    from agriagents.rag.sources.base import dedupe_docs

    docs = dedupe_docs([
        Doc("qa_en:A:one", "qa_en", "qa_pair", "crop rotation helps soil structure",
            meta={"dataset": "A", "source_ref": "https://example.test/A"}),
        Doc("qa_en:B:two", "qa_en", "qa_pair", "crop rotation helps soil structure",
            meta={"dataset": "B", "source_ref": "https://example.test/B"}),
    ])
    kb = KnowledgeBase("unused", index=RagIndex.build(docs))
    monkeypatch.setattr(knowledge_catalog, "get_knowledge_base", lambda: kb)
    catalog = json.loads(knowledge_catalog.list_knowledge_bases())
    assert catalog["sources"][0]["datasets"] == ["A", "B"]
    result = json.loads(knowledge_catalog.search_knowledge_base("crop rotation", source="qa_en", dataset="B"))
    item = result["evidence"][0]
    assert item["doc_id"] == "qa_en:A:one"
    assert item["citation"] == "https://example.test/B"
    assert item["matched_dataset"]["original_doc_id"] == "qa_en:B:two"
    assert not json.loads(knowledge_catalog.search_knowledge_base("crop rotation", dataset="C"))["found"]


def test_live_smoke_rejects_shortened_or_invented_report_citations():
    from tools.verify_agent_knowledge import _answer_citations

    retrieved = {"cropdp:番茄晚疫病:0", "plantinquiry:tomato.disease_fungal.late_blight:management"}
    assert _answer_citations("见 `cropdp:番茄晚疫病:0`。", retrieved)["answer_citations_valid"]
    for answer in ("见 `cropdp:番茄晚疫病`。", "见 `plantinquiry:...late_blight:management`。",
                   "见 cropdp:番茄晚疫病:0。", "没有任何文档引用"):
        assert not _answer_citations(answer, retrieved)["answer_citations_valid"]

