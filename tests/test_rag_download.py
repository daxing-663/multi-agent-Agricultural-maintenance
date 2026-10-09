"""Download integrity: real parquet shards must survive merging and failures."""

import shutil

import pytest

from agriagents.rag import fetch


def test_multishard_parquet_preserves_rows_from_all_shards(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    paths = [tmp_path / "a.parquet", tmp_path / "b.parquet"]
    pq.write_table(pa.table({"text": ["番茄", "黄瓜"]}), paths[0])
    pq.write_table(pa.table({"text": ["soil", "valve", "fan"]}), paths[1])

    def local_download(url, dest, **kwargs):
        shutil.copyfile(url, dest)
        return dest

    monkeypatch.setattr(fetch, "download", local_download)
    merged = str(tmp_path / "merged.parquet")
    fetch._fetch_parquet([str(path) for path in paths], fetch.HF_HUB, merged, resume=False)
    assert fetch._parquet_is_complete(merged)
    assert pq.read_table(merged).column("text").to_pylist() == ["番茄", "黄瓜", "soil", "valve", "fan"]


def test_failed_cache_repair_keeps_original_file(tmp_path, monkeypatch):
    dest = tmp_path / "cache.parquet"
    original = b"PAR1 truncated content"
    dest.write_bytes(original)
    monkeypatch.setattr(fetch, "hf_parquet_urls", lambda *a, **k: ["https://example.invalid/data"])

    def fail(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(fetch, "_fetch_parquet", fail)
    with pytest.raises(RuntimeError, match="失败"):
        fetch.hf_download_parquet("example/data", str(dest), retries=1)
    assert dest.read_bytes() == original


def test_parquet_magic_alone_is_not_valid_metadata(tmp_path):
    pytest.importorskip("pyarrow")
    dest = tmp_path / "false.parquet"
    dest.write_bytes(b"PAR1invalid footerPAR1")
    assert not fetch._parquet_is_complete(str(dest))
