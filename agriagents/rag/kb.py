"""知识库门面：把检索索引、知识图谱、术语表组装成一个可查询对象。

三个部件各司其职，缺一不可：

- ``RagIndex``     —— 模糊检索（"叶子背面有白毛可能是啥"）
- ``CropDpKg``     —— 结构化精确查询（"晚疫病的全部症状是什么"）
- ``TermNormalizer`` —— 跨语言改写（中文提问 → 英文语料）

**跨源对齐**是这一层最关键的职责。CropDP-KG 是中文图谱，
PlantInquiryVQA 是英文卡片，两者讲同一批病害却各说各话。
不把它们连起来，诊断 Agent 就会拿到"中文候选病害"却查不到"英文防治方案"，
最后只能凭空编——这正是框架要防的事。

对齐靠三条线索，按可靠性排序：拉丁学名（最可靠，唯一）→ 英文通用名 → 作物+病害名。
"""

from __future__ import annotations

import os
import threading
from typing import Iterable

from agriagents.rag.config import rag_config
from agriagents.rag.embedder import get_embedder
from agriagents.rag.index import Doc, Hit, RagIndex
from agriagents.rag.kg import CropDpKg, KgEntity, SymptomMatch
from agriagents.rag.normalize import TermNormalizer
from agriagents.rag.text import normalize, snippet


