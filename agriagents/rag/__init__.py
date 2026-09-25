"""RAG 子系统：本地知识库检索，供诊断 Agent 查证而不靠模型记忆。

对外只暴露三样东西：

``KnowledgeBase``
    一站式门面：加载索引、图谱、术语表，提供检索与结构化查询。
``get_knowledge_base()``
    按配置构造并缓存实例。数据源适配器用它，不直接碰底层。
``KB_UNAVAILABLE_HINT``
    索引缺失时给模型看的提示文案。

分层的理由与 ``dataflows`` 一致：``rag`` 只负责"把知识找出来"，
"找出来之后怎么用、失败怎么回退"是 ``dataflows/vendors`` 的事。
两边的边界是 ``Doc`` 与 ``Hit`` 这两个数据结构。
"""

from __future__ import annotations

from agriagents.rag.kb import KnowledgeBase, get_knowledge_base, reset_cache
from agriagents.rag.index import Doc, Hit, RagIndex
from agriagents.rag.kg import CropDpKg, KgEntity, SymptomMatch
from agriagents.rag.normalize import TermNormalizer, TermRecord

KB_UNAVAILABLE_HINT = (
    "本地知识库未构建。请先运行："
    "python tools/build_rag_index.py --all"
)

__all__ = [
    "Doc",
    "Hit",
    "RagIndex",
    "CropDpKg",
    "KgEntity",
    "SymptomMatch",
    "TermNormalizer",
    "TermRecord",
    "KnowledgeBase",
    "get_knowledge_base",
    "reset_cache",
    "KB_UNAVAILABLE_HINT",
]
