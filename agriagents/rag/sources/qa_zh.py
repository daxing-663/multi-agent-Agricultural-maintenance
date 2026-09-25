"""中文农业问答语料：中文农林牧渔问答数据集。

数据集：``Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery``
字段：``id`` / ``prompt`` / ``response``，训练集 **92.7 万条**、172 MB。

**为什么要过滤而不是全量入库**：这是"农林牧渔"四业合集，
大量条目讲的是养猪、水产、林业采伐——对设施作物维护是纯噪声。
全量入索引的后果很具体：检索"番茄叶子发黄"时 top_k 会被一批
语义相近但领域无关的条目占满，把真正有用的挤出去。

**为什么走 parquet 直连**：92.7 万条按 datasets-server 每页 100 条
要 9278 次请求。直连下载 172 MB 后本地扫一遍只要几十秒。
"""

from __future__ import annotations

import hashlib

from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult, dedupe_docs, load_hf_rows

NAME = "qa_zh"
DESCRIPTION = "中文农林牧渔问答数据集（按设施作物相关性过滤后的子集）"
HOMEPAGE = "https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery"
LICENSE = "见上游数据集卡片"

_DATASET = "Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery"

# 本课题关心的作物。命中即强相关。
_CROPS = (
    "番茄", "西红柿", "黄瓜", "辣椒", "茄子", "草莓", "生菜", "白菜", "甘蓝", "芹菜",
    "菠菜", "油菜", "西瓜", "甜瓜", "哈密瓜", "南瓜", "丝瓜", "苦瓜", "冬瓜", "豆角",
    "豇豆", "菜豆", "豌豆", "马铃薯", "土豆", "洋葱", "大葱", "大蒜", "韭菜", "萝卜",
    "胡萝卜", "生姜", "葡萄", "柑橘", "橙子", "苹果", "梨树", "桃树", "樱桃", "香蕉",
    "荔枝", "龙眼", "芒果", "茶树", "烟叶", "食用菌", "蘑菇", "香菇", "平菇", "花卉",
    "月季", "菊花", "兰花", "水稻", "小麦", "玉米", "大豆", "花生", "棉花", "高粱",
)

# 植保与设施栽培术语。单个命中只算弱证据，需要累积。
_PLANT_TERMS = (
    "病害", "虫害", "病虫害", "防治", "农药", "杀菌剂", "杀虫剂", "除草剂", "生物防治",
    "施肥", "追肥", "基肥", "叶面肥", "有机肥", "化肥", "氮肥", "磷肥", "钾肥",
    "灌溉", "滴灌", "喷灌", "浇水", "墒情", "土壤", "盐碱", "板结", "连作", "轮作",
    "大棚", "温室", "日光温室", "拱棚", "育苗", "定植", "移栽", "嫁接", "整枝",
    "打杈", "疏花", "疏果", "授粉", "落花", "落果", "徒长", "缓苗", "蹲苗",
    "叶片", "根系", "茎秆", "果实", "花蕾", "叶斑", "黄化", "枯萎", "腐烂", "霉层",
)

# 强信号词：只有植保词累积但没有这类词时，多半是泛泛而谈的农事问答
_STRONG_TERMS = ("病虫害", "病害", "防治", "农药", "大棚", "温室", "育苗", "嫁接", "连作障碍")

_MIN_TERM_HITS = 2


def build(ctx: IngestContext) -> SourceResult:
    accept_limit = ctx.limit or ctx.config.get("rag_ingest_limit_qa_zh") or 6000
    scan_limit = ctx.config.get("rag_ingest_scan_zh")   # None = 全量扫描

    rows, mode = load_hf_rows(ctx, _DATASET, limit=scan_limit)
    docs: list[Doc] = []
    scanned = 0
    for row in rows:
        scanned += 1
        doc = _row_to_doc(row)
        if doc is not None:
            docs.append(doc)
        if scanned % 100000 == 0:
            ctx.log(f"    … 已扫描 {scanned} 行，合格 {len(docs)} 条")
        if len(docs) >= accept_limit:
            break

    before = len(docs)
    docs = dedupe_docs(docs)
    rate = (before / scanned * 100) if scanned else 0.0
    return SourceResult(
        docs=docs,
        notes=[
            f"扫描 {scanned} 行，合格 {before} 条（命中率 {rate:.1f}%），去重后 {len(docs)} 条（{mode}）",
            "未命中作物或植保词的条目（养殖/林业/水产）已丢弃",
        ],
    )


def _relevance(text: str) -> int:
    """相关性打分：命中作物名直接判强相关，否则靠植保词累积。"""
    hits = sum(1 for crop in _CROPS if crop in text)
    if hits:
        return 10 + hits
    terms = sum(1 for term in _PLANT_TERMS if term in text)
    if terms >= _MIN_TERM_HITS and any(term in text for term in _STRONG_TERMS):
        return terms
    return 0


def _row_to_doc(row: dict) -> Doc | None:
    question = (row.get("prompt") or row.get("question") or "").strip()
    answer = (row.get("response") or row.get("answer") or "").strip()
    if not question or not answer or len(question) + len(answer) < 40:
        return None

    blob = question + " " + answer
    relevance = _relevance(blob)
    if relevance == 0:
        return None

    # 命中的作物名写进 meta，既是检索锚点也供调用方按作物过滤
    crops = [crop for crop in _CROPS if crop in blob]
    raw_id = str(row.get("id") or "").strip()
    return Doc(
        doc_id=f"qa_zh:{raw_id}" if raw_id else f"qa_zh:{_stable_key(question)}",
        source=NAME,
        kind="qa_pair",
        title=question[:120],
        lang="zh",
        text=f"问：{question}\n答：{answer}",
        meta={"crops": crops[:6], "relevance": relevance},
    )


def _stable_key(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()
