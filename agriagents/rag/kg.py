"""CropDP-KG 知识图谱：实体 ↔ 症状/作物/部位/发生条件/温度/分布。

**为什么单独做一张图，而不是全塞进向量库**：向量检索擅长"模糊地找到相关
段落"，不擅长"精确地列出晚疫病的全部症状"。诊断 Agent 要的是后者——
它需要一份完整、确定、可枚举的候选病害画像来做鉴别诊断，
而不是若干条语义相近的片段。

数据来源是 CropDP-KG 的 6 张关系表（``Dataset`` 分支），全部中英双语：

====================  =========================  ================
文件                  关系                        规模
====================  =========================  ================
``relationsym.csv``   ManifestAs   实体 → 症状     10549 条
``relationcrop.csv``  Damage       实体 → 作物     3870 条
``relationcon.csv``   ConditionIs  实体 → 发生条件  2033 条
``relationarea.csv``  RegionIs     实体 → 分布区    1562 条
``relationpart.csv``  InflictHarmPart 实体 → 部位  1321 条
``relationtem.csv``   TemperIs     实体 → 温度     90 条
``relationEng.csv``   —            中文名 → 英文名  2537 条
====================  =========================  ================

同一批 CSV 的编码不统一（UTF-8 与 GB18030 混用），交给 ``fetch.read_text_any``。
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from agriagents.rag.fetch import iter_csv_rows
from agriagents.rag.text import normalize, tokenize

# 关系表 → (中文值列用途, ...)。列名在不同文件里不一致，但**位置**固定，
# 所以按"第 4 列是中文值、第 5 列是英文值（若有）"来解析，比匹配列名稳。
_RELATION_FILES = {
    "relationcrop.csv": "crops",
    "relationsym.csv": "symptoms",
    "relationcon.csv": "conditions",
    "relationarea.csv": "regions",
    "relationpart.csv": "parts",
    "relationtem.csv": "temps",
}
_ENG_FILE = "relationEng.csv"

# 英文别名在关系表里的分隔符是混用的：全角逗号、半角逗号、分号都有。
_ALIAS_SPLIT = "，,;；/"

# 症状匹配用的 BM25 参数，与 ``rag.index`` 保持同一套取值：
# 两处打分尺度一致，融合时不需要再做归一。
_BM25_K1 = 1.5
_BM25_B = 0.75


@dataclass
class KgEntity:
    """一个病害/虫害/杂草实体及其全部已知属性。"""

    name: str
    scientific: str = ""
    english: str = ""
    crops: list[str] = field(default_factory=list)
    crops_en: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    symptoms_en: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    conditions_en: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    parts_en: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    regions_en: list[str] = field(default_factory=list)
    temps: list[str] = field(default_factory=list)

    @property
    def aliases(self) -> list[str]:
        """英文别名列表。原始字段里用多种分隔符混排，全角/半角逗号都出现。"""
        raw = self.english
        if isinstance(raw, (list, tuple)):   # 容忍历史索引里存成列表
            return [str(a).strip() for a in raw if str(a).strip()]
        normalized = raw
        for sep in _ALIAS_SPLIT[1:]:
            normalized = normalized.replace(sep, ",")
        return [a.strip() for a in normalized.split(",") if a.strip()]

    @property
    def all_names(self) -> list[str]:
        names = [self.name]
        if self.scientific:
            names.append(self.scientific)
        names.extend(self.aliases)
        return [n for n in names if n]

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "scientific": self.scientific,
            "english": self.english,
            "crops": self.crops,
            "crops_en": self.crops_en,
            "symptoms": self.symptoms,
            "symptoms_en": self.symptoms_en,
            "conditions": self.conditions,
            "conditions_en": self.conditions_en,
            "parts": self.parts,
            "parts_en": self.parts_en,
            "regions": self.regions,
            "regions_en": self.regions_en,
            "temps": self.temps,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "KgEntity":
        """按字段自身的默认工厂还原。

        不能写 ``payload.get(k) or []``——那会把**空字符串**字段
        （没有英文名的实体很常见）也还原成 ``[]``，之后 ``aliases``
        属性调用 ``.replace()`` 就会炸。默认值必须按字段类型分别取。
        """
        import dataclasses

        kwargs = {}
        for name, fld in cls.__dataclass_fields__.items():
            value = payload.get(name)
            if value is not None:
                kwargs[name] = value
            elif fld.default_factory is not dataclasses.MISSING:
                kwargs[name] = fld.default_factory()
            else:
                kwargs[name] = fld.default
        return cls(**kwargs)


@dataclass
class SymptomMatch:
    entity: KgEntity
    matched: list[str]
    score: float


class CropDpKg:
    """内存图索引。2500 实体规模下全量载入毫无压力，无需图数据库。"""

    def __init__(self, entities: Iterable[KgEntity]) -> None:
        self.entities: dict[str, KgEntity] = {e.name: e for e in entities if e.name}
        self._name_index: dict[str, str] = {}
        self._symptoms: list[tuple[str, str]] = []
        self._symptom_tokens: list[set[str]] = []
        self._symptom_postings: dict[str, list[int]] = defaultdict(list)
        self._build_lookups()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build_lookups(self) -> None:
        self._name_index.clear()
        for entity in self.entities.values():
            for name in entity.all_names:
                key = normalize(name)
                if key:
                    # 先到先得：中文名优先注册（all_names 里它排第一）
                    self._name_index.setdefault(key, entity.name)

        # 症状索引按"每条症状当一篇文档"来建，用 BM25 打分。
        # 早先版本用"查询词覆盖率"，但它对长症状串系统性不利——
        # 英文症状描述动辄十几个词，命中两三个词覆盖率也只有 0.1，
        # 会被阈值全部滤掉。BM25 的 tf 饱和与长度归一正好解决这个问题。
        self._symptoms.clear()
        self._symptom_tokens.clear()
        self._symptom_postings = defaultdict(list)
        lengths: list[int] = []
        for entity in self.entities.values():
            for symptom in list(entity.symptoms) + list(entity.symptoms_en):
                tokens = tokenize(symptom)
                if not tokens:
                    continue
                idx = len(self._symptoms)
                self._symptoms.append((entity.name, symptom))
                counts = Counter(tokens)
                self._symptom_tokens.append(set(counts))
                lengths.append(sum(counts.values()))
                for token, tf in counts.items():
                    self._symptom_postings[token].append((idx, tf))
        self._symptom_len = lengths
        self._symptom_avgdl = (sum(lengths) / len(lengths)) if lengths else 1.0
        self._symptom_df = {t: len(p) for t, p in self._symptom_postings.items()}

    @classmethod
    def from_csv_dir(cls, csv_dir: str) -> "CropDpKg":
        entities: dict[str, KgEntity] = {}

        def get(name: str) -> KgEntity | None:
            name = (name or "").strip()
            if not name:
                return None
            if name not in entities:
                entities[name] = KgEntity(name=name)
            return entities[name]

        for filename, attr in _RELATION_FILES.items():
            path = os.path.join(csv_dir, filename)
            if not os.path.exists(path):
                continue
            for row in iter_csv_rows(path):
                entity = get(row.get("Chinese entity name", ""))
                if entity is None:
                    continue
                if not entity.scientific:
                    entity.scientific = row.get("entity binomial scientific name", "")
                zh, en = _value_columns(row)
                bucket = getattr(entity, attr)
                if zh and zh not in bucket:
                    bucket.append(zh)
                if en:
                    en_bucket = getattr(entity, f"{attr}_en", None)
                    if en_bucket is not None and en not in en_bucket:
                        en_bucket.append(en)

        eng_path = os.path.join(csv_dir, _ENG_FILE)
        if os.path.exists(eng_path):
            for row in iter_csv_rows(eng_path):
                entity = get(row.get("Entity", ""))
                if entity is not None and not entity.english:
                    entity.english = row.get("English entity", "")

        return cls(entities.values())

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, name: str) -> KgEntity | None:
        resolved = self._name_index.get(normalize(name))
        return self.entities.get(resolved) if resolved else None

    def find_entities(self, text: str, limit: int = 10) -> list[KgEntity]:
        """从自由文本里捞出提到的实体（词典扫描）。

        按名称长度降序扫描：不这么做的话"草莓炭疽病"会先命中"草莓"，
        得到一堆与草莓相关但与炭疽无关的实体。
        """
        if not text:
            return []
        haystack = normalize(text)
        found: list[tuple[int, KgEntity]] = []
        seen: set[str] = set()
        for key, canonical in self._name_index.items():
            if canonical in seen or len(key) < 2:
                continue
            if key in haystack:
                entity = self.entities[canonical]
                seen.add(canonical)
                found.append((len(key), entity))
        found.sort(key=lambda kv: (-kv[0], kv[1].name))
        return [entity for _, entity in found[:limit]]

    def match_symptoms(self, text: str, top_k: int = 5, min_score: float = 0.05) -> list[SymptomMatch]:
        """症状反查：给一段观察描述，返回最可能的候选实体。

        把每条症状当一篇小文档做 BM25，再把症状分数聚合到实体上。

        聚合权重 0.7 / 0.2 / 0.1 而不是"取最大值"：一条症状完全命中
        固然是最强证据，但两三条中等命中的组合往往更可信
        （"叶片有水渍斑" + "果面有白霉"比单独的"叶片有水渍斑"更像晚疫病）。
        权重衰减得这么快，是为了不让一堆弱证据把真正的强命中淹掉。
        """
        query_tokens = tokenize(text)
        if not query_tokens or not self._symptoms:
            return []

        total = len(self._symptoms)
        raw: dict[int, float] = defaultdict(float)
        for term in set(query_tokens):
            postings = self._symptom_postings.get(term)
            if not postings:
                continue
            df = self._symptom_df[term]
            # BM25 概率型 IDF。症状语料里专名很多（"炭疽""霜霉"），
            # IDF 会自动让它们比"叶片""出现"这类通用词更值钱。
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for idx, tf in postings:
                dl = self._symptom_len[idx]
                length_norm = 1.0 - _BM25_B + _BM25_B * (dl / self._symptom_avgdl)
                raw[idx] += idf * (tf * (_BM25_K1 + 1.0)) / (tf + _BM25_K1 * length_norm)

        if not raw:
            return []
        peak = max(raw.values()) or 1.0

        per_entity: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for idx, score in raw.items():
            normalized = score / peak          # 归一到 [0,1]，与语料规模无关
            if normalized < min_score:
                continue
            entity_name, symptom = self._symptoms[idx]
            per_entity[entity_name].append((normalized, symptom))

        weights = (0.7, 0.2, 0.1)
        results: list[SymptomMatch] = []
        for entity_name, matches in per_entity.items():
            matches.sort(key=lambda m: -m[0])
            top = matches[: len(weights)]
            score = min(1.0, sum(w * s for w, (s, _) in zip(weights, top)))
            results.append(
                SymptomMatch(
                    entity=self.entities[entity_name],
                    matched=[sym for _, sym in top],
                    score=round(score, 4),
                )
            )
        results.sort(key=lambda m: -m.score)
        return results[:top_k]

    def related_crops(self, crop: str) -> list[KgEntity]:
        """按作物名反查危害该作物的全部实体。"""
        key = normalize(crop)
        out = []
        for entity in self.entities.values():
            names = {normalize(c) for c in entity.crops} | {normalize(c) for c in entity.crops_en}
            if any(key and (key in n or n in key) for n in names):
                out.append(entity)
        return out

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def profile_text(self, entity: KgEntity, *, limit_per_field: int = 12) -> str:
        """把实体画像渲染成给 LLM 读的文本块，中英并列。

        中英并列是刻意的：CropDP-KG 的英文名是拉丁学名与通用名的来源，
        诊断 Agent 要拿它去和 PlantInquiryVQA 的英文卡片对上。
        """

        def block(label: str, zh: list[str], en: list[str] | None = None) -> str:
            if not zh and not en:
                return ""
            zh_part = "、".join(zh[:limit_per_field])
            en_part = "；".join((en or [])[:limit_per_field])
            line = f"{label}：{zh_part}"
            if en_part:
                line += f"（{en_part}）"
            return line

        lines = [f"【{entity.name}】{entity.scientific}".strip()]
        if entity.aliases:
            lines.append("英文名/别名：" + "；".join(entity.aliases[:6]))
        for label, attr in (
            ("危害作物", "crops"),
            ("危害部位", "parts"),
            ("典型症状", "symptoms"),
            ("适宜发生条件", "conditions"),
            ("适宜温度", "temps"),
            ("主要分布", "regions"),
        ):
            zh = getattr(entity, attr)
            en = getattr(entity, f"{attr}_en", None)
            rendered = block(label, zh, en)
            if rendered:
                lines.append(rendered)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 落盘 / 加载
    # ------------------------------------------------------------------

    def save(self, index_dir: str) -> None:
        os.makedirs(index_dir, exist_ok=True)
        path = os.path.join(index_dir, "kg.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(
                {e.name: e.to_json() for e in self.entities.values()},
                fh,
                ensure_ascii=False,
            )

    @classmethod
    def load(cls, index_dir: str) -> "CropDpKg":
        path = os.path.join(index_dir, "kg.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"知识图谱不存在：{path}（先跑 tools/build_rag_index.py）")
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return cls(KgEntity.from_json(v) for v in payload.values())

    def stats(self) -> dict:
        return {
            "entities": len(self.entities),
            "symptoms": len(self._symptoms),
            "with_crops": sum(1 for e in self.entities.values() if e.crops),
            "with_symptoms": sum(1 for e in self.entities.values() if e.symptoms),
        }


# 关系表里这些列不是"值"，是从属信息：编号、实体名、拉丁学名。
# 剩下的列才是值列（中文值在前，英文值在后，温度表只有中文值）。
_NON_VALUE_COLUMNS = frozenset(
    {"number", "chineseentityname", "entitybinomialscientificname"}
)


def _value_columns(row: dict) -> tuple[str, str]:
    """从关系表行里取（中文值, 英文值）。

    列名在各文件间不一致（``Chinese sym`` / ``Chinese crop name`` / ``tem``），
    但**顺序固定**：编号、实体名、拉丁学名之后，中文值在前、英文值在后。

    早先版本直接取 ``keys[2:]``，把"entity binomial scientific name"当成了
    中文值列——结果 ``symptoms_en`` 里装的是中文症状，``crops`` 里装的是
    拉丁学名。这类错误不会报错，只会让检索静默变差，所以按列名显式排除。
    """
    keys = [
        key
        for key in row
        if key and "relation" not in key.lower()
        and key.lower().replace(" ", "") not in _NON_VALUE_COLUMNS
    ]
    values = [row.get(key, "") for key in keys]
    zh = values[0] if values else ""
    en = values[1] if len(values) > 1 else ""
    return zh, en
