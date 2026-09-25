"""人工审批门：执行前唯一的放行开关。

三种模式（配置驱动，代码里没有静默放行的分支）：

1. ``require_human_approval=True``（默认）：调用 LangGraph 的 ``interrupt()``
   把工单抛给人，等回填审批结果后从断点继续（需要开启 checkpoint）。
2. ``require_human_approval=False`` + ``dry_run=True``：自动放行，但审批记录
   里明确写下"演练模式，未取得人工审批"——留痕必须诚实。
3. 两者都为 False：受控自动化场景的自动放行，记录同样写明依据与风险。

注意：审批门只做放行判断，不修改工单内容；要改工单必须退回工单规划节点，
这样"谁改的、改了什么"在轨迹里是清楚的。
"""

import logging

try:  # langgraph>=0.4 提供 interrupt
    from langgraph.types import interrupt as _interrupt
except ImportError:  # pragma: no cover - 老版本兜底
    _interrupt = None

from agriagents.dataflows.config import get_config

logger = logging.getLogger(__name__)


def create_approval_gate():
    def approval_gate_node(state) -> dict:
        config = get_config()
        coordination_state = state["coordination_state"]
        require_human = config.get("require_human_approval", True)
        dry_run = config.get("dry_run", True)
        work_order = coordination_state.get("work_order", "")

        if require_human:
            if _interrupt is None:
                raise RuntimeError(
                    "已配置 require_human_approval=True，但当前 langgraph 版本不支持 interrupt()；"
                    "请升级 langgraph，或显式关闭人工审批（不建议在真实设备上这样做）。"
                )
            decision = _interrupt({
                "type": "maintenance_approval",
                "site": state.get("site_of_interest"),
                "as_of": state.get("as_of"),
                "work_order": work_order,
                "safety_review": coordination_state.get("safety_review", ""),
                "question": "是否批准执行本工单？回填 {'approved': true/false, 'approver': ..., 'note': ...}",
            })
            if isinstance(decision, dict):
                approved = bool(decision.get("approved"))
                approver = decision.get("approver") or "未记录"
                note = decision.get("note") or ""
            else:
                approved, approver, note = bool(decision), "未记录", ""
            record = f"人工审批：{'通过' if approved else '驳回'}；审批人={approver}；意见={note or '无'}"
        elif dry_run:
            approved, record = True, "演练模式（dry_run）：未取得人工审批，自动放行仅用于验证流程。"
        else:
            approved = True
            record = (
                "自动放行：require_human_approval=False 且 dry_run=False。"
                "已记录放行依据，风险由调用方承担。"
            )

        logger.info("审批结果：%s（%s）", "通过" if approved else "驳回", record)
        return {
            "coordination_state": {
                **coordination_state,
                "approval": record,
                "approved": approved,
            }
        }

    return approval_gate_node
