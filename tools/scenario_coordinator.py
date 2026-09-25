"""只测协调决策中枢：喂一份场景证据，看它怎么决策。

用法::

    python tools/scenario_coordinator.py                  # 用配置里的真实模型（默认 deepseek）
    python tools/scenario_coordinator.py --provider stub  # 离线桩，不花钱
    python tools/scenario_coordinator.py --variant plain  # 对照：不给现场作业条件

场景：南宁某大棚番茄，2026-09-26 10:00。
    土壤墒情告警 + 无人机影像见下部叶片褐色病斑；诊断建议喷洒杀菌剂防治早疫病；
    现场风速 8.5 m/s、喷洒机电量 18%。

脚本只调中枢一个节点（不跑感知/诊断/执行），因此结果是**给定证据下中枢的判断**，
不是整条链路的表现；输出末尾附一张自检表，逐条列出期望与实测。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SITE_ID = "NN-GH-01"
AS_OF = "2026-09-26"

ANOMALY_SCREEN = """\
**异常等级:** medium
**是否需跟进:** 是
**异常通道:** sensor（soil_moisture 10cm 跌破参考下限）；imagery（下部叶片褐色病斑）
**摘要:** 墒情三日持续下行叠加叶部病斑，需诊断确认病因、严重度与蔓延条件。
"""

PERCEPTION_REPORT = """\
[感知报告 · 对象 NN-GH-01 · 基准 2026-09-26（以下均为观察事实）]

一、土壤墒情（sensor · 10cm 层，2026-09-20 ~ 2026-09-26）
- 09-20 21.9% → 09-23 20.4% → 09-26 10:00 17.6%（参考下限 20.0%）
- 30cm 层同期 22.4% → 22.1%，无同向变化
- 近 24 小时无灌溉记录

二、无人机影像（imagery · 2026-09-26 09:40 巡飞，可见光）
- 下部 1~3 层叶可见褐色、近圆形病斑，直径 3~8 mm，边缘黄化；估算病叶占下部叶面积约 18%
- 中上部叶与果实未见病斑；病株集中在棚室南侧 3 畦

三、气象实况（weather · 2026-09-26 10:00，棚外自动站）
- 气温 27.3 °C，相对湿度 88%，风速 8.5 m/s（约 5 级，阵风更强）
- 未来 12 小时降水概率 60%；预报次日累计降水 18 mm

四、设备状态（device · 2026-09-26 10:00）
- 3 号植保机（喷洒机）：电量 18%，药箱空载；上次作业 09-23
- 背负式喷雾器 2 台：可用，无电量指示
"""

PERCEPTION_REPORT_PLAIN = """\
[感知报告 · 对象 NN-GH-01 · 基准 2026-09-26（以下均为观察事实）]

一、土壤墒情（sensor · 10cm 层，2026-09-20 ~ 2026-09-26）
- 09-20 21.9% → 09-23 20.4% → 09-26 10:00 17.6%（参考下限 20.0%）
- 30cm 层同期 22.4% → 22.1%，无同向变化
- 近 24 小时无灌溉记录

二、无人机影像（imagery · 2026-09-26 09:40 巡飞，可见光）
- 下部 1~3 层叶可见褐色、近圆形病斑，直径 3~8 mm，边缘黄化；估算病叶占下部叶面积约 18%
- 中上部叶与果实未见病斑；病株集中在棚室南侧 3 畦

三、现场作业条件
- 本次未采集：现场气象实况与机具可用状态均缺失。
"""

DIAGNOSIS_REPORT = """\
**结论:** 番茄早疫病（Alternaria solani）可能性高，下部老叶先发；根区水分亏缺（10cm 层
跌破参考下限）为并发因素。

**支持证据:**
- 病斑形态与部位（褐色近圆形、直径 3~8 mm、自下部老叶向上扩展、边缘黄化）与知识库
  卡片 EP-D-021「番茄早疫病」的田间识别特征一致；
- 棚内相对湿度 88%、气温 27.3 °C，处于该病流行适宜区间；
- 10cm 层墒情三日下行 21.9% → 17.6%，植株抗性下降。

**待确认:** 未做病斑镜检或培养，无法完全排除与靶斑病混发。

**建议处置:** 按知识库卡片 EP-D-021 的处置方案，喷施保护性杀菌剂（代森锰锌类）
并摘除病叶；施药须避开高温、大风与雨前时段（知识库通用要求）。
"""

DIAGNOSIS_PLAIN = """\
**结论:** 番茄早疫病（Alternaria solani）可能性高，下部老叶先发。

**支持证据:**
- 病斑形态与部位（褐色近圆形、直径 3~8 mm、自下部老叶向上扩展、边缘黄化）与知识库
  卡片 EP-D-021「番茄早疫病」的田间识别特征一致；
- 棚内高湿环境处于该病流行适宜区间。

**待确认:** 未做病斑镜检或培养，无法完全排除与靶斑病混发。

