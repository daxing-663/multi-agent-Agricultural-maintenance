"""框架自检：不联网、不花钱，验证图能装配、能跑完、能落台账。

这类测试的价值在于**接线正确性**：节点名写错、条件边返回值没登记、
子状态漏键——这些问题在真实模型下才会以奇怪的方式暴露，
而离线桩模型能把它们一次性暴露出来。
"""

import json

import pytest

from agriagents.agents.rating import DISPOSITIONS_5_TIER, RATING_REVIEW
from agriagents.graph.agri_graph import AgriAgentsGraph

pytestmark = pytest.mark.unit


def _config(tmp_path, **overrides) -> dict:
    from agriagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config.update({
        "llm_provider": "stub",
        "require_human_approval": False,
        "dry_run": True,
        "results_dir": str(tmp_path / "logs"),
        "data_cache_dir": str(tmp_path / "cache"),
        "memory_log_path": str(tmp_path / "memory" / "log.md"),
    })
    config.update(overrides)
    return config


def test_graph_compiles_and_runs_end_to_end(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))
    state, disposition = graph.propagate("FIELD-07", "2026-09-20", site_type="field")

    assert state["final_decision"], "应当产出一份维护决策单"
    assert disposition in DISPOSITIONS_5_TIER + (RATING_REVIEW,)
    assert state["case_status"], "案件应当有收口状态"


def test_run_writes_report_tree_and_decision_log(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))
    state, _ = graph.propagate("FIELD-07", "2026-09-20")

    report = graph.save_reports(state, "FIELD-07")
    assert report.exists(), "报告树应当落盘"
    assert report.name == "complete_report.md"

    log_path = tmp_path / "memory" / "log.md"
    assert log_path.exists(), "决策台账应当写入一条记录"
    assert "[2026-09-20 | FIELD-07 |" in log_path.read_text(encoding="utf-8")


def test_decision_log_is_idempotent_for_same_day(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))
    graph.propagate("FIELD-07", "2026-09-20")
    graph.propagate("FIELD-07", "2026-09-20")

    entries = graph.memory_log.load_entries()
    assert len(entries) == 1, "同一对象同一天只应记录一次"
    assert entries[0]["pending"] is True


def test_settlement_requires_backfill(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))
    graph.propagate("FIELD-07", "2026-09-20")

    # 没有回填源时，条目保持 pending —— 不猜测结果
    assert graph.settle_pending("FIELD-07") == 0

    def fake_lookup(site_id, as_of):
        return {
            "resolved_date": "2026-09-27",
            "status": "success",
            "outcome": "作业按工单完成，复检指标正常。",
        }

    assert graph.settle_pending("FIELD-07", outcome_lookup=fake_lookup) == 1
    entry = graph.memory_log.load_entries()[0]
    assert entry["pending"] is False
    assert "2026-09-27" in entry["status"]


def test_as_of_point_in_time_contract(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))

    with pytest.raises(ValueError):
        graph.propagate("FIELD-07", "2999-01-01")   # 未来日期
    with pytest.raises(ValueError):
        graph.propagate("FIELD-07", "2026/09/20")   # 非规范格式


def test_resource_context_distinguishes_absent_from_empty(tmp_path):
    graph = AgriAgentsGraph(config=_config(tmp_path))

    state, _ = graph.propagate("FIELD-07", "2026-09-20")
    assert "未提供" in state["resource_context"], "没给账本时必须显式标注未知"

    state2, _ = graph.propagate(
        "FIELD-08", "2026-09-20",
        resources={"labor": "2 人", "materials": {"生物农药 X": "12 L"}},
    )
    assert "2 人" in state2["resource_context"]


