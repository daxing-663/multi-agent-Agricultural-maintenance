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
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path

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
from agriagents.rag.sources import cropdp, curated, local, plantinquiry, seed

_INDEX_FILES = frozenset({"corpus.jsonl", "manifest.json", "vectors.npy", "kg.json", "terms.json"})


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _link_or_copy(source: str, target: str) -> str:
    # 原始语料可很大。硬链接避免复制；缓存更新使用 replace，不改旧文件内容。
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def _publish(staged: Path, target: Path, build_id: str) -> Path | None:
    """同卷暂存、验证后切换；保留上一版目录，切换失败就恢复。

    raw 等非索引文件也保留，防止默认 raw 位于索引目录下时丢失缓存。
    两次目录 rename 间存在很短的不可见窗口，构建时应停止新查询。
    """
    if target.is_symlink():
        raise ValueError("索引目标不能是符号链接")
    target = target.resolve()
    staged = staged.resolve()
    if staged.parent != target.parent or not staged.name.startswith(f".{target.name}.build-"):
        raise ValueError("暂存目录必须是目标索引的受控同级目录")
    if target == target.parent or target.is_symlink():
        raise ValueError("索引目标不能是根目录或符号链接")
    backup = target.with_name(f".{target.name}.backup-{build_id}")
    if backup.exists() or backup.resolve().parent != target.parent:
        raise ValueError("备份路径无效或已存在")
    if target.exists():
        for item in target.iterdir():
            if item.name in _INDEX_FILES:
                continue
            destination = staged / item.name
            if item.is_dir():
                shutil.copytree(item, destination, copy_function=_link_or_copy, symlinks=True)
            elif item.is_symlink():
                raise ValueError(f"不自动迁移索引目录中的符号链接文件：{item}")
            else:
                _link_or_copy(str(item), str(destination))
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except BaseException:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    return backup if backup.exists() else None


