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

土壤档案与设备手册还没有真实数据源，直接抛 ``NoDataError``
让供应商链回退到 ``stub_knowledge`` 的占位文案。这比返回一段
"看起来像真的"的假数据安全得多。
"""

from __future__ import annotations

from agriagents.dataflows.errors import NoDataError, VendorNotConfiguredError
from agriagents.rag import KB_UNAVAILABLE_HINT, get_knowledge_base

VENDOR_NAME = "local_rag"

# 渲染时截断的长度上限，防止把整卡塞进提示词把上下文撑爆
_TEXT_LIMIT = 900


def _kb():
    """取知识库；不可用时抛 ``VendorNotConfiguredError`` 触发链回退。"""
    kb = get_knowledge_base()
    if not kb.available:
        raise VendorNotConfiguredError(f"{VENDOR_NAME}：{KB_UNAVAILABLE_HINT}")
    return kb


def _clip(text: str, limit: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def query_agronomy_knowledge(question: str) -> str:
    """查询农艺知识库（栽培、水肥、生育期）。走混合检索 + 跨语言改写。"""
    kb = _kb()
    hits = kb.agronomy(question, top_k=5)
    if not hits:
        raise NoDataError(question, vendor=VENDOR_NAME, detail="知识库无相关条目")

    lines = [f"[{VENDOR_NAME}] 农艺知识检索：{question}", f"命中 {len(hits)} 条："]
    for index, hit in enumerate(hits, 1):
        flag = "【已人工核验】" if hit["verified"] else ""
        origin = hit["citation"] or hit["dataset"] or hit["source"]
        lines.append(f"\n{index}. {flag}{hit['title']}")
        lines.append(f"   出处：{origin}（命中方式 {hit['how']}）")
        lines.append("   " + _clip(hit["text"]).replace("\n", "\n   "))
    lines.append(
        "\n说明：以上为知识库检索原文。若条目之间说法冲突，"
        "以【已人工核验】的为准，并在结论中标注分歧。"
    )
    return "\n".join(lines)


def query_pest_disease_library(crop: str, symptom: str) -> str:
    """查询病虫害图谱库：候选病害、鉴别要点、发生条件、防治方向。

    两路证据合并：CropDP-KG 图谱给结构化候选，向量检索补文本证据。
    """
    kb = _kb()
    result = kb.diagnose(crop, symptom, top_k=5)
    candidates = result["candidates"]
    if not candidates and not result["evidence"]:
        raise NoDataError(f"{crop}/{symptom}", vendor=VENDOR_NAME, detail="图谱与检索均无命中")

    lines = [f"[{VENDOR_NAME}] 病虫害查询：作物={crop or '未指定'}，症状={symptom}", ""]

    if candidates:
        lines.append(f"■ 候选病害（按症状匹配度排序，共 {len(candidates)} 条）")
        for index, item in enumerate(candidates, 1):
            linked = "已关联英文防治卡片" if item["linked_card"] else "无关联卡片"
            lines.append(
                f"\n{index}. {item['disease']}"
                + (f"（{item['scientific_name']}）" if item["scientific_name"] else "")
                + f"  匹配度 {item['score']:.2f}  [{linked}]"
            )
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
        lines.append("■ 候选病害：图谱未给出匹配（图谱覆盖 1200+ 中文病虫害，可能该症状超出收录范围）")

    if result["evidence"]:
        lines.append(f"\n■ 检索到的补充证据（{len(result['evidence'])} 条）")
        for item in result["evidence"]:
            lines.append(f" - [{item['source']}] {item['title']}：{_clip(item['text'], 320)}")

    lines.append(
        "\n置信提示：匹配度基于症状字面与语义重合度，不是诊断结论。"
        "必须结合现场影像、环境条件与鉴别要点复核后再定级。"
    )
    return "\n".join(lines)


def _render_knowledge_hits(hits, header: str, *, limit: int = 700) -> str:
    """把检索结果渲染成统一的文本块。"""
    lines = [header]
    for index, hit in enumerate(hits, 1):
        doc = hit.doc
        origin = doc.meta.get("dataset") or doc.meta.get("label") or doc.source
        lines.append(f"\n{index}. {doc.title}")
        lines.append(f"   来源：{origin}（命中方式 {hit.how}）")
        lines.append("   " + _clip(doc.text, limit).replace("\n", "\n   "))
    return "\n".join(lines)


def query_soil_reference(site_id: str) -> str:
    """查询地块土壤本底档案（pH / 有机质 / EC / 质地 / 施肥历史）。

    知识库里目前只有**模板条目**（``is_template=True``）。模板会照常返回，
    但会在结果里显式标注"这是模板不是实测值"——土壤数值直接决定施肥量，
    拿模板值当实测值用，比查不到更危险。
    """
    kb = _kb()
    hits = kb.search(f"{site_id} 土壤档案 pH 有机质 EC 质地 施肥历史", top_k=3, kinds=("soil_reference",))
    if not hits:
        raise NoDataError(site_id, vendor=VENDOR_NAME, detail="知识库未收录该地块的土壤档案")

    header = f"[{VENDOR_NAME}] 土壤本底档案：{site_id}"
    rendered = _render_knowledge_hits(hits, header)
    if any(h.doc.meta.get("is_template") for h in hits):
        rendered += (
            "\n\n⚠ 上述条目包含**档案模板**，不是该地块的实测数据。"
            "施肥与灌溉决策必须使用最近一次现场取样检测的结果；"
            "模板值只能用来确认字段是否齐全。"
        )
    return rendered


def query_equipment_manual(device_id: str, fault_code: str) -> str:
    """查询设备手册与故障处置指南（按故障码检索）。"""
    kb = _kb()
    query = f"{device_id} {fault_code} 故障 处置 排查".strip()
    hits = kb.search(query, top_k=3, kinds=("equipment_manual",))
    if not hits:
        raise NoDataError(
            f"{device_id}/{fault_code}", vendor=VENDOR_NAME,
            detail="知识库未收录该设备或故障码",
        )
    return _render_knowledge_hits(
        hits, f"[{VENDOR_NAME}] 设备手册：{device_id} / {fault_code}"
    )


def get_treatment_options(diagnosis: str) -> str:
    """查询某诊断对应的可选处置方案及其约束。

    这是执行层动作合法性的唯一来源。返回的文本里会显式标注
    哪些必填字段（安全间隔期/用量/禁用情形）知识库没有收录。
    """
    kb = _kb()
    result = kb.treatments(diagnosis, top_k=4)
    if not result["found"]:
        # 关键：不给"相近病害"的方案。宁可上游转人工，也不能给错药。
        raise NoDataError(
            diagnosis, vendor=VENDOR_NAME,
            detail="知识库未收录该诊断的处置方案；不得据此推断，应转人工评估",
        )

    lines = [f"[{VENDOR_NAME}] 处置方案查询：{diagnosis}", ""]
    for index, plan in enumerate(result["plans"], 1):
        lines.append(f"■ 方案 {index}：{plan['disease']}" + (f"（作物 {plan['crop']}）" if plan["crop"] else ""))
        lines.append(f"  来源：PlantInquiryVQA 知识卡片（{plan['how']}）")
        lines.append("  " + _clip(plan["text"], 1200).replace("\n", "\n  "))

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