def test_conditional_logic_branches(tmp_path):
    """路由分支单测：不必跑整张图就能覆盖每条边。"""
    from agriagents.graph.conditional_logic import ConditionalLogic
    from agriagents.graph.execution_plan import (
        APPROVAL_GATE, CLOSE_CASE, COORDINATOR, DIAGNOSIS_AGENT, EXECUTOR,
        FINALIZE, PERCEPTION_AGENT, ROLLBACK_PLANNER, WORK_ORDER_PLANNER,
    )

    logic = ConditionalLogic(max_coordination_rounds=3, max_safety_rounds=1, max_rollback_rounds=1)

    # 无异常 → 收口
    assert logic.after_anomaly_screen(
        {"perception_state": {"has_anomaly": False}}
    ) == CLOSE_CASE
    # 有异常 → 诊断
    assert logic.after_anomaly_screen(
        {"perception_state": {"has_anomaly": True}}
    ) == DIAGNOSIS_AGENT

    # 中枢要求补采集，且预算未用尽 → 回到感知
    assert logic.coordinator_next({
        "coordination_state": {"next_action": "perceive_more", "coordinator_rounds": 1},
        "perception_state": {"perception_rounds": 0},
        "diagnosis_state": {"diagnosis_rounds": 1},
    }) == PERCEPTION_AGENT

    # 预算用尽 → 就算要补采集也按出工单处理
    assert logic.coordinator_next({
        "coordination_state": {"next_action": "perceive_more", "coordinator_rounds": 1},
        "perception_state": {"perception_rounds": 1},
        "diagnosis_state": {"diagnosis_rounds": 1},
    }) == WORK_ORDER_PLANNER

    # 中枢轮次超预算 → 强制收口
    assert logic.coordinator_next({
        "coordination_state": {"next_action": "plan_work_order", "coordinator_rounds": 10},
        "perception_state": {"perception_rounds": 1},
        "diagnosis_state": {"diagnosis_rounds": 1},
    }) == CLOSE_CASE

    # 安全裁决路由
    assert logic.after_safety_review(
        {"coordination_state": {"safety_verdict": "pass", "safety_rounds": 0}}
    ) == APPROVAL_GATE
    assert logic.after_safety_review(
        {"coordination_state": {"safety_verdict": "revise", "safety_rounds": 0}}
    ) == WORK_ORDER_PLANNER
    assert logic.after_safety_review(
        {"coordination_state": {"safety_verdict": "revise", "safety_rounds": 1}}
    ) == COORDINATOR
    assert logic.after_safety_review(
        {"coordination_state": {"safety_verdict": "block", "safety_rounds": 0}}
    ) == COORDINATOR

    # 审批与执行
    assert logic.after_approval({"coordination_state": {"approved": True}}) == EXECUTOR
    assert logic.after_approval({"coordination_state": {"approved": False}}) == COORDINATOR
    assert logic.after_execution({
        "coordination_state": {"rollback_rounds": 0},
        "execution_state": {"execution_status": "failed"},
    }) == ROLLBACK_PLANNER
    assert logic.after_execution({
        "coordination_state": {"rollback_rounds": 1},
        "execution_state": {"execution_status": "failed"},
    }) == FINALIZE
    assert logic.after_execution({
        "coordination_state": {"rollback_rounds": 0},
        "execution_state": {"execution_status": "success"},
    }) == FINALIZE


def test_tools_are_registered_and_router_resolves(tmp_path):
    """每个工具都能在供应商路由表里解析到实现。"""
    from agriagents.dataflows.config import run_config
    from agriagents.dataflows.router import OPTIONAL_TOOLS, TOOL_CATEGORY, VENDOR_METHODS, resolve_vendor_chain
    from agriagents.agents.tools import (
        check_safety_policy, dispatch_command, get_camera_snapshot, get_device_status,
        get_execution_status, get_remote_sensing_index, get_sensor_readings,
        get_treatment_options, get_weather_forecast, query_agronomy_knowledge,
        query_equipment_manual, query_pest_disease_library, query_soil_reference,
        get_resource_inventory,
    )

    tools = [
        get_sensor_readings, get_weather_forecast, get_camera_snapshot, get_remote_sensing_index,
        get_device_status, query_agronomy_knowledge, query_pest_disease_library,
        query_soil_reference, query_equipment_manual, get_treatment_options,
        get_resource_inventory, check_safety_policy, dispatch_command, get_execution_status,
    ]
    config = _config(tmp_path)

    with run_config(config):
        for tool_item in tools:
            name = tool_item.name
            assert name in TOOL_CATEGORY, f"{name} 未登记类别"
            assert VENDOR_METHODS.get(name), f"{name} 未注册任何供应商实现"
            assert resolve_vendor_chain(name), f"{name} 解析出的供应商链为空"

    # 安全规则取不到时不能降级放行
    assert "check_safety_policy" not in OPTIONAL_TOOLS


def test_stub_run_never_touches_real_actuators(tmp_path):
    """干跑保护：默认配置下执行层不得下发真实指令。"""
    graph = AgriAgentsGraph(config=_config(tmp_path))
    state, _ = graph.propagate("FIELD-07", "2026-09-20")
    dispatch = state["execution_state"].get("dispatch_log", "")
    report = state["execution_state"].get("execution_report", "")
    assert "dry-run" in dispatch or "dry-run" in report or "未获批准" in dispatch or not dispatch
