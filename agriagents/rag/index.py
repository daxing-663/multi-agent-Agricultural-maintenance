"""混合检索引擎：BM25 关键词召回 + 稠密向量语义召回，用 RRF 融合。

**为什么不是纯向量**：农业问句里大量专有名词（"番茄晚疫病""斜纹夜蛾"
"Colletotrichum orbiculare"）。纯向量对低频专名容易糊成近邻（把晚疫病
召回到早疫病），纯关键词又对"叶子发黄"↔"叶片黄化"这种同义改写无能为力。
两者互补，所以融合。

**为什么是 RRF 而不是加权求和**：BM25 分数无上界、余弦在 [0,1]，
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
from typing import Iterable, Iterator, Sequence

from agriagents.rag.text import normalize, tokenize

# BM25 参数。k1 控制词频饱和，b 控制长度归一。
# 1.5 / 0.75 是文献里的通用默认值，对短文本（症状、问答对）表现稳定。
_BM25_K1 = 1.5
_BM25_B = 0.75

# RRF 平滑常数。60 出自原论文，作用是压低头部单条结果的绝对优势。
_RRF_K = 60

MANIFEST_NAME = "manifest.json"
CORPUS_NAME = "corpus.jsonl"
VECTORS_NAME = "vectors.npy"


@dataclass
class Doc:
    """一条可检索的知识块。

    ``text`` 是**唯一**被索引的字段：分词、嵌入都只看它。
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
            counts = Counter(tokenize(doc.text))
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

    def _allowed(self, sources, kinds, lang) -> set[int] | None:
        """先按元数据圈定候选集，再做排序。

        必须先过滤再排序：若先取 top_k 再过滤，会得到"不足 k 条"甚至空结果，
        而库里其实有符合条件的内容。这也是向量库需要 pre-filter 的同一个问题。
        """
        if not sources and not kinds and not lang:
            return None
        src = set(sources) if sources else None
        knd = set(kinds) if kinds else None
        langs = set(lang) if lang else None
        out = set()
        for idx, doc in enumerate(self.docs):
            if src is not None and doc.source not in src:
                continue
            if knd is not None and doc.kind not in knd:
                continue
            if langs is not None and doc.lang not in langs:
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
        embedder=None,
    ) -> list[Hit]:
        """混合检索。``embedder`` 只在索引带向量且与查询编码器一致时才需要传。"""
        if not self.docs:
            return []
        allowed = self._allowed(sources, kinds, lang)
        if allowed is not None and not allowed:
            return []

        bm25 = self._bm25_scores(query, allowed)
        bm25_ranked = sorted(bm25.items(), key=lambda kv: (-kv[1], kv[0]))[:candidate_k]

        dense_ranked: list[tuple[int, float]] = []
        if self.vectors is not None and embedder is not None:
            import numpy as np

            # 维度守卫：编码器与建库时用的若不是同一个模型，直接跳过稠密召回。
            # 否则 numpy 会抛 matmul 维度错误，把整次检索连 BM25 一起带崩——
            # 而这里完全可以从容降级：BM25 结果仍然是可用的。
            if getattr(embedder, "dim", None) not in (None, self.vectors.shape[1]):
                embedder = None
        if embedder is not None and self.vectors is not None:
            import numpy as np

            query_vec = embedder.encode([query])[0]
            sims = self.vectors @ query_vec
            if allowed is not None:
                mask = np.zeros(len(self.docs), dtype=bool)
                mask[list(allowed)] = True
                sims = np.where(mask, sims, -np.inf)
            order = np.argsort(-sims)[:candidate_k]
            dense_ranked = [(int(i), float(sims[i])) for i in order if np.isfinite(sims[i])]

        fused: dict[int, float] = defaultdict(float)
        bm25_rank_of: dict[int, int] = {}
        dense_rank_of: dict[int, int] = {}
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
            )
            for doc_idx, score in fused.items()
        ]
        hits.sort(key=lambda h: (-h.score, h.doc.doc_id))
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
                if len(vectors) != len(docs):
                    # 语料与向量条数不一致：宁可退化成 BM25，也不能拿错位的向量去检索
                    vectors = None
            except ImportError:
                vectors = None

        index = cls(
            docs,
            vectors,
            embedder_name=manifest.get("embedder"),
            built_at=manifest.get("built_at"),
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
        docs = list(docs)
        vectors = None
        embedder_name = None

        if embedder is not None and docs:
            import numpy as np

            chunks: list = []
            total = len(docs)
            for start in range(0, total, batch_size):
                batch = [d.text for d in docs[start : start + batch_size]]
                chunks.append(embedder.encode(batch))
                if progress:
                    progress(min(start + batch_size, total), total)
            vectors = np.vstack(chunks).astype("float32")
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