class KnowledgeBase:
    """只读知识库。构造后不再变化，可安全并发使用。"""

    def __init__(
        self,
        index_dir: str,
        *,
        index: RagIndex | None = None,
        kg: CropDpKg | None = None,
        terms: TermNormalizer | None = None,
        embedder=None,
        embedder_error: str = "",
    ) -> None:
        self.index_dir = index_dir
        self.index = index
        self.kg = kg
        self.terms = terms or TermNormalizer([])
        self.embedder = embedder
        self.embedder_error = embedder_error
        self._card_links: dict[str, str] = {}
        self._cards_by_id: dict[str, dict] = {}
        self._by_parent: dict[str, list[Doc]] = {}
        self._by_scientific: dict[str, str] = {}
        self._by_en: dict[str, str] = {}
        if index:
            for doc in index.docs:
                if doc.parent_id:
                    self._by_parent.setdefault(doc.parent_id, []).append(doc)
        self._build_card_links()

    # ------------------------------------------------------------------
    # 可用性
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """有任何一件可用就算可用。只有图谱也能做结构化诊断。"""
        return bool(self.index and self.index.docs) or bool(self.kg and self.kg.entities)

    def describe(self) -> dict:
        info: dict = {"index_dir": self.index_dir, "available": self.available}
        if self.index:
            info["index"] = self.index.profile()
        if self.kg:
            info["graph"] = self.kg.stats()
        info["terms"] = self.terms.stats()
        info["dense"] = self.embedder is not None and bool(
            self.index and self.index.vectors is not None
        )
        if self.embedder_error:
            info["embedder_error"] = self.embedder_error
        return info

    # ------------------------------------------------------------------
    # 跨源对齐
    # ------------------------------------------------------------------

    def _build_card_links(self) -> None:
        """建立 CropDP 实体名 → PlantInquiryVQA disease_id 的映射。

        卡片与图谱来自两个仓库，没有任何共享主键。只能按名字对齐，
        并且必须按"可靠度递减"的顺序尝试，否则会把同科不同种的病害
        错误地合并——那比查不到更危险，因为它会让模型自信地给出错误方案。
        """
        if not self.index:
            return

        by_scientific: dict[str, str] = {}
        by_en: dict[str, str] = {}
        for doc in self.index.docs:
            if doc.kind != "disease_card":
                continue
            disease_id = doc.meta.get("disease_id")
            if not disease_id:
                continue
            self._cards_by_id[disease_id] = doc.meta
            scientific = normalize(doc.meta.get("pathogen_scientific") or "")
            if scientific:
                by_scientific.setdefault(scientific, disease_id)
                # 属 + 种也注册一份：CropDP 常带 "f. sp. xxx" 后缀，
                # 与卡片里的纯学名对不上
                words = scientific.split()
                if len(words) >= 2:
                    by_scientific.setdefault(" ".join(words[:2]), disease_id)
            for alias in [doc.meta.get("disease"), *(doc.meta.get("aliases") or [])]:
                if alias:
                    by_en.setdefault(normalize(alias), disease_id)

        self._by_scientific = by_scientific
        self._by_en = by_en

    def link_card(self, entity: KgEntity) -> dict | None:
        """给图谱实体找到对应的英文知识卡片。找不到返回 None。"""
        if not self._cards_by_id:
            return None
        scientific = normalize(entity.scientific)
        if scientific:
            hit = self._by_scientific.get(scientific)
            if not hit:
                words = scientific.split()
                if len(words) >= 2:
                    hit = self._by_scientific.get(" ".join(words[:2]))
            if hit:
                return self._cards_by_id.get(hit)
        for alias in entity.aliases:
            hit = self._by_en.get(normalize(alias))
            if hit:
                return self._cards_by_id.get(hit)
        hit = self._by_en.get(normalize(entity.name))
        return self._cards_by_id.get(hit) if hit else None

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        kinds: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
        lang: Iterable[str] | None = None,
        expand: bool = True,
    ) -> list[Hit]:
        """检索知识块。``expand`` 打开跨语言改写。

        改写对混合语料是必需的：中文问句若不补上英文名，
        就永远命中不了只有英文的卡片与问答集。
        """
        if not self.index or not self.index.docs:
            return []
        settings = rag_config()
        query_text = self.terms.expand_query(query) if expand else query
        return self.index.search(
            query_text,
            top_k=top_k if top_k is not None else settings["rag_top_k"],
            candidate_k=settings["rag_candidate_k"],
            kinds=kinds,
            sources=sources,
            lang=lang,
            embedder=self.embedder if self.index.vectors is not None else None,
        )

    # ------------------------------------------------------------------
    # 领域查询
    # ------------------------------------------------------------------

    def _with_crop(self, query: str, crop: str) -> str:
        """把作物名的多语言写法拼进查询串。

        这是跨语言检索能成立的关键一步。``expand_query`` 只有在查询里
        **字面出现**某个已知实体时才生效，而"叶子有水浸状斑点"这种纯症状描述
        里不含任何实体名——所以必须由调用方把已知的作物名显式带上。
        缺了它，中文症状 + 英文语料（或反过来）就永远召不回来。
        """
        if not crop:
            return query
        variants = self.terms.expand(crop)
        return " ".join([*variants, query]).strip()

    def diagnose(self, crop: str, symptom: str, *, top_k: int = 5) -> dict:
        """症状 → 候选病害。

        两条路径并用：
        - **图谱路径**（``CropDpKg.match_symptoms``）：结构化、精确，能给出
          完整症状清单与匹配到的具体症状。有图谱时优先。
        - **检索路径**：从 symptom / disease_card 文档里按语义与关键词召回。
          图谱缺失时（例如只建了内置种子库）这是唯一路径，
          图谱存在时它补的是图谱没覆盖到的条目。

        只有一条路径会让系统在"只装了种子库"时完全不可用，
        而框架必须在最小安装下也是通的。
        """
        candidates: list[dict] = []
        seen: set[str] = set()

        if self.kg and self.kg.entities:
            for match in self.kg.match_symptoms(symptom, top_k=top_k * 2):
                if match.entity.name in seen:
                    continue
                candidates.append(self._candidate(match, crop))
                seen.add(match.entity.name)

            # 显式提到的病害名也要进候选：模型可能直接说"疑似晚疫病"
            for entity in self.kg.find_entities(symptom, limit=top_k):
                if entity.name in seen:
                    continue
                candidates.append(
                    self._candidate(SymptomMatch(entity=entity, matched=[], score=0.0), crop)
                )
                seen.add(entity.name)

        if crop:
            filtered = [c for c in candidates if self._crop_matches(c, crop)]
            candidates = filtered or candidates

        text_hits = self.search(
            self._with_crop(symptom, crop),
            top_k=max(top_k * 2, 6),
            kinds=("symptom", "disease_card", "diagnosis"),
        )

        # 检索路径补候选。图谱候选排前面：它的分数是"症状匹配度"，
        # 与检索的 RRF 分数不同量纲，混在一起排序没有意义。
        kg_count = len(candidates)
        for hit in text_hits:
            disease = hit.doc.meta.get("disease") or hit.doc.title
            if not disease or disease in seen:
                continue
            if crop and not self._crop_matches(
                {"crops": [hit.doc.meta.get("crop", "")]}, crop
            ):
                continue
            seen.add(disease)
            candidates.append(
                {
                    "disease": disease,
                    "disease_id": hit.doc.meta.get("disease_id") or hit.doc.doc_id,
                    "scientific_name": hit.doc.meta.get("pathogen_scientific", ""),
                    "aliases": hit.doc.meta.get("aliases") or [],
                    "score": round(hit.score, 4),
                    "matched_symptoms": [snippet(hit.doc.text, 120)],
                    "crops": [hit.doc.meta["crop"]] if hit.doc.meta.get("crop") else [],
                    "parts": [hit.doc.meta["part"]] if hit.doc.meta.get("part") else [],
                    "conditions": [],
                    "temps": [],
                    "regions": [],
                    "linked_card": hit.doc.kind == "disease_card",
                    "pathogen_type": hit.doc.meta.get("pathogen_type", ""),
                    "from": "retrieval",
                }
            )
        for item in candidates[:kg_count]:
            item.setdefault("from", "graph")

        candidates = candidates[:top_k] if kg_count else candidates[: max(top_k, 1)]
        return {
            "crop": crop,
            "symptom": symptom,
            "candidates": candidates,
            "evidence": [
                {
                    "title": h.doc.title,
                    "source": h.doc.source,
                    "text": snippet(h.doc.text, 320),
                    "how": h.how,
                }
                for h in text_hits
            ],
        }

    def _candidate(self, match: SymptomMatch, crop: str) -> dict:
        entity = match.entity
        card = self.link_card(entity)
        return {
            "disease": entity.name,
            "disease_id": (card or {}).get("disease_id", ""),
            "scientific_name": entity.scientific,
            "aliases": entity.aliases[:6],
            "score": match.score,
            "matched_symptoms": match.matched,
            "crops": entity.crops[:8],
            "parts": entity.parts[:8],
            "conditions": entity.conditions[:6],
            "temps": entity.temps[:4],
            "regions": entity.regions[:8],
            "linked_card": bool(card),
            "pathogen_type": (card or {}).get("pathogen_type", ""),
        }

    def _crop_matches(self, candidate: dict, crop: str) -> bool:
        """作物名匹配。跨语言：``tomato`` 要能匹配到 ``番茄``。

        靠术语表把查询词展开成多语言别名再做比较。术语表为空时退化为
        字面比较——那时的行为与没有这一层一样，不会更差。
        """
        if not crop:
            return True
        variants = {normalize(v) for v in [crop, *self.terms.expand(crop)]}
        variants.discard("")
        if not variants:
            return True
        names = {normalize(c) for c in (candidate.get("crops") or [])}
        names |= {normalize(c) for c in (candidate.get("crops_en") or [])}
        names.discard("")
        return any(
            variant in name or name in variant
            for variant in variants
            for name in names
        )

    def profile(self, disease: str) -> dict | None:
        """取某个病害的完整画像（图谱属性 + 关联卡片元数据）。"""
        if not self.kg:
            return None
        entity = self.kg.get(disease) or next(
            (e for e in self.kg.find_entities(disease, limit=1)), None
        )
        if entity is None:
            return None
        return {
            "disease": entity.name,
            "scientific_name": entity.scientific,
            "aliases": entity.aliases,
            "crops": entity.crops,
            "parts": entity.parts,
            "symptoms": entity.symptoms,
            "conditions": entity.conditions,
            "temps": entity.temps,
            "regions": entity.regions,
            "card": self.link_card(entity),
        }

    def resolve_entity(self, name: str) -> KgEntity | None:
        """把一个病名解析到图谱实体。先精确匹配，再退回词典扫描。"""
        if not self.kg or not name:
            return None
        entity = self.kg.get(name)
        if entity is not None:
            return entity
        found = self.kg.find_entities(name, limit=1)
        return found[0] if found else None

    @staticmethod
    def _doc_mentions(doc, anchors: set[str]) -> bool:
        """文档是否确实在讲这个诊断。

        检索兜底必须过这一关。否则"XX 防治 用药 管理"里的通用词会命中
        任意一篇管理文档——结果是：查询一个**根本不存在的病害**，
        系统也会热心地返回一份别的病害的用药方案。这比查不到危险得多，
        因为它看起来是有出处的。
        """
        haystack = normalize(doc.text) + " " + normalize(doc.title)
        for key in ("disease", "disease_id", "entity", "crop", "aliases"):
            value = doc.meta.get(key)
            if isinstance(value, str):
                haystack += " " + normalize(value)
            elif isinstance(value, (list, tuple)):
                haystack += " " + " ".join(normalize(str(v)) for v in value)
        return any(name in haystack for name in anchors)

    def treatments(self, diagnosis: str, *, crop: str = "", top_k: int = 4) -> dict:
        """查询某诊断的处置方案。

        **这是框架安全边界的落点**：查不到就明确返回"无方案"，
        而不是让调用方去猜。执行 Agent 的动作必须有出处，
        ``found=False`` 时上游必须转人工，不能放行。

        取方案分三跳，可靠性递减：
        1. 图谱实体 → 关联卡片的 management 文档（按 parent_id 精确取，最可靠）
        2. 直接命中本地的 management / treatment 文档
        3. 全文检索兜底
        之所以要第 1 跳：模糊检索对"番茄晚疫病"可能召回"马铃薯晚疫病"的
        防治方案——两者病原相同但作物不同，用药与间隔期未必一致。
        能精确取就不要靠相似度。
        """
        plans: list[dict] = []
        seen_ids: set[str] = set()

        entity = self.resolve_entity(diagnosis)
        card = self.link_card(entity) if entity is not None else None

        def push(doc: Doc, score: float, how: str) -> None:
            if doc.doc_id in seen_ids:
                return
            seen_ids.add(doc.doc_id)
            plans.append(
                {
                    "doc_id": doc.doc_id,
                    "disease": doc.meta.get("disease") or doc.title,
                    "crop": doc.meta.get("crop") or "",
                    "disease_id": doc.meta.get("disease_id") or "",
                    "text": doc.text,
                    "how": how,
                    "score": round(score, 6),
                    "fields_missing": doc.meta.get("fields_missing") or [],
                }
            )

        # 第 1 跳：关联卡片的管理面
        if card and card.get("disease_id"):
            for doc in self._by_parent.get(f"plantinquiry:{card['disease_id']}", []):
                if doc.kind == "management":
                    push(doc, 1.0, "linked-card")

        # 第 1.5 跳：内置种子库按 disease 字段精确取
        if entity is not None:
            for doc in self._by_parent.get(f"seed:{entity.name}", []) or []:
                if doc.kind in ("management", "treatment"):
                    push(doc, 1.0, "exact-entity")

        # 第 2/3 跳：检索兜底。候选必须"确实在讲这个诊断"才会被采纳。
        anchors = {normalize(diagnosis)}
        if entity is not None:
            anchors |= {normalize(n) for n in entity.all_names}
        if card and card.get("disease"):
            anchors |= {normalize(card["disease"]), normalize(card.get("disease_id", ""))}
        anchors = {a for a in anchors if len(a) >= 3}

        if len(plans) < top_k and anchors:
            # 兜底查询只用诊断名本身，不加"防治/用药/管理"这类词——
            # 它们是所有管理文档的公共子串，只会把噪声抬上来。
            hits = self.search(
                self._with_crop(diagnosis, crop),
                top_k=top_k * 3,
                kinds=("management", "treatment", "disease_card"),
            )
            for hit in hits:
                if self._doc_mentions(hit.doc, anchors):
                    push(hit.doc, hit.score, hit.how)

        return {
            "diagnosis": diagnosis,
            "crop": crop,
            "found": bool(plans),
            "plans": plans[:top_k],
            "card": card,
        }

    def agronomy(self, question: str, *, top_k: int = 5) -> list[dict]:
        """农艺问答检索：返回带出处的知识块。"""
        hits = self.search(question, top_k=top_k, kinds=("qa_pair", "kg_entity", "environment"))
        if not hits:
            # 第二跳：放宽到全部形态。内置库的条目形态与真实语料不同，
            # 限定 kinds 会让"明明有答案却查不到"。
            hits = self.search(question, top_k=top_k)
        return [
            {
                "title": h.doc.title,
                "source": h.doc.source,
                "dataset": h.doc.meta.get("dataset", ""),
                "verified": bool(h.doc.meta.get("verified")),
                "citation": h.doc.meta.get("citation", ""),
                "text": snippet(h.doc.text, 700),
                "score": round(h.score, 6),
                "how": h.how,
            }
            for h in hits
        ]

    def symptoms_for(self, disease: str, *, top_k: int = 6) -> list[dict]:
        hits = self.search(disease, top_k=top_k, kinds=("symptom", "diagnosis"))
        return [
            {
                "disease": h.doc.meta.get("disease") or h.doc.title,
                "crop": h.doc.meta.get("crop", ""),
                "part": h.doc.meta.get("part_zh") or h.doc.meta.get("part", ""),
                "text": snippet(h.doc.text, 500),
            }
            for h in hits
        ]


