"""AgriAgents：多智能体协同农业维护框架（框架骨架）。

分层与 TradingAgents 对齐，可逐层替换：

    cli/                    入口层 —— Typer 交互式 CLI
    agriagents/graph/       编排层 —— LangGraph 状态图（协调决策中枢 + 三个执行型 Agent）
    agriagents/agents/      智能体层 —— 感知 / 诊断 / 协调决策 / 执行
    agriagents/dataflows/   数据层 —— 工具 → 路由 → 供应商适配器（含 as-of 防未来信息）
    agriagents/memory/      记忆层 —— 决策台账 / 结果回填结算 / 复盘反思

四个 Agent 的系统提示词（各 Agent 文件里的 ``SYSTEM_PROMPT``）与作业手册
（``agriagents/skills/<agent>/SKILL.md``）已落地；工具层内置``合成演示数据源``，
零配置即可跑通全流程（返回值自带 ``[synthetic_demo]`` 标识，不冒充实测数据）。
真实数据源与农艺规则仍标为 ``TODO(内容)`` 占位。
"""

from agriagents.default_config import DEFAULT_CONFIG
from agriagents.graph.agri_graph import AgriAgentsGraph

__all__ = ["AgriAgentsGraph", "DEFAULT_CONFIG"]
__version__ = "0.1.0"
