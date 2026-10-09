"""领域检索的正反例：宿主、病害、地块、型号和故障码不能靠相似度代替。"""

import pytest

from agriagents.dataflows.errors import NoDataError
from agriagents.dataflows.vendors import local_rag
from agriagents.rag.index import Doc, RagIndex
from agriagents.rag.kb import KnowledgeBase
from agriagents.rag.kg import CropDpKg, KgEntity
from agriagents.rag.normalize import TermNormalizer, TermRecord

pytestmark = pytest.mark.unit


def _doc(doc_id, kind, *, crop="", disease="", text="叶片水浸状病斑", source="local", **meta):
    return Doc(doc_id, source, kind, text, title=disease or doc_id,
               meta={"crop": crop, "disease": disease, "source_ref": f"fixture:{doc_id}", **meta})


def _terms():
    return TermNormalizer([
        TermRecord("crop:tomato", "番茄", {"zh": ["番茄", "西红柿"], "en": ["tomato", "tomatoes"]}),
        TermRecord("crop:potato", "马铃薯", {"zh": ["马铃薯"], "en": ["potato"]}),
        TermRecord("crop:cucumber", "黄瓜", {"zh": ["黄瓜"], "en": ["cucumber"]}),
    ])


def _kb(docs=(), entities=(), terms=None):
    return KnowledgeBase("unused", index=RagIndex.build(docs),
                         kg=CropDpKg(entities), terms=terms or _terms())


def _entity(name="番茄晚疫病", crop="番茄", scientific="Phytophthora infestans"):
    return KgEntity(name, scientific=scientific, crops=[crop], symptoms=["叶片水浸状病斑"])


def _card(crop, *, disease="Late blight", scientific="Phytophthora infestans", suffix=""):
    identity = crop + suffix
    doc = _doc(identity + ":card", "disease_card", crop=crop, disease=disease,
               disease_id=identity, pathogen_scientific=scientific, source="plantinquiry")
    doc.parent_id = f"plantinquiry:{identity}"
    return doc


def _management(card):
    doc = _doc(card.doc_id.replace(":card", ":management"), "management",
               crop=card.meta["crop"], disease=card.meta["disease"],
               disease_id=card.meta["disease_id"], source=card.source,
               text="只适用于 " + card.meta["crop"])
    doc.parent_id = card.parent_id
    return doc


def test_diagnosis_does_not_restore_other_crop_when_filter_empty():
    kb = _kb([_doc("potato", "symptom", crop="马铃薯", disease="马铃薯晚疫病")],
             [_entity("马铃薯晚疫病", "马铃薯")])
    result = kb.diagnose("番茄", "叶片水浸状病斑")
    assert result["candidates"] == []
    assert result["evidence"] == []


def test_crop_filter_applies_before_retrieval_cutoff_and_handles_crop_lists():
    docs = [_doc(f"noise-{i}", "symptom", crop="potato", disease=f"其他病害{i}") for i in range(30)]
    docs.append(_doc("wanted", "symptom", crops=["番茄"], disease="番茄晚疫病"))
    result = _kb(docs).diagnose("tomato", "叶片水浸状病斑", top_k=1)
    assert [item["doc_id"] for item in result["candidates"]] == ["wanted"]
    assert {item["doc_id"] for item in result["evidence"]} == {"wanted"}


def test_crop_name_alone_cannot_create_a_symptom_match():
    kb = _kb([_doc("unrelated", "symptom", crop="tomato", disease="Late blight",
                   text="tomato late blight water soaked lesions")])
    result = kb.diagnose("番茄", "ZXQW-UNRELATED-999")
    assert result["candidates"] == []
    assert result["evidence"] == []


def test_dense_neighbor_without_symptom_anchor_is_not_diagnostic_evidence():
    import numpy as np

    class FixedEmbedder:
        name = "fixture-dense"
        dim = 2

        def encode(self, texts):
            return np.array([[1.0, 0.0] for _ in texts], dtype="float32")

    kb = _kb([
        _doc("anchored", "symptom", crop="tomato", disease="Disease A", text="angular lesions"),
        _doc("neighbor", "symptom", crop="tomato", disease="Disease B", text="root decay"),
    ])
    kb.index.vectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype="float32")
    kb.index.embedder_name = FixedEmbedder.name
    kb.embedder = FixedEmbedder()
    assert {h.doc.doc_id for h in kb.search("angular lesions")} == {"anchored", "neighbor"}
    result = kb.diagnose("tomato", "angular lesions")
    assert {item["doc_id"] for item in result["candidates"]} == {"anchored"}
    assert {item["doc_id"] for item in result["evidence"]} == {"anchored"}


