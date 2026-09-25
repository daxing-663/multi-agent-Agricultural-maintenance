"""RAG 子系统自检：不联网、不下模型，验证"Agent 能找到知识库"。

这组测试守的是一条具体的承诺：**四个 Agent 的知识类工具调用，
必须真的能落到本地知识库并拿到内容**，而不是静默返回占位文案。
往下的每一层都单独测一遍，这样出问题时能立刻定位到是哪一层断了：

    工具声明 → 供应商路由 → 适配器 → 知识库门面 → 索引/图谱

最后一个是端到端的：直接把索引建出来，走 ``route_to_vendor``
（Agent 实际走的那条路）把五个知识工具全调一遍。
"""

import json

import pytest

from agriagents.dataflows.config import run_config
from agriagents.rag import Doc, RagIndex
from agriagents.rag.kg import CropDpKg
from agriagents.rag.sources import seed as seed_source

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _seed_docs() -> list[Doc]:
    """直接从内置语料读出文档，不走下载。"""
    docs = []
    with open(seed_source.SEED_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                docs.append(seed_source._to_doc(json.loads(line)))
    return docs


def _build_seed_index(index_dir, *, with_vectors=False):
    """建一个纯 BM25 的种子索引（不需要嵌入模型，秒级）。"""
    index = RagIndex.build(_seed_docs(), embedder=None)
    index.save(index_dir)
    return index


def _rag_config(tmp_path, **overrides) -> dict:
    from agriagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config.update({
        "rag_index_dir": str(tmp_path / "rag"),
        "debug": False,
        **overrides,
    })
    return config


# ---------------------------------------------------------------------------
# 索引层
# ---------------------------------------------------------------------------


def test_index_bm25_search_finds_seed_entry(tmp_path):
    index = _build_seed_index(tmp_path / "idx")

    hits = index.search("番茄晚疫病 叶片 水浸状病斑", top_k=3)
    assert hits, "BM25 应当能召回内置条目"
    assert any("晚疫病" in h.doc.title or "晚疫病" in h.doc.text for h in hits)
    assert all(h.bm25_rank is not None for h in hits)


def test_index_metadata_filter_restricts_candidates(tmp_path):
    index = _build_seed_index(tmp_path / "idx")

    hits = index.search("故障 处置", top_k=5, kinds=("equipment_manual",))
    assert hits
    assert {h.doc.kind for h in hits} == {"equipment_manual"}


def test_index_roundtrip_preserves_documents(tmp_path):
    index_dir = tmp_path / "idx"
    _build_seed_index(index_dir)
    loaded = RagIndex.load(str(index_dir))

    assert len(loaded.docs) == len(_seed_docs())
    assert loaded.vectors is None
    assert loaded.profile()["dense"] is False


def test_index_load_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        RagIndex.load(str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
# 图谱层
# ---------------------------------------------------------------------------


@pytest.fixture
def mini_kg(tmp_path):
    """手工造一个小图谱目录，不依赖下载。"""
    csv_dir = tmp_path / "cropdp"
    csv_dir.mkdir()

    # 关系表按 CropDP-KG 的列序：编号、实体名、学名、中文值、英文值、中文关系、英文关系
    (csv_dir / "relationsym.csv").write_text(
        "number,Chinese entity name,entity binomial scientific name,Chinese sym,English sym,"
        "Chinese relation,English relation\n"
        "1,番茄晚疫病,Phytophthora infestans,暗绿色水浸状病斑,Dark green water-soaked lesions,存在症状,ManifestAs\n"
        "2,番茄晚疫病,Phytophthora infestans,白色霉状物,White mold,存在症状,ManifestAs\n"
        "3,黄瓜霜霉病,Pseudoperonospora cubensis,多角形黄褐斑,Angular yellow-brown spots,存在症状,ManifestAs\n",
        encoding="utf-8",
    )
    (csv_dir / "relationcrop.csv").write_text(
        "number,Chinese entity name,entity binomial scientific name,Chinese crop name,English crop name,"
        "Chinese relation,English relation\n"
        "1,番茄晚疫病,Phytophthora infestans,番茄,tomato,危害,Damage\n",
        encoding="utf-8",
    )
    (csv_dir / "relationEng.csv").write_text(
        "Entity,English entity\n番茄晚疫病,Tomato late blight\n", encoding="utf-8"
    )
    return csv_dir


def test_kg_parses_columns_by_position_not_by_guess(mini_kg):
    """回归测试：曾经把"拉丁学名"列当成值列，导致症状里装的是中文/学名。"""
    kg = CropDpKg.from_csv_dir(str(mini_kg))
    entity = kg.get("番茄晚疫病")

    assert entity is not None
    assert entity.scientific == "Phytophthora infestans"
    assert "暗绿色水浸状病斑" in entity.symptoms
    assert entity.symptoms_en == ["Dark green water-soaked lesions", "White mold"]
    assert entity.crops == ["番茄"], "crops 不应混入学名"
    assert entity.crops_en == ["tomato"]
    assert entity.aliases == ["Tomato late blight"]


def test_kg_symptom_matching_prefers_relevant_entity(mini_kg):
    kg = CropDpKg.from_csv_dir(str(mini_kg))

    matches = kg.match_symptoms("叶片出现暗绿色水浸状病斑", top_k=3)
    assert matches
    assert matches[0].entity.name == "番茄晚疫病"

    # 英文症状同样要能反查（谱图是中英双语并列的）
    english = kg.match_symptoms("dark green water-soaked lesions on leaves", top_k=3)
    assert english
    assert english[0].entity.name == "番茄晚疫病"


def test_kg_find_entities_prefers_longer_names(mini_kg):
    kg = CropDpKg.from_csv_dir(str(mini_kg))
    found = [e.name for e in kg.find_entities("疑似番茄晚疫病，需与早疫病区分", limit=5)]
    assert "番茄晚疫病" in found


def test_kg_roundtrip(mini_kg, tmp_path):
    kg = CropDpKg.from_csv_dir(str(mini_kg))
    kg.save(str(tmp_path))
    reloaded = CropDpKg.load(str(tmp_path))

    entity = reloaded.get("番茄晚疫病")
    assert entity is not None
    assert entity.aliases == ["Tomato late blight"]
    assert reloaded.stats()["entities"] == kg.stats()["entities"]


# ---------------------------------------------------------------------------
# 端到端：Agent 实际走的那条路
# ---------------------------------------------------------------------------


@pytest.fixture
def routed_kb(tmp_path):
    """建好种子索引并把配置指向它，清掉门面缓存保证隔离。"""
    from agriagents.rag import reset_cache

    config = _rag_config(tmp_path)
    _build_seed_index(config["rag_index_dir"])
    # 术语表也要落盘，否则跨语言改写不生效
    import shutil

    shutil.copy(seed_source.TERM_FILE, tmp_path / "rag" / "term_seed.json")
    from agriagents.rag.normalize import TermNormalizer, TermRecord

    records = [
        TermRecord(term_id=r["id"], pref=r["pref"], labels=r.get("labels") or {}, sources=["seed"])
        for r in seed_source.load_terms()
    ]
    TermNormalizer(records).save(config["rag_index_dir"])

    reset_cache()
    with run_config(config):
        yield
    reset_cache()


def test_all_knowledge_tools_reach_the_local_knowledge_base(routed_kb):
    """五个知识工具必须全部落到本地知识库并返回真实内容。"""
    from agriagents.dataflows.router import route_to_vendor

    cases = [
        ("query_pest_disease_library", ("番茄", "叶片水浸状病斑，湿度大时有白色霉层"), "晚疫病"),
        ("get_treatment_options", ("番茄晚疫病",), "晚疫病"),
        ("query_agronomy_knowledge", ("大棚连作障碍怎么缓解",), None),
        ("query_soil_reference", ("FIELD-07",), None),
        ("query_equipment_manual", ("irrigation_valve", "E04"), None),
    ]

    for tool_name, args, expect in cases:
        output = route_to_vendor(tool_name, *args)
        assert output, f"{tool_name} 返回空"
        assert "未接入真实数据源" not in output, (
            f"{tool_name} 落回了占位实现，说明供应商链没接上知识库：{output[:120]}"
        )
        assert "[local_rag]" in output, f"{tool_name} 未标注来源供应商：{output[:120]}"
        if expect:
            assert expect in output, f"{tool_name} 的内容里没有出现 {expect!r}：{output[:160]}"


def test_cross_language_query_finds_chinese_entry(routed_kb):
    """英文提问要能命中中文条目——靠的是术语表做跨语言改写。"""
    from agriagents.dataflows.router import route_to_vendor

    output = route_to_vendor("query_pest_disease_library", "tomato", "water-soaked spots on leaves")
    assert "晚疫病" in output, f"英文查询未跨语言命中：{output[:200]}"


def test_falls_back_to_stub_when_index_absent(tmp_path):
    """索引不存在时必须回退到占位实现，而不是抛异常中断整个 run。"""
    from agriagents.rag import reset_cache

    config = _rag_config(tmp_path)
    reset_cache()
    try:
        with run_config(config):
            from agriagents.dataflows.router import route_to_vendor

            output = route_to_vendor("query_agronomy_knowledge", "番茄怎么施肥")
        assert "未接入真实数据源" in output, "索引缺失时应当回退到 stub_knowledge"
    finally:
        reset_cache()


def test_treatment_refuses_to_invent_when_not_found(routed_kb):
    """查不到方案时不能拿"相近病害"的方案顶替——适配器必须显式抛错。

    注意这里直接测适配器，不走 ``route_to_vendor``：
    ``get_treatment_options`` 属于 ``OPTIONAL_TOOLS``，路由层在全部供应商
    失败时会降级为哨兵文本而不是抛错（这是给"可选增强数据"设计的语义）。
    要守的安全边界是**适配器不编造**，所以断言点放在适配器上。
    """
    from agriagents.dataflows.errors import NoDataError
    from agriagents.dataflows.vendors import local_rag

    with pytest.raises(NoDataError):
        local_rag.get_treatment_options("完全不存在的病害XYZ")

    # 路由层则会降级为哨兵，且哨兵文本必须自曝其短，不能看起来像真方案
    from agriagents.dataflows.router import route_to_vendor

    fallback = route_to_vendor("get_treatment_options", "完全不存在的病害XYZ")
    assert "未接入真实数据源" in fallback or "不可用" in fallback
