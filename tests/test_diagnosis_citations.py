"""Model-written citations must come from successful evidence tools this round."""

import json
from collections import Counter

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable

from agriagents.agents.diagnosis.citations import check_citations, retrieved_doc_ids
from agriagents.agents.diagnosis.diagnosis_agent import _bounded_tool_calls, create_diagnosis_agent
from agriagents.graph.propagation import Propagator


class ScriptedLLM(Runnable):
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        return AIMessage(content=next(self.answers))


def state_with_evidence():
    state = Propagator().create_initial_state("FIELD-07", "2026-09-29")
    state["messages"] = [
        AIMessage(content="", tool_calls=[{"name": "query_pest_disease_library", "args": {}, "id": "one", "type": "tool_call"}]),
        ToolMessage(name="query_pest_disease_library", tool_call_id="one",
                    content="source=cropdp；doc_id=cropdp:番茄晚疫病:0；出处=fixture"),
    ]
    return state


def test_unknown_reference_is_retried_with_exact_available_ids():
    model = ScriptedLLM(["支持见 `cropdp:番茄晚疫病`。", "疑似，证据 `cropdp:番茄晚疫病:0`。"])
    result = create_diagnosis_agent(model)(state_with_evidence())
    assert model.calls == 2
    assert result["diagnosis_state"]["citation_check"]["status"] == "passed"
    assert result["diagnosis_state"]["citation_check"]["retried"] is True
    assert "`cropdp:番茄晚疫病:0`" in result["diagnosis_state"]["diagnosis_report"]


def test_repeated_invalid_references_fail_closed_without_leaking_bad_report():
    model = ScriptedLLM(["高置信度 `cropdp:不存在`。", "仍引用 `cropdp:不存在`。"])
    result = create_diagnosis_agent(model)(state_with_evidence())
    assert model.calls == 2
    assert result["diagnosis_state"]["citation_check"]["status"] == "failed"
    assert "证据不足" in result["diagnosis_state"]["diagnosis_report"]
    assert result["diagnosis_state"]["confidence"] == "low"
    assert "cropdp:不存在" not in result["messages"][-1].content


def test_valid_reference_needs_no_extra_model_call():
    model = ScriptedLLM(["引用 `cropdp:番茄晚疫病:0`，尚待鉴别。"])
    result = create_diagnosis_agent(model)(state_with_evidence())
    assert model.calls == 1
    assert result["diagnosis_state"]["citation_check"]["status"] == "passed"


def test_oversized_report_is_compacted_before_citation_validation():
    valid = "`cropdp:番茄晚疫病:0`"
    model = ScriptedLLM([("重复诊断内容。" * 280) + valid, "精简诊断：疑似病害，依据 " + valid + "。"])
    result = create_diagnosis_agent(model)(state_with_evidence())
    report = result["diagnosis_state"]["diagnosis_report"]
    assert model.calls == 2
    assert len(report) < 1800
    assert result["diagnosis_state"]["citation_check"]["compacted"] is True
    assert result["diagnosis_state"]["citation_check"]["status"] == "passed"


def test_user_text_and_failed_tools_cannot_authorize_a_citation():
    messages = [HumanMessage(content="doc_id=seed:fake；"),
                ToolMessage(name="query_soil_reference", tool_call_id="bad", status="error", content="doc_id=seed:error；")]
    assert retrieved_doc_ids(messages) == set()


def test_retrieved_doc_ids_accepts_exact_full_document_lookup():
    message = ToolMessage(
        content=json.dumps({"found": True, "doc_id": "plantinquiry:tomato:diagnosis", "document": {"text": "full"}}),
        tool_call_id="full-1", name="get_knowledge_document", status="success",
    )
    assert retrieved_doc_ids([message]) == {"plantinquiry:tomato:diagnosis"}


def test_placeholder_citation_is_rejected_even_when_a_valid_id_exists():
    result = check_citations(
        "依据 `plantinquiry:tomato:diagnosis`，另见 `...:symptom_stems`。",
        {"plantinquiry:tomato:diagnosis"},
    )
    assert result["answer_citations_valid"] is False
    assert result["placeholder_citations"] == ["...:symptom_stems"]


def test_tool_budget_caps_total_searches_and_full_document_reads():
    calls = [
        {"name": "search_knowledge_base", "id": "s1"},
        {"name": "search_knowledge_base", "id": "s2"},
        {"name": "search_knowledge_base", "id": "s3"},
        {"name": "get_knowledge_document", "id": "f1"},
        {"name": "get_knowledge_document", "id": "f2"},
        {"name": "query_agronomy_knowledge", "id": "a1"},
    ]
    accepted = _bounded_tool_calls(calls, Counter())
    assert [call["id"] for call in accepted] == ["s1", "s2", "f1", "a1"]

    accepted = _bounded_tool_calls(
        [{"name": "query_soil_reference", "id": "new"}],
        Counter({"list_knowledge_bases": 1, "search_knowledge_base": 2,
                 "get_knowledge_document": 1, "query_pest_disease_library": 1,
                 "query_agronomy_knowledge": 1, "query_equipment_manual": 1,
                 "query_soil_reference": 1}),
    )
    assert accepted == []