def test_crop_identity_is_not_substring_or_legacy_disease_alias():
    terms = _terms()
    terms.records["disease:legacy"] = TermRecord(
        "disease:legacy", "错误别名组", {"zh": ["番茄", "马铃薯"], "en": ["potato"]})
    terms._rebuild()
    kb = _kb(terms=terms)
    assert not kb._crop_matches({"crop": "cherry tomato"}, "tomato")
    assert not kb._crop_matches({"crop": "马铃薯"}, "番茄")
    assert kb._crop_matches({"crop": "tomatoes"}, "西红柿")
    assert not kb._crop_matches({"crop": "tomato", "crops": ["tomato", "potato"]}, "potato")


def test_text_evidence_is_not_hidden_behind_all_graph_candidates():
    entities = [KgEntity(f"图谱病害{i}", crops=["番茄"], symptoms=["叶片斑点"]) for i in range(8)]
    kb = _kb([_doc("text-only", "symptom", crop="番茄", disease="文档病害",
                   text="叶片斑点呈菱形，边缘呈锯齿状")], entities)
    result = kb.diagnose("番茄", "叶片斑点呈菱形，边缘呈锯齿状", top_k=5)
    assert "文档病害" in [item["disease"] for item in result["candidates"]]
    assert all(item["score_method"] == "reciprocal_rank_fusion" for item in result["candidates"])


def test_disease_rank_fusion_uses_one_vote_per_route_not_document_count():
    docs = [_doc(f"duplicate-{i}", "symptom", crop="番茄", disease="番茄晚疫病") for i in range(8)]
    kb = _kb(docs, [_entity()])
    candidate = kb.diagnose("番茄", "叶片水浸状病斑")["candidates"][0]
    assert candidate["ranks"] == {"graph": 1, "retrieval": 1}
    assert candidate["score"] == round(2 / 61, 6)
    assert len(candidate["supporting_evidence"]) == 9


def test_explicit_document_identity_bridges_incomplete_graph_without_guessing_hosts():
    anchor = _doc("seed:anchor", "disease_card", crop="黄瓜", disease="黄瓜某病",
                  pathogen_scientific="Pathogen exactus", source="seed")
    public = _card("cucumber", disease="Defined disease", scientific="Pathogen exactus")
    wrong_host = _card("tomato", disease="Defined disease", scientific="Pathogen exactus")
    symptom = _doc("seed:symptom", "symptom", crop="黄瓜", disease="黄瓜某病", source="seed")
    public_symptom = _doc("public:symptom", "symptom", crop="cucumber", disease="Defined disease",
                          disease_id=public.meta["disease_id"], source="plantinquiry")
    kb = _kb([anchor, public, wrong_host, symptom, public_symptom,
              _management(public), _management(wrong_host)],
             [KgEntity("黄瓜某病", scientific="Pathogen exactus")])
    # 原图谱缺宿主，保持不可直接使用；已明确的卡片可以提供另一条可审计的身份路径。
    assert kb.resolve_entity("黄瓜某病", crop="黄瓜") is None
    result = kb.treatments("黄瓜某病", crop="黄瓜")
    assert [plan["doc_id"] for plan in result["plans"]] == ["cucumber:management"]
    candidates = kb.diagnose("黄瓜", "叶片水浸状病斑")["candidates"]
    assert len(candidates) == 1
    assert set(candidates[0]["disease_names"]) == {"黄瓜某病", "Defined disease"}
    evidence = candidates[0]["supporting_evidence"]
    assert any(item["demo"] and item["source"] == "seed" for item in evidence)
    assert any(not item["demo"] and item["source"] == "plantinquiry" for item in evidence)


