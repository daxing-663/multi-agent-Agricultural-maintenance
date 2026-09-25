"""提示词接线自检：四个 Agent 的正式提示词是否真的送到模型手里。

这类测试盯的是**接线**而不是文案：上游证据（感知报告、工单、审批记录、补采要求）
必须出现在系统提示词里。消息通道在节点之间会被清空，如果证据只留在 state 里
没有注入提示词，模型就只能靠猜——这属于最难在真实运行中发现的缺陷。

同时用录制型 LLM 断言：提示词里的关键约束（不越界、不发明、按单执行）在位。
"""

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from agriagents.agents.coordinator.coordinator import (
    SYSTEM_PROMPT as COORDINATOR_PROMPT,
    create_coordinator,
)
from agriagents.agents.diagnosis.diagnosis_agent import (
    SYSTEM_PROMPT as DIAGNOSIS_PROMPT,
    create_diagnosis_agent,
)
from agriagents.agents.execution.executor import (
    SYSTEM_PROMPT as EXECUTOR_PROMPT,
    build_system_prompt as build_executor_prompt,
    create_executor,
)
from agriagents.agents.perception.perception_agent import (
    SYSTEM_PROMPT as PERCEPTION_PROMPT,
    create_perception_agent,
)

import pytest

pytestmark = pytest.mark.unit


class RecordingLLM(Runnable):
    """记录最后一次调用收到的提示词；不联网、不调结构化输出。"""

    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        # 让节点走「自由文本」回退路径，本测试只关心提示词内容
        raise AttributeError("测试替身不支持结构化输出")

    def invoke(self, input, config=None, **kwargs):
        self.prompts.append(_to_text(input))
        return AIMessage(content="[recording] 提示词已记录")


def _to_text(value) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "to_messages"):
        return "\n".join(message.content for message in value.to_messages())
    if isinstance(value, list):
        return "\n".join(getattr(item, "content", str(item)) for item in value)
    return str(value)


