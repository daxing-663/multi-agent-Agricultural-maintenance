"""工具层自检：合成演示数据源的契约与安全边界。

盯三件事：

1. **确定性**：同一 (对象, 通道, 日期) 必须给出同一个值，否则复盘与回归测试无从谈起；
2. **链路真的通**：工具经供应商路由返回可读数值与现象，而不是 ``[stub]`` 占位；
3. **不允许静默降级**：安全规则取不到时必须报错；执行台账必须区分演练与实发。
"""

import re

import pytest

from agriagents.agents.tools import (
    check_safety_policy,
    dispatch_command,
    get_execution_status,
    get_sensor_readings,
)
from agriagents.dataflows.config import run_config
from agriagents.dataflows.errors import NoDataError
from agriagents.dataflows.vendors import synthetic

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


def test_synthetic_readings_are_deterministic_and_labelled():
    first = synthetic.get_sensor_readings("FIELD-07", "soil_moisture", "2026-09-19", "2026-09-26")
    second = synthetic.get_sensor_readings("FIELD-07", "soil_moisture", "2026-09-19", "2026-09-26")
    assert first == second, "同一窗口两次调用必须完全一致"
    assert "[synthetic_demo]" in first and "合成演示数据" in first, "必须自曝身份"

    other = synthetic.get_sensor_readings("FIELD-08", "soil_moisture", "2026-09-19", "2026-09-26")
    assert other != first, "不同对象的序列应当不同"


def test_synthetic_source_rejects_bad_window():
    with pytest.raises(NoDataError):
        synthetic.get_sensor_readings("FIELD-07", "soil_moisture", "2026/09/19", "2026-09-26")
    with pytest.raises(NoDataError):
        synthetic.get_sensor_readings("FIELD-07", "soil_moisture", "2026-09-26", "2026-09-19")
    with pytest.raises(NoDataError):
        synthetic.get_sensor_readings("FIELD-07", "soil_moisture", "2024-01-01", "2026-09-26")


def test_tool_returns_numeric_series_through_vendor_chain(tmp_path):
    """工具 → 供应商路由 → 合成数据源：拿到的必须是可读数值。"""
    with run_config(_config(tmp_path)):
        text = get_sensor_readings.invoke({
            "site_id": "FIELD-07",
            "channel": "soil_moisture",
            "start_date": "2026-09-19",
            "end_date": "2026-09-26",
        })

    assert "未接入真实数据源" not in text, "默认配置不应再落到占位供应商"
    values = [float(m) for m in re.findall(r"^\d{4}-\d{2}-\d{2}\s+([\d.]+)", text, re.M)]
    assert len(values) == 8, f"应返回 8 天读数，实际 {len(values)}"
    assert values[-1] < values[0] - 3, "演示场景是持续失墒，端点差应大于 3 个百分点"
    assert "20.0 ~ 30.0" in text, "应给出演示参考区间，供感知 Agent 对比"


def test_safety_rules_flag_forbidden_materials():
    text = synthetic.check_safety_policy("喷施毒死蜱", "毒死蜱 乳油 500 mL")
    assert "存在禁止性冲突" in text
    assert "R-101" in text
    assert synthetic.SAFETY_RULES_VERSION in text, "裁决必须能指回规则库版本"


def test_safety_stub_never_silently_passes(tmp_path):
    """只配占位供应商时，安全检查必须报错而不是返回一段"看过了"的文本。"""
    config = _config(tmp_path)
    config["data_vendors"] = {**config["data_vendors"], "safety_policy": "stub_ops"}

    with run_config(config), pytest.raises(NoDataError):
        check_safety_policy.invoke({"action": "喷施生物农药 X", "materials": "生物农药 X 12 L"})


def test_actuator_ledger_distinguishes_dry_run_from_live(tmp_path):
    params = '{"duration_min": 20}'

    with run_config(_config(tmp_path, dry_run=True)):
        dry = dispatch_command.invoke({
            "site_id": "FIELD-07",
            "actuator": "irrigation",
            "command": "start",
            "params": params,
            "work_order_id": "WO-FIELD-07-DEMO",
        })
    assert "dry-run" in dry and "未下发" in dry

    with run_config(_config(tmp_path, dry_run=False)):
        live = dispatch_command.invoke({
            "site_id": "FIELD-07",
            "actuator": "irrigation",
            "command": "start",
            "params": params,
            "work_order_id": "WO-FIELD-07-DEMO",
        })
        task_id = re.search(r"task_id=(\S+)", live)
        assert task_id, f"实发模式应返回任务编号：{live}"
        status = get_execution_status.invoke({
            "site_id": "FIELD-07", "task_id": task_id.group(1)
        })
    assert "回执" in status and "合成" in status

    ledger = tmp_path / "cache" / "actuator_ledger" / "FIELD-07.jsonl"
    assert ledger.exists(), "下发必须留痕"
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_site_context_uses_demo_profile(tmp_path):
    from agriagents.graph.agri_graph import AgriAgentsGraph

    graph = AgriAgentsGraph(config=_config(tmp_path))
    state, _ = graph.propagate("FIELD-07", "2026-09-26")

    assert "番茄" in state["site_context"], "演示档案应把作物写进对象上下文"
    assert "1.2 亩" in state["site_context"]

