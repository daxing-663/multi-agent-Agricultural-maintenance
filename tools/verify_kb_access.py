"""离线检索验收：确定性分层抽样、自然问句相关性标签、领域拒答回归。

python tools/verify_kb_access.py --index-dir PATH --output-dir reports/kb

不运行 LLM、不下载模型、不加载 .env。标题回查检查索引一致性，
不代表现场诊断准确率；报告列出全部查询、预期文档和实际命中。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# 对检索正文逐条审阅后固定的相关性标签；这不是农艺事实/生产建议的人工核验。
# 2026-09-29 原始 51,598 条 BM25 库的正例 Hit@3=4/9、Hit@1=1/9。
# 第四例原为覆盖缺口；补录并阅读 UAF/UF 的有出处释义后加入确有相关正文的 gold。
# 新补录证据局限于温室基质/容器，仍不代表本地土耕处方或农艺事实的人审认证。
NATURAL_AGRONOMY_GOLD = (
    ("番茄棚里已经出现晚疫病，除了打药，还能怎样减缓蔓延？", (
        "plantinquiry:tomato.disease_fungal.late_blight:management", "seed:tomato-late-blight:management", "qa_zh:dPQK8wLZcYIq")),
    ("黄瓜叶背出现灰黑色霉层，怀疑霜霉病，棚内管理要做哪些调整？", (
        "plantinquiry:cucumber.disease_fungal.downy_mildew:management", "seed:cucumber-downy-mildew:management")),
    ("番茄结果以后该怎样调整浇水和施肥？", (
        "seed:qa:tomato-fertigation", "qa_zh:CtHUEuvTTOch")),
    ("黄瓜刚栽进温室，浇水施肥有哪些注意事项？", (
        "curated:cucumber-transplant-fertigation", "curated:greenhouse-perlite-fertigation")),
    ("一块大棚连续几年种番茄，越种长势越差，怎么改善连作问题？", (
        "seed:qa:continuous-cropping", "qa_zh:UPah9LLFeDex")),
    ("黄瓜在同一个棚里连着种，怎样减少土传病害？", (
        "seed:qa:continuous-cropping", "qa_zh:UPah9LLFeDex")),
    ("最近阴雨天番茄棚湿度很高，叶子早上总有露水，应该如何管理？", (
        "seed:qa:greenhouse-humidity", "qa_zh:Ew1Ga-1FKo8P", "qa_zh:HXiW38dM9fP0")),
    ("冬天黄瓜棚既要保温又要降湿，怎么安排通风和浇水？", (
        "seed:qa:greenhouse-humidity", "qa_zh:EGdjT9XKJby2", "qa_zh:6JJq1kUX_Etd", "qa_zh:HXiW38dM9fP0")),
    ("番茄根区盐分越来越高，水肥管理怎样调整？", ("seed:qa:tomato-fertigation",)),
    ("黄瓜霜霉病怎么做农业防治，哪些措施能减少叶面潮湿？", (
        "plantinquiry:cucumber.disease_fungal.downy_mildew:management", "seed:cucumber-downy-mildew:management")),
)


def _sample(items, count):
    """按主键排序等距抽样；不依赖随机种子或构建时间。"""
    ordered = sorted(items, key=lambda item: item.doc_id)
    if len(ordered) <= count:
        return ordered
    positions = {round(i * (len(ordered) - 1) / max(count - 1, 1)) for i in range(count)}
    return [ordered[i] for i in sorted(positions)]


def evaluate(index_dir: str, *, samples_per_group: int = 3, top_k: int = 5,
             expected_sources=None, dense: bool = False) -> dict:
    from agriagents.dataflows.config import run_config
    from agriagents.rag.index import CORPUS_NAME, RagIndex
    from agriagents.rag.embedder import get_embedder
    from agriagents.rag.kb import KnowledgeBase
    from agriagents.rag.kg import CropDpKg
    from agriagents.rag.normalize import TermNormalizer
    from agriagents.rag.sources import FULL_ORDER
    from agriagents.rag.text import normalize

    if samples_per_group < 1 or top_k < 1:
        raise ValueError("samples_per_group 和 top_k 必须为正整数")
    index = RagIndex.load(index_dir)
    expected_sources = list(FULL_ORDER if expected_sources is None else expected_sources)
    terms = TermNormalizer.load(index_dir)
    embedder = None
    if dense:
        if index.vectors is None or not index.embedder_name:
            raise ValueError("--dense 要求带向量和明确编码器名称的索引")
        embedder = get_embedder({
            "rag_embedding_backend": "hashing" if index.embedder_name.startswith("hashing-") else "fastembed",
            "rag_embedding_model": index.embedder_name,
            "rag_model_cache_dir": os.path.expanduser("~/.agriagents/models"),
        }, allow_download=False)
        if (embedder is None or embedder.name != index.embedder_name
                or embedder.dim != index.vectors.shape[1]):
            raise ValueError("本地没有与索引完全匹配的缓存编码器；不能声称完成 dense 验证")
    try:
        kg = CropDpKg.load(index_dir)
    except FileNotFoundError:
        kg = None
    kb = KnowledgeBase(index_dir, index=index, kg=kg, terms=terms, embedder=embedder)
    cases = []

    def search(query, **kwargs):
        return index.search(query, embedder=embedder, **kwargs)

    def add(case_id, category, passed, **details):
        cases.append({"id": case_id, "category": category, "passed": bool(passed), **details})

    add("integrity:nonempty", "integrity", bool(index.docs), count=len(index.docs))
    present_sources = {doc.source for doc in index.docs}
    for source in expected_sources:
        add(f"source:{source}", "source_presence", source in present_sources, source=source)
    counts = Counter(doc.doc_id for doc in index.docs)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    add("integrity:unique_ids", "integrity", not duplicates, duplicates=duplicates)
    empty = sorted(doc.doc_id for doc in index.docs if not doc.text.strip())
    add("integrity:nonempty_text", "integrity", not empty, empty_doc_ids=empty)

    groups = defaultdict(list)
    for doc in index.docs:
        groups[(doc.source, doc.kind)].append(doc)
    for (source, kind), docs in sorted(groups.items()):
        for doc in _sample(docs, samples_per_group):
            query = doc.title or doc.text[:120]
            expected = sorted(other.doc_id for other in docs
                              if (normalize(other.title) == normalize(doc.title) if doc.title
                                  else other.doc_id == doc.doc_id))
            hits = search(query, top_k=top_k, sources=[source], kinds=[kind])
            actual = [hit.doc.doc_id for hit in hits]
            ranks = [i + 1 for i, doc_id in enumerate(actual) if doc_id in expected]
            add(f"title:{doc.doc_id}", "title_retrieval", bool(ranks),
                source=source, kind=kind, query=query, expected_doc_ids=expected,
                actual_doc_ids=actual, relevant_rank=min(ranks) if ranks else None,
                actual_hits=[{"doc_id": hit.doc.doc_id, "bm25_rank": hit.bm25_rank,
                              "dense_rank": hit.dense_rank, "bm25_score": hit.bm25_score,
                              "dense_score": hit.dense_score} for hit in hits])
            for field in ("crop", "disease", "site_id", "device_id", "fault_code"):
                if not doc.meta.get(field):
                    continue
                filters = {field: doc.meta[field]}
                hits = search(query, top_k=top_k, candidate_k=top_k,
                                    sources=[source], kinds=[kind], meta_filters=filters)
                actual = [hit.doc.doc_id for hit in hits]
                precise = all(RagIndex._values(hit.doc.meta.get(field)) & RagIndex._values(filters[field])
                              for hit in hits)
                add(f"filter:{field}:{doc.doc_id}", "metadata_filter", precise and bool(set(actual) & set(expected)),
                    source=source, kind=kind, query=query, meta_filters=filters,
                    expected_doc_ids=expected, actual_doc_ids=actual)
                break

    secondary_datasets = defaultdict(list)
    for doc in index.docs:
        primary = normalize(doc.meta.get("dataset", ""))
        for origin in doc.meta.get("also_seen_in") or []:
            dataset = origin.get("dataset", "")
            if dataset and normalize(dataset) != primary:
                secondary_datasets[dataset].append(doc)
    for dataset, docs in sorted(secondary_datasets.items()):
        doc = _sample(docs, 1)[0]
        query = doc.title or doc.text[:160]
        hits = search(query, top_k=top_k, sources=[doc.source], meta_filters={"dataset": dataset})
        actual = [hit.doc.doc_id for hit in hits]
        add(f"filter:secondary_dataset:{dataset}", "metadata_filter", doc.doc_id in actual,
            source=doc.source, query=query, meta_filters={"dataset": dataset},
            expected_doc_ids=[doc.doc_id], actual_doc_ids=actual)

    for name, query in (("empty", ""), ("whitespace", "  \n "),
                        ("generic_zh", "请问有哪些相关知识和管理防治方法"),
                        ("generic_en", "please tell me disease management and treatment methods"),
                        ("unrelated_en", "quasarflux xenonwarp qzxv98473")):
        hits = search(query, top_k=top_k)
        add(f"reject:{name}", "index_negative", not hits, query=query,
            actual_doc_ids=[hit.doc.doc_id for hit in hits], expected_doc_ids=[])
    if index.docs:
        query = index.docs[0].title or index.docs[0].text[:120]
        for name, filters in (("unknown_source", {"sources": ["__unknown_source__"]}),
                              ("unknown_meta", {"meta_filters": {"crop": "__unknown_crop__"}}),
                              ("unknown_dataset", {"meta_filters": {"dataset": "__unknown_dataset__"}}),
                              ("empty_ids", {"allowed_doc_ids": []})):
            hits = search(query, top_k=top_k, **filters)
            add(f"reject:{name}", "index_negative", not hits, query=query, filters=filters,
                actual_doc_ids=[hit.doc.doc_id for hit in hits], expected_doc_ids=[])

    with run_config({"rag_index_dir": index_dir, "rag_embedding_backend": "none"}):
        for crop, symptom, name in (
            ("__unknown_crop__", "暗绿色水浸状病斑 白色霉层", "unknown_crop"),
            ("番茄", "", "empty_symptom"),
            ("番茄", "quasarflux xenonwarp", "unrelated_symptom"),
        ):
            result = kb.diagnose(crop, symptom)
            add(f"domain:{name}", "domain_negative", not result["candidates"] and not result["evidence"],
                crop=crop, query=symptom, actual_candidates=[c["disease"] for c in result["candidates"]],
                evidence_count=len(result["evidence"]))
        for disease, crop, name in (
            ("完全未收录的病害XYZ98473", "番茄", "unknown_disease"),
            ("番茄晚疫病", "黄瓜", "cross_crop_treatment"),
        ):
            result = kb.treatments(disease, crop=crop)
            add(f"domain:{name}", "domain_negative", not result["found"] and not result["plans"],
                query=disease, crop=crop, actual_doc_ids=[p["doc_id"] for p in result["plans"]])
        for disease, crop in (("番茄晚疫病", "番茄"), ("黄瓜霜霉病", "黄瓜")):
            expected = [doc.doc_id for doc in index.docs
                        if doc.kind in ("management", "treatment")
                        and normalize(doc.meta.get("disease", "")) == normalize(disease)
                        and normalize(crop) in RagIndex._values(doc.meta.get("crop"))]
            result = kb.treatments(disease, crop=crop)
            actual = [plan["doc_id"] for plan in result["plans"]]
            add(f"domain:treatment:{disease}", "domain_positive", bool(set(actual) & set(expected)),
                query=disease, crop=crop, expected_doc_ids=expected, actual_doc_ids=actual)
        for kind, method, fields in (
            ("soil_reference", kb.soil_reference, ("site_id",)),
            ("equipment_manual", kb.equipment_manual, ("device_id", "fault_code")),
        ):
            docs = [doc for doc in index.docs if doc.kind == kind and not doc.meta.get("is_template")
                    and all(isinstance(doc.meta.get(field), str) and doc.meta[field] for field in fields)]
            for doc in _sample(docs, samples_per_group):
                arguments = [doc.meta[field] for field in fields]
                hits = method(*arguments, top_k=max(top_k, len(docs)))
                actual = [hit.doc.doc_id for hit in hits]
                add(f"domain:{doc.doc_id}", "domain_positive", doc.doc_id in actual,
                    kind=kind, arguments=arguments, expected_doc_ids=[doc.doc_id], actual_doc_ids=actual)
        for name, hits in (
            ("unknown_site", kb.soil_reference("__unknown_site__")),
            ("unknown_device", kb.equipment_manual("__unknown_device__", "E04")),
            ("unknown_fault", kb.equipment_manual("irrigation_valve", "UNKNOWN98473")),
            ("empty_device", kb.equipment_manual("", "E04")),
        ):
            add(f"domain:{name}", "domain_negative", not hits,
                actual_doc_ids=[hit.doc.doc_id for hit in hits], expected_doc_ids=[])
        for crop, symptom, disease in (
            ("番茄", "暗绿色水浸状病斑 白色霉状物", "晚疫病"),
            ("黄瓜", "受叶脉限制的多角形病斑 叶背灰黑色霉层", "霜霉病"),
            ("tomato", "dark green water-soaked lesions white mold", "晚疫病"),
        ):
            result = kb.diagnose(crop, symptom)
            actual = [item["disease"] for item in result["candidates"]]
            add(f"domain:diagnose:{crop}", "domain_positive", any(disease in name for name in actual),
                crop=crop, query=symptom, expected_entity=disease, actual_candidates=actual)
        available_ids = {doc.doc_id for doc in index.docs}
        by_id = {doc.doc_id: doc for doc in index.docs}
        for number, (query, gold) in enumerate(NATURAL_AGRONOMY_GOLD, 1):
            hits = kb.agronomy(query, top_k=3)
            actual = [hit["doc_id"] for hit in hits]
            rank = next((i + 1 for i, doc_id in enumerate(actual) if doc_id in gold), None)
            crop_aliases = ({"番茄", "西红柿", "tomato", "tomatoes"} if "番茄" in query
                            else {"黄瓜", "cucumber", "cucumbers"})
            conflicts = []
            for doc_id in actual:
                doc = by_id[doc_id]
                primary = RagIndex._values(doc.meta.get("crop")) - {""}
                declared = primary or ((RagIndex._values(doc.meta.get("crops"))
                                        | RagIndex._values(doc.meta.get("crops_en"))) - {""})
                generic = {"通用", "设施蔬菜", "蔬菜", "vegetable", "vegetables", "all", "*", "all crops"}
                if declared - generic and not declared & crop_aliases:
                    conflicts.append({"doc_id": doc_id, "declared_crops": sorted(declared)})
            passed = bool(rank) if gold else not hits
            add(f"natural_agronomy_{number:02d}", "natural_query", passed and not conflicts,
                query=query, expected_doc_ids=list(gold), gold_present_doc_ids=sorted(set(gold) & available_ids),
                actual_doc_ids=actual, actual_titles=[hit["title"] for hit in hits],
                relevant_rank=rank, coverage_gap=not gold, declared_crop_conflicts=conflicts)

    dense_hits = sum(hit["dense_rank"] is not None for case in cases
                     for hit in case.get("actual_hits", []))
    if dense:
        add("integrity:dense_exercised", "integrity", dense_hits > 0, dense_hits=dense_hits)
    by_category = {}
    for category in sorted({case["category"] for case in cases}):
        rows = [case for case in cases if case["category"] == category]
        by_category[category] = {"passed": sum(case["passed"] for case in rows), "total": len(rows)}
    titles = [case for case in cases if case["category"] == "title_retrieval"]
    recall = sum(case["passed"] for case in titles) / len(titles) if titles else 0.0
    mrr = sum(1 / case["relevant_rank"] if case["relevant_rank"] else 0 for case in titles) / len(titles) if titles else 0.0
    natural = [case for case in cases if case["category"] == "natural_query" and not case["coverage_gap"]]
    natural_metrics = {
        "positive_cases": len(natural),
        "hit_at_3": round(sum(case["relevant_rank"] is not None for case in natural) / len(natural), 6) if natural else 0.0,
        "hit_at_1": round(sum(case["relevant_rank"] == 1 for case in natural) / len(natural), 6) if natural else 0.0,
        "declared_crop_conflicts": sum(len(case["declared_crop_conflicts"]) for case in natural),
        "baseline_hit_at_3": round(4 / 9, 6), "baseline_hit_at_1": round(1 / 9, 6),
        "baseline_positive_cases": 9,
        "coverage_gap_cases": sum(case["category"] == "natural_query" and case["coverage_gap"] for case in cases),
        "annotation_scope": "Reviewed for query relevance; not agronomic fact verification.",
    }
    return {
        "schema_version": 1, "index_dir": str(Path(index_dir).resolve()),
        "corpus_sha256": hashlib.sha256((Path(index_dir) / CORPUS_NAME).read_bytes()).hexdigest(),
        "mode": "offline_bm25_dense_no_llm_no_download" if dense else "offline_bm25_no_llm_no_download",
        "evaluated_embedder": embedder.name if embedder else None,
        "scope": "Deterministic source/kind title self-retrieval, metadata filters, domain regression and fixed natural-language relevance gold; not an agronomic factual accuracy benchmark.",
        "parameters": {"samples_per_group": samples_per_group, "top_k": top_k,
                       "expected_sources": expected_sources},
        "profile": index.profile(),
        "summary": {"passed": sum(case["passed"] for case in cases), "total": len(cases),
                    "all_passed": all(case["passed"] for case in cases),
                    "dense_hits": dense_hits,
                    "title_recall_at_k": round(recall, 6), "title_mrr": round(mrr, 6),
                    "natural_queries": natural_metrics, "by_category": by_category}, "cases": cases,
    }


def write_report(report: dict, output_dir: str) -> tuple[Path, Path]:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = path / "retrieval_evaluation.json", path / "retrieval_evaluation.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    lines = ["# 知识库检索验收", "", f"结果：{summary['passed']}/{summary['total']} 项通过。",
             f"文档：{report['profile']['documents']}；模式：`{report['mode']}`。",
             f"标题回查 Recall@{report['parameters']['top_k']}：{summary['title_recall_at_k']:.1%}；MRR：{summary['title_mrr']:.4f}。",
             f"自然问句正例 Hit@3：{summary['natural_queries']['hit_at_3']:.1%}；Hit@1：{summary['natural_queries']['hit_at_1']:.1%}（{summary['natural_queries']['positive_cases']} 个固定正例）。",
             "", "本报告测量索引接线、精确过滤和固定回归案例；标题回查不代表现场诊断准确率，也不验证语料事实正确性。",
             f"语料 SHA-256：`{report['corpus_sha256']}`", "",
             "| 类别 | 通过 | 总数 |", "|---|---:|---:|"]
    for name, stats in summary["by_category"].items():
        lines.append(f"| {name} | {stats['passed']} | {stats['total']} |")
    lines.extend(["", "| 来源 | 文档数 |", "|---|---:|"])
    for name, count in report["profile"]["by_source"].items():
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## 逐项结果", "", "| 结果 | 用例 | 查询/参数 | 期望 | 实际 |", "|---|---|---|---|---|"])

    def cell(value):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return value.replace("|", "\\|").replace("\n", " ")

    for case in report["cases"]:
        query = case.get("query", case.get("arguments", ""))
        expected = case.get("expected_doc_ids", case.get("expected_entity", "满足约束"))
        actual = case.get("actual_doc_ids", case.get("actual_candidates", "见 JSON 详情"))
        lines.append("| " + " | ".join(map(cell, ["PASS" if case["passed"] else "FAIL", case["id"], query, expected, actual])) + " |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", default=os.path.expanduser("~/.agriagents/rag"))
    parser.add_argument("--output-dir", default="reports/kb")
    parser.add_argument("--samples-per-group", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--expected-sources", help="逗号分隔的必需来源；默认要求全部公开源，精简演示可填 seed")
    parser.add_argument("--dense", action="store_true", help="使用与索引匹配的本地缓存编码器；不可用时失败，禁止下载")
    parser.add_argument("--stub", action="store_true", help="兼容旧参数；本工具始终离线，不调用模型")
    args = parser.parse_args()
    try:
        expected = [item.strip() for item in args.expected_sources.split(",") if item.strip()] if args.expected_sources else None
        report = evaluate(args.index_dir, samples_per_group=args.samples_per_group, top_k=args.top_k,
                          expected_sources=expected, dense=args.dense)
        paths = write_report(report, args.output_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    summary = report["summary"]
    print(f"{'[OK]' if summary['all_passed'] else '[FAIL]'} {summary['passed']}/{summary['total']} checks passed")
    for case in report["cases"]:
        if not case["passed"]:
            print(f"  FAIL {case['id']}")
    for path in paths:
        print(path.resolve())
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