def test_same_pathogen_cards_are_linked_only_with_the_correct_crop():
    potato, tomato = _card("potato"), _card("tomato")
    kb = _kb([potato, tomato, _management(potato), _management(tomato)], [_entity()])
    assert kb.link_card(_entity())["disease_id"] == "tomato"
    plans = kb.treatments("番茄晚疫病", crop="番茄")["plans"]
    assert [p["doc_id"] for p in plans] == ["tomato:management"]
    assert plans[0]["source"] == "plantinquiry"
    assert plans[0]["source_ref"] == "fixture:tomato:management"


def test_same_pathogen_other_crop_only_has_no_link_or_treatment():
    potato = _card("potato")
    kb = _kb([potato, _management(potato)], [_entity()])
    assert kb.link_card(_entity()) is None
    assert not kb.treatments("番茄晚疫病", crop="番茄")["found"]


def test_pathogen_special_form_must_not_be_truncated_to_species():
    card = _card("tomato", disease="Other wilt", scientific="Fusarium oxysporum")
    entity = _entity("番茄枯萎病", scientific="Fusarium oxysporum f. sp. lycopersici")
    assert _kb([card]).link_card(entity) is None


def test_ambiguous_same_host_pathogen_cards_are_not_arbitrarily_selected():
    a = _card("tomato", disease="Disease A", suffix="-a")
    b = _card("tomato", disease="Disease B", suffix="-b")
    assert _kb([a, b]).link_card(_entity()) is None


def test_shared_scientific_name_requires_unique_crop_entity():
    kb = _kb(entities=[_entity(), _entity("马铃薯晚疫病", "马铃薯")])
    assert kb.resolve_entity("Phytophthora infestans") is None
    assert kb.resolve_entity("Phytophthora infestans", crop="tomato").name == "番茄晚疫病"


def test_treatment_matches_metadata_not_incidental_body_mentions():
    docs = [
        _doc("wanted", "treatment", crop="番茄", disease="番茄晚疫病", text="原文方案"),
        _doc("neighbor", "treatment", crop="番茄", disease="番茄早疫病", text="须与番茄晚疫病鉴别"),
        _doc("unclear", "treatment", disease="番茄晚疫病"),
        _doc("no-plan", "disease_card", crop="番茄", disease="番茄晚疫病"),
    ]
    kb = _kb(docs)
    assert [p["doc_id"] for p in kb.treatments("番茄晚疫病", crop="tomato")["plans"]] == ["wanted"]
    assert not kb.treatments("完全不存在的病害", crop="tomato")["found"]
    assert not kb.treatments("番茄晚疫病", crop="potato")["found"]


def test_card_without_management_is_not_a_treatment():
    assert not _kb([_card("tomato")]).treatments("Late blight", crop="tomato")["found"]


def test_treatment_without_crop_rejects_ambiguous_hosts():
    docs = [_doc(host, "treatment", crop=host, disease="Late blight") for host in ("tomato", "potato")]
    kb = _kb(docs)
    assert not kb.treatments("Late blight")["found"]
    assert [p["doc_id"] for p in kb.treatments("Late blight", crop="tomato")["plans"]] == ["tomato"]


def test_treatment_without_crop_accepts_unique_explicit_disease_scope():
    kb = _kb([_doc("tomato", "management", crop="番茄", disease="番茄晚疫病")])
    assert kb.treatments("番茄晚疫病")["found"]


def test_treatment_primary_crop_is_not_overridden_by_general_host_list():
    kb = _kb([_doc("tomato-plan", "management", crop="番茄", crops=["番茄", "马铃薯"],
                   disease="Late blight")],
             [KgEntity("马铃薯晚疫病", english="Late blight", crops=["马铃薯"])])
    assert not kb.treatments("马铃薯晚疫病")["found"]


def test_soil_requires_exact_site_id_and_excludes_templates():
    kb = _kb([
        _doc("exact", "soil_reference", site_ids=["FIELD-07", "PLOT-7"]),
        _doc("neighbor", "soil_reference", site_id="FIELD-071"),
        _doc("template", "soil_reference", site_id="FIELD-07", is_template=True),
        _doc("body-only", "soil_reference", text="FIELD-07 土壤档案"),
    ])
    assert [hit.doc.doc_id for hit in kb.soil_reference("field-07")] == ["exact"]
    assert [hit.doc.doc_id for hit in kb.soil_reference("PLOT-7")] == ["exact"]
    assert kb.soil_reference("FIELD-0") == []
    assert kb.soil_reference("") == []