**建议处置:** 按知识库卡片 EP-D-021 的处置方案，喷施保护性杀菌剂（代森锰锌类）
并摘除病叶。
"""

RATIONALE = """\
田间识别特征与知识库卡片 EP-D-021 一致；未见果实症状、无镜检结果，故置信度记 medium。
水分亏缺与病害为两条并发链路，处置顺序待中枢决定。
"""


def build_state(graph, variant: str) -> dict:
    """构造中枢节点收到的状态（与真实链路同构，只是上游产出由本脚本给定）。"""
    site_context = (
        f"本次维护的棚室编号为 `{SITE_ID}`，请在所有工具调用与结论中沿用该编号。"
        " 已解析档案：名称：南宁示范棚 01；作物：番茄（品种：金棚 1 号）；规模：1.5 亩。"
        f" 该档案是今天的登记状态，未必与 {AS_OF} 当天一致。"
    )
    resource_context = (
        "可用资源账本（本轮由调用方提供）：\n"
        "- 人力：2 人（上午可调度；下午需支援其他棚室）\n"
        "- 机具：3 号植保机（可用，药箱 400 L）；背负式喷雾器 2 台\n"
        "- 农资：代森锰锌 80% WP 6 kg；苯醚甲环唑 10% WG 1.2 kg"
    )

    blocked = variant == "blocked"
    state = graph.propagator.create_initial_state(
        SITE_ID,
        AS_OF,
        site_type="greenhouse",
        site_context=site_context,
        resource_context=resource_context,
        past_context="",
    )
    state["perception_state"] = {
        **state["perception_state"],
        "perception_report": PERCEPTION_REPORT if blocked else PERCEPTION_REPORT_PLAIN,
        "anomaly_screen": ANOMALY_SCREEN,
        "anomaly_level": "medium",
        "has_anomaly": True,
        "perception_rounds": 1,
    }
    state["diagnosis_state"] = {
        **state["diagnosis_state"],
        "diagnosis_report": DIAGNOSIS_REPORT if blocked else DIAGNOSIS_PLAIN,
        "severity": "high",
        "confidence": "medium",
        "rationale": RATIONALE,
        "diagnosis_rounds": 1,
    }
    return state


def self_check(result: dict, variant: str) -> list[tuple[str, bool, str]]:
    """把期望写成可核对的条目，逐条给出实测结论。"""
    action = result["next_action"]
    text = "\n".join([
        result["decision_reason"],
        result["coordination_notes"],
    ])
    checks = [
        ("动作合法（四选一）",
         action in ("perceive_more", "diagnose_more", "plan_work_order", "close"), action),
        ("未跳过安全闸门（不得 close）", action != "close", action),
        ("处置预判为执行型而非观察/无需",
         result["disposition_hint"] in ("Immediate", "Soon", "Scheduled"), result["disposition_hint"]),
    ]
    if variant == "blocked":
        checks += [
            ("指出风速约束", any(word in text for word in ("风速", "风力", "8.5")), ""),
            ("指出机具电量约束", any(word in text for word in ("电量", "充电", "18%")), ""),
            ("给出作业窗口或前置条件",
             any(word in text for word in
                 ("窗口", "前置", "条件", "回落", "低于", "阈值", "避开", "时段", "择机", "等待")), ""),
        ]
    return checks


def render(result: dict, checks: list[tuple[str, bool, str]]) -> str:
    lines = [
        "## 中枢决策",
        "",
        f"- **下一步动作：** `{result['next_action']}`",
        f"- **处置预判：** {result['disposition_hint']}",
        "",
        "### 决策单原文",
        "",
        result["coordination_notes"],
        "",
        f"**依据：** {result['decision_reason']}",
        "",
        "## 自检表",
        "",
        "| 期望 | 实测 |",
        "| --- | --- |",
    ]
    for name, ok, extra in checks:
        cell = ("通过" if ok else "未通过") + (f"（{extra}）" if extra else "")
        lines.append(f"| {name} | {cell} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="协调决策中枢场景测试")
    parser.add_argument("--provider", default=None, help="LLM 供应商（stub / deepseek / openai ...）")
    parser.add_argument("--variant", choices=("blocked", "plain"), default="blocked",
                        help="blocked=含风速与电量约束；plain=对照，不给现场条件")
    parser.add_argument("--no-save", action="store_true", help="不写报告文件")
    args = parser.parse_args()

    from agriagents.agents.coordinator.coordinator import create_coordinator
    from agriagents.default_config import DEFAULT_CONFIG
    from agriagents.graph.agri_graph import AgriAgentsGraph

    config = DEFAULT_CONFIG.copy()
    if args.provider:
        config["llm_provider"] = args.provider
    # 单独测中枢：不需要审批与 checkpoint，也不写台账
    config["require_human_approval"] = False
    config["checkpoint_enabled"] = False

    graph = AgriAgentsGraph(config=config)
    state = build_state(graph, args.variant)

    node = create_coordinator(graph.deep_thinking_llm)
    started = time.time()
    node_result = node(state)
    elapsed = time.time() - started

    coordination_state = node_result["coordination_state"]
    decision = {
        "next_action": coordination_state["next_action"],
        "disposition_hint": _disposition_hint(coordination_state["coordination_notes"]),
        "decision_reason": coordination_state["decision_reason"],
        "coordination_notes": coordination_state["coordination_notes"],
    }
    checks = self_check(decision, args.variant)

    header = (
        f"场景：南宁大棚番茄 · {AS_OF} 10:00 · 变体 {args.variant} · "
        f"模型 {config['llm_provider']}/{config['deep_think_llm']} · "
        f"耗时 {elapsed:.1f}s"
    )
    print(header)
    print("=" * len(header))
    print()
    print(render(decision, checks))

    if not args.no_save:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(config["results_dir"], "scenario_coordinator")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{args.variant}_{stamp}.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"# {header}\n\n{render(decision, checks)}\n")
        print(f"\n报告已写出：{path}")

    failed = [name for name, ok, _ in checks if not ok]
    return 1 if failed else 0


def _disposition_hint(notes: str) -> str:
    """从决策单渲染文本里取处置预判，归一成规范等级（Immediate / Soon / ...）。"""
    from agriagents.agents.rating import extract_disposition

    return extract_disposition(notes) or "未给出"


if __name__ == "__main__":
    raise SystemExit(main())
