from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agriagents.agents.state import AgriState
from agriagents.api.app import create_app
from agriagents.api.service import CaseService
from agriagents.default_config import DEFAULT_CONFIG
from agriagents.graph.agri_graph import AgriAgentsGraph, CheckpointRun

pytestmark = pytest.mark.unit


def _config(tmp_path: Path) -> dict:
    return {
        **DEFAULT_CONFIG,
        "llm_provider": "stub",
        "deep_think_llm": "stub",
        "quick_think_llm": "stub",
        "checkpoint_enabled": True,
        "require_human_approval": True,
        "dry_run": True,
        "data_cache_dir": str(tmp_path / "cache"),
        "results_dir": str(tmp_path / "results"),
        "memory_log_path": str(tmp_path / "memory.md"),
    }


def _approval_workflow():
    workflow = StateGraph(AgriState)

    def approval(state):
        decision = interrupt({
            "type": "maintenance_approval",
            "site": state["site_of_interest"],
            "work_order": "WO-CHECKPOINT-TEST",
        })
        coordination = {
            **state["coordination_state"],
            "work_order": "WO-CHECKPOINT-TEST",
            "approved": bool(decision["approved"]),
            "approval": f"审批人={decision['approver']}",
        }
        return {"coordination_state": coordination}

    def finish(state):
        return {
            "final_decision": "**处置等级：** 观察",
            "case_status": "executed" if state["coordination_state"]["approved"] else "planned",
        }

    workflow.add_node("approval", approval)
    workflow.add_node("finish", finish)
    workflow.add_edge(START, "approval")
    workflow.add_edge("approval", "finish")
    workflow.add_edge("finish", END)
    return workflow


def test_checkpoint_case_interrupt_resume_and_read(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))
    graph.workflow = _approval_workflow()
    graph.graph = graph.workflow.compile()

    paused = graph.start_checkpointed_case(
        "FIELD-07", "2026-10-12", run_id="API-TEST-1"
    )
    assert paused.lifecycle == "awaiting_approval"
    assert paused.interrupt["type"] == "maintenance_approval"

    completed = graph.resume_checkpointed_case(
        "FIELD-07",
        "2026-10-12",
        approved=True,
        approver="测试审批人",
        run_id="API-TEST-1",
    )
    assert completed.lifecycle == "completed"
    assert completed.state["coordination_state"]["approved"] is True

    restored = graph.read_checkpointed_case(
        "FIELD-07", "2026-10-12", run_id="API-TEST-1"
    )
    assert restored.lifecycle == "completed"
    assert restored.state["case_status"] == "executed"


class FakeGraph:
    runs: dict[str, CheckpointRun] = {}

    def __init__(self, config):
        self.config = config

    @staticmethod
    def _state(site: str, as_of: str, *, approved: bool = False) -> dict:
        return {
            "site_of_interest": site,
            "site_type": "field",
            "as_of": as_of,
            "site_context": site,
            "resource_context": "unknown",
            "perception_state": {
                "perception_report": "土壤湿度异常",
                "anomaly_screen": "发现异常",
                "anomaly_level": "high",
                "has_anomaly": True,
            },
            "diagnosis_state": {
                "diagnosis_report": "疑似缺水",
                "severity": "high",
                "confidence": "medium",
                "rationale": "连续读数低于目标",
                "recommended_checks": "现场复核",
            },
            "coordination_state": {
                "work_order": "**工单：** 补水并复检",
                "safety_review": "安全检查通过",
                "safety_verdict": "pass",
                "approval": "审批人=林场长" if approved else "",
                "approved": approved,
                "rollback_plan": "",
            },
            "execution_state": {
                "dispatch_log": "dry_run" if approved else "",
                "execution_status": "success" if approved else "",
                "execution_report": "演练完成" if approved else "",
                "needs_followup": False,
            },
            "final_decision": "**处置等级：** 尽快处置" if approved else "",
            "case_status": "executed" if approved else "",
        }

    def start_checkpointed_case(self, site, as_of, site_type="field", resources=None, run_id="", request_note=""):
        outcome = CheckpointRun(
            self._state(site, as_of),
            f"thread-{run_id}",
            "awaiting_approval",
            {"type": "maintenance_approval", "question": "是否批准？"},
            ("approval_gate",),
        )
        self.runs[run_id] = outcome
        return outcome

    def resume_checkpointed_case(self, site, as_of, *, approved, approver, note="", run_id=""):
        outcome = CheckpointRun(
            self._state(site, as_of, approved=approved),
            f"thread-{run_id}",
            "completed",
            None,
            (),
        )
        self.runs[run_id] = outcome
        return outcome

    def read_checkpointed_case(self, site, as_of, *, run_id=""):
        return self.runs[run_id]

    def save_reports(self, state, site_id, save_path):
        path = Path(save_path) / "complete_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.get("final_decision") or "等待审批", encoding="utf-8")
        return path


def test_api_case_approval_and_report_flow(tmp_path):
    FakeGraph.runs.clear()
    service = CaseService(config=_config(tmp_path), graph_factory=FakeGraph)
    client = TestClient(create_app(service))

    created = client.post("/api/cases", json={
        "site_id": "FIELD-07",
        "site_type": "field",
        "as_of": "2026-10-12",
        "dry_run": True,
        "note": "API 回归测试",
    })
    assert created.status_code == 202
    case_id = created.json()["id"]

    waiting = client.get(f"/api/cases/{case_id}")
    assert waiting.status_code == 200
    assert waiting.json()["status"] == "awaiting_approval"
    assert waiting.json()["work_order"]

    approval = client.post(f"/api/cases/{case_id}/approval", json={
        "approved": True,
        "approver": "林场长",
        "note": "同意干跑",
    })
    assert approval.status_code == 202

    completed = client.get(f"/api/cases/{case_id}").json()
    assert completed["status"] == "completed"
    assert completed["approved"] is True
    assert completed["state"]["final_decision"]

    report = client.get(f"/api/cases/{case_id}/report")
    assert report.status_code == 200
    assert "处置等级" in report.text