@pytest.mark.parametrize("kind,metadata,query", [
    ("soil_reference", {"site_id": "FIELD-07"}, lambda kb, k: kb.soil_reference("FIELD-07", top_k=k)),
    ("equipment_manual", {"model": "V1", "fault_code": "E04"},
     lambda kb, k: kb.equipment_manual("V1", "E04", top_k=k)),
])
def test_exact_object_records_prefer_non_demo_without_hiding_other_sources(kind, metadata, query):
    kb = _kb([
        _doc("a-demo", kind, evidence_status="demo", **metadata),
        _doc("z-local", kind, **metadata),
    ])
    assert [hit.doc.doc_id for hit in query(kb, 1)] == ["z-local"]
    assert [hit.doc.doc_id for hit in query(kb, 3)] == ["z-local", "a-demo"]


def test_treatments_prefer_non_demo_without_hiding_demo_sources():
    kb = _kb([
        _doc("a-demo", "treatment", crop="番茄", disease="番茄晚疫病", source="seed"),
        _doc("z-local", "treatment", crop="番茄", disease="番茄晚疫病"),
    ])
    assert [p["doc_id"] for p in kb.treatments("番茄晚疫病", crop="番茄", top_k=1)["plans"]] == ["z-local"]
    assert [p["doc_id"] for p in kb.treatments("番茄晚疫病", crop="番茄")["plans"]] == ["z-local", "a-demo"]


@pytest.mark.parametrize("device,fault,expected", [
    ("DEMO-VALVE-V1", "E04", True), ("VALVE-11", "e04", True),
    ("V-11", "E04", True), ("OTHER-VALVE", "E04", False),
    ("VALVE-11", "E05", False), ("irrigation_valve", "E04", False),
    ("VALVE-1", "E04", False), ("VALVE-11", "", False),
])
def test_equipment_requires_exact_device_or_model_and_fault(device, fault, expected):
    kb = _kb([
        _doc("exact", "equipment_manual", model="DEMO-VALVE-V1", device_ids=["VALVE-11"],
             device_aliases=["V-11"], device_type="irrigation_valve", fault_code="E04"),
        _doc("type-only", "equipment_manual", device_type="irrigation_valve", fault_code="E04"),
    ])
    assert bool(kb.equipment_manual(device, fault)) is expected


def test_adapters_render_provenance_demo_and_accurate_source(monkeypatch):
    docs = [
        _doc("seed-plan", "management", crop="番茄", disease="番茄晚疫病", source="seed"),
        _doc("seed-symptom", "symptom", crop="番茄", disease="番茄晚疫病", source="seed"),
        _doc("seed-soil", "soil_reference", site_id="FIELD-07", is_demo=True),
        _doc("seed-device", "equipment_manual", model="V1", fault_code="E04", evidence_status="demo"),
        _doc("qa", "qa_pair", text="叶片水浸状病斑", verified=True),
    ]
    monkeypatch.setattr(local_rag, "_kb", lambda: _kb(docs))
    outputs = [
        local_rag.get_treatment_options("番茄晚疫病", "tomato"),
        local_rag.query_pest_disease_library("番茄", "叶片水浸状病斑"),
        local_rag.query_soil_reference("FIELD-07"),
        local_rag.query_equipment_manual("V1", "E04"),
    ]
    for output in outputs:
        assert "doc_id=" in output and "source=" in output and "fixture:" in output
        assert "演示/模拟数据" in output
    assert "PlantInquiryVQA 知识卡片" not in outputs[0]
    qa = local_rag.query_agronomy_knowledge("叶片水浸状病斑")
    assert "上游标注已验证" in qa
    assert "已人工核验" not in qa


def test_adapters_raise_for_missing_identity_instead_of_nearby_record(monkeypatch):
    kb = _kb([
        _doc("soil", "soil_reference", site_id="FIELD-07"),
        _doc("manual", "equipment_manual", model="V1", fault_code="E04"),
    ])
    monkeypatch.setattr(local_rag, "_kb", lambda: kb)
    with pytest.raises(NoDataError):
        local_rag.query_soil_reference("FIELD-08")
    with pytest.raises(NoDataError):
        local_rag.query_equipment_manual("V1", "E05")
    with pytest.raises(NoDataError):
        local_rag.get_treatment_options("晚疫病", "番茄")