# ---------------------------------------------------------------------------
# 构造与缓存
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict[str, KnowledgeBase] = {}


def load_knowledge_base(config: dict | None = None) -> KnowledgeBase:
    """从磁盘加载知识库。任何一件缺失都不致命，能加载多少算多少。"""
    settings = rag_config(config)
    index_dir = settings["rag_index_dir"]

    index = None
    kg = None
    try:
        index = RagIndex.load(index_dir)
    except FileNotFoundError:
        pass
    try:
        kg = CropDpKg.load(index_dir)
    except FileNotFoundError:
        pass
    terms = TermNormalizer.load(index_dir)

    embedder = None
    embedder_error = ""
    if index is not None and index.vectors is not None:
        # 运行期不允许下载模型：一次诊断请求不该卡在几百 MB 的下载上。
        # 模型没缓存就退化为纯 BM25，宁可召回弱一点，也不能让 agent 挂住。
        try:
            embedder = get_embedder(settings, allow_download=False)
        except Exception as exc:  # noqa: BLE001
            embedder_error = repr(exc)
        if embedder is not None and index.vectors is not None:
            expected = index.vectors.shape[1]
            if getattr(embedder, "dim", 0) != expected:
                embedder_error = (
                    f"嵌入维度不符（索引 {expected} 维，当前编码器 "
                    f"{getattr(embedder, 'dim', '?')} 维）。"
                    "已退化为纯 BM25 检索；重跑 tools/build_rag_index.py 可修复。"
                )
                embedder = None
        if embedder is None and not embedder_error:
            embedder_error = (
                "嵌入模型未就绪（未缓存或维度不符），本次退化为纯 BM25 检索。"
                "运行期刻意不下载模型：一次诊断请求不该卡在几百 MB 的下载上。"
            )

    return KnowledgeBase(
        index_dir, index=index, kg=kg, terms=terms,
        embedder=embedder, embedder_error=embedder_error,
    )


def get_knowledge_base(config: dict | None = None, *, force_reload: bool = False) -> KnowledgeBase:
    """按索引目录缓存实例。索引有几十 MB，不能每次工具调用都重载。"""
    settings = rag_config(config)
    key = settings["rag_index_dir"]
    with _lock:
        if force_reload or key not in _cache:
            _cache[key] = load_knowledge_base(config)
        return _cache[key]


def reset_cache() -> None:
    """清空实例缓存。重建索引后调用，或供测试隔离用。"""
    with _lock:
        _cache.clear()
