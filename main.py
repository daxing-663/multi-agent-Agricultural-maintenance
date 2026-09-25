"""最小可运行示例：不接任何 API key，也能把整张图跑完。

    python main.py

真实使用：把 provider 换成 openai / openai_compatible，
并在 .env 里配置对应密钥与模型名。
"""

from agriagents.default_config import DEFAULT_CONFIG
from agriagents.graph.agri_graph import AgriAgentsGraph

config = DEFAULT_CONFIG.copy()

# 演示用：自动放行（人工审批默认是开启的，这里关掉只为让示例一条命令跑完）
config["require_human_approval"] = False
config["dry_run"] = True

graph = AgriAgentsGraph(debug=True, config=config)

# 资源账本可选：给出来，中枢就会按实际人力/机具/库存分配；不给则明确标注为未知
resources = {
    "labor": "2 人（上午可调度）",
    "machinery": ["3 号植保机", "滴灌阀门 A 组"],
    "materials": {"生物农药 X": "12 L", "叶面肥 Y": "不足"},
}

state, disposition = graph.propagate("FIELD-07", "2026-09-26", site_type="field", resources=resources)

print("=" * 60)
print(state["final_decision"])
print("=" * 60)
print("处置等级：", disposition)
print("案件状态：", state["case_status"])
print("报告目录：", graph.save_reports(state, "FIELD-07"))
