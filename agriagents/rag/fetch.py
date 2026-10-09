"""语料获取与落盘：HTTP 下载、HF 直连 parquet、HF datasets-server 分页、宽容编码读取。

**为什么默认走 parquet 直连而不是 datasets-server 分页**：分页端点每页
上限 100 行，实测约 135 行/秒。中文农林牧渔那个集有 92.7 万条，
光扫一遍就要近两小时；直连 parquet 是几秒到几分钟。分页端点保留为
**回退路径**：它不需要 pyarrow，且能按行偏移精确取样。

**编码是历史包袱**：CropDP-KG 的同一批 CSV 里，``relationcrop.csv`` 是
UTF-8，``relationsym.csv`` 却是 GB18030。逐文件猜编码不现实，所以统一走
``read_text_any`` 依次尝试，失败时明确报错而不是产出乱码——
乱码进了索引比没有索引更糟，它会以"召回到了但内容不可读"的形式
浪费每一次诊断。
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

USER_AGENT = "AgriAgents-RAG/0.1"
HF_DATASETS_SERVER = "https://datasets-server.huggingface.co"
HF_HUB = "https://huggingface.co"

# 按优先级尝试的编码。gb18030 能吃下 utf-8 的大部分字节序列而反过来不成立，
# 所以必须先试严格的 utf-8，再退到 gb18030。
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

# parquet 文件首尾各有一个 PAR1 magic，缺任一即为截断
_PARQUET_MAGIC = b"PAR1"


def get_bytes(url: str, *, timeout: int = 90, retries: int = 3, headers: dict | None = None) -> bytes:
    """带重试的 GET。网络抖动是常态，重试比让整条 ingest 挂掉划算。"""
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                last = exc
                time.sleep(3.0 * (attempt + 1))
                continue
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {exc.code} @ {url} :: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下载失败（重试 {retries} 次）：{url} :: {last!r}")


def download(url: str, dest: str, *, timeout: int = 300, force: bool = False) -> str:
    """流式下载到 ``dest``。已存在且非空则跳过（增量构建靠这个）。

    先写 ``.part`` 再改名：中断时不会留下一个"看起来存在但内容被截断"的文件，
    而截断的语料文件是最难排查的一类问题。走流式是因为中文集约 172 MB，
    整块读进内存没必要。
    """
    if os.path.exists(dest) and os.path.getsize(dest) > 0 and not force:
        return dest
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------------------
# 文本 / CSV / JSONL
# ---------------------------------------------------------------------------


def read_text_any(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        f"无法解码 {path}（已尝试 {', '.join(_ENCODINGS)}）。"
        "请确认文件未被截断，或为其补充编码。"
    )


def iter_csv_rows(path: str, *, delimiter: str = ",") -> Iterator[dict]:
    """流式读 CSV 为字典。关系表可能上万行，不整块解析。"""
    reader = csv.DictReader(io.StringIO(read_text_any(path)), delimiter=delimiter)
    for row in reader:
        # CropDP-KG 的表里混有大量空行
        if any((v or "").strip() for v in row.values()):
            yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def write_jsonl(path: str, rows) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: str) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


# ---------------------------------------------------------------------------
# HuggingFace：直连 parquet（首选）
# ---------------------------------------------------------------------------


def hf_endpoint() -> str:
    """HF 镜像端点。国内直连 huggingface.co 常常超时，默认走 hf-mirror。"""
    return (os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com").rstrip("/")


def hf_parquet_urls(dataset: str, *, config: str = "default", split: str = "train") -> list[str]:
    """取数据集自动转换后的 parquet 文件地址。

    ``/parquet`` 是 HF 为每个数据集预先转好的 parquet，不用 clone 仓库。
    返回的地址是 huggingface.co 的 API 地址，实际下载时再按镜像端点重写。
    """
    payload = json.loads(
        get_bytes(f"{HF_HUB}/api/datasets/{dataset}/parquet").decode("utf-8")
    )
    files = payload.get(config, {}).get(split) or []
    return list(files)


def _parquet_is_complete(path: str) -> bool:
    """检查 parquet 文件首尾的 ``PAR1`` magic。

    parquet 的 magic 在文件**两端**都有，所以"头部对、尾部不对"能精确
    识别出被截断的下载。这个检查是必要的：镜像站拉大文件时连接中断
    并不罕见，而一个截断的 parquet 在读取时才会报 ``ArrowInvalid``，
    那时错误信息离真正的原因（下载不完整）已经很远了。
    """
    try:
        size = os.path.getsize(path)
        if size < 12:
            return False
        with open(path, "rb") as fh:
            if fh.read(4) != _PARQUET_MAGIC:
                return False
            fh.seek(-4, os.SEEK_END)
            if fh.read(4) != _PARQUET_MAGIC:
                return False
        # Magic alone also accepts concatenated shards and corrupt metadata.
        try:
            import pyarrow.parquet as pq
        except ImportError:
            return True
        try:
            with pq.ParquetFile(path) as parquet:
                return parquet.metadata is not None
        except Exception:
            return False
    except OSError:
        return False


def _fetch_parquet(files: list[str], endpoint: str, tmp: str, *, resume: bool) -> None:
    """把分片写入 ``tmp``。单分片支持 Range 续传，多分片重来。"""
    single = len(files) == 1
    offset = 0
    if resume and single and os.path.exists(tmp):
        offset = os.path.getsize(tmp)
    if offset == 0 and os.path.exists(tmp):
        os.remove(tmp)

    if single:
        req = urllib.request.Request(
            files[0].replace(HF_HUB, endpoint, 1),
            headers={"User-Agent": USER_AGENT, **({"Range": f"bytes={offset}-"} if offset else {})},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            # 服务端不支持 Range 时会直接回 200 并从头开始，此时必须用 wb 覆盖，
            # 否则会在截断的文件后面追加完整文件，得到一个更"完整"的坏文件。
            if offset and getattr(resp, "status", 200) != 206:
                offset = 0
            elif offset:
                content_range = resp.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {offset}-"):
                    raise RuntimeError("Range 响应起点不符，拒绝拼接损坏语料")
            with open(tmp, "ab" if offset else "wb") as out:
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
        return

    # Parquet files cannot be concatenated byte-for-byte: footer offsets refer
    # to each individual file. Rewrite row groups through a single writer.
    import tempfile
    import pyarrow.parquet as pq

    writer = None
    try:
        with tempfile.TemporaryDirectory(prefix="agri-parquet-", dir=os.path.dirname(tmp)) as staging:
            for index, url in enumerate(files):
                shard = os.path.join(staging, f"{index}.parquet")
                download(url.replace(HF_HUB, endpoint, 1), shard, timeout=600)
                if not _parquet_is_complete(shard):
                    raise RuntimeError(f"parquet 分片 {index} 下载不完整")
                with pq.ParquetFile(shard) as parquet:
                    if writer is None:
                        writer = pq.ParquetWriter(tmp, parquet.schema_arrow)
                    for batch in parquet.iter_batches(batch_size=2048):
                        writer.write_batch(batch)
    finally:
        if writer is not None:
            writer.close()


def hf_download_parquet(
    dataset: str,
    dest: str,
    *,
    config: str = "default",
    split: str = "train",
    force: bool = False,
    prefer_mirror: bool = True,
    retries: int = 4,
    validate: bool = True,
) -> str:
    """下载数据集的 parquet 分片（多分片会合并到同一个文件）。

    合并而不是保留多片：调用方只需要"一个可以流式扫的文件"，
    让分片细节泄漏到上层会让每个源都要写一遍遍历逻辑。

    下载后会校验 magic；不合格就丢弃重试，并支持 Range 续传。
    缓存命中同样要校验——否则一次中断留下的坏文件会被永久当成有效缓存。
    """
    if os.path.exists(dest) and os.path.getsize(dest) > 0 and not force:
        if not validate or _parquet_is_complete(dest):
            return dest
        # Preserve the invalid cache until a validated replacement is ready.

    files = hf_parquet_urls(dataset, config=config, split=split)
    if not files:
        raise RuntimeError(f"{dataset} 没有可用的 parquet（config={config}, split={split}）")

    endpoints = list(dict.fromkeys([hf_endpoint(), HF_HUB])) if prefer_mirror else [HF_HUB]
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    tmp = dest + ".part"

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            endpoint = endpoints[min(attempt, len(endpoints) - 1)]
            _fetch_parquet(files, endpoint, tmp, resume=attempt > 0)
            if validate and not _parquet_is_complete(tmp):
                got = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                raise RuntimeError(f"下载不完整（{got} 字节，缺少 parquet footer）")
            os.replace(tmp, dest)
            return dest
        except Exception as exc:  # noqa: BLE001 - 重试是这里的全部意义
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))

    if os.path.exists(tmp):
        os.remove(tmp)
    raise RuntimeError(f"下载 {dataset} 失败（重试 {retries} 次）：{last_error!r}")


def iter_parquet(path: str, *, columns: list[str] | None = None, batch_size: int = 2048) -> Iterator[dict]:
    """流式读取 parquet。

    分批而不是 ``read_table`` 全量：中文集解压后约 230 MB，
    全量载入再转 Python 对象会吃掉几百 MB 内存，没必要。
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ImportError(
            "读取 parquet 需要 pyarrow。安装："
            "pip install --index-url https://pypi.org/simple pyarrow"
        ) from exc

    handle = pq.ParquetFile(path)
    names = columns or handle.schema_arrow.names
    for batch in handle.iter_batches(batch_size=batch_size, columns=names):
        for row in batch.to_pylist():
            yield row


