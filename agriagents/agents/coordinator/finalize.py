"""收口节点：把流程结果合成为《维护决策单》。

两个确定性节点（不调用 LLM，因此可测试、零成本、不会跑偏）：

- ``Close Case``：无需处置时收口，等级取中枢的处置预判；但如果是"强制收口"
  （中枢本轮并未选择 close，而是被安全检查或轮次预算兜底到这里），
  一律产出 REVIEW 而不是任何一档处置结论——没走完的流程不冒充结论；
- ``Finalize``：执行（或回滚）之后收口，等级取工单等级，并附执行与审批记录。

决策单的正文由上游节点写出，本节点只做**组装与核对**：
如果关键字段缺失，宁可产出 REVIEW 标记，也不猜一个等级填上去。
"""

from agriagents.agents.rating import RATING_REVIEW, extract_disposition
from agriagents.agents.schemas import MaintenanceDecision, render_maintenance_decision


def _disposition_from(text: str, fallback: str = RATING_REVIEW) -> str:
    """从正文里解析处置等级，解析不出返回兜底值。"""
    return extract_disposition(text) or fallback


def _assemble(state, disposition: str, summary: str, work_order_ref: str,
              approval_note: str, basis: str) -> dict:
    if disposition == RATING_REVIEW:
        return "\n".join([
            "**处置等级：** REVIEW（待人工复核）",
            f"**对象：** {state.get('site_of_interest', '')}",
            "",
            f"**结论：** {summary or '流程未能产出一个可解析的处置等级，请人工复核完整轨迹后再决定。'}",
            f"**依据：** {basis or '（见报告树中的各阶段产出）'}",
            f"**关联工单：** {work_order_ref}",
            f"**审批：** {approval_note}",
            "**改变条件：** 人工复核后按复核结论执行。",
        ])
    decision = MaintenanceDecision(
        disposition=disposition,
        site_id=str(state.get("site_of_interest", "")),
        executive_summary=summary,
        basis=basis,
        work_order_ref=work_order_ref,
        approval_note=approval_note,
        what_would_change="出现新的感知证据或诊断结论时，本结论需重新评估。",
    )
    return render_maintenance_decision(decision)


def create_close_case_node():
    """无需处置时的收口节点。"""

    def close_case_node(state) -> dict:
        coordination_state = state["coordination_state"]
        notes = coordination_state.get("coordination_notes", "")
        work_order_ref = _work_order_id(coordination_state.get("work_order", ""))
        next_action = coordination_state.get("next_action", "")

        # 强制收口：中枢本轮并没有选择 close（例如安全检查连续打回、或轮次预算耗尽后
        # 由路由兜底收口）。此时绝不能把它的处置预判渲染成一个普通结论——
        # 那会让一份"从未执行、也没走完审批"的决策单看起来像已经定了处置方案。
        if next_action not in ("", "close"):
            summary = (
                "流程未走完："
                + (coordination_state.get("decision_reason") or "上游未给出可执行结论。")
                + " 已起草的工单与安全检查意见见报告树，请人工复核后再决定是否执行。"
            )
            text = _assemble(
                state,
                disposition=RATING_REVIEW,
                summary=summary,
                work_order_ref=work_order_ref,
                approval_note="未进入执行环节，未取得审批。",
                basis=coordination_state.get("safety_review", "")
                or state["perception_state"].get("anomaly_screen", ""),
            )
            return {"final_decision": text, "case_status": "closed_no_action"}

        disposition = _disposition_from(notes, fallback="Monitor")
        summary = (
            coordination_state.get("decision_reason")
            or "未发现需要处置的异常，本轮不安排作业。"
        )
        text = _assemble(
            state,
            disposition=disposition,
            summary=summary,
            work_order_ref="无",
            approval_note="未进入执行环节，无需审批。",
            basis=state["perception_state"].get("anomaly_screen", ""),
        )
        return {"final_decision": text, "case_status": "closed_no_action"}

    return close_case_node


def create_finalize_node():
    """执行结束后的收口节点。"""

    def finalize_node(state) -> dict:
        coordination_state = state["coordination_state"]
        execution_state = state["execution_state"]
        disposition = _disposition_from(coordination_state.get("work_order", ""), fallback="Scheduled")
        status = execution_state.get("execution_status", "unknown")
        rolled_back = bool(coordination_state.get("rollback_plan")) or status == "failed"

        summary = (
            f"工单已执行，执行结果：{status}。"
            + ("流程经过回滚处置，需人工复检。" if rolled_back else "按工单验收标准复检即可。")
        )
        text = _assemble(
            state,
            disposition=disposition,
            summary=summary,
            work_order_ref=_work_order_id(coordination_state.get("work_order", "")),
            approval_note=coordination_state.get("approval", "（无审批记录）"),
            basis=state["diagnosis_state"].get("rationale", ""),
        )
        return {
            "final_decision": text,
            "case_status": "rolled_back" if rolled_back else "executed",
        }

    return finalize_node


def _work_order_id(work_order_text: str) -> str:
    """从工单正文里取工单号，取不到返回「无」。"""
    for line in work_order_text.splitlines():
        if "工单号" in line:
            return line.split("：")[-1].split(":")[-1].strip().strip("*") or "无"
    return "无"
