"""本地 RAG 知识库适配器：把 ``agriagents.rag`` 的检索能力接进供应商路由。

这是知识类工具的真实数据源，替换 ``stub_knowledge`` 的占位实现。
注册方式见 ``dataflows/router.py`` 的 ``VENDOR_METHODS``，
启用方式见 ``default_config.py`` 的 ``data_vendors["agronomy_knowledge"]``。

**安全边界**（本框架最重要的一条约定，在此重申）：
本层是唯一允许给出"处置建议"的数据源。Agent 只能引用这里返回的方案，
不允许自行发明药剂与用量。因此：

- 查不到方案时返回明确的"未收录"，**绝不**用相邻病害的方案顶替；
- 安全间隔期、最大用药次数、禁用情形若知识库没有，就标注缺失，
  并提示上游转人工——这些字段缺失时放行，等于让模型替人签字。

土壤档案按地块主键、设备手册按型号或设备标识与故障码同时匹配。
演示记录明确标示为模拟数据，未知对象抛 ``NoDataError``，不返回相邻对象的档案。
"""

from __future__ import annotations

from agriagents.dataflows.errors import NoDataError, VendorNotConfiguredError
from agriagents.rag import KB_UNAVAILABLE_HINT, get_knowledge_base
from agriagents.rag.kb import KnowledgeBase

VENDOR_NAME = "local_rag"

# 渲染时截断的长度上限，防止把整卡塞进提示词把上下文撑爆
_TEXT_LIMIT = 520
_PEST_CANDIDATES = 3
_PEST_EXTRA_EVIDENCE = 3


def _kb():
    """取知识库；不可用时抛 ``VendorNotConfiguredError`` 触发链回退。"""
    kb = get_knowledge_base()
    if not kb.available:
        raise VendorNotConfiguredError(f"{VENDOR_NAME}：{KB_UNAVAILABLE_HINT}")
    return kb