# ---------------------------------------------------------------------------
# HuggingFace：datasets-server 分页（回退路径；不需要 pyarrow）
# ---------------------------------------------------------------------------


def hf_split_info(dataset: str, *, timeout: int = 60) -> dict:
    payload = get_bytes(
        f"{HF_DATASETS_SERVER}/info?" + urllib.parse.urlencode({"dataset": dataset}),
        timeout=timeout,
    )
    return json.loads(payload.decode("utf-8"))


def hf_rows(
    dataset: str,
    split: str = "train",
    *,
    config: str | None = None,
    offset: int = 0,
    length: int = 100,
) -> dict:
    params = {"dataset": dataset, "split": split, "offset": offset, "length": length}
    if config:
        params["config"] = config
    return json.loads(get_bytes(f"{HF_DATASETS_SERVER}/rows?" + urllib.parse.urlencode(params)).decode("utf-8"))


def hf_iter_rows(
    dataset: str,
    split: str = "train",
    *,
    config: str | None = None,
    limit: int | None = None,
    page: int = 100,
) -> Iterator[dict]:
    """自动分页遍历。实测约 135 行/秒，只适合小数据集或精确取样。"""
    fetched = 0
    offset = 0
    while True:
        want = page if limit is None else min(page, limit - fetched)
        if want <= 0:
            return
        payload = hf_rows(dataset, split, config=config, offset=offset, length=want)
        rows = payload.get("rows") or []
        if not rows:
            return
        for item in rows:
            yield item.get("row") or {}
            fetched += 1
            if limit is not None and fetched >= limit:
                return
        offset += len(rows)
        if offset >= (payload.get("num_rows_total") or 0):
            return