def test_agronomy_includes_local_agronomy_without_domain_fallback():
    kb = _kb([
        _doc("public-qa", "qa_pair", text="番茄水肥管理"),
        _doc("local-agronomy", "agronomy", text="番茄水肥管理"),
        _doc("private-soil", "soil_reference", site_id="FIELD-07", text="UNICODE-SOIL-SECRET"),
        _doc("private-device", "equipment_manual", model="V1", fault_code="E04", text="DEVICE-SECRET"),
        _doc("private-treatment", "treatment", crop="番茄", disease="晚疫病", text="TREATMENT-SECRET"),
    ])
    assert {hit["doc_id"] for hit in kb.agronomy("番茄水肥管理")} == {"public-qa", "local-agronomy"}
    for query in ("UNICODE-SOIL-SECRET", "DEVICE-SECRET", "TREATMENT-SECRET"):
        assert kb.agronomy(query) == []


def test_agronomy_filters_crop_before_ranking_and_rejects_crop_only_food_hits():
    kb = _kb([
        _doc("tomato-water", "qa_pair", crop="番茄", text="番茄应调整浇水和施肥，采用滴灌。"),
        _doc("potato-water", "qa_pair", crop="马铃薯", text="马铃薯浇水施肥水肥管理，应采用滴灌。"),
        _doc("tomato-food", "qa_pair", crop="番茄", text="番茄红素与人体营养，不是种植资料。"),
        _doc("tomato-taxonomy", "kg_entity", crop="番茄", text="番茄浇水施肥管理属于植物分类。"),
    ])
    hits = kb.agronomy("番茄如何浇水施肥？")
    assert [hit["doc_id"] for hit in hits] == ["tomato-water"]
    assert hits[0]["crop_scope"] == "crop_matched"


def test_agronomy_keeps_general_facility_evidence_and_marks_scope_unverified():
    doc = _doc("general", "agronomy", text="温室湿度高时，应采用通风排湿，避免过量浇水。")
    kb = _kb([doc, _doc("other-crop", "agronomy", crop="马铃薯", text=doc.text)])
    hits = kb.agronomy("黄瓜棚湿度太高怎么调整？")
    assert [hit["doc_id"] for hit in hits] == ["general"]
    assert hits[0]["crop_scope"] == "general_agronomy"
    assert hits[0]["applicability_validated"] is False


def test_agronomy_does_not_replace_transplant_question_with_fruiting_advice():
    kb = _kb([_doc("fruiting-only", "qa_pair", crop="黄瓜",
                   text="黄瓜结果期应增加浇水与追肥，注意通风。")])
    assert kb.agronomy("黄瓜刚栽进温室，浇水施肥有哪些注意事项？") == []


def test_agronomy_control_intent_uses_crop_checked_treatments():
    anchor = _doc("named", "disease_card", crop="番茄", disease="番茄甲病",
                  pathogen_scientific="Pathogen alpha")
    card = _card("tomato", disease="Disease alpha", scientific="Pathogen alpha")
    wrong = _card("potato", disease="Disease alpha", scientific="Pathogen alpha")
    kb = _kb([anchor, card, wrong, _management(card), _management(wrong),
              _doc("taxonomy", "kg_entity", crop="番茄", text="番茄甲病病原分类与防治管理")])
    hits = kb.agronomy("番茄棚已经出现甲病，如何调整管理减缓蔓延？")
    assert [hit["doc_id"] for hit in hits] == ["tomato:management"]
    assert hits[0]["applicability_validated"] is True


def test_agronomy_does_not_open_arbitrary_management_on_generic_questions():
    kb = _kb([_doc("unrelated-treatment", "management", crop="番茄", disease="番茄未知病",
                   text="番茄浇水施肥水肥管理，采用某病处置方案。")])
    assert kb.agronomy("番茄如何管理浇水施肥？") == []


