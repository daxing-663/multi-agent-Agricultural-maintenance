"""API 案件索引。

LangGraph checkpoint 保存可恢复的业务状态；这个小型 SQLite 只保存列表检索所需的
元数据和状态投影，避免前端扫描每个地块的 checkpoint 文件。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CaseStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _setup(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cases (
                    id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL,
                    site_type TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    thread_id TEXT,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    interrupt_json TEXT,
                    report_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS api_cases_updated ON api_cases(updated_at DESC)"
            )

    def create(
        self,
        *,
        case_id: str,
        site_id: str,
        site_type: str,
        as_of: str,
        dry_run: bool,
        note: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_cases
                    (id, site_id, site_type, as_of, status, dry_run, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (case_id, site_id, site_type, as_of, int(dry_run), note, now, now),
            )
        return self.get(case_id)

    def update(self, case_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status", "thread_id", "state_json", "interrupt_json", "report_path", "error"
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新字段：{sorted(unknown)}")
        if not fields:
            return self.get(case_id)
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = [fields[key] for key in fields]
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE api_cases SET {assignments} WHERE id = ?", (*values, case_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(case_id)
        return self.get(case_id)

    def get(self, case_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return self._decode(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_cases ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["dry_run"] = bool(item["dry_run"])
        item["state"] = json.loads(item.pop("state_json") or "{}")
        item["interrupt"] = json.loads(item.pop("interrupt_json") or "null")
        return item

