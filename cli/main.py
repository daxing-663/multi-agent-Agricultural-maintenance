"""命令行入口：一次巡检、以及图的节点自检。

CLI 只负责**参数解析 + 展示**，业务判断全在图里。
这样同一套流程既能被人手工跑，也能被定时任务/平台调用，两者不会分叉。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from agriagents.agents.rating import label as disposition_label
from agriagents.agents.rating import is_review
from agriagents.dataflows.time_window import get_current_date
from agriagents.default_config import DEFAULT_CONFIG
from agriagents.graph.agri_graph import AgriAgentsGraph

app = typer.Typer(
    name="agriagents",
    help="AgriAgents：多智能体协同农业维护框架",
    add_completion=False,
)
console = Console()


def _build_config(
    approval: Optional[bool],
    dry_run: Optional[bool],
    checkpoint: Optional[bool],
    provider: Optional[str],
    deep_model: Optional[str],
    quick_model: Optional[str],
) -> dict:
    """把命令行开关叠加到默认配置上（未指定则保持默认）。"""
    config = DEFAULT_CONFIG.copy()
    overrides = {
        "require_human_approval": approval,
        "dry_run": dry_run,
        "checkpoint_enabled": checkpoint,
        "llm_provider": provider,
        "deep_think_llm": deep_model,
        "quick_think_llm": quick_model,
    }
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
    return config


@app.callback(invoke_without_command=True)
def analyze(
    ctx: typer.Context,
    site: Optional[str] = typer.Option(None, "--site", "-s", help="维护对象编号，例如 FIELD-07"),
    as_of: Optional[str] = typer.Option(None, "--date", "-d", help="决策基准日期 YYYY-MM-DD，默认今天"),
    site_type: str = typer.Option("field", "--type", help="对象类型：field / greenhouse / orchard / machine"),
    resources: Optional[Path] = typer.Option(None, "--resources", help="资源账本 JSON 文件（人力/机具/农资）"),
    approval: Optional[bool] = typer.Option(None, "--approval/--no-approval", help="是否要求人工审批（默认要求）"),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run/--live", help="干跑：只记录指令不下发设备（默认干跑）"),
    checkpoint: Optional[bool] = typer.Option(None, "--checkpoint/--no-checkpoint", help="开启断点续跑"),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM 供应商（stub / openai / openai_compatible）"),
    deep_model: Optional[str] = typer.Option(None, "--deep-model", help="深思考模型名"),
    quick_model: Optional[str] = typer.Option(None, "--quick-model", help="快思考模型名"),
    debug: bool = typer.Option(False, "--debug", help="打印每一步的进度"),
    save: bool = typer.Option(True, "--save/--no-save", help="是否写出报告树"),
):
    """对某个维护对象跑一次维护决策流程。"""
    if ctx.invoked_subcommand is not None:
        return
    if not site:
        console.print("[yellow]缺少 --site。示例：[/yellow] agriagents --site FIELD-07 --type field")
        raise typer.Exit(code=1)

    config = _build_config(approval, dry_run, checkpoint, provider, deep_model, quick_model)
    graph = AgriAgentsGraph(debug=debug, config=config)

    resources_data = None
    if resources:
        resources_data = json.loads(Path(resources).read_text(encoding="utf-8"))

    console.print(
        Panel(
            f"对象：{site}（{site_type}）\n基准日期：{as_of or get_current_date()}\n"
            f"模型：{config['llm_provider']} / {config['deep_think_llm']} + {config['quick_think_llm']}\n"
            f"人工审批：{'要求' if config['require_human_approval'] else '不要求'}"
            f"　干跑：{'是' if config['dry_run'] else '否'}",
            title="AgriAgents",
        )
    )

    with console.status("流程运行中…"):
        state, disposition = graph.propagate(
            site, as_of or get_current_date(), site_type=site_type, resources=resources_data
        )

    console.rule("维护决策单")
    console.print(state.get("final_decision") or "（未产出决策单）")
    console.print(
        f"\n处置等级：[bold]{'REVIEW（待人工复核）' if is_review(disposition) else disposition_label(disposition)}[/bold]"
        f"　案件状态：{state.get('case_status')}"
    )

    if save:
        path = graph.save_reports(state, site)
        console.print(f"报告已写出：{path}")


@app.command()
def graph_nodes():
    """打印图的节点清单（自检用）。"""
    from agriagents.graph.execution_plan import NODE_SPECS

    console.print(f"{'组':<12}{'节点':<24}{'类型':<16}{'模型'}")
    for spec in NODE_SPECS:
        console.print(f"{spec.team:<12}{spec.node:<24}{spec.kind:<16}{spec.model}")


if __name__ == "__main__":
    app()