def _clip(text: str, limit: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _origin(item: dict) -> str:
    """每一段证据都可回到来源与文档主键，模拟数据不可冒充实测。"""
    reference = item.get("source_ref") or item.get("citation") or item.get("dataset") or "未提供外部引用"
    demo = "【演示/模拟数据，非现场实测，不可直接用于生产】 " if item.get("demo") else ""
    status = "【公开来源摘要，未经本项目专家审核】 " if item.get("evidence_status") == "source_summary" else ""
    return f"{demo}{status}source={item.get('source', '')}；doc_id={item.get('doc_id', '')}；出处={reference}"


def query_agronomy_knowledge(question: str) -> str:
    """查询农艺知识库（栽培、水肥、生育期）。走混合检索 + 跨语言改写。"""
    kb = _kb()
    hits = kb.agronomy(question, top_k=3)
    if not hits:
        raise NoDataError(question, vendor=VENDOR_NAME, detail="知识库无相关条目")

    lines = [f"[{VENDOR_NAME}] 农艺知识检索：{question}", f"命中 {len(hits)} 条："]
    for index, hit in enumerate(hits, 1):
        flag = "【上游标注已验证】" if hit["verified"] else ""
        lines.append(f"\n{index}. {flag}{hit['title']}")
        lines.append(f"   {_origin(hit)}（命中方式 {hit['how']}）")
        if hit.get("crop_scope") == "general_agronomy":
            lines.append("   适用范围：通用农艺资料，尚未验证对所问作物的适用性。")
        if hit.get("usage"):
            lines.append("   使用约束：" + hit["usage"])
        lines.append("   " + _clip(hit["text"]).replace("\n", "\n   "))
    lines.append(
        "\n说明：以上为知识库原文或标有来源的摘要。「上游标注已验证」不代表人工或本项目专家审核。"
        "条目冲突时须标注分歧并核对适用范围与原始来源。"
    )
    return "\n".join(lines)


def query_pest_disease_library(crop: str, symptom: str) -> str:
    """查询病虫害图谱库：候选病害、鉴别要点、发生条件、防治方向。

    两路证据合并：CropDP-KG 图谱给结构化候选，向量检索补文本证据。
    """
    kb = _kb()
    result = kb.diagnose(crop, symptom, top_k=_PEST_CANDIDATES)
    candidates = result["candidates"]
    if not candidates and not result["evidence"]:
        raise NoDataError(f"{crop}/{symptom}", vendor=VENDOR_NAME, detail="图谱与检索均无命中")

    lines = [f"[{VENDOR_NAME}] 病虫害查询：作物={crop or '未指定'}，症状={symptom}", ""]

    if candidates:
        lines.append(f"■ 候选病害（按证据检索排序，共 {len(candidates)} 条）")
        for index, item in enumerate(candidates, 1):
            linked = "已关联英文防治卡片" if item["linked_card"] else "无关联卡片"
            lines.append(
                f"\n{index}. {item['disease']}"
                + (f"（{item['scientific_name']}）" if item["scientific_name"] else "")
                + f"  检索排序分 {item['score']:.4f}  [{linked}]"
            )
            lines.append("   " + _origin(item))
            for support in item.get("supporting_evidence", [])[:2]:
                if support.get("doc_id") != item.get("doc_id"):
                    lines.append("   交叉证据：" + _origin(support))
            if item["matched_symptoms"]:
                lines.append("   命中的症状：" + "；".join(item["matched_symptoms"][:3]))
            if item["parts"]:
                lines.append("   危害部位：" + "、".join(item["parts"][:8]))
            if item["crops"]:
                lines.append("   危害作物：" + "、".join(item["crops"][:8]))
            if item["conditions"]:
                lines.append("   适宜发生条件：" + "；".join(item["conditions"][:5]))
            if item["temps"]:
                lines.append("   适宜温度：" + "、".join(item["temps"][:5]))
            if item["regions"]:
                lines.append("   主要分布：" + "、".join(item["regions"][:8]))
            if item["aliases"]:
                lines.append("   英文名/别名：" + "；".join(item["aliases"][:5]))
    else:
        lines.append("■ 候选病害：当前知识库未给出匹配，不得据此排除病害。")

    if result["evidence"]:
        evidence = result["evidence"][:_PEST_EXTRA_EVIDENCE]
        lines.append(f"\n■ 关键补充证据（{len(evidence)} 条；其余按需定向检索）")
        for item in evidence:
            lines.append(f" - {_origin(item)}\n   {item['title']}：{_clip(item['text'], 220)}")

    lines.append(
        "\n置信提示：检索排序分用于融合图谱和文本证据的名次，不是诊断概率或诊断结论。"
        "必须结合现场影像、环境条件与鉴别要点复核后再定级。"
    )
    return "\n".join(lines)


def _render_knowledge_hits(hits, header: str, *, limit: int = 700) -> str:
    """把检索结果渲染成统一的文本块。"""
    lines = [header]
    for index, hit in enumerate(hits, 1):
        doc = hit.doc
        origin = _origin(KnowledgeBase._provenance(doc))
        lines.append(f"\n{index}. {doc.title}")
        lines.append(f"   {origin}（命中方式 元数据精确匹配）")
        lines.append("   " + _clip(doc.text, limit).replace("\n", "\n   "))
    return "\n".join(lines)


def query_soil_reference(site_id: str) -> str:
    """查询地块土壤本底档案（pH / 有机质 / EC / 质地 / 施肥历史）。

    只接受显式 site_id/site_ids；不返回通用模板或相邻地块。
    """
    kb = _kb()
    hits = kb.soil_reference(site_id, top_k=3)
    if not hits:
        raise NoDataError(site_id, vendor=VENDOR_NAME, detail="知识库未收录该地块的土壤档案")

    header = f"[{VENDOR_NAME}] 土壤本底档案：{site_id}"
    return _render_knowledge_hits(hits, header)


def query_equipment_manual(device_id: str, fault_code: str) -> str:
    """查询设备手册与故障处置指南（按故障码检索）。"""
    kb = _kb()
    hits = kb.equipment_manual(device_id, fault_code, top_k=3)
    if not hits:
        raise NoDataError(
            f"{device_id}/{fault_code}", vendor=VENDOR_NAME,
            detail="知识库未收录该设备或故障码",
        )
    return _render_knowledge_hits(
        hits, f"[{VENDOR_NAME}] 设备手册：{device_id} / {fault_code}"
    )


def get_treatment_options(diagnosis: str, crop: str = "") -> str:
    """查询某诊断对应的可选处置方案及其约束。

    这是执行层动作合法性的唯一来源。返回的文本里会显式标注
    哪些必填字段（安全间隔期/用量/禁用情形）知识库没有收录。
    """
    kb = _kb()
    result = kb.treatments(diagnosis, crop=crop, top_k=4)
    if not result["found"]:
        # 关键：不给"相近病害"的方案。宁可上游转人工，也不能给错药。
        raise NoDataError(
            diagnosis, vendor=VENDOR_NAME,
            detail=result.get("reason", "") + "；不得据此推断，应转人工评估",
        )

    lines = [f"[{VENDOR_NAME}] 处置方案查询：{diagnosis}，作物={crop or '由唯一适用范围确定'}", ""]
    for index, plan in enumerate(result["plans"], 1):
        lines.append(f"■ 方案 {index}：{plan['disease']}" + (f"（作物 {plan['crop']}）" if plan["crop"] else ""))
        lines.append(f"  {_origin(plan)}（{plan['how']}）")
        if plan.get("fields_missing"):
            lines.append("  原文缺失字段：" + "、".join(plan["fields_missing"]))
        lines.append("  " + _clip(plan["text"], 650).replace("\n", "\n  "))

    card = result.get("card") or {}
    if card:
        lines.append("\n■ 关联卡片摘要")
        for key, label in (
            ("pathogen_type", "病害类型"),
            ("crop", "作物"),
            ("pathogen_scientific", "病原学名"),
        ):
            if card.get(key):
                lines.append(f"  {label}：{card[key]}")

    lines.append(
        "\n⚠ 字段完整性：知识库收录的是防治**方向**（栽培/生物/化学措施），"
        "通常**不含**具体用药量、安全间隔期与禁用情形。"
        "这些字段缺失时不得由模型补全；涉及化学药剂须转人工确认后方可执行。"
    )
    return "\n".join(lines)