def test_explicit_question_crop_overrides_legacy_answer_crop_metadata():
    terms = _terms()
    terms.records["crop:rose"] = TermRecord("crop:rose", "月季", {"zh": ["月季"], "en": ["rose"]})
    terms._rebuild()
    doc = _doc("wrong-crop", "qa_pair", crop="番茄", text="月季应调整浇水和施肥。")
    doc.title = "月季如何浇水施肥？"
    assert _kb([doc], terms=terms).agronomy("番茄如何浇水施肥？") == []


def test_paddy_scope_is_recognized_even_when_old_import_marked_general():
    doc = _doc("paddy-rate", "qa_pair", text="Apply fertilizer after transplanting paddy.", crop_scope="general")
    doc.title = "Post transplanting fertilization of paddy"
    kb = _kb([doc])
    assert kb._agronomy_scope(doc, ["cucumber"]) is None
    assert kb.agronomy("黄瓜定植之后如何施肥？") == []


def test_general_unknown_crop_rate_is_not_exposed_as_requested_crop_advice():
    doc = _doc("unknown-host-dose", "qa_pair", text="移栽定植后应施肥，施用肥料 2 kg。", crop_scope="general")
    assert _kb([doc]).agronomy("黄瓜移栽定植之后如何施肥？") == []


@pytest.mark.parametrize("crop", ["黄瓜", "番茄"])
def test_curated_source_summary_supports_multiple_explicit_crops_without_claiming_validation(crop):
    doc = _doc("curated:fertigation", "agronomy", source="curated", crops=["番茄", "黄瓜"],
               crop_scope="explicit", evidence_status="source_summary",
               text="移栽定植后应检查灌溉浇水及施肥，保持适当水肥并避免过量。")
    hits = _kb([doc]).agronomy(f"{crop}定植后浇水施肥有哪些注意事项？")
    assert [hit["doc_id"] for hit in hits] == [doc.doc_id]
    assert hits[0]["evidence_status"] == "source_summary"
    assert not hits[0]["demo"]
    assert not hits[0]["applicability_validated"]


def test_cache_refreshes_when_same_directory_is_replaced(tmp_path):
    from agriagents.rag.kb import get_knowledge_base, reset_cache

    index_dir = tmp_path / "index"
    stage = tmp_path / "stage"
    config = {"rag_index_dir": str(index_dir), "rag_embedding_backend": "none"}
    RagIndex.build([_doc("old", "symptom", crop="番茄")]).save(str(index_dir))
    reset_cache()
    first = get_knowledge_base(config)
    assert get_knowledge_base(config) is first
    RagIndex.build([_doc("new", "symptom", crop="黄瓜")]).save(str(stage))
    index_dir.rename(tmp_path / "backup")
    stage.rename(index_dir)
    second = get_knowledge_base(config)
    assert second is not first
    assert [doc.doc_id for doc in second.index.docs] == ["new"]
    reset_cache()


@pytest.mark.parametrize("changed", ["terms", "kg"])
def test_cache_refreshes_when_terms_or_graph_changes(tmp_path, changed):
    from agriagents.rag.kb import get_knowledge_base, reset_cache

    config = {"rag_index_dir": str(tmp_path), "rag_embedding_backend": "none"}
    RagIndex.build([_doc("document", "symptom", crop="番茄")]).save(str(tmp_path))
    reset_cache()
    first = get_knowledge_base(config)
    if changed == "terms":
        _terms().save(str(tmp_path))
    else:
        CropDpKg([_entity()]).save(str(tmp_path))
    second = get_knowledge_base(config)
    assert second is not first
    assert second.terms.lookup("tomato") if changed == "terms" else second.kg.get("番茄晚疫病")
    reset_cache()


def test_cache_separates_embedding_configuration(tmp_path, monkeypatch):
    import agriagents.rag.kb as kb_module

    loaded = []

    def load(config):
        loaded.append(config["rag_embedding_model"])
        return object()

    monkeypatch.setattr(kb_module, "load_knowledge_base", load)
    kb_module.reset_cache()
    config = {"rag_index_dir": str(tmp_path), "rag_embedding_model": "model-a"}
    first = kb_module.get_knowledge_base(config)
    other = kb_module.get_knowledge_base({**config, "rag_embedding_model": "model-b"})
    assert other is not first
    assert kb_module.get_knowledge_base(config) is first
    assert loaded == ["model-a", "model-b"]
    kb_module.reset_cache()