def _validate_staged(path: Path, expected: int, *, dense: bool, kg=None, normalizer=None) -> dict:
    loaded = RagIndex.load(str(path))
    if expected <= 0 or len(loaded.docs) != expected:
        raise ValueError("暂存索引文档数校验失败")
    if len({doc.doc_id for doc in loaded.docs}) != expected:
        raise ValueError("暂存索引包含重复 doc_id")
    if dense:
        import numpy as np
        if loaded.vectors is None or loaded.vectors.ndim != 2 or not np.isfinite(loaded.vectors).all():
            raise ValueError("暂存向量缺失、形状无效或包含非有限数")
    if kg is not None and len(CropDpKg.load(str(path)).entities) != len(kg.entities):
        raise ValueError("暂存图谱实体数校验失败")
    if normalizer is not None and normalizer.records:
        if TermNormalizer.load(str(path)).stats() != normalizer.stats():
            raise ValueError("暂存术语表校验失败")
    return {
        item.name: {"bytes": item.stat().st_size, "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
        for item in path.iterdir() if item.name in _INDEX_FILES and item.name != "manifest.json"
    }


def _log(message: str = "") -> None:
    print(message, flush=True)


def build(args: argparse.Namespace) -> int:
    settings = rag_config()
    target = Path(args.index_dir or settings["rag_index_dir"]).expanduser().absolute()
    if target.is_symlink():
        raise ValueError("索引目标不能是符号链接")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(f".{target.name}.build.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        _log(f"已有构建锁：{lock}；确认没有构建进程后再清理陈旧锁。")
        return 1
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
        try:
            return _build(args)
        except Exception as exc:  # 保留任何失败暂存物，便于检查；不覆盖当前索引。
            report_file = getattr(args, "_build_report_path", None)
            if report_file and Path(report_file).exists():
                report = json.loads(Path(report_file).read_text(encoding="utf-8"))
                report.update(status="failed", error=repr(exc))
                _write_json(Path(report_file), report)
            _log(f"构建失败：{exc!r}；已发布的旧索引未被覆盖。")
            return 1
    finally:
        lock.unlink(missing_ok=True)


def _build(args: argparse.Namespace) -> int:
    settings = rag_config()
    index_dir = str(Path(args.index_dir or settings["rag_index_dir"]).expanduser().resolve())
    data_dir = str(Path(args.data_dir or settings["rag_data_dir"]).expanduser().resolve())
    if getattr(args, "local_path", None):
        settings["rag_local_paths"] = args.local_path
    source_names = list(args.sources or DEFAULT_ORDER)
    if local.configured_paths(settings) and local.NAME not in source_names:
        source_names.append(local.NAME)

    modules = resolve(source_names)
    ctx = IngestContext(data_dir=data_dir, config=settings, limit=args.limit, force=args.force, log=_log)

    _log(f"索引目录：{index_dir}")
    _log(f"语料缓存：{data_dir}")
    _log(f"语料源　：{', '.join(m.NAME for m in modules)}")
    _log("")

    started = time.time()
    all_docs = []
    artifacts: dict = {}
    summary: list[str] = []
    build_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    report_path = Path(index_dir).with_name(f".{Path(index_dir).name}.builds") / f"{build_id}.json"
    report = {"build_id": build_id, "status": "building", "requested_sources": source_names,
              "source_status": {}, "index_dir": index_dir, "data_dir": data_dir,
              "debug_limit": args.limit, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    args._build_report_path = str(report_path)
    _write_json(report_path, report)
    failures = []

    for module in modules:
        _log(f"[{module.NAME}] {module.DESCRIPTION}")
        source_started = time.time()
        try:
            result = module.build(ctx)
            if not result.docs:
                raise ValueError("源未产出任何可用文档")
            ids = set()
            for doc in result.docs:
                if not doc.doc_id or doc.doc_id in ids or not doc.text.strip():
                    raise ValueError(f"无效/重复文档 id：{doc.doc_id!r}")
                if doc.source != module.NAME or not doc.meta.get("source_ref"):
                    raise ValueError(f"文档缺少规范 source/source_ref：{doc.doc_id}")
                ids.add(doc.doc_id)
        except Exception as exc:  # noqa: BLE001 - 单个源失败不该毁掉整次构建
            _log(f"  ✗ 失败：{exc!r}")
            _log("    其余语料源继续构建。")
            summary.append(f"{module.NAME}: 失败 {exc!r}")
            failures.append(module.NAME)
            report["source_status"][module.NAME] = {"status": "failed", "documents": 0,
                "error": repr(exc), "homepage": module.HOMEPAGE, "license": module.LICENSE}
            _write_json(report_path, report)
            continue
        all_docs.extend(result.docs)
        artifacts.update(result.artifacts)
        elapsed = time.time() - source_started
        _log(f"  ✓ {len(result.docs)} 条文档（{elapsed:.1f}s）")
        for note in result.notes:
            _log(f"    · {note}")
        summary.append(f"{module.NAME}: {len(result.docs)} 条")
        report["source_status"][module.NAME] = {
            "status": "success", "documents": len(result.docs), "seconds": round(elapsed, 3),
            "homepage": module.HOMEPAGE, "license": module.LICENSE,
            "notes": result.notes, "details": result.details,
            "kinds": dict(Counter(doc.kind for doc in result.docs)),
        }
        _write_json(report_path, report)
        _log("")

    if not all_docs or (failures and not getattr(args, "allow_partial", False)):
        report["status"] = "failed"
        report["error"] = "没有有效文档" if not all_docs else f"语料源失败：{', '.join(failures)}"
        _write_json(report_path, report)
        _log(f"构建失败，旧索引保持不变。详情：{report_path}")
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
    target = Path(index_dir)
    staged = Path(tempfile.mkdtemp(prefix=f".{target.name}.build-", dir=target.parent))
    report["staging_dir"] = str(staged)
    report["status"] = "validating"
    _write_json(report_path, report)
    index.save(str(staged))

    # ---- 图谱与术语表 ----
    if kg is not None:
        kg.save(str(staged))
        _log(f"  ✓ 图谱已落盘：{kg.stats()}")
    if normalizer.records:
        normalizer.save(str(staged))
        _log(f"  ✓ 术语表已落盘")

    checksums = _validate_staged(staged, len(all_docs), dense=index.vectors is not None,
                                 kg=kg, normalizer=normalizer)
    if args.probe and not probe(str(staged), embedder_name=index.embedder_name):
        raise ValueError("暂存知识库查询自检未全部通过，旧索引保持不变")
    report["status"] = "partial" if failures else "complete"
    report["scope"] = "limited" if args.limit is not None else "selected_sources"
    report["documents"] = len(index.docs)
    report["embedding"] = {"requested": not args.no_embed,
        "backend": index.embedder_name, "dense": index.vectors is not None,
        "fallback": not args.no_embed and embedder is None}
    report["seconds"] = round(time.time() - started, 3)
    report["backup_dir"] = str(target.with_name(f".{target.name}.backup-{build_id}")) if target.exists() else None
    manifest_path = staged / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"build": report, "source_status": report["source_status"], "files": checksums})
    _write_json(manifest_path, manifest)
    _write_json(report_path, report)
    backup = _publish(staged, target, build_id)
    _log(f"  ✓ 索引验证后已切换：{index_dir}")
    if backup:
        _log(f"  · 上一版已保留：{backup}")

    _log("")
    _log("=" * 62)
    _log(f"{'部分完成（存在失败源）' if failures else '完成'}，用时 {time.time() - started:.1f}s")
    for line in summary:
        _log(f"  · {line}")
    for key, value in index.profile().items():
        _log(f"  · {key}: {value}")
    _log("=" * 62)

    return 1 if failures else 0


def probe(index_dir: str, *, embedder_name: str | None = None) -> bool:
    """连通检查：确认读取/查询链路及非空返回；不测定检索相关性或农艺正确性。"""
    from agriagents.rag.kb import load_knowledge_base

    _log("\n[连通检查] 验证索引加载与查询非空返回，不代表检索精度或农艺正确性")
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
    passed = True
    for label, case in cases:
        try:
            result = case()
        except Exception as exc:  # noqa: BLE001
            _log(f"  ✗ {label}：{exc!r}")
            passed = False
            continue
        if "candidates" in result:
            names = [c["disease"] for c in result["candidates"]]
            ok = bool(names or result["evidence"])
            passed = passed and ok
            _log(f"  {'✓' if ok else '✗'} {label}：候选 {names or '（无）'}；证据 {len(result['evidence'])} 条")
        elif "plans" in result:
            names = [p["disease"] for p in result["plans"]]
            ok = bool(result["found"])
            passed = passed and ok
            _log(f"  {'✓' if ok else '✗'} {label}：found={result['found']}；{'、'.join(names) or '（无）'}")
        else:
            passed = passed and bool(result)
            _log(f"  {'✓' if result else '✗'} {label}：命中 {len(result)} 条" + (f"；首条 = {result[0]['title'][:60]}" if result else ""))
    return passed


def status(args: argparse.Namespace) -> int:
    settings = rag_config()
    index_dir = args.index_dir or settings["rag_index_dir"]
    if not os.path.isdir(index_dir):
        _log(f"索引目录不存在：{index_dir}")
        _log("先运行：python tools/build_rag_index.py --all")
        return 1
    from agriagents.rag.kb import load_knowledge_base

    kb = load_knowledge_base({"rag_index_dir": index_dir})
    for key, value in kb.describe().items():
        if isinstance(value, dict):
            _log(f"{key}:")
            for sub_key, sub_value in value.items():
                _log(f"  {sub_key}: {sub_value}")
        else:
            _log(f"{key}: {value}")
    manifest_path = Path(index_dir) / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _log("构建状态：" + manifest.get("build", {}).get("status", "legacy_unknown"))
        for name, details in manifest.get("source_status", {}).items():
            _log(f"  {name}: {details['status']}，{details['documents']} 条")
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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="构建全部公开语料源（默认行为，含 seed）")
    selection.add_argument(
        "--lean", action="store_true",
        help="精简构建：seed + curated + CropDP-KG 图谱 + PlantInquiryVQA 卡片",
    )
    selection.add_argument("--sources", type=str, default="", help=f"逗号分隔；默认 {','.join(DEFAULT_ORDER)}；快速演示用 seed")
    parser.add_argument("--limit", type=int, default=None, help="每源/英文子数据集的条数上限（调试用）")
    parser.add_argument("--local-path", action="append", default=[], help="自有 JSONL 文件或目录（可重复；自动追加 local 源）")
    parser.add_argument("--allow-partial", action="store_true", help="显式允许发布部分源成功的降级库；manifest 标 partial，退出码仍为 1")
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
    parser.add_argument("--probe", action="store_true", help="切换前检查查询连通/非空返回（不代表相关性或正确性评测）")
    parser.add_argument("--status", action="store_true", help="只看当前索引状态")
    parser.add_argument("--list", action="store_true", help="列出可用语料源")

    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须为正整数")

    if args.list:
        return list_sources()
    if args.status:
        return status(args)

    if args.lean:
        args.sources = [seed.NAME, curated.NAME, cropdp.NAME, plantinquiry.NAME]
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
