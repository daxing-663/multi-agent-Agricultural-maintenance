"""农艺知识适配器（占位）：病虫害库、土壤档案、设备手册、处置方案。

TODO(内容): 接入知识库（可先用本地文档 + 检索，再逐步替换为专业库）。
本层是**唯一**允许给出"处置建议"的数据源，Agent 只能引用它给出的方案，
不允许自行发明药剂与用量——这是安全边界，不是风格问题。
"""

from __future__ import annotations

VENDOR_NAME = "stub_knowledge"
_PENDING = "未接入真实数据源（框架占位）。"


def query_agronomy_knowledge(question: str) -> str:
    """查询农艺知识库（栽培、水肥、生育期）。"""
    return f"[{VENDOR_NAME}] 农艺知识查询：{question}\n{_PENDING}"


def query_pest_disease_library(crop: str, symptom: str) -> str:
    """查询病虫害图谱库。"""
    return (
        f"[{VENDOR_NAME}] 病虫害查询：作物={crop}，症状={symptom}\n{_PENDING}\n"
        "契约：返回候选病害（含鉴别要点与置信提示）、发生条件、防治方案与安全间隔期。"
    )


def query_soil_reference(site_id: str) -> str:
    """查询地块土壤本底档案。"""
    return f"[{VENDOR_NAME}] 土壤档案 @ {site_id}\n{_PENDING}"


def query_equipment_manual(device_id: str, fault_code: str) -> str:
    """查询设备手册与故障处置指南。"""
    return f"[{VENDOR_NAME}] 设备手册：{device_id} / {fault_code}\n{_PENDING}"


def get_treatment_options(diagnosis: str) -> str:
    """查询某诊断对应的可选处置方案及其约束。"""
    return (
        f"[{VENDOR_NAME}] 处置方案查询：{diagnosis}\n{_PENDING}\n"
        "契约：每条方案必须带适用条件、用量、安全间隔期与禁用情形。"
    )
