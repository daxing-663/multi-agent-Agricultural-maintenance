"""报告树：把一次 run 的产物按阶段落盘。

与 ``AgriAgentsGraph.save_reports`` 共用，保证 CLI 与程序化调用产出完全一致
（否则"命令行跑出来的报告"和"接口跑出来的报告"会慢慢分叉）。
"""

from __future__ import annotations

from pathlib import Path


def write_report_tree(final_state: dict, site_id: str, save_path) -> Path:
    """写出报告树，返回 complete_report.md 的路径。"""
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []

    perception = final_state.get("perception_state", {}) or {}
    diagnosis = final_state.get("diagnosis_state", {}) or {}
    coordination = final_state.get("coordination_state", {}) or {}
    execution = final_state.get("execution_state", {}) or {}

    # 1. 感知
    if perception.get("perception_report") or perception.get("anomaly_screen"):
        path = save_path / "1_perception"
        path.mkdir(exist_ok=True)
        _write(path / "report.md", perception.get("perception_report", ""))
        _write(path / "anomaly_screen.md", perception.get("anomaly_screen", ""))
        sections.append(_section("一、感知", [
            ("感知报告", perception.get("perception_report", "")),
            ("异常初筛", perception.get("anomaly_screen", "")),
        ]))

    # 2. 诊断
    if diagnosis.get("diagnosis_report") or diagnosis.get("rationale"):
        path = save_path / "2_diagnosis"
        path.mkdir(exist_ok=True)
        _write(path / "report.md", diagnosis.get("diagnosis_report", ""))
        _write(path / "severity.md",
               f"严重度：{diagnosis.get('severity', '')}\n置信度：{diagnosis.get('confidence', '')}\n\n"
               f"{diagnosis.get('rationale', '')}")
        sections.append(_section("二、诊断", [
            ("诊断报告", diagnosis.get("diagnosis_report", "")),
            ("严重度与依据",
             f"严重度：{diagnosis.get('severity', '')}；置信度：{diagnosis.get('confidence', '')}\n\n"
             f"{diagnosis.get('rationale', '')}"),
        ]))

    # 3. 协调决策
    if coordination.get("coordination_notes") or coordination.get("work_order"):
        path = save_path / "3_coordination"
        path.mkdir(exist_ok=True)
        _write(path / "decisions.md", coordination.get("coordination_history", ""))
        _write(path / "work_order.md", coordination.get("work_order", ""))
        _write(path / "safety_review.md", coordination.get("safety_review", ""))
        _write(path / "rollback_plan.md", coordination.get("rollback_plan", ""))
        sections.append(_section("三、协调决策", [
            ("中枢决策记录", coordination.get("coordination_history", "")),
            ("维护工单", coordination.get("work_order", "")),
            ("安全检查", coordination.get("safety_review", "")),
            ("审批", coordination.get("approval", "")),
            ("回滚预案", coordination.get("rollback_plan", "")),
        ]))

    # 4. 执行
    if execution.get("dispatch_log") or execution.get("execution_report"):
        path = save_path / "4_execution"
        path.mkdir(exist_ok=True)
        _write(path / "dispatch_log.md", execution.get("dispatch_log", ""))
        _write(path / "feedback.md", execution.get("execution_report", ""))
        sections.append(_section("四、执行", [
            ("下发记录", execution.get("dispatch_log", "")),
            ("执行反馈", execution.get("execution_report", "")),
        ]))

    # 5. 决策单
    if final_state.get("final_decision"):
        sections.insert(0, _section("维护决策单", [("", final_state.get("final_decision", ""))]))

    complete = save_path / "complete_report.md"
    header = f"# 维护决策报告：{site_id}\n\n基准日期：{final_state.get('as_of', '')}\n案件状态：{final_state.get('case_status', '')}\n"
    complete.write_text(header + "\n\n".join(sections) + "\n", encoding="utf-8")
    return complete


def _write(path: Path, text: str) -> None:
    if text:
        path.write_text(text, encoding="utf-8")


def _section(title: str, parts: list[tuple[str, str]]) -> str:
    body = []
    for name, text in parts:
        if not text:
            continue
        body.append(f"### {name}\n{text}" if name else text)
    return f"## {title}\n\n" + "\n\n".join(body)
