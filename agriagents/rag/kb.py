"""知识库门面：把检索索引、知识图谱、术语表组装成一个可查询对象。

三个部件各司其职，缺一不可：

- ``RagIndex``     —— 模糊检索（"叶子背面有白毛可能是啥"）
- ``CropDpKg``     —— 结构化精确查询（"晚疫病的全部症状是什么"）
- ``TermNormalizer`` —— 跨语言改写（中文提问 → 英文语料）

**跨源对齐**是这一层最关键的职责。CropDP-KG 是中文图谱，
PlantInquiryVQA 是英文卡片，两者讲同一批病害却各说各话。
不把它们连起来，诊断 Agent 就会拿到"中文候选病害"却查不到"英文防治方案"，
最后只能凭空编——这正是框架要防的事。

对齐必须同时满足病害身份与作物范围；同一病原可感染不同作物，学名不是工单主键。
"""

from __future__ import annotations

import os
import re
import threading
from typing import Iterable

from agriagents.rag.config import rag_config
from agriagents.rag.embedder import get_embedder
from agriagents.rag.index import Doc, Hit, RagIndex
from agriagents.rag.kg import CropDpKg, KgEntity, SymptomMatch
from agriagents.rag.normalize import TermNormalizer
from agriagents.rag.text import normalize, query_terms, snippet


