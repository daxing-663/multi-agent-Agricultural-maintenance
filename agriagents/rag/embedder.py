"""嵌入后端：把文本编码为稠密向量，供 RAG 的语义召回使用。

**为什么是本地模型**：DeepSeek 只提供对话补全，没有 ``/v1/embeddings``
（实测返回 404）。所以嵌入只能在本地算，或者再接一家厂商。这里选本地，
理由是农业现场的部署环境常常没有稳定外网，本地模型断网也能跑。

**为什么不用 sentence-transformers**：它会拖进 torch（约 2.5 GB）。
``fastembed`` 走 ONNX Runtime，装上只有约 40 MB，模型按需下载，
在多语言检索上的表现对本场景足够。为此多一个可选依赖是划算的。

三个后端，按可用性依次回退：

===================  ==================  ==========================================
后端                 依赖                适用
===================  ==================  ==========================================
``FastEmbedEmbedder`` ``fastembed``       默认。多语言神经嵌入，需一次模型下载
``HashingEmbedder``   ``numpy``          断网/隔离环境的确定性兜底，质量较低
``None``              无                 只用 BM25 关键词召回，仍然可用
===================  ==================  ==========================================

注意 ``HF_ENDPOINT``：``huggingface_hub`` 只在**导入时**读这个变量，
所以必须在 import fastembed 之前设好，晚了不生效。见 ``_prepare_hf_env``。
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol, Sequence, runtime_checkable

# 默认模型：多语言，中英混检。384 维，量化 ONNX 约 225 MB。
# 换模型只改配置里的 rag_embedding_model，接口不变。
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# 纯中文、更小（92 MB）的备选：BAAI/bge-small-zh-v1.5
# 若语料确定只有中文，换它更快；但它对英文实体名的检索会明显变差。


@runtime_checkable
class Embedder(Protocol):
    """嵌入后端协议。实现必须返回 L2 归一化后的 float32 矩阵。

    归一化在实现内部完成，调用方（``RagIndex``）直接用点积当余弦相似度。
    """

    name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> "object":
        """返回 shape=(len(texts), dim) 的 L2 归一化矩阵。"""
        ...


def _require_numpy():
    try:
        import numpy  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ImportError(
            "稠密检索需要 numpy。安装：pip install numpy "
            "（或直接装 fastembed，它会带上 numpy）"
        ) from exc
    import numpy

    return numpy


def _prepare_hf_env(endpoint: str | None, cache_dir: str | None) -> None:
    """在导入 huggingface_hub 之前设置环境变量。"""
    if endpoint and not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = endpoint
    if cache_dir:
        os.environ.setdefault("HF_HOME", cache_dir)
    # Windows 上默认走符号链接会打一堆告警，且普通用户没有权限，噪声大。
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


class FastEmbedEmbedder:
    """基于 ``fastembed``（ONNX Runtime）的多语言嵌入。"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: str | None = None,
        hf_endpoint: str | None = None,
        threads: int | None = None,
    ) -> None:
        _prepare_hf_env(hf_endpoint, cache_dir)
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise ImportError(
                "未安装 fastembed，无法使用本地稠密嵌入。安装："
                "pip install --index-url https://pypi.org/simple fastembed"
            ) from exc

        _require_numpy()
        kwargs: dict = {"model_name": model_name}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        if threads:
            kwargs["threads"] = threads
        self._model = TextEmbedding(**kwargs)
        self.name = model_name
        self.dim = self._probe_dim()

    def _probe_dim(self) -> int:
        probe = next(iter(self._model.embed(["dimension probe"])))
        return int(len(probe))

    def token_lengths(self, texts: Sequence[str]) -> list[int]:
        """Estimate actual model lengths so build batches waste less padding.

        Older/other FastEmbed backends may not expose a tokenizer; character
        length remains a safe performance-only fallback in that case.
        """
        tokenizer = getattr(getattr(self._model, "model", None), "tokenizer", None)
        if tokenizer is None:
            return [len(text) for text in texts]
        lengths = []
        for start in range(0, len(texts), 1024):
            encoded = tokenizer.encode_batch(list(texts[start:start + 1024]))
            lengths.extend(sum(item.attention_mask) for item in encoded)
        return lengths

    def encode(self, texts: Sequence[str]) -> "object":
        import numpy as np

        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        vectors = np.asarray(list(self._model.embed(list(texts))), dtype="float32")
        return _l2_normalize(vectors)


