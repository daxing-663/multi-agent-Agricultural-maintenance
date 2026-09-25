"""RAG 配置解析。

默认值定义在 ``agriagents/default_config.py``（项目约定：所有可调项集中在那里），
这里只负责把它解析成带兜底的字典——这样 ``rag`` 子系统在只拿到部分配置
（比如测试里手搓的 config）时也能正常工作，而不是 KeyError。
"""

from __future__ import annotations

import agriagents.default_config as default_config

# 本子系统读取的全部配置键。要新增可调项：先在 default_config 里加默认值，
# 再把键名加到这张表。
KEYS = (
    "rag_index_dir",
    "rag_data_dir",
    "rag_embedding_backend",
    "rag_embedding_model",
    "rag_model_cache_dir",
    "rag_hf_endpoint",
    "rag_top_k",
    "rag_candidate_k",
    "rag_ingest_limit_qa_zh",
    "rag_ingest_scan_zh",
)


def rag_config(config: dict | None = None) -> dict:
    """解析 RAG 配置。

    ``config`` 里显式给出的键优先，缺失的键回落到 ``DEFAULT_CONFIG``，
    再缺失就放弃该键（调用方用 ``.get`` 读取）。
    """
    base = getattr(default_config, "DEFAULT_CONFIG", {}) or {}
    if config is None:
        from agriagents.dataflows.config import get_config

        config = get_config()

    resolved: dict = {}
    for key in KEYS:
        if key in config and config[key] is not None:
            resolved[key] = config[key]
        elif key in base:
            resolved[key] = base[key]
    return resolved
