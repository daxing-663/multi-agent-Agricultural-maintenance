"""离线构建回归：源字段、精确导入、失败保护与发布回滚。"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agriagents.rag.index import Doc, RagIndex
from agriagents.rag.sources import DEFAULT_ORDER, FULL_ORDER, curated, local, qa_en, qa_zh, seed
from agriagents.rag.sources.base import IngestContext, SourceResult, dedupe_docs
from agriagents.rag.sources.scope import crop_metadata, is_food_health_qa
from tools import build_rag_index as builder

pytestmark = pytest.mark.unit


def _args(tmp_path, **overrides):
    values = dict(index_dir=str(tmp_path / "index"), data_dir=str(tmp_path / "raw"),
                  sources=["seed"], limit=None, force=False, no_embed=True,
                  agrovoc=False, agrovoc_limit=0, agrovoc_budget=0, probe=False,
                  local_path=[], allow_partial=False)
    values.update(overrides)
    return argparse.Namespace(**values)


def _local_row(kind="soil_reference", **meta):
    return {"id": "fixture", "kind": kind, "title": "Test document", "text": "Test fixture, not field data",
            "meta": {"source_ref": "test-fixture-document", **meta}}


def test_qa_numeric_short_answer_keeps_solution_and_upstream_verification():
    row = {"id": "units_1", "question": "How many acres is 336.4 hectares?", "answer": "831.26 ac",
           "worked_solution": "1 ha = 2.47105 ac. Multiply 336.4 by 2.47105 to get 831.26 ac.",
           "verified_by": "closed-form", "citation": "SI / US customary conversion factors"}
    doc = qa_en._row_to_doc(row, "manifesta/verified-agronomy-17k", "Verified Agronomy", True)
    assert doc is not None
    assert row["worked_solution"] in doc.text
    assert doc.meta["verified_by"] == "closed-form"
    assert doc.meta["project_human_verified"] is False
    assert doc.meta["verified"] is False
    assert doc.meta["upstream_verification_claim"] is True
    assert doc.meta["source_ref"].endswith("manifesta/verified-agronomy-17k")


def test_english_sources_preserve_dataset_counts(monkeypatch, tmp_path):
    def rows(ctx, dataset, **kwargs):
        if dataset.startswith("manifesta"):
            return iter([{"question": "A units question", "answer": "4 kg", "worked_solution": "A sufficiently detailed worked solution for a short numeric answer."}]), "fixture"
        return iter([{"question": dataset + " crop rotation?", "answers": "Crop rotation helps reduce the build-up of soil-borne pathogens."}]), "fixture"
    monkeypatch.setattr(qa_en, "load_hf_rows", rows)
    result = qa_en.build(IngestContext(str(tmp_path)))
    assert len(result.docs) == 3
    assert len(result.details["datasets"]) == 3
    assert all(info["documents"] == 1 for info in result.details["datasets"].values())


def test_dedupe_uses_full_text_and_retains_source_provenance():
    prefix = "same prefix " * 60
    docs = [Doc(str(i), "qa_en", "qa_pair", prefix + text, meta={"source_ref": str(i)})
            for i, text in enumerate(("A", "B", "A"))]
    result = dedupe_docs(docs)
    assert len(result) == 2
    assert result[0].meta["also_seen_in"][0]["source_ref"] == "2"


def test_chinese_qa_retains_dataset_and_record_id():
    doc = qa_zh._row_to_doc({"id": "row-9", "prompt": "设施黄瓜叶片出现黄斑时应收集哪些资料？",
                           "response": "先记录症状出现时间、受害部位与分布，并采集叶片正反面照片，结合棚内环境数据进行鉴别。"})
    assert doc is not None and doc.meta["record_id"] == "row-9"
    assert "黄瓜" in doc.meta["crops"]
    assert doc.meta["source_ref"] == qa_zh.HOMEPAGE


@pytest.mark.parametrize("question,answer", [
    ("哪两种食物搭配可以预防心脏病，并且熟吃西红柿更有助于吸收番茄红素？", "西红柿与橄榄油搭配有助于人体吸收。"),
    ("西红柿在烹制过程中如何使番茄红素更容易被人体吸收？", "将食物加热，并配合油脂烹调。"),
    ("黄瓜中的哪种成分对降低尿酸水平有帮助？", "这是一条关于人类膳食和痛风的资料。"),
    ("How can I cook tomatoes to improve lycopene absorption?", "Heat them gently with oil to improve absorption in the human body."),
    ("What are the health benefits of cucumbers?", "Cucumbers provide nutrients that may support human digestion."),
])
def test_food_and_human_health_qa_is_excluded(question, answer):
    assert is_food_health_qa(question, answer)
    assert qa_en._row_to_doc({"question": question, "answer": answer}, "fixture", "fixture", False) is None
    assert qa_zh._row_to_doc({"prompt": question, "response": answer}) is None


@pytest.mark.parametrize("question,answer", [
    ("温室黄瓜霜霉病怎样防治？", "先核实叶片症状，再检查通风和叶面潮湿情况，涉及农药作业时注意人体防护。"),
    ("食用菌栽培怎样控制土壤湿度？", "按生育阶段检查基质湿度并维持设施通风，不要将食用菌栽培误判为食物营养问答。"),
    ("番茄缺素导致叶片黄化时如何施肥？", "结合土壤检测与叶片位置判断植物营养缺乏，不应按人体补充维生素的方法处理。"),
    ("How can cucumber growers manage downy mildew in a greenhouse?", "Use disease monitoring and check irrigation practices; protect human operators during pesticide work."),
    ("How should farmers fertilize tomatoes with nutrient deficiency?", "Base nutrient application on soil tests and the crop growth stage, not dietary nutritional guidance."),
])
def test_plant_production_and_operator_protection_are_preserved(question, answer):
    assert not is_food_health_qa(question, answer)
    assert qa_en._row_to_doc({"question": question, "answer": answer}, "fixture", "fixture", False) is not None


def test_crop_tags_use_question_subject_before_answer_comparisons():
    meta = crop_metadata("西红柿在结果期怎样施肥？", "番茄和黄瓜需要不同水肥方案。", lang="zh")
    assert meta["crops"] == ["番茄"]
    assert meta["crop_scope"] == "explicit"
    assert meta["crop_metadata_basis"] == "question"
    assert meta["crop_metadata_inferred"] is True
    en = crop_metadata("How do cherry tomatoes respond to irrigation?", "Cucumbers are different.", lang="en")
    assert en["crops"] == ["tomato"]
    assert "cherry" not in en["crops"]


def test_crop_metadata_distinguishes_answer_inference_from_general_knowledge():
    inferred = crop_metadata("How does irrigation demand change during growth?", "Tomatoes and cucumbers differ.", lang="en")
    assert set(inferred["crops"]) == {"tomato", "cucumber"}
    assert inferred["crop_scope"] == "inferred"
    general = crop_metadata("温室内空气很潮湿时怎么办？", "检查通风与浇水安排。", lang="zh")
    assert general["crops"] == [] and general["crop_scope"] == "general"


def test_english_crop_aliases_require_word_boundaries():
    assert crop_metadata("How many kilograms of pepper are harvested?", "A pepperoni pizza is unrelated.", lang="en")["crops"] == ["pepper"]
    assert crop_metadata("A pepperoni pizza recipe", "No production evidence.", lang="en")["crops"] == []


@pytest.mark.parametrize("kind,meta", [
    ("soil_reference", {"site_id": "FIELD-07"}),
    ("equipment_manual", {"model": "DEMO-VALVE-V1", "device_ids": ["VALVE-11"], "fault_code": "E04"}),
    ("agronomy", {"crop": "番茄"}),
    ("treatment", {"disease": "番茄晚疫病", "crop": "番茄"}),
])
def test_local_import_accepts_precise_metadata(tmp_path, kind, meta):
    path = tmp_path / "knowledge.jsonl"
    path.write_text(json.dumps(_local_row(kind, **meta)), encoding="utf-8")
    result = local.build(IngestContext(str(tmp_path), {"rag_local_paths": [str(path)]}))
    assert len(result.docs) == 1
    assert result.docs[0].meta["import_line"] == 1
    assert result.docs[0].meta["evidence_status"] == "user_provided"
    assert result.docs[0].meta["source_ref"] == "test-fixture-document"


@pytest.mark.parametrize("kind,meta", [
    ("soil_reference", {}),
    ("soil_reference", {"site_id": "FIELD-*"}),
    ("equipment_manual", {"device_type": "valve", "fault_code": "E04"}),
    ("equipment_manual", {"model": "DEMO-VALVE-V1"}),
    ("equipment_manual", {"model": "DEMO-VALVE-V1", "fault_codes": "E04"}),
    ("treatment", {"disease": "late blight"}),
    ("agronomy", {}),
])
def test_local_import_rejects_unbound_knowledge(tmp_path, kind, meta):
    with pytest.raises(ValueError):
        local.row_to_doc(_local_row(kind, **meta), path=tmp_path / "bad.jsonl", line=1)


def test_local_import_reports_bad_line_even_after_debug_limit(tmp_path):
    path = tmp_path / "knowledge.jsonl"
    path.write_text(json.dumps(_local_row(site_id="FIELD-07")) + "\n{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"knowledge\.jsonl:2"):
        local.build(IngestContext(str(tmp_path), {"rag_local_paths": [str(path)]}, limit=1))


def test_seed_has_bound_demo_soil_and_device_records(tmp_path):
    result = seed.build(IngestContext(str(tmp_path)))
    soil = {d.meta.get("site_id"): d for d in result.docs if d.kind == "soil_reference"}
    for site in ("FIELD-07", "GH-09"):
        assert soil[site].meta["is_demo"] is True
        assert soil[site].meta["measurements"]["EC"]["unit"] == "mS/cm"
    devices = {d.meta.get("device_id"): d for d in result.docs if d.kind == "equipment_manual"}
    assert devices["VALVE-11"].meta["fault_code"] == "E04"
    assert devices["FAN-03"].meta["fault_code"] == "OFFLINE"
    assert devices["irrigation_valve"].meta["is_demo"] is True
    assert all(d.meta["evidence_status"] == "demo" and d.meta["source_ref"] for d in result.docs)


def test_default_build_contains_all_public_and_bundled_sources():
    assert DEFAULT_ORDER == FULL_ORDER == ("seed", "curated", "cropdp", "plantinquiry", "qa_en", "qa_zh")


def test_curated_summaries_have_provenance_and_explicit_scope(tmp_path):
    result = curated.build(IngestContext(str(tmp_path)))
    assert {d.doc_id for d in result.docs} == {
        "curated:cucumber-transplant-fertigation", "curated:greenhouse-perlite-fertigation"}
    for doc in result.docs:
        assert doc.source == "curated" and doc.kind == "agronomy"
        assert doc.meta["source_ref"].startswith("https://")
        assert doc.meta["publisher"] and doc.meta["citation"]
        assert doc.meta["retrieved_at"] == "2026-09-29"
        assert doc.meta["evidence_status"] == "source_summary"
        assert doc.meta["is_demo"] is False
        assert doc.meta["project_human_verified"] is False
        assert doc.meta["quantitative_prescription"] is False
        assert "适用范围" in doc.text and "本地" in doc.text
    perlite = next(d for d in result.docs if "perlite" in d.doc_id)
    assert perlite.meta["crops"] == ["番茄", "黄瓜"]
    assert "crop" not in perlite.meta
    assert "EC" in perlite.text and "排液" in perlite.text
    assert len(curated.build(IngestContext(str(tmp_path), limit=1)).docs) == 1


def test_curated_loader_rejects_missing_provenance():
    with pytest.raises(ValueError, match="source_ref"):
        curated._to_doc({"id": "curated:invalid", "kind": "agronomy", "title": "Title", "text": "Text", "meta": {}})


def test_curated_source_build_is_published_with_source_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRIAGENTS_RAG_LOCAL_PATHS", raising=False)
    args = _args(tmp_path, sources=["curated"])
    assert builder.build(args) == 0
    manifest = json.loads((Path(args.index_dir) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_status"]["curated"]["documents"] == 2
    assert manifest["source_status"]["curated"]["status"] == "success"


def test_successful_build_manifest_and_rollback_backup(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRIAGENTS_RAG_LOCAL_PATHS", raising=False)
    args = _args(tmp_path)
    assert builder.build(args) == 0
    index = Path(args.index_dir)
    original = (index / "corpus.jsonl").read_bytes()
    raw = index / "raw"
    raw.mkdir()
    (raw / "cached.bin").write_bytes(b"reusable raw content")
    assert builder.build(args) == 0
    manifest = json.loads((index / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["build"]["status"] == "complete"
    assert manifest["source_status"]["seed"]["documents"] == manifest["documents"]
    assert manifest["files"]["corpus.jsonl"]["sha256"]
    backup = Path(manifest["build"]["backup_dir"])
    assert (backup / "corpus.jsonl").read_bytes() == original
    assert (index / "raw" / "cached.bin").read_bytes() == b"reusable raw content"


def test_failed_source_does_not_replace_existing_index(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRIAGENTS_RAG_LOCAL_PATHS", raising=False)
    args = _args(tmp_path)
    assert builder.build(args) == 0
    original = (Path(args.index_dir) / "manifest.json").read_bytes()
    failed = SimpleNamespace(NAME="failed", DESCRIPTION="failure fixture", HOMEPAGE="test", LICENSE="test",
                             build=lambda ctx: (_ for _ in ()).throw(ValueError("bad cached data")))
    monkeypatch.setattr(builder, "resolve", lambda names: [seed, failed])
    args.sources = ["seed", "failed"]
    assert builder.build(args) == 1
    assert (Path(args.index_dir) / "manifest.json").read_bytes() == original
    report = json.loads(Path(args._build_report_path).read_text(encoding="utf-8"))
    assert report["source_status"]["failed"]["status"] == "failed"
    assert report["status"] == "failed"


def test_empty_selected_source_cannot_publish_even_with_allow_partial(tmp_path, monkeypatch):
    source = SimpleNamespace(NAME="empty", DESCRIPTION="empty fixture", HOMEPAGE="test", LICENSE="test",
                              build=lambda ctx: SourceResult())
    monkeypatch.setattr(builder, "resolve", lambda names: [source])
    args = _args(tmp_path, sources=["empty"], allow_partial=True)
    assert builder.build(args) == 1
    assert not Path(args.index_dir).exists()


def test_explicit_partial_build_is_marked_and_returns_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRIAGENTS_RAG_LOCAL_PATHS", raising=False)
    source = SimpleNamespace(NAME="empty", DESCRIPTION="empty fixture", HOMEPAGE="test", LICENSE="test",
                             build=lambda ctx: SourceResult())
    monkeypatch.setattr(builder, "resolve", lambda names: [seed, source])
    args = _args(tmp_path, sources=["seed", "empty"], allow_partial=True)
    assert builder.build(args) == 1
    manifest = json.loads((Path(args.index_dir) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["build"]["status"] == "partial"
    assert manifest["source_status"]["empty"]["status"] == "failed"
    assert manifest["source_status"]["seed"]["documents"] == manifest["documents"]


def test_staged_validation_failure_preserves_current_index(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRIAGENTS_RAG_LOCAL_PATHS", raising=False)
    args = _args(tmp_path)
    assert builder.build(args) == 0
    original = (Path(args.index_dir) / "manifest.json").read_bytes()
    def fail_validation(*args, **kwargs):
        raise ValueError("bad staged vectors")
    monkeypatch.setattr(builder, "_validate_staged", fail_validation)
    assert builder.build(args) == 1
    assert (Path(args.index_dir) / "manifest.json").read_bytes() == original
    report = json.loads(Path(args._build_report_path).read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert Path(report["staging_dir"]).is_dir()


def test_publish_rename_failure_restores_old_directory(tmp_path, monkeypatch):
    target = tmp_path / "index"
    target.mkdir()
    (target / "corpus.jsonl").write_text("old", encoding="utf-8")
    staged = tmp_path / ".index.build-fixture"
    staged.mkdir()
    (staged / "corpus.jsonl").write_text("new", encoding="utf-8")
    replace = builder.os.replace
    def fail_publication(source, dest):
        if Path(source) == staged:
            raise OSError("simulated locked target")
        return replace(source, dest)
    monkeypatch.setattr(builder.os, "replace", fail_publication)
    with pytest.raises(OSError, match="simulated"):
        builder._publish(staged, target, "test")
    assert (target / "corpus.jsonl").read_text(encoding="utf-8") == "old"


def test_local_paths_are_automatically_included_in_build(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRIAGENTS_RAG_LOCAL_PATHS", raising=False)
    path = tmp_path / "local.jsonl"
    path.write_text(json.dumps(_local_row(site_id="FIELD-07")), encoding="utf-8")
    args = _args(tmp_path, local_path=[str(path)])
    assert builder.build(args) == 0
    manifest = json.loads((Path(args.index_dir) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_status"]["local"]["documents"] == 1
    assert any(doc.source == "local" for doc in RagIndex.load(args.index_dir).docs)