# 仅改写主题词汇，不生成栽培措施或从作物推断病害。
_AGRONOMY_CONCEPTS = (
    ("浇水", "灌溉", "灌水", "滴灌", "水分", "水肥", "watering", "irrigation", "irrigate"),
    ("施肥", "追肥", "肥料", "养分", "水肥", "fertilizer", "fertilization", "fertilisation", "fertility"),
    ("湿度", "降湿", "除湿", "叶面潮湿", "露水", "结露", "humidity", "condensation", "leaf wetness"),
    ("连作", "重茬", "连着种", "连续种植", "土传", "continuous cropping", "soilborne", "crop rotation"),
    ("盐分", "盐渍", "盐碱", "根区ec", "salinity", "salinization", "salt accumulation"),
    ("通风", "ventilation", "air circulation"),
)
_GROWTH_STAGES = (
    ("定植", "刚栽", "移栽", "缓苗", "transplant", "transplanting"),
    ("结果", "坐果", "膨果", "膨大", "fruiting", "fruit set", "fruit development"),
)
_MANAGEMENT_WORDS = (
    "管理", "防治", "打药", "用药", "减缓", "减少", "改善", "调整", "注意事项", "栽培", "措施",
    "control", "treat", "treatment", "manage", "management", "reduce", "improve", "cultivation",
)
_ACTION_WORDS = _MANAGEMENT_WORDS + (
    "采用", "避免", "保持", "降低", "控制", "选用", "清除", "轮作", "嫁接", "增施", "排湿", "灌水",
    "浇水", "追肥", "施肥", "通风", "防止", "应当", "应及时", "建议", "需要", "注意",
    "use", "avoid", "maintain", "apply", "irrigate", "fertilize", "remove", "rotate", "prevent",
)
_PADDY_ALIASES = ("paddy", "sali paddy", "kharif paddy")
_UNSCOPED_RATE = re.compile(
    r"\d[\d.,]*\s*(?:(?:kg|mg|g|ml|l|ppm)(?![a-z])|公斤|千克|毫克|克|毫升|升)", re.IGNORECASE
)


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
        self._by_scientific: dict[str, list[str]] = {}
        self._by_en: dict[str, list[str]] = {}
        self._crop_aliases: dict[str, set[str]] = {}
        for record in self.terms.records.values():
            # 旧索引曾把作物写进病害的别名，只信独立的作物词条。
            if record.term_id.startswith("crop:") or ":crop:" in record.term_id:
                names = {normalize(v) for v in [record.pref, *record.all_labels()] if v}
                for name in list(names):
                    names.update(self._crop_aliases.get(name, set()))
                for name in names:
                    self._crop_aliases[name] = set(names)
        rice = {"水稻", "rice", *_PADDY_ALIASES} | self._crop_aliases.get("rice", set())
        for name in rice:
            self._crop_aliases[name] = set(rice)
        self._crop_pattern = re.compile("|".join(
            self._label_pattern(name) for name in sorted(self._crop_aliases, key=len, reverse=True)
            if len(name) >= 2
        ) or r"(?!)")
        self._document_crops: dict[str, list[str]] = {}
        self._agronomy_features: dict[str, tuple[int, int, bool, int, bool]] = {}
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

        by_scientific: dict[str, list[str]] = {}
        by_en: dict[str, list[str]] = {}
        for doc in self.index.docs:
            if doc.kind != "disease_card":
                continue
            disease_id = doc.meta.get("disease_id")
            if not disease_id:
                continue
            self._cards_by_id[disease_id] = {**doc.meta, **self._provenance(doc), "parent_id": doc.parent_id}
            scientific = normalize(doc.meta.get("pathogen_scientific") or "")
            if scientific:
                by_scientific.setdefault(scientific, []).append(disease_id)
            for alias in [doc.meta.get("disease"), *(doc.meta.get("aliases") or [])]:
                if alias:
                    by_en.setdefault(normalize(alias), []).append(disease_id)

        self._by_scientific = by_scientific
        self._by_en = by_en

    def link_card(self, entity: KgEntity, *, crop: str = "") -> dict | None:
        """只关联病害与作物均相容且唯一的卡片；不截断病原专化型。"""
        if not self._cards_by_id:
            return None
        hosts = {"crops": entity.crops, "crops_en": entity.crops_en}
        if crop and not self._crop_matches(hosts, crop):
            return None
        scope = [crop] if crop else [*entity.crops, *entity.crops_en]
        if not scope:
            return None
        named = set()
        for name in [entity.name, *entity.aliases]:
            named.update(self._by_en.get(normalize(name), []))
        scientific = set(self._by_scientific.get(normalize(entity.scientific), []))

        def eligible(ids):
            return [self._cards_by_id[key] for key in sorted(ids)
                    if any(self._crop_matches(self._cards_by_id[key], host) for host in scope)]

        # 同宿主存在多个同病原病害时，也不能用第一个卡片消除歧义。
        matches = eligible(named) or eligible(scientific)
        return matches[0] if len(matches) == 1 else None

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
        allowed_doc_ids: Iterable[str] | None = None,
        meta_filters: dict | None = None,
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
            allowed_doc_ids=allowed_doc_ids,
            meta_filters=meta_filters,
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
        variants = sorted(self._crop_aliases.get(normalize(crop), {crop}))
        return " ".join([*variants, query]).strip()

    @staticmethod
    def _values(meta: dict, *keys: str) -> list[str]:
        values = []
        for key in keys:
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value)
            elif isinstance(value, (list, tuple, set)):
                values.extend(v for v in value if isinstance(v, str) and v.strip())
        return values

    @staticmethod
    def _provenance(doc: Doc) -> dict:
        demo = doc.source == "seed" or bool(
            doc.meta.get("is_demo") or doc.meta.get("demo") or doc.meta.get("evidence_status") == "demo"
        )
        return {
            "doc_id": doc.doc_id,
            "source": doc.source,
            "source_ref": doc.meta.get("source_ref") or doc.meta.get("citation") or "",
            "citation": doc.meta.get("citation") or doc.meta.get("source_ref") or "",
            "demo": demo,
            "evidence_status": "demo" if demo else doc.meta.get("evidence_status", "unverified"),
        }

    def crop_doc_matches(self, doc: Doc, crop: str) -> bool:
        """供工具目录与领域检索复用的作物元数据精确匹配。"""
        return self._crop_matches(doc.meta, crop)

    def _anchor_card_document(self, disease: str, crop: str) -> Doc | None:
        """读取已有卡片明确声明的病害与宿主，不从病害名字猜作物。"""
        key = normalize(disease)
        found = [doc for doc in self.index.docs if doc.kind == "disease_card"
                 and key in {normalize(v) for v in self._values(doc.meta, "disease", "disease_id", "aliases")}
                 and (not crop or self.crop_doc_matches(doc, crop))] if self.index else []
        # 不同宿主的同名卡片不能凭顺序选定；相同作用域优先非演示源。
        scopes = {tuple(sorted(normalize(v) for v in self._values(doc.meta, "crop")
                               or self._values(doc.meta, "crops"))) for doc in found}
        if not found or len(scopes) != 1:
            return None
        return min(found, key=lambda doc: (self._provenance(doc)["demo"], doc.doc_id))

    def _linked_doc_card(self, doc: Doc, crop: str) -> dict | None:
        """将来源明确的病害卡片桥接到唯一、同作物的公开卡片。"""
        direct = self._cards_by_id.get(doc.meta.get("disease_id", ""))
        if direct and (not crop or self._crop_matches(direct, crop)):
            return direct
        anchor = doc if doc.kind == "disease_card" else self._anchor_card_document(
            doc.meta.get("disease") or doc.title, crop
        )
        if anchor is None:
            return None
        hosts = self._values(anchor.meta, "crop") or self._values(anchor.meta, "crops")
        identity = KgEntity(
            name=anchor.meta.get("disease") or anchor.title,
            scientific=anchor.meta.get("pathogen_scientific", ""),
            english=self._values(anchor.meta, "aliases"),
            crops=hosts,
        )
        return self.link_card(identity, crop=crop)

    def diagnose(self, crop: str, symptom: str, *, top_k: int = 5) -> dict:
        """按病害身份融合图谱与文本两路排名；排名分不是诊断置信度。

        两路分数的量纲不同，因此使用 reciprocal rank fusion。每个病害在
        每一路只投一票，不让重复切块挤占名额，也不让图谱缺项压掉文本证据。
        """
        merged: dict[tuple, dict] = {}

        def key_for(item: dict) -> tuple:
            if item.get("disease_id") in self._cards_by_id:
                return ("card", item["disease_id"])
            return ("disease", normalize(item["disease"]))

        def add(item: dict, route: str, rank: int, evidence: dict) -> None:
            key = key_for(item)
            if key not in merged:
                merged[key] = {**item, "ranks": {}, "supporting_evidence": [], "disease_names": []}
            candidate = merged[key]
            if item["disease"] not in candidate["disease_names"]:
                candidate["disease_names"].append(item["disease"])
            if any("\u4e00" <= ch <= "\u9fff" for ch in item["disease"]) and not any(
                "\u4e00" <= ch <= "\u9fff" for ch in candidate["disease"]
            ):
                candidate["disease"] = item["disease"]
            candidate["ranks"].setdefault(route, rank)
            candidate["supporting_evidence"].append(evidence)
            if route == "graph":
                candidate["graph_score"] = item["score"]
            else:
                candidate["retrieval_score"] = max(candidate.get("retrieval_score", 0), item["score"])
                for text in item["matched_symptoms"]:
                    if text not in candidate["matched_symptoms"]:
                        candidate["matched_symptoms"].append(text)
            candidate["from"] = "+".join(candidate["ranks"])

        graph_seen = set()
        if self.kg and self.kg.entities:
            matches = self.kg.match_symptoms(symptom, top_k=len(self.kg.entities))
            named = self.kg.find_entities(symptom, limit=top_k)
            matched_names = {match.entity.name for match in matches}
            matches.extend(SymptomMatch(entity=entity, matched=[], score=0.0)
                           for entity in named if entity.name not in matched_names)
            for match in matches:
                if crop and not self._crop_matches(
                    {"crops": match.entity.crops, "crops_en": match.entity.crops_en}, crop
                ):
                    continue
                item = self._candidate(match, crop)
                key = key_for(item)
                if key in graph_seen:
                    continue
                graph_seen.add(key)
                add(item, "graph", len(graph_seen), {
                    key: item[key] for key in ("doc_id", "source", "source_ref", "demo")
                } | {"matched_symptoms": match.matched, "how": "graph-symptoms"})

        allowed = None
        if crop and self.index:
            allowed = {doc.doc_id for doc in self.index.docs if self.crop_doc_matches(doc, crop)}
        text_hits = self.search(
            symptom, top_k=max(top_k * 4, 12),
            kinds=("symptom", "disease_card", "diagnosis"), allowed_doc_ids=allowed,
        )
        # 纯向量近邻只能作泛检索线索；诊断候选还必须有词汇/同义词证据。
        text_hits = [hit for hit in text_hits if hit.bm25_score > 0]
        text_ranks = {}
        for hit in text_hits:
            doc = hit.doc
            disease = doc.meta.get("disease") or doc.title
            if not disease or (crop and not self.crop_doc_matches(doc, crop)):
                continue
            card = self._linked_doc_card(doc, crop)
            item = {
                "disease": disease,
                "disease_id": (card or {}).get("disease_id") or doc.meta.get("disease_id") or doc.doc_id,
                "scientific_name": doc.meta.get("pathogen_scientific", ""),
                "aliases": doc.meta.get("aliases") or [],
                "score": hit.score,
                "matched_symptoms": [snippet(doc.text, 160)],
                "crops": self._values(doc.meta, "crop") or self._values(doc.meta, "crops"),
                "parts": [doc.meta["part"]] if doc.meta.get("part") else [],
                "conditions": [], "temps": [], "regions": [],
                "linked_card": bool(card),
                "pathogen_type": doc.meta.get("pathogen_type", ""),
                **self._provenance(doc),
            }
            key = key_for(item)
            text_ranks.setdefault(key, len(text_ranks) + 1)
            add(item, "retrieval", text_ranks[key], {
                **self._provenance(doc), "text": snippet(doc.text, 320),
                "how": hit.how, "bm25_score": hit.bm25_score,
            })

        candidates = list(merged.values())
        for item in candidates:
            item["score"] = round(sum(1.0 / (60 + rank) for rank in item["ranks"].values()), 6)
            item["score_method"] = "reciprocal_rank_fusion"
        candidates.sort(key=lambda item: (-item["score"], min(item["ranks"].values()), normalize(item["disease"])))
        return {
            "crop": crop, "symptom": symptom,
            "candidates": candidates[:max(top_k, 0)],
            "evidence": [{
                "title": hit.doc.title, "text": snippet(hit.doc.text, 320),
                "how": hit.how, **self._provenance(hit.doc),
            } for hit in text_hits],
            "ranking": "两路病害排名融合；score 是检索排序分，不是诊断概率",
        }

    def _candidate(self, match: SymptomMatch, crop: str) -> dict:
        entity = match.entity
        card = self.link_card(entity, crop=crop)
        graph_docs = self._by_parent.get(f"cropdp:{entity.name}", [])
        provenance = self._provenance(graph_docs[0]) if graph_docs else {
            "source": "cropdp", "doc_id": f"cropdp:{entity.name}",
            "source_ref": f"kg.json#/{entity.name}", "demo": False,
        }
        return {
            "disease": entity.name,
            "disease_id": (card or {}).get("disease_id", ""),
            "scientific_name": entity.scientific,
            "aliases": entity.aliases[:6],
            "score": match.score,
            "matched_symptoms": match.matched,
            "crops": entity.crops[:8],
            "crops_en": entity.crops_en[:8],
            "parts": entity.parts[:8],
            "conditions": entity.conditions[:6],
            "temps": entity.temps[:4],
            "regions": entity.regions[:8],
            "linked_card": bool(card),
            "pathogen_type": (card or {}).get("pathogen_type", ""),
            **provenance,
        }

    def _crop_matches(self, candidate: dict, crop: str) -> bool:
        """作物名匹配。跨语言：``tomato`` 要能匹配到 ``番茄``。

        靠术语表把查询词展开成多语言别名再做比较。术语表为空时退化为
        字面比较——那时的行为与没有这一层一样，不会更差。
        """
        if not crop:
            return True
        key = normalize(crop)
        variants = self._crop_aliases.get(key, {key})
        if not variants:
            return True
        names = {normalize(c) for c in (self._values(candidate, "crop")
                 or self._values(candidate, "crops", "crops_en"))}
        expanded = set(names)
        for name in names:
            expanded.update(self._crop_aliases.get(name, set()))
        return bool(variants & expanded)

    def profile(self, disease: str) -> dict | None:
        """取某个病害的完整画像（图谱属性 + 关联卡片元数据）。"""
        if not self.kg:
            return None
        entity = self.resolve_entity(disease)
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

    def resolve_entity(self, name: str, *, crop: str = "") -> KgEntity | None:
        """病害身份必须唯一；共享病原名不会任取第一个宿主实体。"""
        if not self.kg or not normalize(name):
            return None
        key = normalize(name)
        found = [entity for entity in self.kg.entities.values()
                 if key in {normalize(n) for n in entity.all_names}
                 and (not crop or self._crop_matches(
                     {"crops": entity.crops, "crops_en": entity.crops_en}, crop))]
        return found[0] if len(found) == 1 else None

    def treatments(self, diagnosis: str, *, crop: str = "", top_k: int = 4) -> dict:
        """按明确病害身份和宿主查处置；相邻文档、正文提及不构成适用依据。

        未指定作物时仅允许唯一的宿主范围。检索排名不能决定用哪种作物的方案，
        disease_card 没有管理内容也不能充当处置文档。
        """
        entity = self.resolve_entity(diagnosis, crop=crop)
        card = self.link_card(entity, crop=crop) if entity is not None else None
        if card is None:
            anchor = self._anchor_card_document(diagnosis, crop)
            if anchor is not None:
                card = self._linked_doc_card(anchor, crop)
        anchors = {normalize(diagnosis)} - {""}
        if entity:
            anchors.update(normalize(n) for n in [entity.name, *entity.aliases] if n)
        record = self.terms.lookup(diagnosis)
        if record and not (record.term_id.startswith("crop:") or ":crop:" in record.term_id):
            scientific = {normalize(v) for v in record.labels.get("la", [])}
            for value in [record.pref, *record.labels.get("zh", []), *record.labels.get("en", [])]:
                key = normalize(value)
                if key and key not in scientific and key not in self._crop_aliases:
                    anchors.add(key)

        cards = []
        if card:
            cards.append(card)
        # 无图谱时也支持疾病卡片的显式名称与别名，不用正文相似度猜身份。
        for item in self._cards_by_id.values():
            names = {normalize(v) for v in self._values(item, "disease", "disease_id", "aliases")}
            if names & anchors and (not crop or self._crop_matches(item, crop)):
                if item not in cards:
                    cards.append(item)
        card_ids = {item["disease_id"] for item in cards}
        parents = {item.get("parent_id") or f"plantinquiry:{item['disease_id']}" for item in cards}
        for item in cards:
            anchors.update(normalize(v) for v in self._values(item, "disease", "disease_id", "aliases"))

        docs = []
        for doc in self.index.docs if self.index else []:
            if doc.kind not in ("management", "treatment"):
                continue
            hosts = self._values(doc.meta, "crop") or self._values(doc.meta, "crops", "crops_en")
            if not hosts or (crop and not self.crop_doc_matches(doc, crop)):
                continue
            if entity and not any(self._crop_matches(
                    {"crops": entity.crops, "crops_en": entity.crops_en}, host) for host in hosts):
                continue
            names = {normalize(v) for v in self._values(doc.meta, "disease", "entity", "disease_id", "aliases")}
            linked = doc.parent_id in parents or doc.meta.get("disease_id") in card_ids
            if linked or names & anchors:
                docs.append((doc, "linked-card" if linked else "exact-disease-crop"))

        # 没有 crop 时，多宿主候选必须退回澄清，不能将相同病害名当作同一作业范围。
        host_groups = set()
        for doc, _ in docs:
            for host in (self._values(doc.meta, "crop") or self._values(doc.meta, "crops", "crops_en")):
                aliases = self._crop_aliases.get(normalize(host), {normalize(host)})
                host_groups.add(tuple(sorted(aliases)))
        ambiguous = not crop and len(host_groups) > 1
        if ambiguous:
            docs = []
        docs.sort(key=lambda item: (self._provenance(item[0])["demo"], item[0].doc_id))
        plans = [{
            **self._provenance(doc),
            "disease": doc.meta.get("disease") or doc.meta.get("entity") or doc.title,
            "crop": doc.meta.get("crop") or ", ".join(self._values(doc.meta, "crops", "crops_en")),
            "disease_id": doc.meta.get("disease_id") or "",
            "text": doc.text,
            "how": how,
            "score": 1.0,
            "fields_missing": doc.meta.get("fields_missing") or [],
        } for doc, how in docs[:max(top_k, 0)]]
        return {
            "diagnosis": diagnosis,
            "crop": crop,
            "found": bool(plans),
            "plans": plans,
            "card": card if plans else None,
            "reason": "作物范围不唯一，请明确作物" if ambiguous else (
                "" if plans else "没有病害身份与作物范围均匹配的处置文档"),
        }

    def soil_reference(self, site_id: str, *, top_k: int = 3) -> list[Hit]:
        """地块主键精确查询；通用模板不作为任何地块的实测档案。"""
        key = normalize(site_id)
        if not key or not self.index:
            return []
        docs = [doc for doc in self.index.docs
                if doc.kind == "soil_reference" and not doc.meta.get("is_template")
                and key in {normalize(v) for v in self._values(doc.meta, "site_id", "site_ids")}]
        docs.sort(key=lambda doc: (self._provenance(doc)["demo"], doc.doc_id))
        return [Hit(doc, 1.0) for doc in docs[:max(top_k, 0)]]

    def equipment_manual(self, device_id: str, fault_code: str, *, top_k: int = 3) -> list[Hit]:
        """设备/型号与故障码同时精确匹配，不把设备类型当成型号。"""
        device, fault = normalize(device_id), normalize(fault_code)
        if not device or not fault or not self.index:
            return []
        docs = [doc for doc in self.index.docs
                if doc.kind == "equipment_manual"
                and device in {normalize(v) for v in self._values(doc.meta,
                    "device_id", "device_ids", "device_aliases", "device_model", "model", "model_id")}
                and fault in {normalize(v) for v in self._values(doc.meta, "fault_code", "fault_codes")}]
        docs.sort(key=lambda doc: (self._provenance(doc)["demo"], doc.doc_id))
        return [Hit(doc, 1.0) for doc in docs[:max(top_k, 0)]]

    @staticmethod
    def _label_pattern(label: str) -> str:
        label = normalize(label)
        prefix = r"(?<![a-z0-9_])" if label and label[0].isascii() and label[0].isalnum() else ""
        suffix = r"(?![a-z0-9_])" if label and label[-1].isascii() and label[-1].isalnum() else ""
        return prefix + re.escape(label) + suffix

    def _has_label(self, text: str, label: str) -> bool:
        return bool(label and re.search(self._label_pattern(label), normalize(text)))

    @staticmethod
    def _contains_phrase(text: str, phrase: str) -> bool:
        """已规范化正文的快速短语查找，英文仍要求词边界。"""
        def word_char(char):
            return char.isascii() and (char.isalnum() or char == "_")
        start = text.find(phrase)
        while start >= 0:
            end = start + len(phrase)
            left = not word_char(phrase[0]) or start == 0 or not word_char(text[start - 1])
            right = not word_char(phrase[-1]) or end == len(text) or not word_char(text[end])
            if left and right:
                return True
            start = text.find(phrase, start + 1)
        return False

    def _question_crops(self, text: str) -> list[str]:
        """长名称优先，英文按词边界识别显式作物；不从相似文档倒推作物。"""
        found = []
        for match in self._crop_pattern.finditer(normalize(text)):
            name = match.group()
            if not any(self._crop_matches({"crop": name}, item) for item in found):
                found.append(name)
        return found

    def _agronomy_scope(self, doc: Doc, crops: list[str]) -> str | None:
        """作物问答只能用相符作物或未声明专属作物的通用管理证据。"""
        if not crops:
            return "unspecified"
        if doc.doc_id not in self._document_crops:
            explicit = self._values(doc.meta, "crop") or self._values(doc.meta, "crops", "crops_en")
            if doc.meta.get("crop_scope") in ("explicit", "inferred", "general"):
                declared = explicit
                if not declared and any(self._has_label(doc.title, alias) for alias in _PADDY_ALIASES):
                    declared = ["rice"]
            else:
                # 旧索引的答案例子可能含其他作物，优先读取明确题目并缓存。
                declared = self._question_crops(doc.title) or explicit
                if not declared:
                    declared = self._question_crops(doc.text)
            self._document_crops[doc.doc_id] = declared
        declared = self._document_crops[doc.doc_id]
        if not declared:
            return "general_agronomy"
        return "crop_matched" if any(self._crop_matches({"crops": declared}, crop) for crop in crops) else None

    def _agronomy_treatments(self, question: str, crops: list[str], top_k: int) -> list[dict]:
        """防治问题仅使用明确提及、同宿主病害的专用处置接口。"""
        if not self.index or not crops:
            return []
        requests = set()
        for doc in self.index.docs:
            if doc.kind != "disease_card":
                continue
            for crop in crops:
                if not self.crop_doc_matches(doc, crop):
                    continue
                labels = self._values(doc.meta, "disease", "aliases")
                for label in labels:
                    bare = normalize(label)
                    for alias in sorted(self._crop_aliases.get(normalize(crop), {crop}), key=len, reverse=True):
                        bare = re.sub(self._label_pattern(alias), " ", bare).strip()
                    if self._has_label(question, label) or (len(bare) >= 2 and self._has_label(question, bare)):
                        requests.add((doc.meta.get("disease") or label, crop))
                        break
        plans = {}
        for disease, crop in sorted(requests):
            for plan in self.treatments(disease, crop=crop, top_k=top_k)["plans"]:
                plans.setdefault(plan["doc_id"], {
                    **plan, "title": plan["disease"] + " · 处置原文", "dataset": "",
                    "verified": False, "text": snippet(plan["text"], 700),
                    "crop_scope": "crop_matched", "applicability_validated": True,
                    "usage": "已核对病害与作物身份；原文仍须核对当地适用性、用量和安全约束。",
                })
        return sorted(plans.values(), key=lambda plan: (plan["demo"], plan["doc_id"]))[:top_k]

    def _management_features(self, doc: Doc) -> tuple[int, int, bool, int, bool]:
        """缓存只读语料的主题/阶段特征，避免每个问句重复扫描数万段正文。"""
        if doc.doc_id not in self._agronomy_features:
            body, title = normalize(doc.text), normalize(doc.title)
            self._agronomy_features[doc.doc_id] = (
                sum(1 << i for i, group in enumerate(_AGRONOMY_CONCEPTS)
                    if any(self._contains_phrase(body, word) for word in group)),
                sum(1 << i for i, group in enumerate(_GROWTH_STAGES)
                    if any(self._contains_phrase(body, word) for word in group)),
                any(self._contains_phrase(body, word) for word in _ACTION_WORDS),
                sum(1 << i for i, group in enumerate(_AGRONOMY_CONCEPTS)
                    if any(self._contains_phrase(title, word) for word in group)),
                bool(_UNSCOPED_RATE.search(body)),
            )
        return self._agronomy_features[doc.doc_id]

    def agronomy(self, question: str, *, top_k: int = 5) -> list[dict]:
        """自然农艺问句按作物、管理主题和生育期先限域，再检索原文。"""
        if not self.index or top_k <= 0:
            return []
        crops = self._question_crops(question)
        concepts = [group for group in _AGRONOMY_CONCEPTS if any(self._has_label(question, word) for word in group)]
        stages = [group for group in _GROWTH_STAGES if any(self._has_label(question, word) for word in group)]
        concept_mask = sum(1 << i for i, group in enumerate(_AGRONOMY_CONCEPTS) if group in concepts)
        stage_mask = sum(1 << i for i, group in enumerate(_GROWTH_STAGES) if group in stages)
        management = bool(concepts or stages or any(self._has_label(question, word) for word in _MANAGEMENT_WORDS))
        if management:
            plans = self._agronomy_treatments(question, crops, top_k)
            if plans:
                return plans

        # 作物已在候选集合中校验，排序只看余下问题，防止仅共享作物词就命中。
        query = normalize(question)
        for crop in crops:
            for alias in sorted(self._crop_aliases.get(crop, {crop}), key=len, reverse=True):
                query = re.sub(self._label_pattern(alias), " ", query)
        for group in concepts:
            query += " " + " ".join(group)
        kinds = {"qa_pair", "agronomy", "environment"}
        if not management:
            kinds.add("kg_entity")
        scopes = {}
        topic_matches = {}
        for doc in self.index.docs:
            if doc.kind not in kinds:
                continue
            topics, phases, action, title_topics, rate = self._management_features(doc)
            if stages and phases & stage_mask != stage_mask:
                continue
            matched = (topics & concept_mask).bit_count()
            if concepts and not matched:
                continue
            if management and not action:
                continue
            scope = self._agronomy_scope(doc, crops)
            if scope is None:
                continue
            if scope == "general_agronomy" and rate:
                # 词典未识别宿主的定量施用说明，不可作为指定作物的处方证据。
                continue
            scopes[doc.doc_id] = scope
            topic_matches[doc.doc_id] = matched
        if not query_terms(self.terms.expand_query(query)):
            return []
        hits = self.search(query, top_k=max(top_k * 5, 20), kinds=kinds, allowed_doc_ids=set(scopes))
        # 主题与阶段经过前置校验；纯向量相似不能补上缺少的原文支持。
        hits = [hit for hit in hits if hit.bm25_score > 0]
        hits.sort(key=lambda hit: (
            -topic_matches[hit.doc.doc_id],
            -(self._management_features(hit.doc)[3] & concept_mask).bit_count(),
            -hit.score, hit.doc.doc_id,
        ))
        return [{
            "title": hit.doc.title, **self._provenance(hit.doc),
            "dataset": hit.doc.meta.get("dataset", ""), "verified": bool(hit.doc.meta.get("verified")),
            "text": snippet(hit.doc.text, 700), "score": round(hit.score, 6), "how": hit.how,
            "crop_scope": scopes[hit.doc.doc_id], "requested_crops": crops,
            "matched_topics": topic_matches[hit.doc.doc_id], "requested_topics": len(concepts),
            "applicability_validated": False,
            "usage": "主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。",
        } for hit in hits[:top_k]]

    def symptoms_for(self, disease: str, *, top_k: int = 6) -> list[dict]:
        hits = self.search(disease, top_k=top_k, kinds=("symptom", "diagnosis"))
        return [
            {
                **self._provenance(h.doc),
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
_cache: dict[tuple, tuple[tuple, KnowledgeBase]] = {}


def _index_fingerprint(index_dir: str) -> tuple:
    """文件身份、长度和纳秒时间共同识别同目录重建及目录替换。"""
    stamps = []
    for name in ("manifest.json", "corpus.jsonl", "terms.json", "kg.json", "vectors.npy"):
        try:
            stat = os.stat(os.path.join(index_dir, name))
            stamps.append((name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            stamps.append((name, None))
    return tuple(stamps)


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
            elif index.embedder_name and getattr(embedder, "name", None) != index.embedder_name:
                embedder_error = (
                    f"嵌入模型不符（索引 {index.embedder_name}，当前编码器 "
                    f"{getattr(embedder, 'name', '?')}）。已退化为纯 BM25 检索。"
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
    """按目录和编码器配置缓存；重建或替换索引文件后自动重新加载。"""
    settings = rag_config(config)
    index_dir = os.path.normcase(os.path.abspath(settings["rag_index_dir"]))
    key = (index_dir, *(str(settings.get(name, "")) for name in (
        "rag_embedding_backend", "rag_embedding_model", "rag_model_cache_dir", "rag_hf_endpoint"
    )))
    with _lock:
        for _ in range(3):
            fingerprint = _index_fingerprint(index_dir)
            cached = _cache.get(key)
            if not force_reload and cached and cached[0] == fingerprint:
                return cached[1]
            kb = load_knowledge_base(settings)
            if fingerprint == _index_fingerprint(index_dir):
                _cache[key] = (fingerprint, kb)
                return kb
        raise RuntimeError("知识库正在更新，未获得一致的索引快照，请重试")


def reset_cache() -> None:
    """清空实例缓存。重建索引后调用，或供测试隔离用。"""
    with _lock:
        _cache.clear()
