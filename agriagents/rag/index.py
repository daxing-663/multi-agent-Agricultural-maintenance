"""混合检索引擎：BM25 关键词召回 + 稠密向量语义召回，用 RRF 融合。

**为什么不是纯向量**：农业问句里大量专有名词（"番茄晚疫病""斜纹夜蛾"
"Colletotrichum orbiculare"）。纯向量对低频专名容易糊成近邻（把晚疫病
召回到早疫病），纯关键词又对"叶子发黄"↔"叶片黄化"这种同义改写无能为力。
两者互补，所以融合。

**为什么是 RRF 而不是加权求和**：BM25 分数无上界、余弦在 [-1,1]，
加权求和需要每次调参且对语料规模敏感。RRF 只看排名，天然尺度无关，
在混合检索里是更稳的默认选择。``rrf_k`` 越小越强调头部结果。

**存储格式**（人类可读、可 diff、可手工修）：

- ``corpus.jsonl``：一行一个文档，UTF-8
- ``vectors.npy``：float32 矩阵，行序与 corpus 一致；纯 BM25 索引不产出
- ``manifest.json``：嵌入模型/维度/条数/构建时间/各源计数

刻意不用向量数据库：几万条文档的暴力点积在 numpy 上是毫秒级，
引入 FAISS/Milvus 只会让"把索引拷到温室那台机器上"变复杂。
规模真的上来再换，接口不变。
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Iterator, Mapping, Sequence

from agriagents.rag.text import normalize, query_terms, tokenize

# BM25 参数。k1 控制词频饱和，b 控制长度归一。
# 1.5 / 0.75 是文献里的通用默认值，对短文本（症状、问答对）表现稳定。
_BM25_K1 = 1.5
_BM25_B = 0.75

# RRF 平滑常数。60 出自原论文，作用是压低头部单条结果的绝对优势。
_RRF_K = 60

MANIFEST_NAME = "manifest.json"
CORPUS_NAME = "corpus.jsonl"
VECTORS_NAME = "vectors.npy"
_MANIFEST_RESERVED = {"version", "built_at", "documents", "embedder", "dim", "by_source", "by_kind", "dense"}


@dataclass
class Doc:
    """一条可检索的知识块。

    ``title`` 与 ``text`` 参与关键词索引；嵌入只看 ``text``。
    ``meta`` 存放结构化字段（作物、病原、危害部位、发生条件…），
    供调用方做过滤与拼装回答，不参与打分——这样调整元数据不会让索引失效。
    """

    doc_id: str
    source: str
    kind: str
    text: str
    title: str = ""
    lang: str = "zh"
    parent_id: str = ""
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "lang": self.lang,
            "parent_id": self.parent_id,
            "text": self.text,
            "meta": self.meta,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "Doc":
        return cls(
            doc_id=payload["doc_id"],
            source=payload.get("source", ""),
            kind=payload.get("kind", ""),
            text=payload.get("text", ""),
            title=payload.get("title", ""),
            lang=payload.get("lang", "zh"),
            parent_id=payload.get("parent_id", ""),
            meta=payload.get("meta") or {},
        )


@dataclass
class Hit:
    """一条检索结果，带可解释的打分。"""

    doc: Doc
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None
    bm25_score: float = 0.0
    dense_score: float = 0.0

    @property
    def how(self) -> str:
        """命中来源，便于排查"为什么召回了这条"。"""
        parts = []
        if self.bm25_rank is not None:
            parts.append(f"bm25#{self.bm25_rank + 1}")
        if self.dense_rank is not None:
            parts.append(f"dense#{self.dense_rank + 1}")
        return "+".join(parts) or "none"


class RagIndex:
    """混合索引。构建一次、落盘、之后只读加载。"""

    def __init__(
        self,
        docs: Sequence[Doc],
        vectors=None,
        *,
        embedder_name: str | None = None,
        built_at: str | None = None,
        extra: dict | None = None,
    ) -> None:
        self.docs: list[Doc] = list(docs)
        self.vectors = vectors
        self.embedder_name = embedder_name
        self.built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.extra = extra or {}
        self._bm25_ready = False

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _rebuild_bm25(self) -> None:
        """建倒排索引与 BM25 统计量。加载后调用一次即可。"""
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._doc_len: list[int] = []
        self._df: Counter = Counter()

        lengths: list[int] = []
        for idx, doc in enumerate(self.docs):
            counts = Counter(tokenize(f"{doc.title}\n{doc.text}"))
            lengths.append(sum(counts.values()) or 1)
            for term, tf in counts.items():
                self._postings[term].append((idx, tf))
                self._df[term] += 1

        self._doc_len = lengths
        self._avgdl = (sum(lengths) / len(lengths)) if lengths else 1.0
        self._bm25_ready = True

    def _bm25_scores(self, query: str, allowed: set[int] | None) -> dict[int, float]:
        if not self._bm25_ready:
            self._rebuild_bm25()

        terms = tokenize(query)
        if not terms:
            return {}

        total = len(self.docs) or 1
        scores: dict[int, float] = defaultdict(float)
        for term in set(terms):
            postings = self._postings.get(term)
            if not postings:
                continue
            df = self._df[term]
            # BM25 概率型 IDF；加 0.5 平滑避免 df=total 时取负。
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for doc_idx, tf in postings:
                if allowed is not None and doc_idx not in allowed:
                    continue
                dl = self._doc_len[doc_idx]
                norm = 1.0 - _BM25_B + _BM25_B * (dl / self._avgdl)
                scores[doc_idx] += idf * (tf * (_BM25_K1 + 1.0)) / (tf + _BM25_K1 * norm)
        return dict(scores)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    @staticmethod
    def _values(value) -> set[str]:
        values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        return {normalize(str(item)) for item in values if item is not None}

    @classmethod
    def _metadata_values(cls, meta: dict, key: str) -> set[str]:
        values = cls._values(meta.get(key))
        if key == "dataset":
            # 去重仅合并正文，副数据集出处仍是这个规范文档的真实来源。
            for origin in meta.get("also_seen_in") or []:
                if isinstance(origin, dict):
                    values |= cls._values(origin.get("dataset"))
        return values

    def _allowed(self, sources, kinds, lang, meta_filters=None, allowed_doc_ids=None) -> set[int] | None:
        """先按元数据圈定候选集，再做排序。

        必须先过滤再排序：若先取 top_k 再过滤，会得到"不足 k 条"甚至空结果，
        而库里其实有符合条件的内容。这也是向量库需要 pre-filter 的同一个问题。
        """
        if sources is None and kinds is None and lang is None and not meta_filters and allowed_doc_ids is None:
            return None
        def values(items):
            return {items} if isinstance(items, str) else set(items)
        src = values(sources) if sources is not None else None
        knd = values(kinds) if kinds is not None else None
        langs = values(lang) if lang is not None else None
        ids = values(allowed_doc_ids) if allowed_doc_ids is not None else None
        filters = {key: self._values(value) for key, value in (meta_filters or {}).items()}
        out = set()
        for idx, doc in enumerate(self.docs):
            if ids is not None and doc.doc_id not in ids:
                continue
            if src is not None and doc.source not in src:
                continue
            if knd is not None and doc.kind not in knd:
                continue
            if langs is not None and doc.lang not in langs:
                continue
            if any(not (wanted & self._metadata_values(doc.meta, key))
                   for key, wanted in filters.items()):
                continue
            out.add(idx)
        return out

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        candidate_k: int = 30,
        sources: Iterable[str] | None = None,
        kinds: Iterable[str] | None = None,
        lang: Iterable[str] | None = None,
        meta_filters: Mapping[str, object] | None = None,
        allowed_doc_ids: Iterable[str] | None = None,
        min_dense_score: float = 0.15,
        embedder=None,
    ) -> list[Hit]:
        """混合检索；所有过滤条件在候选截断前生效。

        ``meta_filters`` 键间 AND、单键多值 OR，规范化后精确匹配元数据；
        ``allowed_doc_ids`` 供领域门面表达复杂的作物/设备约束，空集拒绝全部。
        分数是排序依据，不是诊断概率。哈希向量必须有关键词证据才能参与。
        """
        informative = query_terms(query)
        if not self.docs or top_k <= 0 or candidate_k <= 0 or not informative:
            return []
        allowed = self._allowed(sources, kinds, lang, meta_filters, allowed_doc_ids)
        if allowed is not None and not allowed:
            return []

        bm25 = self._bm25_scores(query, allowed)
        lexical = set()
        for term in informative:
            lexical.update(idx for idx, _ in self._postings.get(term, ()))
        if allowed is not None:
            lexical &= allowed
        # 完全没有领域词面证据的查询不应仅凭稠密余弦被强行分配一个答案。
        # 已知术语的中英/同义改写由门面的 TermNormalizer 补足。
        if not lexical:
            return []
        bm25 = {idx: score for idx, score in bm25.items() if idx in lexical}
        bm25_ranked = sorted(bm25.items(), key=lambda kv: (-kv[1], kv[0]))[:candidate_k]

        dense_ranked: list[tuple[int, float]] = []
        if self.vectors is not None and embedder is not None:
            # 维度守卫：编码器与建库时用的若不是同一个模型，直接跳过稠密召回。
            # 否则 numpy 会抛 matmul 维度错误，把整次检索连 BM25 一起带崩——
            # 而这里完全可以从容降级：BM25 结果仍然是可用的。
            if (getattr(embedder, "dim", None) not in (None, self.vectors.shape[1])
                    or (self.embedder_name and getattr(embedder, "name", self.embedder_name) != self.embedder_name)):
                embedder = None
        if embedder is not None and self.vectors is not None:
            import numpy as np

            query_vec = embedder.encode([query])[0]
            sims = self.vectors @ query_vec
            if allowed is not None:
                mask = np.zeros(len(self.docs), dtype=bool)
                mask[list(allowed)] = True
                sims = np.where(mask, sims, -np.inf)
            if (self.embedder_name or getattr(embedder, "name", "")).startswith("hashing-"):
                sims = np.where(np.array([i in lexical for i in range(len(self.docs))]), sims, -np.inf)
            order = np.argsort(-sims, kind="stable")[:candidate_k]
            dense_ranked = [(int(i), float(sims[i])) for i in order
                            if np.isfinite(sims[i]) and sims[i] > max(0.0, min_dense_score)]

        fused: dict[int, float] = defaultdict(float)
        bm25_rank_of: dict[int, int] = {}
        dense_rank_of: dict[int, int] = {}
        dense_score_of = dict(dense_ranked)
        for rank, (doc_idx, _) in enumerate(bm25_ranked):
            fused[doc_idx] += 1.0 / (_RRF_K + rank + 1)
            bm25_rank_of[doc_idx] = rank
        for rank, (doc_idx, _) in enumerate(dense_ranked):
            fused[doc_idx] += 1.0 / (_RRF_K + rank + 1)
            dense_rank_of[doc_idx] = rank

        hits = [
            Hit(
                doc=self.docs[doc_idx],
                score=score,
                bm25_rank=bm25_rank_of.get(doc_idx),
                dense_rank=dense_rank_of.get(doc_idx),
                bm25_score=bm25.get(doc_idx, 0.0),
                dense_score=dense_score_of.get(doc_idx, 0.0),
            )
            for doc_idx, score in fused.items()
        ]
        hits.sort(key=lambda h: (normalize(h.doc.title) != normalize(query), -h.score, h.doc.doc_id))
        return hits[:top_k]

    def profile(self) -> dict:
        by_source: Counter = Counter(doc.source for doc in self.docs)
        by_kind: Counter = Counter(doc.kind for doc in self.docs)
        return {
            "documents": len(self.docs),
            "by_source": dict(by_source.most_common()),
            "by_kind": dict(by_kind.most_common()),
            "dense": self.vectors is not None,
            "embedder": self.embedder_name,
            "built_at": self.built_at,
        }

    # ------------------------------------------------------------------
    # 落盘 / 加载
    # ------------------------------------------------------------------

    def save(self, index_dir: str) -> None:
        os.makedirs(index_dir, exist_ok=True)
        corpus_path = os.path.join(index_dir, CORPUS_NAME)
        with open(corpus_path, "w", encoding="utf-8", newline="\n") as fh:
            for doc in self.docs:
                fh.write(json.dumps(doc.to_json(), ensure_ascii=False) + "\n")

        vector_path = os.path.join(index_dir, VECTORS_NAME)
        if self.vectors is not None:
            import numpy as np

            np.save(vector_path, self.vectors)
        elif os.path.exists(vector_path):
            os.remove(vector_path)  # 重建为纯 BM25 时清掉旧向量，避免维度错配

        manifest = {
            "version": 1,
            "built_at": self.built_at,
            "documents": len(self.docs),
            "embedder": self.embedder_name,
            "dim": int(self.vectors.shape[1]) if self.vectors is not None else None,
            **self.profile(),
            **self.extra,
        }
        with open(os.path.join(index_dir, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, index_dir: str) -> "RagIndex":
        corpus_path = os.path.join(index_dir, CORPUS_NAME)
        if not os.path.exists(corpus_path):
            raise FileNotFoundError(f"索引不存在：{corpus_path}（先跑 tools/build_rag_index.py）")

        docs: list[Doc] = []
        with open(corpus_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    docs.append(Doc.from_json(json.loads(line)))

        manifest_path = os.path.join(index_dir, MANIFEST_NAME)
        manifest: dict = {}
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)

        vectors = None
        vector_path = os.path.join(index_dir, VECTORS_NAME)
        if os.path.exists(vector_path):
            try:
                import numpy as np

                vectors = np.load(vector_path)
                if (vectors.ndim != 2 or vectors.shape[0] != len(docs) or vectors.shape[1] == 0
                        or not np.issubdtype(vectors.dtype, np.floating)
                        or not np.isfinite(vectors).all()
                        or (manifest.get("dim") is not None and manifest["dim"] != vectors.shape[1])):
                    # 形状、条数或数值不一致：不能用错位/损坏的向量，保留 BM25。
                    vectors = None
            except (ImportError, ValueError, TypeError, OSError):
                vectors = None

        index = cls(
            docs,
            vectors,
            embedder_name=manifest.get("embedder"),
            built_at=manifest.get("built_at"),
            extra={key: value for key, value in manifest.items() if key not in _MANIFEST_RESERVED},
        )
        index._rebuild_bm25()
        return index

    @classmethod
    def build(
        cls,
        docs: Sequence[Doc],
        embedder=None,
        *,
        batch_size: int = 64,
        progress: callable | None = None,
    ) -> "RagIndex":
        """用嵌入后端构建带向量的索引；``embedder`` 为 None 时退化为纯 BM25。"""
        if batch_size <= 0:
            raise ValueError("batch_size 必须为正整数")
        docs = list(docs)
        vectors = None
        embedder_name = None

        if embedder is not None and docs:
            import numpy as np

            total = len(docs)
            # Transformer 按批内最长文本补齐；混排长短文档会浪费大量计算。
            # 编码顺序按长度分组，但必须写回原位置，保持 corpus 与向量行身份一致。
            token_lengths = getattr(embedder, "token_lengths", None)
            lengths = token_lengths([doc.text for doc in docs]) if token_lengths else [len(doc.text) for doc in docs]
            if len(lengths) != total:
                raise ValueError("编码器返回的文本长度条数不匹配")
            order = sorted(range(total), key=lambda idx: lengths[idx])
            for start in range(0, total, batch_size):
                positions = order[start : start + batch_size]
                batch = [docs[idx].text for idx in positions]
                encoded = np.asarray(embedder.encode(batch), dtype="float32")
                if (encoded.ndim != 2 or encoded.shape[0] != len(positions)
                        or encoded.shape[1] == 0 or not np.isfinite(encoded).all()):
                    raise ValueError("编码器返回的向量条数、形状或数值不合法")
                if (getattr(embedder, "dim", encoded.shape[1]) not in (None, encoded.shape[1])
                        or (vectors is not None and encoded.shape[1] != vectors.shape[1])):
                    raise ValueError("编码器返回的向量维度不一致")
                if vectors is None:
                    vectors = np.empty((total, encoded.shape[1]), dtype="float32")
                vectors[positions] = encoded
                if progress:
                    progress(min(start + batch_size, total), total)
            embedder_name = getattr(embedder, "name", repr(embedder))

        index = cls(docs, vectors, embedder_name=embedder_name)
        index._rebuild_bm25()
        return index


def iter_jsonl(path: str) -> Iterator[dict]:
    """流式读取 JSONL。语料可能上百 MB，不要一次读进内存。"""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
