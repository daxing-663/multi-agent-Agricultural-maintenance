"""断点续跑：每个案件一个 SQLite 检查点库。

用途有两个，缺一不可：

1. **崩溃恢复**——长流程（采集→诊断→审批→执行）中途失败可续跑；
2. **人工审批**——``interrupt()`` 必须在带 checkpointer 的图上才能工作，
   没有它，"停下来等人签字"在技术上是做不到的。

路径与 thread 标识：
- 每个案件一个库，避免并发案件互相争锁；
- thread = 案件 + 基准日期 + 图形态签名，形状变了就新起而不是误续。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from agriagents.dataflows.config import get_config
from agriagents.dataflows.symbols import safe_site_component

logger = logging.getLogger(__name__)


def _db_path(data_dir: str | Path, site_id: str) -> Path:
    """案件对应的检查点数据库路径。"""
    safe = safe_site_component(site_id).upper()
    p = Path(data_dir) / "checkpoints"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{safe}.db"


def thread_id(site_id: str, as_of: str, signature: str = "") -> str:
    """由 案件 + 基准日期(+图形态签名) 决定的稳定 thread ID。"""
    base = f"{site_id.upper()}:{as_of}"
    if signature:
        base = f"{base}:{signature}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]


@contextmanager
def get_checkpointer(data_dir: str | Path, site_id: str) -> Generator[SqliteSaver, None, None]:
    """产出该案件的 SqliteSaver 上下文。"""
    db = _db_path(data_dir, site_id)
    conn = sqlite3.connect(str(db), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()


def checkpoint_step_exists(site_id: str, as_of: str, signature: str = "") -> bool:
    """该 thread 是否已有检查点（决定"新起"还是"续跑"）。

    查不到一律按"没有"处理：宁可重跑一次，也不要因为读不到检查点而误判为续跑。
    """
    db = _db_path(get_config()["data_cache_dir"], site_id)
    if not db.exists():
        return False
    tid = thread_id(site_id, str(as_of), signature)
    try:
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (tid,)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:  # 库还没建表 / 被占用
        logger.debug("读取检查点失败，按无检查点处理：%s", exc)
        return False
    return bool(rows)


def clear_checkpoint(data_dir: str | Path, site_id: str, as_of: str, signature: str = "") -> None:
    """删除该 thread 的检查点（成功收口后调用，避免下次误续）。"""
    db = _db_path(data_dir, site_id)
    if not db.exists():
        return
    tid = thread_id(site_id, str(as_of), signature)
    conn = sqlite3.connect(str(db))
    try:
        for table in ("writes", "checkpoints"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (tid,))
            except sqlite3.Error:
                continue
        conn.commit()
    finally:
        conn.close()


def clear_all_checkpoints(data_dir: str | Path) -> int:
    """删除全部检查点库，返回删除的文件数。用于强制全新开始。"""
    directory = Path(data_dir) / "checkpoints"
    if not directory.exists():
        return 0
    count = 0
    for db in directory.glob("*.db"):
        db.unlink()
        count += 1
    return count
