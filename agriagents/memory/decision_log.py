"""维护决策台账：追加式 markdown 记录 + 历史教训检索。

台账是这套系统的"记忆"。写入路径分两段：

- **写入（run 结束时）**：追加一条 ``pending`` 记录，只落决策正文，不调用 LLM；
- **结算（结果回填后）**：改写该条为已结算，附现场结果与复盘，供后续 run 检索。

与 TradingAgents 的 ``TradingMemoryLog`` 同构，两处农业特有的差异：

1. 结算不由固定"持有期"触发，而由**现场回填**触发（是否作业、复检指标如何、
   成本与产量变化），因此本层不自己取数，只接受回填；
2. 台账同时是**合规留痕**：审批人、是否 dry-run、执行偏差都留在条目里，
   事后追责与审计不必再翻日志。

存储格式（HTML 注释作硬分隔符，模型不会写出这种字符串）::

    [2026-09-26 | FIELD-07 | Immediate | pending]

    DECISION:
    ...

    <!-- ENTRY_END -->
"""

from __future__ import annotations

import re
from pathlib import Path


class MaintenanceMemoryLog:
    """维护决策的追加式台账。"""

    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    _TAG_RE = re.compile(
        r"^\[(?P<as_of>[^|\]]+)\s*\|\s*(?P<site>[^|\]]+)\s*\|\s*(?P<disposition>[^|\]]+)"
        r"\s*\|\s*(?P<status>[^\]]+)\]\s*$",
        re.MULTILINE,
    )
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nOUTCOME:|\nREFLECTION:|\Z)", re.DOTALL)
    _OUTCOME_RE = re.compile(r"OUTCOME:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)\Z", re.DOTALL)

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._log_path: Path | None = None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_entries = cfg.get("memory_log_max_entries")

    # ---------------- 写入路径 ----------------

    def store_decision(self, site_id: str, as_of: str, final_decision: str) -> None:
        """追加一条 pending 记录；同一对象同一天只记一次。"""
        if not self._log_path:
            return
        text = (final_decision or "").strip()
        if not text:
            return

        # 幂等：重跑同一天不能把同一条决策记两次（会污染所有统计与教训检索）
        marker = f"[{as_of} | {site_id} |"
        if self._log_path.exists() and marker in self._log_path.read_text(encoding="utf-8"):
            return

        from agriagents.agents.rating import parse_disposition

        tag = f"[{as_of} | {site_id} | {parse_disposition(text)} | pending]"
        entry = f"{tag}\n\nDECISION:\n{text}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as handle:
            handle.write(entry)

    def settle_entry(self, site_id: str, as_of: str, outcome: str, reflection: str,
                     resolved_date: str, status: str) -> bool:
        """把一条 pending 记录改写为已结算；找不到则返回 False。"""
        entries = self.load_entries()
        hit = False
        for entry in entries:
            if entry["site"] == site_id and entry["as_of"] == as_of and entry["pending"]:
                entry.update({
                    "pending": False,
                    "status": status,
                    "resolved": resolved_date,
                    "outcome": outcome,
                    "reflection": reflection,
                })
                hit = True
        if hit:
            self._rewrite(entries)
        return hit

    def _rewrite(self, entries: list[dict]) -> None:
        """按条目列表重写整个台账（结算需要改写历史行）。"""
        if not self._log_path:
            return
        blocks = []
        for entry in entries:
            status = "pending" if entry["pending"] else f"resolved:{entry.get('resolved', '')} | {entry.get('status', '')}"
            block = [
                f"[{entry['as_of']} | {entry['site']} | {entry['disposition']} | {status}]",
                "",
                "DECISION:",
                entry.get("decision", "").strip(),
            ]
            if entry.get("outcome"):
                block += ["", "OUTCOME:", entry["outcome"].strip()]
            if entry.get("reflection"):
                block += ["", "REFLECTION:", entry["reflection"].strip()]
            blocks.append("\n".join(block))
        self._log_path.write_text(self._SEPARATOR.join(blocks) + self._SEPARATOR, encoding="utf-8")

    # ---------------- 读取路径 ----------------

    def load_entries(self) -> list[dict]:
        """解析全部条目。"""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")
        entries = []
        for raw in (chunk.strip() for chunk in text.split(self._SEPARATOR)):
            if not raw:
                continue
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def _parse_entry(self, raw: str) -> dict | None:
        tag = self._TAG_RE.search(raw)
        if not tag:
            return None
        status = tag.group("status").strip()
        decision = self._DECISION_RE.search(raw)
        outcome = self._OUTCOME_RE.search(raw)
        reflection = self._REFLECTION_RE.search(raw)
        return {
            "as_of": tag.group("as_of").strip(),
            "site": tag.group("site").strip(),
            "disposition": tag.group("disposition").strip(),
            "status": status,
            "pending": status == "pending",
            "resolved": status.split("resolved:")[-1].split("|")[0].strip() if "resolved:" in status else "",
            "decision": decision.group(1).strip() if decision else "",
            "outcome": outcome.group(1).strip() if outcome else "",
            "reflection": reflection.group(1).strip() if reflection else "",
        }

    def get_pending_entries(self) -> list[dict]:
        """尚未回填结果的条目。"""
        return [e for e in self.load_entries() if e["pending"]]

    def get_past_context(self, site_id: str, n_same: int = 5, n_cross: int = 3,
                         as_of: str | None = None) -> str:
        """生成注入提示词的历史上下文。

        ``as_of`` 给定时，只纳入"在那个日期之前就已结算"的教训——
        回看 7 月的决策时，绝不能引用 8 月才知道的结果，否则复盘是假的。
        """
        entries = self.load_entries()
        usable = []
        for entry in entries:
            if entry["pending"]:
                continue
            if as_of and entry.get("resolved") and entry["resolved"] > as_of:
                continue
            if as_of and not entry.get("resolved"):
                # 没有结算日期就无法证明它在 as_of 之前已知，回看场景一律排除
                continue
            usable.append(entry)

        same = [e for e in usable if e["site"] == site_id][-n_same:]
        cross = [e for e in usable if e["site"] != site_id][-n_cross:]
        if not same and not cross:
            return ""

        lines = []
        if same:
            lines.append("同一对象的历史决策与复盘：")
            lines += [self._render_lesson(e) for e in same]
        if cross:
            lines.append("其他对象可借鉴的教训：")
            lines += [self._render_lesson(e) for e in cross]
        return "\n".join(lines)

    @staticmethod
    def _render_lesson(entry: dict) -> str:
        parts = [f"- [{entry['as_of']} | {entry['site']} | {entry['disposition']}]"]
        if entry.get("outcome"):
            parts.append(f"  结果：{entry['outcome']}")
        if entry.get("reflection"):
            parts.append(f"  复盘：{entry['reflection']}")
        return "\n".join(parts)
