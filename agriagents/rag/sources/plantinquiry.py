"""PlantInquiryVQA 疾病知识卡片：结构化、带防治方案的英文知识源。

仓库：https://github.com/syed-nazmus-sakib/PlantInquiryVQA
文件：``diseases_knowledge_base/all_cards.jsonl``（203 张卡，52 种作物）

这套卡片的价值在于它是**唯一带防治方案**的来源——CropDP-KG 只有
症状/条件/分布，没有"该打什么药、什么时候不能打"。
而框架里"处置建议只能来自知识库、不允许 Agent 自创"这条安全边界，
必须有人提供真实方案才能落地，否则执行层永远拿不到合法输入。

卡片还自带 ``severity_rubric``（mild/moderate/severe 三级判定标准），
正好对接框架的 5 档处置等级：严重度不是让模型拍脑袋，而是按面积占比判定。

一张卡拆成 6 类文档而非整卡入库：整卡动辄两三千字符，
向量会被平均成一团，"叶子有褐色斑点"和"用什么药"会落到几乎同一个向量上。
按语义面拆开，检索精度才有保证。同一张卡的文档共享 ``parent_id``，
调用方可以按需回捞整卡。
"""

from __future__ import annotations

import os

from agriagents.rag.fetch import download, read_jsonl
from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult

NAME = "plantinquiry"
DESCRIPTION = "PlantInquiryVQA 疾病知识卡片（症状/防治/严重度分级/鉴别要点，英文）"
HOMEPAGE = "https://github.com/syed-nazmus-sakib/PlantInquiryVQA"
LICENSE = "见上游仓库"

_URL = (
    "https://raw.githubusercontent.com/syed-nazmus-sakib/PlantInquiryVQA/main/"
    "diseases_knowledge_base/all_cards.jsonl"
)

# 症状按部位拆开。部位名同时进中文，方便中文查询命中——
# 这一步是"轻量本地化"：不翻译正文，只给检索锚点补中文同义词。
_PART_LABELS = {
    "leaves": "叶片",
    "stems": "茎秆",
    "fruit": "果实",
    "roots": "根系",
    "whole_plant": "整株",
    "signs_microscopic_or_visible": "病征（镜检/可见）",
}


def fetch_cards(ctx: IngestContext) -> str:
    dest = os.path.join(ctx.data_dir, "plantinquiry", "all_cards.jsonl")
    existed = os.path.exists(dest)
    download(_URL, dest, force=ctx.force)
    if not existed:
        ctx.log("    ↓ all_cards.jsonl")
    return dest


def load_cards(ctx: IngestContext) -> list[dict]:
    return list(read_jsonl(fetch_cards(ctx)))


def build(ctx: IngestContext) -> SourceResult:
    cards = load_cards(ctx)
    if not cards:
        raise ValueError("PlantInquiryVQA 未读取到任何卡片")
    docs: list[Doc] = []
    for card in cards:
        if ctx.limit is not None and len(docs) >= ctx.limit:
            break
        docs.extend(card_docs(card))
    return SourceResult(
        docs=docs[:ctx.limit] if ctx.limit is not None else docs,
        artifacts={"cards": cards},
        notes=[f"卡片 {len(cards)} 张，文档 {len(docs)} 条"],
        details={"cards": len(cards), "source_ref": _URL},
    )


