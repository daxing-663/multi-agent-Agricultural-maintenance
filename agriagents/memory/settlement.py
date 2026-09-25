"""结算：把已回填的现场结果写回台账，并生成复盘。

农业与交易在这一点上差别最大：交易的"结算"是等 N 个交易日后读行情，
农业的结算依赖**现场回填**（作业是否执行、复检指标、产量与成本变化）。
框架因此把回填做成一个可注入的 ``outcome_lookup``：

- 默认返回 None（尚未回填）→ 条目继续留在 pending，不做任何猜测；
- 接入真实来源（作业记录、物联网复测、农事日志）后，条目自动开始结算。

原则：**拿不到结果就不结算**。用"大概没问题"去补一条结果，
会让整本台账的教训变成噪音，比不记更糟。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def default_outcome_lookup(site_id: str, as_of: str) -> dict | None:
    """默认回填源：尚无接入，返回 None。

    TODO(内容): 接入真实回填来源，返回形如::

        {
            "resolved_date": "2026-10-03",
            "status": "success",              # success / partial / failed
            "outcome": "作业按工单完成；7 天后复检病斑停止扩展，新叶正常。",
        }
    """
    return None


def settle_pending(site_id: str, memory_log, reflector, config: dict,
                   outcome_lookup=None) -> int:
    """结算该对象所有已可结算的 pending 条目，返回结算条数。"""
    lookup = outcome_lookup or default_outcome_lookup
    settled = 0

    for entry in memory_log.get_pending_entries():
        if entry["site"] != site_id:
            continue
        result = lookup(site_id, entry["as_of"])
        if not result:
            continue

        outcome = str(result.get("outcome", "")).strip()
        if not outcome:
            logger.warning("回填结果缺少 outcome 正文，跳过 %s/%s", site_id, entry["as_of"])
            continue

        reflection = reflector.reflect_on_outcome(
            final_decision=entry.get("decision", ""),
            outcome=outcome,
            status=str(result.get("status", "")),
            site_id=site_id,
            as_of=entry["as_of"],
        )

        ok = memory_log.settle_entry(
            site_id=site_id,
            as_of=entry["as_of"],
            outcome=outcome,
            reflection=reflection,
            resolved_date=str(result.get("resolved_date", "")),
            status=str(result.get("status", "")),
        )
        settled += 1 if ok else 0

    if settled:
        logger.info("已结算 %d 条历史决策（对象 %s）", settled, site_id)
    return settled