def _state(**overrides) -> dict:
    state = {
        "messages": [],
        "site_of_interest": "FIELD-07",
        "site_type": "field",
        "as_of": "2026-09-26",
        "site_context": "本次维护的地块编号为 FIELD-07。作物：番茄。",
        "resource_context": "",
        "past_context": "",
        "perception_state": {
            "perception_report": "10cm 墒情连续 3 天低于参考基线，叶片出现水渍状斑点。",
            "anomaly_screen": "**异常等级:** medium\n**异常通道:** sensor, vision",
            "anomaly_level": "medium",
            "has_anomaly": True,
            "perception_rounds": 1,
            "pending_requests": "",
        },
        "diagnosis_state": {
            "diagnosis_report": "疑似真菌性病害，置信度 medium。",
            "severity": "medium",
            "confidence": "medium",
            "rationale": "叶片斑点 + 高湿环境。",
            "recommended_checks": "取样镜检。",
            "diagnosis_history": "",
            "diagnosis_rounds": 1,
            "pending_request": "",
        },
        "coordination_state": {
            "next_action": "",
            "decision_reason": "",
            "coordination_notes": "",
            "work_order": "**工单号:** WO-FIELD-07-2026-09-26\n**任务分解:** 1) 定向灌溉 20 分钟",
            "safety_review": "",
            "safety_verdict": "",
            "approval": "人工审批：通过；审批人=张工；意见=注意晚间作业",
            "approved": True,
            "rollback_plan": "",
            "coordination_history": "",
            "coordinator_rounds": 0,
            "safety_rounds": 0,
            "rollback_rounds": 0,
        },
        "execution_state": {
            "dispatch_log": "",
            "execution_status": "",
            "execution_report": "",
            "needs_followup": False,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key] = {**state[key], **value}
        else:
            state[key] = value
    return state


def test_perception_prompt_receives_pending_request():
    llm = RecordingLLM()
    node = create_perception_agent(llm)
    state = _state(perception_state={"pending_requests": "补取 08-20 前后的 30cm 墒情通道"})

    node(state)

    prompt = llm.prompts[0]
    assert "补取 08-20 前后的 30cm 墒情通道" in prompt
    assert "FIELD-07" in prompt
    assert "2026-09-26" in prompt


def test_diagnosis_prompt_carries_upstream_evidence():
    """回归：感知报告与初筛曾经只算不用，诊断 Agent 在提示词里看不到证据。"""
    llm = RecordingLLM()
    node = create_diagnosis_agent(llm)

    node(_state())

    prompt = llm.prompts[0]
    assert "水渍状斑点" in prompt, "感知报告必须注入诊断提示词"
    assert "异常等级" in prompt, "异常初筛必须注入诊断提示词"
    assert "本轮上游证据" in prompt


def test_diagnosis_prompt_reports_absent_evidence_explicitly():
    llm = RecordingLLM()
    node = create_diagnosis_agent(llm)
    state = _state(perception_state={"perception_report": "", "anomaly_screen": ""})

    node(state)

    prompt = llm.prompts[0]
    assert "没有感知报告" in prompt, "缺失必须是显式缺失，不能留空"


def test_executor_prompt_carries_work_order_and_approval():
    """回归：工单正文曾经只算不用，执行 Agent 在提示词里看不到工单。"""
    llm = RecordingLLM()
    node = create_executor(llm)

    node(_state())

    prompt = llm.prompts[0]
    assert "WO-FIELD-07-2026-09-26" in prompt, "工单必须注入执行提示词"
    assert "审批人=张工" in prompt, "审批记录必须注入执行提示词"
    assert "演练" in prompt, "默认 dry_run 时必须写明是演练模式"


def test_executor_refuses_without_approval():
    llm = RecordingLLM()
    node = create_executor(llm)
    state = _state(coordination_state={"approved": False, "approval": ""})

    result = node(state)

    assert result["execution_state"]["execution_status"] == "failed"
    assert "未获批准" in result["execution_state"]["dispatch_log"]
    assert llm.prompts == [], "未获批准时不得调用模型下发指令"


def test_executor_prompt_gate_wording():
    """演练放行、审批缺失、受控自动放行，三种情形必须给出不同的提示词口径。"""
    common = dict(
        as_of="2026-09-26",
        site_context="FIELD-07",
        tool_names="dispatch_command",
        work_order="WO-FIELD-07-2026-09-26",
    )

    dry = build_executor_prompt(**common, approval="", dry_run=True, require_human_approval=True)
    assert "按 dry_run 放行" in dry, "演练模式下应明确这是可留痕的放行，而不是拒绝执行"

    missing = build_executor_prompt(**common, approval="", dry_run=False, require_human_approval=True)
    assert "按未批准处理" in missing, "审批缺失时必须按未批准处理"

    automated = build_executor_prompt(**common, approval="", dry_run=False, require_human_approval=False)
    assert "受控自动化放行" in automated


def test_coordinator_prompt_lists_actions_and_evidence():
    llm = RecordingLLM()
    node = create_coordinator(llm)

    node(_state())

    prompt = llm.prompts[0]
    for action in ("perceive_more", "diagnose_more", "plan_work_order", "close"):
        assert action in prompt, f"中枢提示词必须列出 {action}"
    assert "水渍状斑点" in prompt, "感知证据必须进入中枢决策视野"
    assert "疑似真菌性病害" in prompt, "诊断结论必须进入中枢决策视野"


def test_coordinator_prompt_marks_exhausted_budget():
    llm = RecordingLLM()
    node = create_coordinator(llm)
    state = _state(coordination_state={"coordinator_rounds": 99})

    node(state)

    assert "轮次上限" in llm.prompts[0], "预算耗尽时必须写进提示词"


def test_prompts_keep_their_hard_boundaries():
    """四个提示词各自的硬边界，防止后续编辑把约束删掉。"""
    assert "不下诊断结论" in PERCEPTION_PROMPT
    assert "缺失即缺失" in PERCEPTION_PROMPT

    assert "只能引用知识库原文" in DIAGNOSIS_PROMPT
    assert "假设 → 排除 → 收敛" in DIAGNOSIS_PROMPT

    assert "安全优先" in COORDINATOR_PROMPT
    assert "也不要在证据不足时宣布" in COORDINATOR_PROMPT

    assert "严格按单执行" in EXECUTOR_PROMPT
    assert "参数缺失不猜" in EXECUTOR_PROMPT
    assert "仍要走完下发与回读流程" in EXECUTOR_PROMPT
