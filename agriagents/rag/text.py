"""文本规范化、切分与分词：RAG 全链路的底层约定。

两个刻意的选择：

1. **中文不引分词器**。农业语料的专有名词（"番茄晚疫病""斜纹夜蛾""小菜蛾"）
   在通用词典里多半是生词，分词器会把它们切碎，检索时反而丢召回。
   这里改用字级 uni-gram + bi-gram：零依赖、不丢召回，代价是索引略大。
2. **英文不做词干还原**。语料里大量拉丁学名（Colletotrichum orbiculare）
   被词干还原破坏后无法还原；BM25 的 IDF 已经能压低无信息词。
   只做小写折叠与常见停用词过滤。

所有函数都是纯函数。ingest 与 query 两端**必须**用同一套切分，
否则召回会静默劣化——所以切分逻辑只此一份，不允许在别处重写。
"""

from __future__ import annotations

import re
import unicodedata

# CJK 及日韩字符区间：落在这些区间里的字符按"字"处理，逐字切分。
_CJK_RANGES = (
    (0x3040, 0x30FF),   # 日文假名
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 基本区
    (0xF900, 0xFAFF),   # CJK 兼容区
)

_ASCII_WORD = re.compile(r"[a-z0-9]+")
_WS = re.compile(r"\s+")

# 英文停用词。中文不做停用词过滤——单字在中文里往往承载信息（"叶""果""茎"）。
_STOPWORDS_EN = frozenset(
    """a an the of in on for to and or is are was were be been being with by from
    as at it its this that these those there their they we you i not no if then
    than which who whom what when where how can may might must should would could
    will shall do does did done have has had""".split()
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def normalize(text: str) -> str:
    """NFKC 折叠 + 小写 + 空白归一。全链路唯一的规范化入口。"""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).lower()
    return _WS.sub(" ", folded).strip()


def tokenize(text: str) -> list[str]:
    """切分为检索词元。

    中文：连续汉字串同时产出 uni-gram 与 bi-gram。
    英文/数字：按词切分并过滤停用词。
    """
    text = normalize(text)
    if not text:
        return []

    tokens: list[str] = []
    cjk_run: list[str] = []

    def flush_cjk() -> None:
        if not cjk_run:
            return
        tokens.extend(cjk_run)
        tokens.extend(a + b for a, b in zip(cjk_run, cjk_run[1:]))
        cjk_run.clear()

    for ch in text:
        if _is_cjk(ch):
            cjk_run.append(ch)
            continue
        flush_cjk()
        if ch.isascii() and (ch.isalnum()):
            continue  # 由下面的正则统一收词，避免逐字符拼装
    flush_cjk()

    for word in _ASCII_WORD.findall(text):
        if word not in _STOPWORDS_EN:
            tokens.append(word)

    return tokens


_SENT_BREAK = re.compile(r"(?<=[。！？；!?;])|(?<=\.\s)|\n+")


def sentences(text: str) -> list[str]:
    """粗切句。只为切块服务，不追求语言学正确。"""
    if not text:
        return []
    parts = [p.strip() for p in _SENT_BREAK.split(text)]
    return [p for p in parts if p]


def chunk(text: str, max_chars: int = 520, overlap: int = 80) -> list[str]:
    """按句打包切块，块间保留 ``overlap`` 个字符的重叠。

    重叠是必要的：一个症状描述常跨句（"叶片出现水渍状斑点。随后扩大为
    深褐色圆形病斑。"），不重叠会把因果切断，检索到半句话。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf = ""
    for sent in sentences(text):
        # 单句超长：先冲刷缓冲区，再对这句做硬切
        if len(sent) > max_chars:
            if buf.strip():
                chunks.append(buf.strip())
                buf = ""
            for i in range(0, len(sent), max_chars - overlap):
                piece = sent[i : i + max_chars]
                if piece.strip():
                    chunks.append(piece.strip())
            continue

        if buf and len(buf) + len(sent) > max_chars:
            chunks.append(buf.strip())
            buf = (buf[-overlap:] if overlap > 0 else "") + sent
        else:
            buf += sent

    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def snippet(text: str, limit: int = 240) -> str:
    """截断为便于塞进提示词的一行摘要。"""
    text = _WS.sub(" ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"
