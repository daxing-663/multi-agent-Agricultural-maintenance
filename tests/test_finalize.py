"""收口语义自检：没走完的流程不能冒充结论。

真实运行里出现过这种情况：中枢连着两轮要求补诊断、又被安全检查打回，
最后被轮次预算兜底收口——如果这时把中枢的处置预判渲染成决策单，
一份"从未执行、也没走完审批"的单子会看起来像已经定案（例如「立即处置」）。
本文件盯住这条边界。
"""

import pytest

from agriagents.agents.coordinator.finalize import create_close_case_node
from agriagents.agents.rating import RATING_REVIEW, extract_disposition, parse_disposition

pytestmark = pytest.mark.unit


def _state(*, next_action: str, notes: str, work_order: str = "", safety: str = "",
           reason: str = "证据充分，进入工单规划。") -> dict:
    return {
        "site_of_interest": "FIELD-07",
        "perception_state": {"anomaly_screen": "**异常等级:** medium"},
        "coordination_state": {
            "next_action": next_action,
            "decision_reason": reason,
            "coordination_notes": notes,
            "work_order": work_order,
            "safety_review": safety,
        },
    }


def test_normal_close_keeps_coordinator_disposition():
    node = create_close_case_node()
    result = node(_state(next_action="close", notes="**处置预判:** 观察\n\n本轮无异常需处置。"))

    assert result["case_status"] == "closed_no_action"
    assert "观察" in result["final_decision"]
    assert parse_disposition(result["final_decision"]) == "Monitor"


def test_forced_close_is_marked_review():
    """中枢要出工单、却被兜底收口时，绝不能渲染成「立即处置」这种定案结论。"""
    node = create_close_case_node()
    work_order = "**工单号:** WO-FIELD-07-20260926\n**任务分解:** 1) 复检叶背霉层"
    result = node(_state(
        next_action="plan_work_order",
        notes="**处置预判:** 立即处置\n\n证据充分。",
        work_order=work_order,
        safety="**安全裁决:** revise",
    ))

    text = result["final_decision"]
    assert "REVIEW（待人工复核）" in text
    assert "流程未走完" in text
    assert "WO-FIELD-07-20260926" in text, "已起草的工单必须留给人去复核"
    assert "立即处置" not in text, "强制收口不得冒充某一档处置结论"
    assert parse_disposition(text) == RATING_REVIEW
    assert result["case_status"] == "closed_no_action"


def test_forced_close_review_label_beats_embedded_urgency_words():
    """回归：复核模板内嵌了中枢「应尽快处置」的原话，等级仍必须解析为 REVIEW。

    真实运行里的表现：标签行「**处置等级：** REVIEW（待人工复核）」被正则匹配上，
    但词表里没有 review，解析退回全文扫描，命中理由里的「尽快」→ 决策单被读成
    「尽快处置」。一条没走完的流程，就这样长成了已经定案的样子。
    """
    node = create_close_case_node()
    result = node(_state(
        next_action="plan_work_order",
        notes="**处置预判:** 立即处置\n\n证据充分。",
        reason="两轮补检仍未取得叶背霉层证据，异常等级 medium，应尽快处置。",
        safety="**安全裁决:** revise",
    ))

    text = result["final_decision"]
    assert "REVIEW（待人工复核）" in text
    assert "应尽快处置" in text, "理由原话要留给人看，但它只是理由"
    assert extract_disposition(text) == RATING_REVIEW, "理由里的等级词不得盖过标签行"
    assert parse_disposition(text) == RATING_REVIEW


def test_graph_forced_close_yields_review(tmp_path, monkeypatch):
    """端到端：异常成立且中枢轮次预算为 0 ⇒ 路由兜底收口，决策单必须是 REVIEW。

    这是真实运行里踩到的那条路径——预算兜底收口时绝不能渲染成某一档处置结论。
    """
    from langchain_core.runnables import Runnable

    from agriagents.agents.schemas import AnomalyScreenResult
    from agriagents.graph import agri_graph as agri_graph_module
    from agriagents.llm import StubChatModel

    class _FixedStructured(Runnable):
        """把结构化调用固定成给定实例。"""

        def __init__(self, instance):
            super().__init__()
            self._instance = instance

        def invoke(self, input, config=None, **kwargs):
            return self._instance

    class _AnomalyStub(StubChatModel):
        """桩模型：异常初筛固定 medium，其余节点沿用默认桩值。"""

        def with_structured_output(self, schema, **kwargs):
            if schema is AnomalyScreenResult:
                return _FixedStructured(AnomalyScreenResult(
                    has_anomaly=True,
                    anomaly_level="medium",
                    channels=["sensor"],
                    summary="桩：存在需跟进的异常",
                    evidence="桩证据：墒情偏低",
                    uncertainty="无",
                ))
            return super().with_structured_output(schema, **kwargs)

    monkeypatch.setattr(agri_graph_module, "create_llm", lambda *args, **kwargs: _AnomalyStub())

    from agriagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config.update({
        "llm_provider": "stub",
        "require_human_approval": False,
        "dry_run": True,
        "max_coordination_rounds": 0,
        "results_dir": str(tmp_path / "logs"),
        "data_cache_dir": str(tmp_path / "cache"),
        "memory_log_path": str(tmp_path / "memory" / "log.md"),
    })

    graph = agri_graph_module.AgriAgentsGraph(config=config)
    state, disposition = graph.propagate("FIELD-07", "2026-09-26", site_type="field")

    assert state["coordination_state"]["next_action"] != "close"
    assert "REVIEW（待人工复核）" in state["final_decision"]
    assert state["case_status"] == "closed_no_action"
    assert parse_disposition(state["final_decision"]) == RATING_REVIEW
