"""技能包自检：每个 Agent 上岗时必须带着自己的作业手册。

技能包回答的是"具体怎么做"（取数配方、请求模板、停止条件），
和角色提示词一样参与真实运行——它丢了、读不出来、没进提示词，
都属于接线缺陷，必须在这里拦住。
"""

import pytest

from agriagents import skills

pytestmark = pytest.mark.unit


def test_all_skills_load_with_metadata():
    assert set(skills.SKILL_NAMES) == {"perception", "diagnosis", "coordinator", "execution"}

    for name in skills.SKILL_NAMES:
        meta = skills.skill_metadata(name)
        body = skills.load_skill(name)
        assert meta.get("description"), f"{name} 缺 description"
        assert meta.get("version"), f"{name} 缺 version（手册要能指回版本）"
        assert len(body) > 800, f"{name} 手册过短，像占位"

    assert "取数配方" in skills.load_skill("perception")
    assert "假设清单" in skills.load_skill("diagnosis")
    assert "动作速查表" in skills.load_skill("coordinator")
    assert "执行前核对清单" in skills.load_skill("execution")


def test_unknown_skill_name_rejected():
    with pytest.raises(ValueError):
        skills.load_skill("nope")


def test_missing_skill_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "SKILL_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        skills.load_skill("perception")


def test_loader_picks_up_edits(monkeypatch, tmp_path):
    """手册改完必须立刻生效（按 mtime+size 缓存，而不是进程内永久缓存）。"""
    target = tmp_path / "perception"
    target.mkdir()
    path = target / "SKILL.md"
    path.write_text("---\nname: perception\nversion: vtest\n---\n\n# 手册\n第一版", encoding="utf-8")

    monkeypatch.setattr(skills, "SKILL_ROOT", tmp_path)
    skills._cache.pop("perception", None)
    assert "第一版" in skills.load_skill("perception")

    path.write_text("---\nname: perception\nversion: vtest\n---\n\n# 手册\n第二版（改过）", encoding="utf-8")
    assert "第二版" in skills.load_skill("perception")


def test_skill_digest_tracks_content(monkeypatch, tmp_path):
    real_digest = skills.skill_digest()
    assert "perception" in real_digest and "missing" not in real_digest


def test_prompts_carry_their_skill_manifest():
    """四份系统提示词里必须能读到各自手册的正文，而不是只在磁盘上存在。"""
    from agriagents.agents.coordinator.coordinator import build_prompt
    from agriagents.agents.diagnosis.diagnosis_agent import build_system_prompt as diagnosis_prompt
    from agriagents.agents.execution.executor import build_system_prompt as execution_prompt
    from agriagents.agents.perception.perception_agent import build_system_prompt as perception_prompt

    assert "取数配方" in perception_prompt(
        as_of="2026-09-26", site_context="FIELD-07", tool_names="get_sensor_readings"
    )
    assert "假设清单" in diagnosis_prompt(
        as_of="2026-09-26",
        site_context="FIELD-07",
        tool_names="query_pest_disease_library",
        perception_report="（本轮没有感知报告：它是缺失。）",
        anomaly_screen="（本轮没有初筛报告：它是缺失。）",
    )
    assert "动作速查表" in build_prompt(
        site_context="FIELD-07",
        resource_context="资源账本：未提供。",
        perception_report="P",
        anomaly_screen="S",
        diagnosis_report="D",
        severity="medium",
        confidence="low",
        rationale="",
    )
    assert "执行前核对清单" in execution_prompt(
        as_of="2026-09-26",
        site_context="FIELD-07",
        tool_names="dispatch_command",
        work_order="WO-1",
        approval="人工审批：通过",
        dry_run=True,
        require_human_approval=True,
    )

