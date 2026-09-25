"""构建 RAG 知识库索引。

用法：

    # 全量构建（首次，需联网下载语料与嵌入模型）
    python tools/build_rag_index.py --all

    # 只重建图谱与卡片，跳过两个问答集
    python tools/build_rag_index.py --sources cropdp,plantinquiry

    # 不下载嵌入模型，只建 BM25 索引（离线环境 / 只想快速试一下）
    python tools/build_rag_index.py --all --no-embed

    # 看当前索引状态 / 看有哪些源
    python tools/build_rag_index.py --status
    python tools/build_rag_index.py --list

    # 构建完跑一遍自检查询，确认真的能检索到东西
    python tools/build_rag_index.py --all --probe

产物落在 ``~/.agriagents/rag/``（可用 AGRIAGENTS_RAG_INDEX_DIR 改）：

- ``corpus.jsonl``  知识块，一行一条，人类可读可 diff
- ``kg.json``       CropDP-KG 图谱（实体 → 症状/作物/部位/条件/分布）
- ``terms.json``    多语言术语表（实体标准化用）
- ``vectors.npy``   稠密向量，行序与 corpus 一致；纯 BM25 时不存在
- ``manifest.json`` 元信息

原始语料缓存在 ``~/.agriagents/rag/raw/``，重复构建不会重新下载。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agriagents.rag.config import rag_config
from agriagents.rag.embedder import get_embedder
from agriagents.rag.index import RagIndex
from agriagents.rag.kg import CropDpKg
from agriagents.rag.normalize import (
    TermNormalizer,
    TermRecord,
    enrich_with_agrovoc,
    from_disease_cards,
    from_knowledge_graph,
)
from agriagents.rag.sources import (
    DEFAULT_ORDER,
    FULL_ORDER,
    SOURCES,
    IngestContext,
    describe_all,
    resolve,
)
from agriagents.rag.sources import cropdp, plantinquiry


def _log(message: str = "") -> None:
    print(message, flush=True)


def build(args: argparse.Namespace) -> int:
    settings = rag_config()
    index_dir = args.index_dir or settings["rag_index_dir"]
    data_dir = args.data_dir or settings["rag_data_dir"]

    modules = resolve(args.sources)
    ctx = IngestContext(data_dir=data_dir, config=settings, limit=args.limit, force=args.force, log=_log)

    _log(f"索引目录：{index_dir}")
    _log(f"语料缓存：{data_dir}")
    _log(f"语料源　：{', '.join(m.NAME for m in modules)}")
    _log("")

    started = time.time()
    all_docs = []
    artifacts: dict = {}
    summary: list[str] = []

    for module in modules:
        _log(f"[{module.NAME}] {module.DESCRIPTION}")
        source_started = time.time()
        try:
            result = module.build(ctx)
        except Exception as exc:  # noqa: BLE001 - 单个源失败不该毁掉整次构建
            _log(f"  ✗ 失败：{exc!r}")
            _log("    其余语料源继续构建。")
            summary.append(f"{module.NAME}: 失败 {exc!r}")
            continue
        all_docs.extend(result.docs)
        artifacts.update(result.artifacts)
        elapsed = time.time() - source_started
        _log(f"  ✓ {len(result.docs)} 条文档（{elapsed:.1f}s）")
        for note in result.notes:
            _log(f"    · {note}")
        summary.append(f"{module.NAME}: {len(result.docs)} 条")
        _log("")

    if not all_docs and not args.sources:
        _log("没有产出任何文档；检查网络与语料源。")
        return 1

    # ---- 术语标准化：图谱与卡片是离线主力，AGROVOC 是联网增强 ----
    _log("[标准化] 构建多语言术语表")
    records = []
    kg = artifacts.get("kg")
    if kg is not None:
        records.extend(from_knowledge_graph(kg))
        _log(f"  · 来自 CropDP-KG：{len(kg.entities)} 个实体")
    for row in artifacts.get("seed_terms") or []:
        records.append(TermRecord(
            term_id=row["id"], pref=row["pref"],
            labels=row.get("labels") or {}, sources=["seed"],
        ))
    if records and artifacts.get("seed_terms"):
        _log(f"  · 来自内置术语表：{len(artifacts['seed_terms'])} 条")

    cards = artifacts.get("cards")
    if cards:
        records.extend(from_disease_cards(cards))
        _log(f"  · 来自 PlantInquiryVQA：{len(cards)} 张卡片")

    if records and args.agrovoc:
        cache_path = os.path.join(data_dir, "agrovoc_cache.json")

        def _progress(done: int, total: int) -> None:
            if done % 20 == 0:
                _log(f"    … AGROVOC {done}/{total}")

        stats = enrich_with_agrovoc(
            records,
            cache_path=cache_path,
            limit=args.agrovoc_limit,
            time_budget=args.agrovoc_budget,
            progress=_progress,
        )
        _log(
            f"  · AGROVOC：查询 {stats['attempted']} 词，命中 {stats['merged']}，"
            f"未收录 {stats['missed']}，网络错误 {stats['errors']}，缓存累计 {stats['cached']}"
        )
        if stats.get("timed_out"):
            _log("    （达到时长上限提前收工；AGROVOC 端点较慢，属正常）")
        elif stats["errors"] >= 5:
            _log("    （AGROVOC 连续报错，判定为网络不可达，已提前退出；不影响构建）")
    else:
        _log("  · 跳过 AGROVOC（默认关闭；加 --agrovoc 启用在线增强）")

    normalizer = TermNormalizer(records)
    _log(f"  ✓ 术语 {normalizer.stats()['terms']} 条，索引标签 {normalizer.stats()['labels']} 个")

    # ---- 嵌入 ----
    embedder = None
    if args.no_embed:
        _log("\n[嵌入] 已按 --no-embed 跳过，仅建 BM25 索引")
    else:
        _log("\n[嵌入] 加载本地嵌入模型（首次会下载，约 200 MB）")
        _log("  提示：国内直连 huggingface.co 常超时，配置里默认走 hf-mirror 镜像")
        embed_started = time.time()
        try:
            embedder = get_embedder(settings, allow_download=True)
            if embedder is not None:
                _log(f"  ✓ {embedder.name}，维度 {embedder.dim}（{time.time() - embed_started:.1f}s）")
            else:
                _log("  · 无法构建嵌入后端，退化为纯 BM25 检索")
        except Exception as exc:  # noqa: BLE001 - 没有嵌入也要能建索引
            _log(f"  ✗ 嵌入后端不可用：{exc!r}")
            _log("    继续构建纯 BM25 索引；装了 fastembed 后重跑即可补上向量。")
            embedder = None

    # ---- 建索引 ----
    _log(f"\n[索引] 编码 {len(all_docs)} 条文档")
    last = {"at": 0.0}

    def _index_progress(done: int, total: int) -> None:
        now = time.time()
        if now - last["at"] > 2.0 or done >= total:
            last["at"] = now
            _log(f"    … {done}/{total}")

    index = RagIndex.build(all_docs, embedder, progress=_index_progress)
    os.makedirs(index_dir, exist_ok=True)
    index.save(index_dir)
    _log(f"  ✓ 索引已落盘：{index_dir}")

    # ---- 图谱与术语表 ----
    if kg is not None:
        kg.save(index_dir)
        _log(f"  ✓ 图谱已落盘：{kg.stats()}")
    if normalizer.records:
        normalizer.save(index_dir)
        _log(f"  ✓ 术语表已落盘")

    _log("")
    _log("=" * 62)
    _log(f"完成，用时 {time.time() - started:.1f}s")
    for line in summary:
        _log(f"  · {line}")
    for key, value in index.profile().items():
        _log(f"  · {key}: {value}")
    _log("=" * 62)

    if args.probe:
        probe(index_dir, embedder_name=index.embedder_name)
    return 0


def probe(index_dir: str, *, embedder_name: str | None = None) -> None:
    """自检：跑几条典型查询，确认索引真的能用。"""
    from agriagents.rag.kb import KnowledgeBase, load_knowledge_base

    _log("\n[自检] 加载刚构建的索引并试查")
    kb = load_knowledge_base({"rag_index_dir": index_dir})
    info = kb.describe()
    _log(f"  · 文档 {info.get('index', {}).get('documents')}，稠密检索={'开' if info['dense'] else '关'}")
    if info.get("embedder_error"):
        _log(f"  · 嵌入说明：{info['embedder_error']}")

    cases = [
        ("病虫害查询（中文症状）", lambda: kb.diagnose("番茄", "叶片出现水渍状斑点，随后扩大为褐色病斑", top_k=3)),
        ("病虫害查询（英文）", lambda: kb.diagnose("tomato", "water-soaked spots on leaves", top_k=3)),
        ("农艺问答", lambda: kb.agronomy("番茄如何防治晚疫病", top_k=3)),
        ("处置方案", lambda: kb.treatments("番茄晚疫病", crop="番茄", top_k=2)),
    ]
    for label, case in cases:
        try:
            result = case()
        except Exception as exc:  # noqa: BLE001
            _log(f"  ✗ {label}：{exc!r}")
            continue
        if "candidates" in result:
            names = [c["disease"] for c in result["candidates"]]
            _log(f"  ✓ {label}：候选 {names or '（无）'}；证据 {len(result['evidence'])} 条")
        elif "plans" in result:
            names = [p["disease"] for p in result["plans"]]
            _log(f"  ✓ {label}：found={result['found']}；{'、'.join(names) or '（无）'}")
        else:
            _log(f"  ✓ {label}：命中 {len(result)} 条" + (f"；首条 = {result[0]['title'][:60]}" if result else ""))


def status(args: argparse.Namespace) -> int:
    settings = rag_config()
    index_dir = args.index_dir or settings["rag_index_dir"]
    if not os.path.isdir(index_dir):
        _log(f"索引目录不存在：{index_dir}")
        _log("先运行：python tools/build_rag_index.py --all")
        return 1
    from agriagents.rag.kb import load_knowledge_base

    kb = load_knowledge_base({"rag_index_dir": index_dir})
    import json

    for key, value in kb.describe().items():
        if isinstance(value, dict):
            _log(f"{key}:")
            for sub_key, sub_value in value.items():
                _log(f"  {sub_key}: {sub_value}")
        else:
            _log(f"{key}: {value}")
    return 0


def list_sources() -> int:
    _log("可用语料源（--sources 接受的名字）：")
    for item in describe_all():
        _log(f"\n  {item['name']}")
        _log(f"    {item['description']}")
        _log(f"    来源：{item['homepage']}")
        _log(f"    许可：{item['license']}")
    _log(f"\n默认顺序：{', '.join(DEFAULT_ORDER)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建 AgriAgents 的 RAG 知识库索引",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--all", action="store_true", help="构建全部语料源（含两个大型问答集）")
    parser.add_argument(
        "--lean", action="store_true",
        help="精简构建：只建 CropDP-KG 图谱 + PlantInquiryVQA 卡片（约 4500 条，几分钟完成）",
    )
    parser.add_argument("--sources", type=str, default="", help=f"逗号分隔；默认 {','.join(DEFAULT_ORDER)}")
    parser.add_argument("--limit", type=int, default=None, help="每个语料源的文档数上限（调试用）")
    parser.add_argument("--no-embed", action="store_true", help="不建向量，只做 BM25 检索")
    parser.add_argument(
        "--agrovoc", action="store_true",
        help="启用 AGROVOC 在线术语增强（默认关闭：SPARQL 端点很慢，且离线术语表已够用）",
    )
    parser.add_argument("--agrovoc-limit", type=int, default=60, help="AGROVOC 最多查询多少个词（默认 60）")
    parser.add_argument("--agrovoc-budget", type=float, default=180.0, help="AGROVOC 阶段总时长上限（秒）")
    parser.add_argument("--force", action="store_true", help="重新下载语料（默认复用缓存）")
    parser.add_argument("--index-dir", type=str, default=None, help="索引输出目录")
    parser.add_argument("--data-dir", type=str, default=None, help="原始语料缓存目录")
    parser.add_argument("--probe", action="store_true", help="构建后跑自检查询")
    parser.add_argument("--status", action="store_true", help="只看当前索引状态")
    parser.add_argument("--list", action="store_true", help="列出可用语料源")

    args = parser.parse_args()

    if args.list:
        return list_sources()
    if args.status:
        return status(args)

    if args.lean:
        args.sources = [plantinquiry.NAME, cropdp.NAME]
    elif args.all:
        args.sources = list(FULL_ORDER)
    elif args.sources:
        args.sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    else:
        args.sources = list(DEFAULT_ORDER)

    try:
        return build(args)
    except KeyError as exc:
        _log(f"参数错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