class HashingEmbedder:
    """确定性哈希嵌入：字符 n-gram 哈希到固定维度。

    不需要下载任何模型，中英混合可用，进程间结果稳定
    （用 blake2b 而不是内置 ``hash()``——后者每进程加盐，重启后索引就废了）。

    质量明显不如神经嵌入，命中靠的是字面重叠而非语义。它的价值在于
    **保证隔离环境也有稠密召回**：温室、大棚、内网部署时先跑得起来，
    等能联网再换成 FastEmbed。
    """

    def __init__(self, dim: int = 512, ngrams: tuple[int, ...] = (2, 3)) -> None:
        _require_numpy()
        self.name = f"hashing-{dim}d-ng{''.join(map(str, ngrams))}"
        self.dim = dim
        self._ngrams = ngrams

    def _buckets(self, text: str) -> list[int]:
        norm = "".join(ch for ch in text.lower() if not ch.isspace())
        out: list[int] = []
        for n in self._ngrams:
            if len(norm) < n:
                continue
            for i in range(len(norm) - n + 1):
                gram = norm[i : i + n]
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                out.append(int.from_bytes(digest, "big") % self.dim)
        return out

    def encode(self, texts: Sequence[str]) -> "object":
        import numpy as np

        matrix = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            for bucket in self._buckets(text):
                matrix[row, bucket] += 1.0
        return _l2_normalize(matrix)


def _l2_normalize(matrix: "object") -> "object":
    import numpy as np

    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype("float32")


def get_embedder(config: dict | None = None, *, allow_download: bool = True) -> Embedder | None:
    """按配置构建嵌入后端。

    ``allow_download=False`` 时只用本地已缓存的模型，不发起任何网络请求——
    build 脚本用它做"离线模式"，运行期用它避免首次查询卡在几百 MB 的下载上。
    取不到就回退到 ``HashingEmbedder``；再不行（连 numpy 都没有）返回 ``None``，
    调用方据此走纯 BM25 路径。
    """
    config = config or {}
    backend = (config.get("rag_embedding_backend") or "auto").lower()
    model_name = config.get("rag_embedding_model") or DEFAULT_MODEL
    cache_dir = config.get("rag_model_cache_dir")
    endpoint = config.get("rag_hf_endpoint")
    threads = config.get("rag_embedding_threads", 2)

    if backend == "none":
        return None

    if backend in ("auto", "fastembed"):
        try:
            if allow_download:
                return FastEmbedEmbedder(model_name, cache_dir=cache_dir, hf_endpoint=endpoint, threads=threads)
            if _model_is_cached(model_name, cache_dir):
                return FastEmbedEmbedder(model_name, cache_dir=cache_dir, hf_endpoint=endpoint, threads=threads)
        except Exception:  # noqa: BLE001 - 回退是设计的一部分，不是兜错
            if backend == "fastembed":
                raise
    if backend == "hashing":
        return HashingEmbedder()

    try:
        return HashingEmbedder()
    except ImportError:
        return None


def _model_is_cached(model_name: str, cache_dir: str | None) -> bool:
    """粗略判断模型是否已在本地缓存，避免离线模式下触发下载。

    不能用"拼一个精确 slug"的办法：fastembed 会把模型名映射到它自己的
    ONNX 仓库（``sentence-transformers/xxx`` → ``qdrant/xxx-onnx-Q``），
    目录名与 ``model_name`` 对不上。所以拿模型名的最后一段做模糊匹配。

    误判的代价是不对称的：宁可漏判（多下载一次）也不能误判为"已缓存"
    而实际没有——那样会退化成哈希嵌入，与已建好的 384 维向量对不上。
    """
    needle = model_name.split("/")[-1].lower()
    home = os.path.expanduser("~")
    roots = [
        cache_dir,
        os.path.join(home, ".agriagents", "models"),
        os.path.join(home, ".cache", "huggingface"),
        os.path.join(home, ".cache", "huggingface", "hub"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            if entry.startswith("models--") and needle in entry.lower():
                return True
    return False
