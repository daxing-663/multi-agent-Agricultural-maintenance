"""Read-only real-model smoke test of the diagnosis Agent's knowledge tools.

Requires --live because this uses the configured model API. No execution,
approval, settlement or actuator nodes are present in this verification graph.
For deterministic offline retrieval evaluation use verify_kb_access.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _evidence_ids(tool: str, text: str) -> tuple[list[str], bool]:
    """A successful tool call must contain evidence, not an optional-data sentinel."""
    if tool in {"list_knowledge_bases", "search_knowledge_base", "get_knowledge_document"}:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return [], False
        if tool == "list_knowledge_bases":
            return [], bool(payload.get("available") and payload.get("documents") and payload.get("sources"))
        if tool == "get_knowledge_document":
            doc_id = payload.get("doc_id", "")
            document = payload.get("document") or {}
            return [doc_id] if doc_id else [], bool(payload.get("found") and doc_id and document.get("text"))
        ids = [item.get("doc_id", "") for item in payload.get("evidence", [])]
        return ids, bool(payload.get("found") and ids and all(ids))
    ids = re.findall(r"doc_id=([^；\s]+)", text)
    return ids, bool(ids)


def _scenario_checks(trace: list[dict]) -> dict[str, bool]:
    successful = [row for row in trace if row["evidence_valid"]]

    def seen(tool, **filters):
        return any(row["tool"] == tool and all(
            str(row["arguments"].get(key, "")).strip().casefold() in {str(value).casefold() for value in values}
            for key, values in filters.items()
        ) for row in successful)

    checks = {}
    for crop, aliases in (("tomato", {"番茄", "tomato", "西红柿"}), ("cucumber", {"黄瓜", "cucumber"})):
        for tool in ("query_pest_disease_library", "get_treatment_options"):
            checks[f"{tool}:{crop}"] = seen(tool, crop=aliases)
    for site in ("FIELD-07", "GH-09"):
        checks[f"soil:{site}"] = seen("query_soil_reference", site_id={site})
    for device, model, fault in (("VALVE-11", "DEMO-VALVE-V1", "E04"), ("FAN-03", "DEMO-FAN-V1", "OFFLINE")):
        checks[f"equipment:{device}:{fault}"] = seen("query_equipment_manual", device_id={device, model}, fault_code={fault})
    for dataset in ("talhakk/agriculture-qa", "KisanVaani/agriculture-qa-english-only", "manifesta/verified-agronomy-17k"):
        checks[f"dataset:{dataset}"] = seen("search_knowledge_base", source={"qa_en"}, dataset={dataset})
    return checks


def _answer_citations(answer: str, retrieved: set[str]) -> dict:
    """Validate final report references as well as the underlying tool messages."""
    from agriagents.agents.diagnosis.citations import check_citations
    return check_citations(answer, retrieved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--index-dir", default=str(Path.home() / ".agriagents" / "rag"))
    parser.add_argument("--output", default="reports/kb/agent_smoke.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("传入 --live 才会调用已配置的模型 API；离线测试请用 verify_kb_access.py")

    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode

    from agriagents.agents.diagnosis.diagnosis_agent import TOOLS, create_diagnosis_agent
    from agriagents.agents.state import AgriState
    from agriagents.dataflows.config import run_config
    from agriagents.default_config import DEFAULT_CONFIG
    from agriagents.graph.propagation import Propagator
    from agriagents.llm import create_llm
    from agriagents.rag import get_knowledge_base

    config = {**DEFAULT_CONFIG, "rag_index_dir": args.index_dir, "dry_run": True}
    if config["llm_provider"] == "stub":
        parser.error("--live 需要真实模型供应商，当前 provider=stub")
    kwargs = {"max_retries": 1, "timeout": 90, "max_tokens": 2600}
    if config["llm_provider"] == "deepseek":
        kwargs["thinking_enabled"] = False
    llm = create_llm(config["llm_provider"], config["quick_think_llm"], config.get("backend_url"), **kwargs)
    workflow = StateGraph(AgriState)
    workflow.add_node("diagnosis", create_diagnosis_agent(llm))
    workflow.add_node("knowledge", ToolNode(list(TOOLS)))
    workflow.add_edge(START, "diagnosis")
    workflow.add_conditional_edges(
        "diagnosis", lambda state: "knowledge" if state["messages"][-1].tool_calls else END,
        {"knowledge": "knowledge", END: END},
    )
    workflow.add_edge("knowledge", "diagnosis")
    initial = Propagator().create_initial_state(
        "FIELD-07", date.today().isoformat(), site_type="greenhouse",
        site_context="这是检索链路验收；FIELD-07番茄/GH-09黄瓜及设备均为演示场景，不是现场事实。",
    )
    initial["perception_state"]["perception_report"] = (
        "演示输入：FIELD-07番茄叶片暗绿色水浸状病斑，潮湿时有白色霉层；VALVE-11报告E04。"
        "GH-09黄瓜叶片多角形黄褐色斑、叶背灰黑霉层；FAN-03报告OFFLINE。"
    )
    initial["messages"] = [HumanMessage(content=(
        "请验收知识库工具接线，先调用list_knowledge_bases，然后实际调用全部八种知识工具。"
        "检查番茄和黄瓜病害候选、对应作物方案、FIELD-07/GH-09土壤、"
        "VALVE-11 E04/FAN-03 OFFLINE设备手册、番茄水肥农艺问答；"
        "再用search_knowledge_base指定source=qa_en，分别用dataset精确限定"
        "talhakk/agriculture-qa和KisanVaani/agriculture-qa-english-only查crop rotation，"
        "限定manifesta/verified-agronomy-17k查hectares acres conversion；"
        "再分别指定source=qa_zh查番茄施肥、"
        "cropdp查番茄晚疫病、plantinquiry查cucumber downy mildew叶片症状，"
        "curated查黄瓜温室定植水肥。"
        "从search_knowledge_base已返回的候选中选择一条，调用get_knowledge_document按完整doc_id读取全文一次。"
        "完成后列出关键证据的完整doc_id，用反引号包围并逐字复制返回值，"
        "禁止省略号、去掉编号或拼接ID；只引用本轮工具的主doc_id。"
        "明确演示档案身份；不制定或执行作业。"
    ))]
    trace = []
    final = None
    with run_config(config):
        for state in workflow.compile().stream(initial, config={"recursion_limit": 18}, stream_mode="values"):
            final = state
        known_ids = {doc.doc_id for doc in get_knowledge_base().index.docs}
    requests = {call["id"]: call for message in final["messages"] if message.type == "ai"
                for call in message.tool_calls}
    used_sources = set()
    for message in final["messages"]:
        if message.type == "tool":
            text = str(message.content)
            ids, nonempty = _evidence_ids(message.name, text)
            evidence_valid = (nonempty and all(doc_id in known_ids for doc_id in ids)
                              and message.status == "success")
            if message.name != "list_knowledge_bases":
                used_sources.update(doc_id.split(":", 1)[0] for doc_id in ids if doc_id in known_ids)
            trace.append({
                "tool": message.name, "status": message.status,
                "arguments": requests.get(message.tool_call_id, {}).get("args", {}),
                "local_rag": "local_rag" in text,
                "fallback": "stub_knowledge" in text or "未接入真实数据源" in text,
                "doc_ids": ids, "evidence_valid": evidence_valid,
                "result": text,
            })
    called = {row["tool"] for row in trace}
    required = {tool.name for tool in TOOLS}
    required_sources = {"seed", "cropdp", "plantinquiry", "qa_en", "qa_zh", "curated"}
    scenarios = _scenario_checks(trace)
    answer = final.get("diagnosis_state", {}).get("diagnosis_report", "")
    citations = _answer_citations(answer, {doc_id for row in trace for doc_id in row["doc_ids"]})
    runtime_citation_check = final.get("diagnosis_state", {}).get("citation_check", {})
    passed = (required <= called and required_sources <= used_sources
              and all(scenarios.values())
              and citations["answer_citations_valid"]
              and runtime_citation_check.get("status") == "passed"
              and all(row["evidence_valid"] and row["local_rag"] and not row["fallback"] for row in trace))
    report = {
        "mode": "live_read_only_diagnosis_agent",
        "provider": config["llm_provider"], "model": config["quick_think_llm"],
        "passed": passed, "missing_tools": sorted(required - called),
        "index_dir": str(Path(args.index_dir).resolve()),
        "corpus_sha256": hashlib.sha256((Path(args.index_dir) / "corpus.jsonl").read_bytes()).hexdigest(),
        "scenario_checks": scenarios,
        "missing_scenarios": [name for name, ok in scenarios.items() if not ok],
        "evidence_sources": sorted(used_sources), "missing_sources": sorted(required_sources - used_sources),
        "tool_calls": trace, "answer": answer, **citations,
        "runtime_citation_check": runtime_citation_check,
        "scope": "验证真实模型能够通过诊断Agent调用知识工具；不等同于现场诊断准确率。",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'PASS' if passed else 'FAIL'}: {len(called)}/{len(required)} tools, {len(trace)} calls; {output.resolve()}")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