def card_docs(card: dict) -> list[Doc]:
    """把一张卡片拆成若干语义面文档。"""
    disease_id = card.get("disease_id")
    if not isinstance(disease_id, str) or not disease_id.strip():
        raise ValueError("PlantInquiryVQA 卡片缺少 disease_id")
    crop = card.get("crop") or {}
    condition = card.get("condition") or {}
    pathogen = condition.get("pathogen") or {}

    crop_name = crop.get("common_name") or ""
    disease_name = condition.get("common_name") or disease_id
    sci_name = condition.get("scientific_name") or ""
    issue_type = card.get("issue_type") or ""

    base_meta = {
        "dataset": "PlantInquiryVQA",
        "source_ref": HOMEPAGE + "/blob/main/diseases_knowledge_base/all_cards.jsonl",
        "citation": f"PlantInquiryVQA, disease_id={disease_id}",
        "record_id": disease_id,
        "schema_version": card.get("schema_version", ""),
        "evidence_status": "public_disease_card",
        "project_human_verified": False,
        "disease_id": disease_id,
        "disease": disease_name,
        "crop": crop_name,
        "crop_scientific": crop.get("scientific_name") or "",
        "crop_family": crop.get("family") or "",
        "pathogen_scientific": sci_name,
        "pathogen_type": pathogen.get("type") or issue_type,
        "aliases": card.get("aliases") or [],
    }
    # 疾病名 + 作物名都塞进正文：BM25 靠字面命中，正文里没有"tomato"
    # 就永远召不回番茄相关卡片。
    header = f"{crop_name} / {disease_name}（{sci_name}）"

    docs: list[Doc] = []

    def add(kind: str, suffix: str, body: str, extra: dict | None = None) -> None:
        body = (body or "").strip()
        if not body:
            return
        meta = dict(base_meta)
        if extra:
            meta.update(extra)
        docs.append(
            Doc(
                doc_id=f"plantinquiry:{disease_id}:{suffix}",
                source=NAME,
                kind=kind,
                title=f"{crop_name}·{disease_name}" if crop_name else disease_name,
                lang="en",
                parent_id=f"plantinquiry:{disease_id}",
                text=f"{header}\n{body}",
                meta=meta,
            )
        )

    taxonomy = pathogen.get("taxonomy") or {}
    taxonomy_line = " > ".join(
        str(taxonomy[key]) for key in ("kingdom", "phylum", "class", "order", "family", "genus", "species")
        if taxonomy.get(key)
    )
    add(
        "disease_card",
        "card",
        "\n".join(
            x for x in [
                "别名：" + "；".join(card.get("aliases") or []) if card.get("aliases") else "",
                f"病害类型：{issue_type}",
                f"病原分类：{taxonomy_line}" if taxonomy_line else "",
            ] if x
        ),
    )

    # ---- 症状：按部位各成一条，检索时能精确定位到"果实上有什么表现" ----
    symptoms = card.get("symptoms") or {}
    for part, items in symptoms.items():
        if not items:
            continue
        label = _PART_LABELS.get(part, part)
        add(
            "symptom",
            f"symptom_{part}",
            f"{label}症状：\n" + "\n".join(f"- {item}" for item in items),
            {"part": part, "part_zh": label},
        )

    # ---- 防治：这是框架安全边界依赖的核心内容 ----
    management = card.get("management") or {}
    management_lines: list[str] = []
    for key, label in (("cultural", "栽培措施"), ("biological", "生物防治"), ("chemical", "化学防治")):
        items = management.get(key) or []
        if items:
            management_lines.append(f"{label}：\n" + "\n".join(f"- {item}" for item in items))
    if management.get("notes"):
        management_lines.append(f"注意事项：{management['notes']}")
    add("management", "management", "\n".join(management_lines))

    # ---- 严重度分级：对接框架的 5 档处置等级，判定依据是面积占比而非主观判断 ----
    rubric = card.get("severity_rubric") or {}
    severity_lines = [
        f"严重度判定（单位：{rubric.get('unit', '未标注')}）",
        f"- 轻度 mild：{rubric.get('mild', '')}" if rubric.get("mild") else "",
        f"- 中度 moderate：{rubric.get('moderate', '')}" if rubric.get("moderate") else "",
        f"- 重度 severe：{rubric.get('severe', '')}" if rubric.get("severe") else "",
        f"判定说明：{rubric.get('notes', '')}" if rubric.get("notes") else "",
    ]
    add("severity", "severity", "\n".join(x for x in severity_lines if x))

    # ---- 鉴别要点：诊断 Agent 最需要的一段，直接决定它能否排除近缘病害 ----
    lookalikes = card.get("lookalikes") or []
    if lookalikes:
        blocks = []
        for item in lookalikes:
            diffs = item.get("key_differences") or []
            blocks.append(
                f"易混淆：{item.get('condition_name', '')}\n"
                + "\n".join(f"- {d}" for d in diffs)
            )
        add(
            "diagnosis",
            "lookalikes",
            "鉴别诊断要点：\n" + "\n".join(blocks),
            {"lookalikes": [x.get("condition_id") for x in lookalikes]},
        )

    # ---- 发生条件：给感知 Agent 判断"当前环境是否具备发病条件"用 ----
    env = card.get("environmental_risk") or {}
    env_lines = []
    if env.get("risk_factors"):
        env_lines.append("风险因子：" + "；".join(env["risk_factors"]))
    for key, label in (
        ("temp_c_day", "适宜温度（昼）"),
        ("temp_c_night", "适宜温度（夜）"),
        ("relative_humidity_pct", "适宜相对湿度%"),
    ):
        values = env.get(key) or []
        if values:
            env_lines.append(f"{label}：{values[0]}–{values[-1]}" if len(values) > 1 else f"{label}：{values[0]}")
    if env.get("leaf_wetness_hours_threshold"):
        env_lines.append(f"叶面湿润时长阈值：{env['leaf_wetness_hours_threshold']} 小时")

    transmission = card.get("transmission") or {}
    if transmission.get("dispersal"):
        env_lines.append("传播途径：" + "；".join(transmission["dispersal"]))
    if transmission.get("overwintering"):
        env_lines.append("越冬场所：" + "；".join(transmission["overwintering"]))
    add("environment", "environment", "\n".join(env_lines), {"environmental_risk": env})

    return docs
