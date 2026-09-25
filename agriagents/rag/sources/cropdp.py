"""CropDP-KG：中文植物病害/虫害知识图谱（中英双语关系表）。

仓库：https://github.com/dadadaray/CropDP-KG
真正的数据在 ``Dataset`` 分支——``main`` 分支只有一个空 README，
如果按仓库首页去 clone，会以为这是个空仓库。

产出两类东西：
1. ``KgEntity`` 图谱对象（供 ``rag.kg`` 做结构化查询）
2. ``kg_entity`` 文档（供混合检索召回）

图谱与文档**都要**：前者回答"晚疫病有哪些症状"（要完整、要确定），
后者回答"叶子背面有白毛可能是啥"（要模糊匹配、要排序）。
"""

from __future__ import annotations

import os

from agriagents.rag.fetch import download
from agriagents.rag.index import Doc
from agriagents.rag.kg import CropDpKg
from agriagents.rag.sources.base import IngestContext, SourceResult
from agriagents.rag.text import chunk

NAME = "cropdp"
DESCRIPTION = "CropDP-KG 中文作物病虫害知识图谱（症状/作物/部位/条件/温度/分布，中英双语）"
HOMEPAGE = "https://github.com/dadadaray/CropDP-KG"
LICENSE = "见上游仓库"

_BRANCH = "Dataset"
_RAW = f"https://raw.githubusercontent.com/dadadaray/CropDP-KG/{_BRANCH}/"
_FILES = (
    "named entity.csv",
    "relationEng.csv",
    "relationarea.csv",
    "relationcon.csv",
    "relationcrop.csv",
    "relationpart.csv",
    "relationsym.csv",
    "relationtem.csv",
)

# 单个实体的画像文本超过这个长度就切块。太长的块会让向量被平均掉，
# 检索时"哪一段都不够像"。
_CHUNK_CHARS = 600


def fetch_csvs(ctx: IngestContext) -> str:
    """下载关系表，返回本地目录。已存在的文件默认跳过（人肉可断点续传）。"""
    import urllib.parse

    target = os.path.join(ctx.data_dir, "cropdp")
    os.makedirs(target, exist_ok=True)
    for name in _FILES:
        url = _RAW + urllib.parse.quote(name)
        dest = os.path.join(target, name.replace(" ", "_"))
        before = os.path.exists(dest)
        download(url, dest, force=ctx.force)
        if not before:
            ctx.log(f"    ↓ {name}")
    return target


def build(ctx: IngestContext) -> SourceResult:
    csv_dir = fetch_csvs(ctx)
    kg = CropDpKg.from_csv_dir(csv_dir)
    docs = entity_docs(kg, limit=ctx.limit)
    return SourceResult(
        docs=docs,
        artifacts={"kg": kg, "csv_dir": csv_dir},
        notes=[f"实体 {len(kg.entities)} 个，症状 {len(kg._symptoms)} 条"],
    )


def entity_docs(kg: CropDpKg, *, limit: int | None = None) -> list[Doc]:
    """把每个实体渲染成可检索文档。"""
    docs: list[Doc] = []
    for index, entity in enumerate(kg.entities.values()):
        if limit is not None and len(docs) >= limit:
            break
        text = kg.profile_text(entity)
        pieces = chunk(text, max_chars=_CHUNK_CHARS, overlap=60)
        for part_index, piece in enumerate(pieces):
            docs.append(
                Doc(
                    doc_id=f"cropdp:{entity.name}:{part_index}",
                    source=NAME,
                    kind="kg_entity",
                    title=entity.name,
                    lang="zh" if _has_cjk(piece) else "en",
                    parent_id=f"cropdp:{entity.name}",
                    text=piece,
                    meta={
                        "entity": entity.name,
                        "scientific_name": entity.scientific,
                        "english": entity.aliases,
                        "crops": entity.crops[:20],
                        "parts": entity.parts[:20],
                        "conditions": entity.conditions[:20],
                        "temps": entity.temps[:10],
                        "regions": entity.regions[:20],
                        "symptom_count": len(entity.symptoms),
                        "seq": index,
                    },
                )
            )
    return docs


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
