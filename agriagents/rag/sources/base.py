"""语料源适配器约定。

每个源模块暴露同名的一组模块级常量与一个 ``build(ctx)``：
``NAME`` / ``DESCRIPTION`` / ``HOMEPAGE`` / ``LICENSE`` / ``build``。

新增一个知识源＝新增一个文件 ＋ 在 ``sources/__init__.py`` 的 ``SOURCES``
里登记一行。不允许在 build 脚本里写 ``if source == ...`` 的分支——
那样加源就要改三处，迟早漏。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Iterator

from agriagents.rag.fetch import (
    hf_download_parquet,
    hf_iter_rows,
    iter_parquet,
)
from agriagents.rag.index import Doc


@dataclass
class IngestContext:
    """一次 ingest 的运行上下文，由 build 脚本注入。"""

    data_dir: str                 # 原始下载物料的落盘目录（可复用，增量构建）
    config: dict = field(default_factory=dict)
    limit: int | None = None      # 每个源的文档上限；None 表示不设限
    force: bool = False           # 忽略已下载文件，重新拉取
    log: Callable[[str], None] = print


@dataclass
class SourceResult:
    """一个源的产出：文档 ＋ 供下游复用的结构化产物。"""

    docs: list[Doc] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def dedupe_docs(docs: Iterator[Doc] | list[Doc], *, max_chars: int = 2000) -> list[Doc]:
    """按 ``text`` 前缀去重并截断。

    公开问答集里重复率高得离谱——同一个问题被改写十几种问法是常态。
    全部入索引只会让 top_k 被近重复条目占满，挤掉真正互补的证据。
    """
    seen: set[str] = set()
    out: list[Doc] = []
    for doc in docs:
        text = (doc.text or "").strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars]
            doc.text = text
        key = text[:400]
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def load_hf_rows(
    ctx: IngestContext,
    dataset: str,
    *,
    config: str = "default",
    split: str = "train",
    limit: int | None = None,
    columns: list[str] | None = None,
) -> tuple[Iterator[dict], str]:
    """取 HF 数据集的行，返回 ``(行迭代器, 模式说明)``。

    优先直连 parquet（快两个数量级），pyarrow 缺失或下载失败时
    回退到 datasets-server 分页（慢，但不需要额外依赖，也不需要下载整个文件）。

    这条回退路径是故意保留的：pyarrow 是个 28 MB 的可选依赖，
    不该让"只想跑一下 RAG"的人被它挡住。
    """
    parquet_path = os.path.join(
        ctx.data_dir, "hf", f"{dataset.replace('/', '__')}.{config}.{split}.parquet"
    )
    rows: Iterator[dict]
    mode: str
    try:
        hf_download_parquet(dataset, parquet_path, config=config, split=split, force=ctx.force)
        rows = iter_parquet(parquet_path, columns=columns)
        mode = f"parquet:{os.path.getsize(parquet_path) // 1024}KB"
    except ImportError as exc:
        ctx.log(f"    · 缺少 pyarrow，改用分页端点（慢）：{exc}")
        rows = hf_iter_rows(dataset, split, config=config, limit=limit)
        mode = "datasets-server"
    except Exception as exc:  # noqa: BLE001 - 网络/镜像问题不该让整源失败
        ctx.log(f"    · parquet 直连失败，改用分页端点：{exc}")
        rows = hf_iter_rows(dataset, split, config=config, limit=limit)
        mode = "datasets-server"

    def _limited() -> Iterator[dict]:
        for index, row in enumerate(rows):
            if limit is not None and index >= limit:
                return
            yield row

    return _limited(), mode
