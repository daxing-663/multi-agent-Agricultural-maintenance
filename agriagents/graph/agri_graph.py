"""AgriAgentsGraph：整张图的对外门面（唯一入口）。

调用方只需要知道两件事：

    graph = AgriAgentsGraph()
    state, disposition = graph.propagate("FIELD-07", "2026-09-26")

其余（模型、数据源、轮次、审批策略、断点续跑、决策台账）都由配置驱动。
这一层的职责边界很明确：**装配 + 生命周期**，不含任何业务判断——
业务判断全在 Agent 节点里，这样换领域时不必动门面代码。
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.types import Command

from agriagents.agents.context import (
    RESOURCE_CONTEXT_ABSENT,
    build_site_context,
    render_resource_context,
)
from agriagents.agents.rating import parse_disposition
from agriagents.dataflows.config import run_config, set_config
from agriagents.dataflows.symbols import safe_site_component
from agriagents.dataflows.time_window import get_current_date
from agriagents.default_config import DEFAULT_CONFIG
from agriagents.graph.checkpointer import (
    checkpoint_step_exists,
    clear_checkpoint,
    get_checkpointer,
    thread_id,
)
from agriagents.graph.conditional_logic import ConditionalLogic
from agriagents.graph.propagation import Propagator
from agriagents.graph.setup import GraphSetup
from agriagents.llm import create_llm
from agriagents.memory import settlement
from agriagents.memory.decision_log import MaintenanceMemoryLog
from agriagents.memory.reflection import Reflector
from agriagents.reporting import write_report_tree
from agriagents.skills import skill_digest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckpointRun:
    """一次可暂停 LangGraph 调用的公开结果。"""

    state: dict
    thread_id: str
    lifecycle: str
    interrupt: dict | None
    next_nodes: tuple[str, ...]


def _validate_as_of(as_of) -> str:
    """基准日期必须是规范的 YYYY-MM-DD，且不得晚于今天。

    不允许未来日期：整条流水线的取数都以该日期为上界，
    若允许未来，模型会以为"明天已经发生的事"是可查的。
    """
    value = str(as_of)
    try:
        canonical = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        canonical = False
    if not canonical:
        raise ValueError(f"as_of 必须是 YYYY-MM-DD 格式，收到 {as_of!r}")
    if value > get_current_date():
        raise ValueError(f"as_of 不能是未来日期：{value}")
    return value


class AgriAgentsGraph:
    """多智能体协同农业维护流程的主类。"""

    def __init__(self, debug: bool = False, config: dict[str, Any] | None = None,
                 callbacks: list | None = None):
        self.debug = debug
        self.config = config or DEFAULT_CONFIG.copy()
        set_config(self.config)

        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        llm_kwargs = self._build_llm_kwargs()

        # 双模型分级：深思考模型只用在判断类节点上，成本与质量兼顾
        self.deep_thinking_llm = create_llm(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        self.quick_thinking_llm = create_llm(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.memory_log = MaintenanceMemoryLog(self.config)
        self.reflector = Reflector(self.quick_thinking_llm)
        self.conditional_logic = ConditionalLogic(
            max_perception_rounds=self.config["max_perception_rounds"],
            max_diagnosis_rounds=self.config["max_diagnosis_rounds"],
            max_coordination_rounds=self.config["max_coordination_rounds"],
            max_safety_rounds=self.config["max_safety_rounds"],
            max_rollback_rounds=self.config["max_rollback_rounds"],
            auto_close_when_no_anomaly=self.config["auto_close_when_no_anomaly"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm, self.deep_thinking_llm, self.conditional_logic
        )
        self.propagator = Propagator(max_recur_limit=self.config.get("max_recur_limit", 100))

        self.workflow = self.graph_setup.setup_graph()
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None
        self._resuming = False

    # ------------------------------------------------------------------
    # 配置与上下文
    # ------------------------------------------------------------------

    def _build_llm_kwargs(self) -> dict:
        """把通用 LLM 参数透传给客户端；None 表示交给供应商默认值。"""
        kwargs: dict = {}
        api_key = self.config.get("api_key")
        if api_key:
            kwargs["api_key"] = api_key
        for key in ("temperature", "max_tokens", "llm_max_retries"):
            value = self.config.get(key)
            if value is not None:
                kwargs[key if key != "llm_max_retries" else "max_retries"] = value
        # DeepSeek 专属：结构化输出那次调用关闭 thinking（实测结论，见 llm.py 模块文档）
        if str(self.config.get("llm_provider", "")).lower() == "deepseek":
            kwargs["disable_thinking_for_structured"] = self.config.get(
                "deepseek_quiet_structured", True
            )
            kwargs["thinking_enabled"] = self.config.get("deepseek_thinking", False)
        return kwargs

    def resolve_site_context(self, site_id: str, site_type: str = "field",
                             as_of: str | None = None) -> str:
        """确定性解析对象档案并生成上下文。

        目前先接内置的**演示地块档案**（`dataflows/vendors/synthetic.py` 的
        ``lookup_site_profile``），让诊断能拿到作物与规模；接入真实台账后替换该来源，
        仍然 fail-open（查不到就退回编号级上下文），不能因为台账缺失让整次巡检做不了。
        """
        from agriagents.dataflows.vendors.synthetic import lookup_site_profile

        try:
            identity = lookup_site_profile(site_id) or {}
        except Exception:  # 台账来源异常不得阻断巡检
            identity = {}
        return build_site_context(site_id, site_type, identity, as_of)

    def _memory_as_of(self, as_of) -> str | None:
        """历史教训的时点截断：回看过去时，只允许使用当时已知的教训。"""
        value = str(as_of)
        return value if value < datetime.now().strftime("%Y-%m-%d") else None

    def _run_signature(self) -> str:
        """影响图形态的配置，参与 checkpoint 的 thread 标识。

        这些参数变了还复用同一个 checkpoint，会续跑在一张已经不同的图上。
        """
        return "|".join([
            f"approval={self.config['require_human_approval']}",
            f"dry_run={self.config['dry_run']}",
            f"coordination={self.config['max_coordination_rounds']}",
            f"safety={self.config['max_safety_rounds']}",
            f"rollback={self.config['max_rollback_rounds']}",
            # 技能包（作业手册）改了，跑出来的东西就不一样，不能复用旧断点
            f"skills={skill_digest()}",
        ])

    # ------------------------------------------------------------------
    # 断点续跑
    # ------------------------------------------------------------------

    def begin_checkpoint(self, site_id: str, as_of: str, run_id: str = "") -> str | None:
        """按需重编译带 checkpointer 的图，返回 thread_id（未开启则返回 None）。"""
        self._resuming = False
        if not self.config.get("checkpoint_enabled"):
            return None
        signature = self._run_signature()
        self._checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], site_id)
        saver = self._checkpointer_ctx.__enter__()
        self.graph = self.workflow.compile(checkpointer=saver)

        tid = thread_id(site_id, str(as_of), signature, run_id)
        self._resuming = checkpoint_step_exists(site_id, str(as_of), signature, run_id)
        logger.info("%s %s / %s", "续跑" if self._resuming else "新起", site_id, as_of)
        return tid

    def end_checkpoint(self):
        """恢复成不带 checkpointer 的普通图。"""
        if self._checkpointer_ctx is not None:
            self._checkpointer_ctx.__exit__(None, None, None)
            self._checkpointer_ctx = None
            self.graph = self.workflow.compile()
        self._resuming = False

    @contextmanager
    def checkpoint_scope(self, site_id: str, as_of: str, run_id: str = ""):
        try:
            yield self.begin_checkpoint(site_id, as_of, run_id)
        finally:
            self.end_checkpoint()

    def checkpoint_input(self, init_state: dict):
        """续跑时传 None，否则传初始状态。

        续跑时重新传初始状态会经 messages reducer 再插一遍消息，导致历史重复。
        """
        return None if self._resuming else init_state

    def clear_checkpoint_on_success(self, site_id: str, as_of: str):
        """成功收口后清掉检查点，避免下一次 run 误续。"""
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(self.config["data_cache_dir"], site_id, str(as_of), self._run_signature())

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def create_run_state(self, site_id: str, as_of: str, site_type: str = "field",
                         resources: dict | None = None, request_note: str = "") -> dict:
        """构造初始状态：先结算历史条目，再注入档案与教训。"""
        self.settle_pending(site_id)
        site_context = self.resolve_site_context(site_id, site_type, as_of)
        if request_note.strip():
            site_context += f"\n巡检请求备注：{request_note.strip()}"
        return self.propagator.create_initial_state(
            site_id,
            as_of,
            site_type=site_type,
            site_context=site_context,
            resource_context=render_resource_context(resources) or RESOURCE_CONTEXT_ABSENT,
            past_context=self.memory_log.get_past_context(
                site_id, as_of=self._memory_as_of(as_of)
            ),
        )

    def propagate(self, site_id: str, as_of: str, site_type: str = "field",
                  resources: dict | None = None):
        """跑一次维护决策，返回 ``(最终状态, 处置等级)``。

        处置等级为五档之一；解析不出时返回 ``"REVIEW"``，调用方应先判
        ``agriagents.agents.rating.is_review`` 再当成可执行结论使用。
        """
        as_of = _validate_as_of(as_of)
        with run_config(self.config), self.checkpoint_scope(site_id, as_of) as tid:
            return self._run_graph(site_id, as_of, site_type, tid, resources)

    def start_checkpointed_case(
        self,
        site_id: str,
        as_of: str,
        site_type: str = "field",
        resources: dict | None = None,
        run_id: str = "",
        request_note: str = "",
    ) -> CheckpointRun:
        """启动案件并运行到人工审批中断或流程结束。

        与 :meth:`propagate` 不同，本方法保留成功后的 checkpoint，供 API 查询报告与
        审批恢复使用。调用方必须开启 ``checkpoint_enabled`` 和人工审批。
        """
        as_of = _validate_as_of(as_of)
        self._require_checkpointed_approval()
        with run_config(self.config), self.checkpoint_scope(site_id, as_of, run_id) as tid:
            if not tid:
                raise RuntimeError("checkpoint 未开启")
            if self._resuming:
                raise RuntimeError(f"案件 {site_id}/{as_of} 已存在 checkpoint，不能重复创建")
            initial = self.create_run_state(
                site_id, as_of, site_type, resources, request_note=request_note
            )
            return self._stream_checkpointed(self.checkpoint_input(initial), tid)

    def resume_checkpointed_case(
        self,
        site_id: str,
        as_of: str,
        *,
        approved: bool,
        approver: str,
        note: str = "",
        run_id: str = "",
    ) -> CheckpointRun:
        """把人工审批结果送入 ``Command(resume=...)`` 并从 checkpoint 继续。"""
        as_of = _validate_as_of(as_of)
        self._require_checkpointed_approval()
        decision = {"approved": bool(approved), "approver": approver, "note": note}
        with run_config(self.config), self.checkpoint_scope(site_id, as_of, run_id) as tid:
            if not tid or not self._resuming:
                raise RuntimeError(f"案件 {site_id}/{as_of} 没有可恢复的 checkpoint")
            current = self._checkpoint_outcome(tid)
            if current.lifecycle != "awaiting_approval":
                raise RuntimeError(f"案件当前状态为 {current.lifecycle}，不接受审批")
            return self._stream_checkpointed(Command(resume=decision), tid)

    def read_checkpointed_case(
        self,
        site_id: str,
        as_of: str,
        *,
        run_id: str = "",
    ) -> CheckpointRun:
        """只读返回 checkpoint 中的最新业务状态和中断信息。"""
        as_of = _validate_as_of(as_of)
        if not self.config.get("checkpoint_enabled"):
            raise RuntimeError("读取案件需要开启 checkpoint_enabled")
        with run_config(self.config), self.checkpoint_scope(site_id, as_of, run_id) as tid:
            if not tid or not self._resuming:
                raise RuntimeError(f"案件 {site_id}/{as_of} 没有 checkpoint")
            return self._checkpoint_outcome(tid)

    def _require_checkpointed_approval(self) -> None:
        if not self.config.get("checkpoint_enabled"):
            raise RuntimeError("API 案件必须开启 checkpoint_enabled")
        if not self.config.get("require_human_approval"):
            raise RuntimeError("API 案件必须开启 require_human_approval")

    def _checkpoint_config(self, tid: str) -> dict:
        config = dict(self.propagator.get_graph_args()["config"])
        config["configurable"] = {"thread_id": tid}
        return config

    def _stream_checkpointed(self, graph_input, tid: str) -> CheckpointRun:
        args = self.propagator.get_graph_args()
        config = self._checkpoint_config(tid)
        last_state: dict | None = None
        for chunk in self.graph.stream(graph_input, **{**args, "config": config}):
            if isinstance(chunk, dict) and "__interrupt__" not in chunk:
                last_state = chunk
            if self.debug:
                self._print_progress(chunk)

        outcome = self._checkpoint_outcome(tid, fallback=last_state)
        if outcome.lifecycle == "completed":
            decision = outcome.state.get("final_decision", "")
            if decision:
                self.memory_log.store_decision(
                    outcome.state.get("site_of_interest", ""),
                    outcome.state.get("as_of", ""),
                    decision,
                )
        return outcome

    def _checkpoint_outcome(self, tid: str, fallback: dict | None = None) -> CheckpointRun:
        snapshot = self.graph.get_state(self._checkpoint_config(tid))
        state = dict(snapshot.values or fallback or {})
        interrupt_value = None
        for task in getattr(snapshot, "tasks", ()):
            for item in getattr(task, "interrupts", ()):
                value = getattr(item, "value", item)
                interrupt_value = value if isinstance(value, dict) else {"value": value}
                break
            if interrupt_value is not None:
                break
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if interrupt_value is not None:
            lifecycle = "awaiting_approval"
        elif next_nodes:
            lifecycle = "paused"
        else:
            lifecycle = "completed"
        return CheckpointRun(state, tid, lifecycle, interrupt_value, next_nodes)

    def _run_graph(self, site_id: str, as_of: str, site_type: str, tid: str | None,
                   resources: dict | None) -> tuple[dict, str]:
        init_state = self.create_run_state(site_id, as_of, site_type, resources)
        args = self.propagator.get_graph_args()
        config = dict(args["config"])
        if tid:
            config["configurable"] = {"thread_id": tid}

        final_state = None
        for chunk in self.graph.stream(self.checkpoint_input(init_state), **{**args, "config": config}):
            if self.debug:
                self._print_progress(chunk)
            final_state = chunk

        if final_state is None:
            raise RuntimeError("图没有产出任何状态，请检查装配是否正确")

        decision = final_state.get("final_decision", "")
        self.memory_log.store_decision(site_id, as_of, decision)
        self.clear_checkpoint_on_success(site_id, as_of)
        return final_state, parse_disposition(decision)

    def _print_progress(self, state: dict) -> None:
        """调试输出：每步打印已完成的报告长度，便于定位卡在哪一环。"""
        coordination = state.get("coordination_state", {})
        logger.debug(
            "感知=%d字 诊断=%d字 中枢轮次=%s 裁决=%s 执行=%s",
            len(state.get("perception_state", {}).get("perception_report", "") or ""),
            len(state.get("diagnosis_state", {}).get("diagnosis_report", "") or ""),
            coordination.get("coordinator_rounds"),
            coordination.get("safety_verdict"),
            state.get("execution_state", {}).get("execution_status"),
        )

    def settle_pending(self, site_id: str, outcome_lookup=None):
        """结算该对象此前未回填的决策条目。

        默认的 ``outcome_lookup`` 返回 None（尚无回填），因此不会有任何条目被结算。
        TODO(内容): 接入真实回填来源（作业记录、复检读数、产量/成本数据）。
        """
        with run_config(self.config):
            return settlement.settle_pending(
                site_id, self.memory_log, self.reflector, self.config, outcome_lookup
            )

    def save_reports(self, final_state: dict, site_id: str, save_path=None) -> Path:
        """把一次 run 的报告树写到磁盘。"""
        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = Path(self.config["results_dir"]) / "reports" / f"{safe_site_component(site_id)}_{stamp}"
        return write_report_tree(final_state, site_id, save_path)

    def describe_graph(self) -> str:
        """输出节点清单（供文档/自检使用）。"""
        from agriagents.graph.execution_plan import NODE_SPECS
        lines = [f"{spec.team:12s} {spec.node:22s} {spec.kind:14s} {spec.model}" for spec in NODE_SPECS]
        return "\n".join(lines)


def load_resources(path: str | None) -> dict | None:
    """从 JSON 文件读取资源账本（人力/机具/农资）。"""
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
