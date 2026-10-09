"""多语言实体标准化：把中/英/学名/别名的各种写法归一到同一个规范条目。

诊断 Agent 的输入是中文口语（"叶子背面有白毛"），而 PlantInquiryVQA 的
知识卡片是英文的，CropDP-KG 的关系表是中英并列的。不把这三者对齐，
检索就会在语言边界上断掉。这一层就干这件事。

三级来源，**可靠性递减，且后级都不参与关键路径**：

1. ``CropDP-KG relationEng.csv``：2537 条中↔英实体对齐。随图谱离线加载，
   是标准化的主力。
2. ``PlantInquiryVQA`` 卡片 ``aliases``：英文卡片自己声明的疾病别名。
3. ``AGROVOC``：FAO 多语言叙词表，走 SPARQL。**需要外网**，
   所以它是"联网就变强、断网就跳过"的增强层，查询结果缓存到磁盘。
   温室现场没网也必须能诊断，因此它不能被写成依赖项。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Iterable

from agriagents.rag.fetch import get_bytes
from agriagents.rag.text import normalize

AGROVOC_SPARQL_ENDPOINT = "https://agrovoc.fao.org/sparql"

# AGROVOC 是 40+ 语言的叙词表，但本场景只需要这三类。
# ``la`` 是拉丁学名——它对不上海量语料，但在植物病理里是唯一无歧义的名字。
_AGROVOC_LANGS = ("en", "zh", "la")

# 有限的症状表述对齐，只扩展观察描述，不从作物名推断病害。
# 查询侧常量，不改已建库的术语/向量；诊断仍需作物与鉴别证据约束。
_SYMPTOM_EQUIVALENTS = (
    ("water-soaked", "water soaked", "水浸状", "水渍状"),
    ("white mold", "white mould", "白色霉层", "白色霉状物"),
    ("angular spots", "angular lesions", "多角形病斑", "角斑"),
    ("dark green", "暗绿色"),
    ("chlorosis", "yellowing", "黄化"),
    ("powdery growth", "白色粉层"),
)


def _label_pattern(label: str) -> str:
    pattern = re.escape(label)
    if label[0].isascii() and label[0].isalnum():
        pattern = r"(?<![a-z0-9])" + pattern
    if label[-1].isascii() and label[-1].isalnum():
        pattern += r"(?![a-z0-9])"
    return pattern

_AGROVOC_QUERY = """PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?c ?pref ?lang WHERE {
  {
    ?c skos:prefLabel ?match .
  } UNION {
    ?c skos:altLabel ?match .
  }
  FILTER(STR(?match) = "%s")
  ?c skos:prefLabel ?pref .
  BIND(LANG(?pref) AS ?lang)
  FILTER(?lang IN (%s))
}
LIMIT 40"""


@dataclass
class TermRecord:
    """一个规范条目：一个主名 + 若干语言的别名。"""

    term_id: str
    pref: str                 # 主名（优先中文）
    labels: dict[str, list[str]] = field(default_factory=dict)   # lang -> labels
    sources: list[str] = field(default_factory=list)

    def all_labels(self) -> list[str]:
        out: list[str] = []
        for lang, values in self.labels.items():
            if lang == "crop":
                continue  # 旧版卡片宿主字段是属性，不是疾病的别名。
            for value in values:
                if value and value not in out:
                    out.append(value)
        return out

    def to_json(self) -> dict:
        return {
            "term_id": self.term_id,
            "pref": self.pref,
            "labels": self.labels,
            "sources": self.sources,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "TermRecord":
        return cls(
            term_id=payload["term_id"],
            pref=payload.get("pref", ""),
            labels=payload.get("labels") or {},
            sources=payload.get("sources") or [],
        )


class TermNormalizer:
    """别名 → 规范条目的查找表。查询侧用它做跨语言查询改写。"""

    def __init__(self, records: Iterable[TermRecord]) -> None:
        self.records: dict[str, TermRecord] = {}
        self._index: dict[str, str] = {}
        for record in records:
            existing = self.records.get(record.term_id)
            if existing is None:
                self.records[record.term_id] = TermRecord(
                    term_id=record.term_id, pref=record.pref,
                    labels={lang: list(values) for lang, values in record.labels.items()},
                    sources=list(record.sources),
                )
                continue
            for lang, labels in record.labels.items():
                bucket = existing.labels.setdefault(lang, [])
                for label in labels:
                    if label not in bucket:
                        bucket.append(label)
            for source in record.sources:
                if source not in existing.sources:
                    existing.sources.append(source)
        self._rebuild()

    def _rebuild(self) -> None:
        self._index.clear()
        # 明确的作物条目优先于历史病害条目中混入的宿主标签。
        records = sorted(self.records.values(), key=lambda rec: not rec.term_id.startswith("crop:"))
        for record in records:
            for label in [record.pref, *record.all_labels()]:
                key = normalize(label)
                if key and len(key) >= 2:
                    self._index.setdefault(key, record.term_id)

    # ------------------------------------------------------------------

    def lookup(self, term: str) -> TermRecord | None:
        term_id = self._index.get(normalize(term))
        return self.records.get(term_id) if term_id else None

    def canonical(self, term: str) -> str | None:
        record = self.lookup(term)
        return record.term_id if record else None

    def expand(self, term: str, *, langs: Iterable[str] = ("zh", "en", "la")) -> list[str]:
        """返回该词的全部等价写法（含原词）。用于跨语言查询改写。"""
        record = self.lookup(term)
        if record is None:
            return [term]
        wanted = set(langs)
        out = [record.pref]
        for lang, values in record.labels.items():
            if lang in wanted:
                out.extend(values)
        seen: list[str] = []
        for item in out:
            if item and item not in seen:
                seen.append(item)
        return seen

    def expand_query(self, query: str, *, max_terms: int = 4) -> str:
        """把查询里出现的实体替换为"原词 + 跨语言别名"的拼接串。

        这一步对混合语料是必要的：中文问句"番茄晚疫病怎么治"若不改写成
        "番茄晚疫病 Phytophthora infestans late blight tomato"，
        就永远命中不了只有英文的 PlantInquiryVQA 卡片。
        """
        if not query or max_terms <= 0:
            return query
        haystack = normalize(query)
        extra: list[str] = []
        for equivalents in _SYMPTOM_EQUIVALENTS:
            if any(re.search(_label_pattern(label), haystack) for label in equivalents):
                extra.extend(label for label in equivalents if label not in haystack and label not in extra)
        used: set[str] = set()
        spans: list[tuple[int, int]] = []
        # 长词优先，避免"番茄"抢先匹配掉"番茄晚疫病"
        for key in sorted(self._index, key=len, reverse=True):
            if len(key) < 2 or key not in haystack:
                continue
            # rust 不能匹配 trust；中文二字作物“番茄”“黄瓜”仍能展开。
            pattern = _label_pattern(key)
            matches = [match for match in re.finditer(pattern, haystack)
                       if not any(match.start() < end and match.end() > start for start, end in spans)]
            term_id = self._index[key]
            if not matches or term_id in used:
                continue
            used.add(term_id)
            spans.extend((match.start(), match.end()) for match in matches)
            for label in self.expand(key):
                if label and label not in extra and normalize(label) not in haystack:
                    extra.append(label)
            if len(used) >= max_terms:
                break
        if not extra:
            return query
        return query + " " + " ".join(extra[: max_terms * 3])

    # ------------------------------------------------------------------

    def save(self, index_dir: str) -> None:
        os.makedirs(index_dir, exist_ok=True)
        path = os.path.join(index_dir, "terms.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(
                {tid: rec.to_json() for tid, rec in self.records.items()},
                fh,
                ensure_ascii=False,
            )

    @classmethod
    def load(cls, index_dir: str) -> "TermNormalizer":
        path = os.path.join(index_dir, "terms.json")
        if not os.path.exists(path):
            return cls([])   # 标准化是增强，缺失不该让整个 RAG 起不来
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return cls(TermRecord.from_json(v) for v in payload.values())

    def stats(self) -> dict:
        langs: dict[str, int] = {}
        for record in self.records.values():
            for lang in record.labels:
                langs[lang] = langs.get(lang, 0) + 1
        return {"terms": len(self.records), "labels": len(self._index), "by_lang": langs}


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def from_knowledge_graph(kg, *, source: str = "cropdp") -> list[TermRecord]:
    """从 CropDP-KG 抽中↔英对齐。这是离线主力来源。"""
    records: list[TermRecord] = []
    crops: dict[str, TermRecord] = {}
    for entity in kg.entities.values():
        labels: dict[str, list[str]] = {"zh": [entity.name], "en": list(entity.aliases)}
        if entity.scientific:
            labels["la"] = [entity.scientific]
        # 宿主是独立实体。“番茄”绝不是“番茄晚疫病”的同义词。
        # 只在中英列表可一一对应时配对，否则保留单语，不猜测跨语言对应。
        english = entity.crops_en if len(entity.crops_en) == len(entity.crops) else []
        for idx, crop in enumerate(entity.crops):
            if not crop:
                continue
            crop_en = english[idx] if english else ""
            term_id = f"crop:{normalize(crop_en or crop)}"
            crop_record = crops.setdefault(term_id, TermRecord(
                term_id=term_id, pref=crop, labels={"zh": [], "en": []}, sources=[source],
            ))
            for lang, value in (("zh", crop), ("en", crop_en)):
                if value and value not in crop_record.labels[lang]:
                    crop_record.labels[lang].append(value)
        records.append(
            TermRecord(
                term_id=f"{source}:{entity.name}",
                pref=entity.name,
                labels={k: v for k, v in labels.items() if v},
                sources=[source],
            )
        )
    return [*records, *crops.values()]


def from_disease_cards(cards: Iterable[dict], *, source: str = "plantinquiry") -> list[TermRecord]:
    """从 PlantInquiryVQA 卡片抽别名。"""
    records: list[TermRecord] = []
    for card in cards:
        disease_id = card.get("disease_id")
        condition = card.get("condition") or {}
        crop = card.get("crop") or {}
        if not disease_id:
            continue
        en = [n for n in [condition.get("common_name"), *(card.get("aliases") or [])] if n]
        if condition.get("scientific_name"):
            en.append(condition["scientific_name"])
        labels: dict[str, list[str]] = {"en": en}
        if condition.get("scientific_name"):
            labels["la"] = [condition["scientific_name"]]
        records.append(
            TermRecord(
                term_id=disease_id,
                pref=condition.get("common_name") or disease_id,
                labels={k: v for k, v in labels.items() if v},
                sources=[source],
            )
        )
        if crop.get("common_name"):
            crop_name = crop["common_name"]
            records.append(TermRecord(
                term_id=f"crop:{normalize(crop_name)}", pref=crop_name,
                labels={"en": [crop_name]}, sources=[source],
            ))
    return records


def agrovoc_lookup(term: str, *, timeout: int = 20) -> TermRecord | None:
    """查 AGROVOC 的一个概念。需要外网；调用方必须能容忍它失败。

    注意 SPARQL 端点是**全表扫描**式的匹配（``FILTER(STR(?label) = ...)``），
    常见作物名的查询可能要几十秒。所以这个函数有超时，且调用方
    （``enrich_with_agrovoc``）必须带总时长预算——否则构建会被它拖住。
    """
    langs = ", ".join(f'"{lang}"' for lang in _AGROVOC_LANGS)
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    query = _AGROVOC_QUERY % (escaped, langs)
    url = AGROVOC_SPARQL_ENDPOINT + "?" + urllib.parse.urlencode(
        {"query": query, "format": "application/sparql-results+json"}
    )
    payload = json.loads(
        get_bytes(url, timeout=timeout, retries=1, headers={"Accept": "application/sparql-results+json"})
    )
    bindings = ((payload.get("results") or {}).get("bindings")) or []
    if not bindings:
        return None

    concept = bindings[0].get("c", {}).get("value", "")
    labels: dict[str, list[str]] = {}
    for row in bindings:
        lang = (row.get("lang") or {}).get("value") or ""
        value = (row.get("pref") or {}).get("value") or ""
        if lang in _AGROVOC_LANGS and value:
            labels.setdefault(lang, [])
            if value not in labels[lang]:
                labels[lang].append(value)
    if not labels:
        return None
    pref = (labels.get("zh") or labels.get("en") or [term])[0]
    return TermRecord(
        term_id=concept or f"agrovoc:{term}",
        pref=pref,
        labels=labels,
        sources=["agrovoc"],
    )


# 查询词的优先级。AGROVOC 是 FAO 的**通用农业**叙词表：
# 作物名（"tomato"）几乎都有，拉丁学名部分有，
# 而中文病害俗名（"草莓枯萎病"）基本查不到——实测 120 个中文病名只中 9 个。
# 所以按"命中概率"排序查询，预算花在刀刃上。
_QUERY_PRIORITY = ("crop", "la", "en")


def _query_candidates(records: list[TermRecord]) -> list[tuple[str, str]]:
    """列出待查词，按 AGROVOC 命中概率降序，且去重。"""
    buckets: dict[str, list[str]] = {lang: [] for lang in _QUERY_PRIORITY}
    for record in records:
        for lang in _QUERY_PRIORITY:
            for label in record.labels.get(lang) or []:
                label = (label or "").strip()
                if label:
                    buckets[lang].append(label)

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lang in _QUERY_PRIORITY:
        for label in buckets[lang]:
            key = label.lower()
            if key not in seen:
                seen.add(key)
                ordered.append((lang, label))
    return ordered


def enrich_with_agrovoc(
    records: list[TermRecord],
    *,
    cache_path: str,
    limit: int | None = 100,
    time_budget: float = 180.0,
    progress=None,
) -> dict:
    """用 AGROVOC 补充多语言标签，结果缓存到 ``cache_path``。

    只对**已有条目**补充标签，不新建条目——AGROVOC 是通用叙词表，
    颗粒度与植物病理知识库不一致，直接并入会产生重复实体。
    缓存到磁盘后，后续构建完全离线。

    **网络不可达时提前退出**：连错 5 次就停。继续试下去只是把
    100 × 1.5 秒白扔掉，而调用方需要的是快速失败。
    """
    cache: dict[str, dict] = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)

    attempted = 0
    merged = 0
    missed = 0
    errors = 0
    deadline = time.monotonic() + time_budget

    for lang, label in _query_candidates(records):
        if limit is not None and attempted >= limit:
            break
        # 总时长预算：AGROVOC 查得慢，不能让一个可选的增强步骤
        # 把整次构建拖到无法接受。超时就带着已有结果收工。
        if time.monotonic() >= deadline:
            break
        key = f"{lang}:{label}"
        if key in cache:
            payload = cache[key]
        else:
            try:
                found = agrovoc_lookup(label)
            except Exception:  # noqa: BLE001 - 外网不可达是预期情况
                errors += 1
                if errors >= 5:
                    break
                continue
            payload = found.to_json() if found else {"miss": True}
            cache[key] = payload
            attempted += 1
            if progress:
                progress(attempted, limit or attempted)

        if not payload or payload.get("miss"):
            missed += 1
            continue

        labels = payload.get("labels") or {}
        touched = False
        for record in records:
            if label not in (record.labels.get(lang) or []):
                continue
            for extra_lang, values in labels.items():
                bucket = record.labels.setdefault(extra_lang, [])
                for value in values:
                    if value not in bucket:
                        bucket.append(value)
            if "agrovoc" not in record.sources:
                record.sources.append("agrovoc")
            touched = True
        if touched:
            merged += 1

    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0)
    return {
        "merged": merged,
        "missed": missed,
        "attempted": attempted,
        "errors": errors,
        "timed_out": time.monotonic() >= deadline,
        "cached": len(cache),
    }
