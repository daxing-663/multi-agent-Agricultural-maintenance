"""案件生命周期服务：HTTP 与 LangGraph checkpoint 之间的适配层。"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from agriagents.default_config import DEFAULT_CONFIG
from agriagents.graph.agri_graph import AgriAgentsGraph, CheckpointRun

from .store import CaseStore


class CaseService:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        graph_factory: Callable[..., AgriAgentsGraph] = AgriAgentsGraph,
    ):
        self.base_config = {**DEFAULT_CONFIG, **(config or {})}
        self.base_config.update({"checkpoint_enabled": True, "require_human_approval": True})
        db = Path(self.base_config["data_cache_dir"]) / "api" / "cases.db"
        self.store = CaseStore(db)
        self.graph_factory = graph_factory
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def create_case(
        self,
        *,
        site_id: str,
        site_type: str,
        as_of: str,
        dry_run: bool,
        note: str = "",
    ) -> dict[str, Any]:
        case_id = f"AG-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6].upper()}"
        return self.store.create(
            case_id=case_id,
            site_id=site_id,
            site_type=site_type,
            as_of=as_of,
            dry_run=dry_run,
            note=note,
        )

    def run_case(self, case_id: str) -> None:
        with self._case_lock(case_id):
            case = self.store.get(case_id)
            self.store.update(case_id, status="running", error=None)
            try:
                graph = self._graph(case)
                outcome = graph.start_checkpointed_case(
                    case["site_id"],
                    case["as_of"],
                    site_type=case["site_type"],
                    run_id=case_id,
                    request_note=case["note"],
                )
                self._save_outcome(case, graph, outcome)
            except Exception as exc:
                self.store.update(case_id, status="failed", error=str(exc))

    def approve_case(
        self,
        case_id: str,
        *,
        approved: bool,
        approver: str,
        note: str = "",
    ) -> None:
        with self._case_lock(case_id):
            case = self.store.get(case_id)
            if case["status"] not in {"awaiting_approval", "resuming"}:
                raise RuntimeError(f"案件状态为 {case['status']}，当前不能审批")
            self.store.update(case_id, status="resuming", error=None)
            try:
                graph = self._graph(case)
                outcome = graph.resume_checkpointed_case(
                    case["site_id"],
                    case["as_of"],
                    approved=approved,
                    approver=approver,
                    note=note,
                    run_id=case_id,
                )
                self._save_outcome(case, graph, outcome)
            except Exception as exc:
                self.store.update(case_id, status="failed", error=str(exc))
                raise

    def refresh_case(self, case_id: str) -> dict[str, Any]:
        case = self.store.get(case_id)
        if case["status"] in {"queued", "running", "resuming", "failed"}:
            return case
        try:
            graph = self._graph(case)
            outcome = graph.read_checkpointed_case(
                case["site_id"], case["as_of"], run_id=case_id
            )
            return self._save_outcome(case, graph, outcome)
        except RuntimeError:
            return case

    def list_cases(self) -> list[dict[str, Any]]:
        return [self.to_response(case) for case in self.store.list()]

    def get_case(self, case_id: str, *, refresh: bool = True) -> dict[str, Any]:
        case = self.refresh_case(case_id) if refresh else self.store.get(case_id)
        return self.to_response(case)

    def report_markdown(self, case_id: str) -> str:
        case = self.refresh_case(case_id)
        path_text = case.get("report_path")
        if not path_text:
            raise FileNotFoundError("案件尚未生成报告")
        path = Path(path_text)
        if not path.is_file():
            raise FileNotFoundError("报告文件不存在")
        return path.read_text(encoding="utf-8")

    def _graph(self, case: dict[str, Any]) -> AgriAgentsGraph:
        config = {**self.base_config, "dry_run": case["dry_run"]}
        return self.graph_factory(config=config)

    def _save_outcome(
        self,
        case: dict[str, Any],
        graph: AgriAgentsGraph,
        outcome: CheckpointRun,
    ) -> dict[str, Any]:
        public = project_state(outcome.state)
        report_dir = Path(self.base_config["results_dir"]) / "api_reports" / case["id"]
        report_path = graph.save_reports(outcome.state, case["site_id"], report_dir)
        status = "awaiting_approval" if outcome.lifecycle == "awaiting_approval" else outcome.lifecycle
        return self.store.update(
            case["id"],
            status=status,
            thread_id=outcome.thread_id,
            state_json=json.dumps(public, ensure_ascii=False),
            interrupt_json=json.dumps(outcome.interrupt, ensure_ascii=False),
            report_path=str(report_path),
            error=None,
        )

    def _case_lock(self, case_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(case_id, threading.Lock())

    @staticmethod
    def to_response(case: dict[str, Any]) -> dict[str, Any]:
        state = case.get("state") or {}
        diagnosis = state.get("diagnosis") or {}
        perception = state.get("perception") or {}
        coordination = state.get("coordination") or {}
        severity = diagnosis.get("severity") or perception.get("anomaly_level") or "unknown"
        description = (
            diagnosis.get("rationale")
            or diagnosis.get("report")
            or perception.get("anomaly_screen")
            or "流程尚未产出诊断结论"
        )
        return {
            **{key: value for key, value in case.items() if key not in {"state"}},
            "severity": severity,
            "description": description[:500],
            "work_order": coordination.get("work_order", ""),
            "safety_review": coordination.get("safety_review", ""),
            "approval": coordination.get("approval", ""),
            "approved": coordination.get("approved", False),
            "case_status": state.get("case_status", ""),
            "report_available": bool(case.get("report_path")),
            "state": state,
        }


def project_state(state: dict[str, Any]) -> dict[str, Any]:
    """移除消息对象与内部元数据，只暴露前端需要的 LangGraph 状态。"""
    perception = state.get("perception_state") or {}
    diagnosis = state.get("diagnosis_state") or {}
    coordination = state.get("coordination_state") or {}
    execution = state.get("execution_state") or {}
    return {
        "site_of_interest": state.get("site_of_interest", ""),
        "site_type": state.get("site_type", ""),
        "as_of": state.get("as_of", ""),
        "site_context": state.get("site_context", ""),
        "resource_context": state.get("resource_context", ""),
        "perception": {
            "report": perception.get("perception_report", ""),
            "anomaly_screen": perception.get("anomaly_screen", ""),
            "anomaly_level": perception.get("anomaly_level", ""),
            "has_anomaly": perception.get("has_anomaly", False),
        },
        "diagnosis": {
            "report": diagnosis.get("diagnosis_report", ""),
            "severity": diagnosis.get("severity", ""),
            "confidence": diagnosis.get("confidence", ""),
            "rationale": diagnosis.get("rationale", ""),
            "recommended_checks": diagnosis.get("recommended_checks", ""),
        },
        "coordination": {
            "work_order": coordination.get("work_order", ""),
            "safety_review": coordination.get("safety_review", ""),
            "safety_verdict": coordination.get("safety_verdict", ""),
            "approval": coordination.get("approval", ""),
            "approved": coordination.get("approved", False),
            "rollback_plan": coordination.get("rollback_plan", ""),
        },
        "execution": {
            "dispatch_log": execution.get("dispatch_log", ""),
            "status": execution.get("execution_status", ""),
            "report": execution.get("execution_report", ""),
            "needs_followup": execution.get("needs_followup", False),
        },
        "final_decision": state.get("final_decision", ""),
        "case_status": state.get("case_status", ""),
    }

