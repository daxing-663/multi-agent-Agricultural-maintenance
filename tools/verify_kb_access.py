"""验证智能体能否真的检索到本地知识库。

跑一次完整的维护决策流程，把 ``route_to_vendor`` 插桩，
记录四个 Agent 到底调了哪些数据工具、命中的是哪家供应商、
拿回来的内容是真实知识还是占位文案。

用法：

    python tools/verify_kb_access.py              # 用配置里的真实模型
    python tools/verify_kb_access.py --stub       # 离线桩模型，不花钱

判定标准（脚本会自己给出结论）：

- 知识类工具（query_* / get_treatment_options）至少被调用一次；
- 这些调用命中的供应商是 ``local_rag`` 而不是 ``stub_knowledge``；
- 返回内容里不含"未接入真实数据源"。

只要这三条成立，就说明"智能体能找到对应的知识库"这件事是通的。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认 GBK，项目文本里有大量非 GBK 字符，先兜住编码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

KNOWLEDGE_TOOLS = {
    "query_agronomy_knowledge",
    "query_pest_disease_library",
    "query_soil_reference",
    "query_equipment_manual",
    "get_treatment_options",
}

CALLS: list[dict] = []


def instrument() -> None:
    """给路由层加一层记录。"""
    from agriagents.dataflows import router

    original = router.route_to_vendor

    def traced(method: str, *args, **kwargs):
        started = time.time()
        try:
            output = original(method, *args, **kwargs)
            error = ""
        except Exception as exc:  # noqa: BLE001 - 这里就是要连失败一起记下来
            output = ""
            error = repr(exc)
            raise
        finally:
            CALLS.append({
                "tool": method,
                "args": [str(a)[:60] for a in args],
                "seconds": round(time.time() - started, 2),
                "output": output,
                "error": error,
                "vendor": _vendor_of(output, method),
            })
        return output

    router.route_to_vendor = traced
    # 工具层是 `from ... import route_to_vendor` 绑定的名字，必须一起换
    from agriagents.agents import tools as tool_module

    tool_module.route_to_vendor = traced


def _vendor_of(output: str, method: str) -> str:
    prefix = output.lstrip()[:40]
    if prefix.startswith("[local_rag]"):
        return "local_rag"
    if prefix.startswith("[stub_"):
        return "stub"
    if "未接入真实数据源" in output:
        return "stub"
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="GH-03", help="维护对象编号")
    parser.add_argument("--type", default="greenhouse", help="field / greenhouse / orchard / machine")
    parser.add_argument("--date", default="2026-09-26")
    parser.add_argument("--stub", action="store_true", help="用离线桩模型（不调用真实 AI）")
    args = parser.parse_args()

    from agriagents.default_config import DEFAULT_CONFIG
    from agriagents.graph.agri_graph import AgriAgentsGraph

    config = DEFAULT_CONFIG.copy()
    config.update({
        "require_human_approval": False,
        "dry_run": True,
        "debug": True,
        "llm_provider": "stub" if args.stub else config["llm_provider"],
    })

    instrument()

    print("=" * 70)
    print(f"运行一次完整维护决策：{args.site} / {args.type} / {args.date}")
    print(f"模型：{'stub（离线桩）' if args.stub else config['deep_think_llm'] + ' + ' + config['quick_think_llm']}")
    print("=" * 70)

    graph = AgriAgentsGraph(config=config)
    started = time.time()
    state, disposition = graph.propagate(args.site, args.date, site_type=args.type)
    elapsed = time.time() - started

    print("\n" + "=" * 70)
    print(f"运行结束，用时 {elapsed:.0f}s，处置等级 = {disposition}")
    print("=" * 70)

    print("\n[1] 数据工具调用记录")
    if not CALLS:
        print("  （一次工具都没调）")
    for i, call in enumerate(CALLS, 1):
        flag = "*" if call["tool"] in KNOWLEDGE_TOOLS else " "
        print(f" {flag}[{i:02d}] {call['tool']}({', '.join(call['args'])})")
        print(f"       供应商={call['vendor']}  耗时={call['seconds']}s  返回{len(call['output'])}字")
        if call["error"]:
            print(f"       [ERR] {call['error'][:120]}")

    kb_calls = [c for c in CALLS if c["tool"] in KNOWLEDGE_TOOLS]
    kb_live = [c for c in kb_calls if c["vendor"] == "local_rag"]

    print("\n[2] 知识库检索判定")
    print(f"  知识类工具调用次数：{len(kb_calls)}")
    print(f"  实际命中 local_rag：{len(kb_live)}")
    if kb_live:
        sample = kb_live[0]
        print(f"\n  样例（{sample['tool']}）返回内容前 400 字：")
        for line in sample["output"][:400].splitlines():
            print("    " + line)

    ok = bool(kb_live)
    print("\n" + ("[OK] 结论：智能体成功检索到本地知识库" if ok else
                  "[FAIL] 结论：本次运行没有命中本地知识库（见上方调用记录）"))
    if not kb_calls:
        print("  提示：本次没有走到诊断环节。可能是感知层拿不到数据后提前收口——")
        print("        换成 --site GH-03 --type greenhouse，或先把某路数据源接真实值。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
