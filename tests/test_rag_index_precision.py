"""检索精度边界：不联网、不调用 LLM，不把任何非空召回当作正确。"""
from types import SimpleNamespace

import pytest

from agriagents.rag.index import Doc, RagIndex
from agriagents.rag.normalize import (
    TermNormalizer,
    TermRecord,
    from_disease_cards,
    from_knowledge_graph,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def index():
    return RagIndex.build([
        Doc("tomato", "seed", "symptom", "暗绿色水浸状病斑 白色霉层", "番茄晚疫病", meta={"crop": "番茄"}),
        Doc("cucumber", "seed", "symptom", "叶背灰黑霉层 多角形病斑", "黄瓜霜霉病", meta={"crop": "黄瓜"}),
        Doc("device", "manual", "equipment_manual", "irrigation valve stalled 故障 处置",
            "灌溉阀 E04", meta={"device_id": ["VALVE-A", "valve-b"], "fault_code": "E04"}),
    ])


@pytest.mark.parametrize("query", ["", "  ", "!!!", "the and of", "管理 防治 方法", "农业 作物 植物 知识",
                                     "please tell me disease management", "quasarflux xenonwarp", "叶面旅行"])
def test_empty_generic_and_unrelated_queries_do_not_match(index, query):
    assert index.search(query) == []


def test_title_is_searchable_and_exact_title_ranks_first(index):
    assert index.search("番茄晚疫病", top_k=1)[0].doc.doc_id == "tomato"


def test_filters_apply_before_candidate_limit():
    docs = [Doc(f"wrong-{i}", "seed", "symptom", "leaf spot", meta={"crop": "cucumber"})
            for i in range(60)]
    docs.append(Doc("right", "seed", "symptom", "leaf spot and water soaking", meta={"crop": "tomato"}))
    index = RagIndex.build(docs)
    hits = index.search("leaf spot", candidate_k=1, top_k=1, meta_filters={"crop": ["TOMATO"]})
    assert [hit.doc.doc_id for hit in hits] == ["right"]
    hits = index.search("leaf spot", candidate_k=1, top_k=1, allowed_doc_ids=["right"])
    assert [hit.doc.doc_id for hit in hits] == ["right"]


def test_metadata_exact_list_or_and_across_fields(index):
    hits = index.search("irrigation valve", meta_filters={"device_id": ["VALVE-B"], "fault_code": "e04"})
    assert [hit.doc.doc_id for hit in hits] == ["device"]
    assert index.search("irrigation valve", meta_filters={"device_id": "VALVE", "fault_code": "e04"}) == []
    assert index.search("irrigation valve", meta_filters={"unknown_field": "e04"}) == []


def test_deduplicated_secondary_dataset_filters_keep_canonical_document():
    from agriagents.rag.sources.base import dedupe_docs

    docs = dedupe_docs([
        Doc("canonical", "qa_en", "qa_pair", "tomato irrigation schedule", "Tomato irrigation",
            meta={"dataset": "Dataset A", "crop": "tomato"}),
        Doc("duplicate", "qa_en", "qa_pair", "tomato irrigation schedule", "Tomato irrigation",
            meta={"dataset": "Dataset B", "crop": "tomato"}),
    ])
    assert len(docs) == 1
    index = RagIndex.build(docs)
    for dataset in ("Dataset A", "DATASET B"):
        hits = index.search("tomato irrigation", candidate_k=1, meta_filters={"dataset": dataset})
        assert [hit.doc.doc_id for hit in hits] == ["canonical"]
    assert index.search("tomato irrigation", meta_filters={"dataset": "Dataset C"}) == []
    assert index.search("tomato irrigation", meta_filters={"dataset": "Dataset B", "crop": "cucumber"}) == []


def test_empty_filters_and_nonpositive_limits_refuse(index):
    for filters in ({"allowed_doc_ids": []}, {"sources": []}, {"kinds": []},
                    {"meta_filters": {"crop": []}}, {"top_k": 0}, {"candidate_k": -1}):
        assert index.search("番茄晚疫病", **filters) == []
    assert index.search("番茄晚疫病", sources="seed", kinds="symptom")


def test_dense_rejects_nonpositive_scores_and_hash_collisions():
    np = pytest.importorskip("numpy")

    class Encoder:
        name = "test-encoder"
        dim = 2

        def encode(self, texts):
            return np.asarray([[1.0, 0.0] for _ in texts])

    docs = [Doc("negative", "s", "k", "orchid root"), Doc("zero", "s", "k", "orange fruit")]
    index = RagIndex(docs, np.asarray([[-1.0, 0.0], [0.0, 1.0]]), embedder_name="test-encoder")
    assert index.search("quasarflux", embedder=Encoder()) == []
    hits = index.search("orchid", embedder=Encoder())
    assert [hit.doc.doc_id for hit in hits] == ["negative"]
    assert all(hit.dense_rank is None for hit in hits)
    index.vectors = np.asarray([[1.0, 0.0], [1.0, 0.0]])
    index.embedder_name = "hashing-2d-ng23"
    encoder = Encoder()
    encoder.name = index.embedder_name
    assert index.search("quasarflux", embedder=encoder) == []
    assert index.search("orchid", embedder=encoder)[0].doc.doc_id == "negative"
    encoder.name = "different-model-same-dim"
    assert all(hit.dense_rank is None for hit in index.search("orchid", embedder=encoder))


def test_dense_evaluation_refuses_missing_vectors(tmp_path):
    from tools.verify_kb_access import evaluate

    RagIndex.build([Doc("a", "s", "k", "tomato")]).save(str(tmp_path))
    with pytest.raises(ValueError, match="向量"):
        evaluate(str(tmp_path), dense=True)


def test_length_batched_build_preserves_vector_row_identity(tmp_path):
    np = pytest.importorskip("numpy")

    class Encoder:
        name = "identity"
        batches = []

        def token_lengths(self, texts):
            return [int(text.split(":")[0]) for text in texts]

        def encode(self, texts):
            self.batches.append(list(texts))
            return np.asarray([[float(text.split(":")[0]), 1.0] for text in texts])

    texts = ["9:x", "2:longer text", "7:y", "1:longest example"]
    docs = [Doc(str(i), "s", "k", text) for i, text in enumerate(texts)]
    encoder, progress = Encoder(), []
    index = RagIndex.build(docs, encoder, batch_size=2, progress=lambda *values: progress.append(values))
    assert encoder.batches == [[texts[3], texts[1]], [texts[2], texts[0]]]
    assert [doc.text for doc in index.docs] == texts
    assert index.vectors[:, 0].tolist() == [9.0, 2.0, 7.0, 1.0]
    assert progress == [(2, 4), (4, 4)]
    index.save(str(tmp_path))
    loaded = RagIndex.load(str(tmp_path))
    assert [doc.text for doc in loaded.docs] == texts
    assert loaded.vectors[:, 0].tolist() == [9.0, 2.0, 7.0, 1.0]


def test_manifest_provenance_survives_load_save_roundtrip(tmp_path):
    import json

    extra = {"build": {"status": "complete"}, "source_status": {"seed": {"documents": 1}},
             "files": {"source_revision": "known-revision"}}
    index = RagIndex([Doc("a", "seed", "k", "tomato")], extra=extra)
    index.save(str(tmp_path / "first"))
    loaded = RagIndex.load(str(tmp_path / "first"))
    assert loaded.extra == extra
    loaded.save(str(tmp_path / "second"))
    manifest = json.loads((tmp_path / "second" / "manifest.json").read_text(encoding="utf-8"))
    assert all(manifest[key] == value for key, value in extra.items())
    assert manifest["documents"] == 1


@pytest.mark.parametrize("bad", [[[float("nan"), 1.0]], [[float("inf"), 0.0]], [1.0], [[]]])
def test_build_rejects_invalid_vector_shape_or_nonfinite_values(bad):
    np = pytest.importorskip("numpy")

    class Encoder:
        def encode(self, texts):
            return np.asarray(bad)

    with pytest.raises(ValueError, match="不合法"):
        RagIndex.build([Doc("a", "s", "k", "tomato")], Encoder())


def test_load_invalid_vectors_falls_back_to_usable_bm25(tmp_path):
    np = pytest.importorskip("numpy")
    RagIndex.build([Doc("a", "s", "k", "tomato")]).save(str(tmp_path))
    np.save(tmp_path / "vectors.npy", np.asarray([1.0]))
    loaded = RagIndex.load(str(tmp_path))
    assert loaded.vectors is None
    assert loaded.search("tomato")[0].doc.doc_id == "a"


def test_build_rejects_dimension_change_instead_of_broadcasting():
    np = pytest.importorskip("numpy")

    class Encoder:
        calls = 0

        def encode(self, texts):
            self.calls += 1
            return np.ones((len(texts), 2 if self.calls == 1 else 1))

    with pytest.raises(ValueError, match="维度不一致"):
        RagIndex.build([Doc("a", "s", "k", "a"), Doc("b", "s", "k", "b")], Encoder(), batch_size=1)


def test_query_expansion_handles_two_character_crop_and_english_boundaries():
    normalizer = TermNormalizer([
        TermRecord("crop:tomato", "番茄", {"zh": ["番茄"], "en": ["tomato"]}),
        TermRecord("disease:rust", "锈病", {"en": ["rust"]}),
    ])
    assert "tomato" in normalizer.expand_query("番茄叶片发黄")
    assert "番茄" in normalizer.expand_query("tomato leaves")
    assert normalizer.expand_query("trust worthy") == "trust worthy"
    assert normalizer.expand_query("tomatoes") == "tomatoes"
    assert normalizer.expand_query("番茄", max_terms=0) == "番茄"


def test_symptom_expansion_is_bilingual_without_guessing_from_crop():
    normalizer = TermNormalizer([])
    assert "水浸状" in normalizer.expand_query("water-soaked spots on leaves")
    assert "white mold" in normalizer.expand_query("白色霉层")
    assert normalizer.expand_query("unknown symptoms") == "unknown symptoms"
    assert normalizer.expand_query("tomato") == "tomato"


def test_kg_crops_are_separate_entities_not_disease_aliases():
    from agriagents.rag.kg import KgEntity

    entity = KgEntity("番茄晚疫病", scientific="Phytophthora infestans", english="Tomato late blight",
                      crops=["番茄", "马铃薯"], crops_en=["tomato", "potato"])
    records = from_knowledge_graph(SimpleNamespace(entities={entity.name: entity}))
    normalizer = TermNormalizer(records)
    assert set(normalizer.expand("番茄")) == {"番茄", "tomato"}
    assert set(normalizer.expand("tomato")) == {"番茄", "tomato"}
    assert "马铃薯" not in normalizer.expand("番茄晚疫病")
    assert "Phytophthora infestans" in normalizer.expand("番茄晚疫病")


def test_card_crop_cannot_overwrite_bilingual_crop_or_alias_disease():
    records = [TermRecord("crop:tomato", "番茄", {"en": ["tomato"], "zh": ["番茄", "西红柿"]})]
    records += from_disease_cards([{
        "disease_id": "late-blight", "condition": {"common_name": "Late blight"},
        "crop": {"common_name": "tomato"},
    }])
    normalizer = TermNormalizer(records)
    assert set(normalizer.expand("tomato")) == {"番茄", "西红柿", "tomato"}
    assert "Late blight" not in normalizer.expand("tomato")
    legacy = TermNormalizer([TermRecord("legacy", "blight", {"crop": ["tomato"]})])
    assert legacy.lookup("tomato") is None


def test_evaluation_reports_failures_instead_of_accepting_any_hits(tmp_path):
    from tools.verify_kb_access import evaluate, write_report

    index = RagIndex.build([Doc("wrong", "seed", "symptom", "A fig leaf", "Fig leaf")])
    index.save(str(tmp_path / "index"))
    report = evaluate(str(tmp_path / "index"), samples_per_group=1)
    assert not report["summary"]["all_passed"]  # 没有番茄/黄瓜知识，必须报失败。
    assert any(not case["passed"] for case in report["cases"] if case["category"] == "domain_positive")
    assert report == evaluate(str(tmp_path / "index"), samples_per_group=1)
    paths = write_report(report, str(tmp_path / "reports"))
    assert all(path.exists() for path in paths)
