# AgriAgents —— 多智能体协同农业维护框架（骨架）

> **架构提取自 [TradingAgents](https://github.com/TauricResearch/TradingAgents)**，
> 保留其分层方式与可复用设计模式；**系统提示词、技能包（SKILL.md）与工具层均已落地（2026-09-26）**，
> 真实数据源与农艺规则仍留空（标为 `TODO(内容)`）。
> 当前状态：已接入五源本地知识库及合成演示数据；真实设备与生产规则仍需单独验证。

---

## 一、这套框架解决什么问题

给定「**一个维护对象 + 一个基准日期**」：

```python
graph = AgriAgentsGraph()
state, disposition = graph.propagate("FIELD-07", "2026-09-26")
```

产出一份《维护决策单》+ 全流程留痕（感知 → 诊断 → 协调决策 → 安全检查 → 人工审批 → 执行 → 反馈回填 → 复盘）。

**四个 Agent 的分工**（协调决策 Agent 是唯一决策者，另外三个是它的执行方）：

| Agent | 职责 | 对应代码 |
| --- | --- | --- |
| 感知 Agent | 采集传感器、摄像头、无人机、设备状态；做异常初筛 | `agriagents/agents/perception/` |
| 诊断 Agent | 判断病虫害、土壤问题、设备故障；给严重度和建议 | `agriagents/agents/diagnosis/` |
| **协调决策 Agent** | **任务分解、资源分配、生成维护工单、安全检查、请求人工审批、失败回滚** | `agriagents/agents/coordinator/` |
| 执行 Agent | 控制灌溉、喷洒、农机、阀门、机械臂；执行后反馈 | `agriagents/agents/execution/` |

---

## 二、架构映射：TradingAgents → AgriAgents

提取时保留了**结构**，替换了**语义**：

| TradingAgents | AgriAgents | 保留下来的机制 |
| --- | --- | --- |
| 分析师团队（技术/情绪/新闻/基本面） | 感知 Agent（传感器/影像/设备/气象） | 带工具的分析节点 + 工具节点回环 + 清空消息 |
| 多空研究员辩论 | —（本框架不需要对抗辩论） | — |
| Research Manager（辩论裁决） | **协调决策中枢**（分派：补采集/补诊断/出工单/收口） | 深思考模型做判断 + 结构化输出 |
| Trader（交易提案） | 工单规划 | 把"方向"翻译成"可执行动作" |
| 风控三方辩论 | 安全检查（规则判定 pass/revise/block） | 执行前的独立复核环节 |
| Portfolio Manager（终审） | 人工审批门 + 决策单组装 | 执行前的最后一道闸门 + 唯一交付物 |
| 决策日志 / 反思 / 结算 | 维护台账 / 复盘 / 现场回填结算 | 记忆闭环：写入 → 回填 → 复盘 → 注入下一轮 |
| 5 档评级（Buy…Sell） | 5 档处置等级（立即处置…无需处置） | 统一的机器可读词汇 + 确定性解析器 |
| 双模型（quick / deep） | 同左 | 高频节点用快模型，判断节点用深思考模型 |
| LangGraph checkpoint 续跑 | 同左（+ 人工审批中断依赖它） | 按案件分库的 SqliteSaver |

**没有搬过来的**：多空辩论。农业维护的分歧不是"看涨看跌"这种观点之争，
而是"证据是否充分"的判定，用中枢分派 + 规则判定更贴合，也更容易审计。

---

## 三、五层架构与目录

```
AgriAgents/
├── cli/                       入口层：Typer 命令行（解析参数 + 展示）
├── agriagents/
│   ├── default_config.py      配置中心：唯一事实来源 + AGRIAGENTS_* 环境覆盖
│   ├── llm.py                 LLM 工厂 + 离线桩模型
│   ├── reporting.py           报告树落盘（CLI 与接口共用）
│   ├── graph/                 编排层 ★
│   │   ├── execution_plan.py     节点清单（单一事实来源）
│   │   ├── setup.py              图装配（节点 + 边）
│   │   ├── conditional_logic.py  全部路由判断（可单测）
│   │   ├── propagation.py        初始状态装配
│   │   ├── checkpointer.py       断点续跑 / 审批中断
│   │   └── agri_graph.py         门面类 AgriAgentsGraph
│   ├── agents/                智能体层 ★
│   │   ├── state.py              状态契约（顶层 + 4 个子状态）
│   │   ├── schemas.py            结构化输出 schema + 渲染
│   │   ├── rating.py             处置等级词表 + 解析器
│   │   ├── context.py            上下文注入 / 缺席显式化 / 消息清理
│   │   ├── structured.py         结构化输出 + 优雅回退
│   │   ├── tools.py              工具声明（14 个；默认走内置合成演示源）
│   │   ├── perception/           感知组：采集 + 异常初筛
│   │   ├── diagnosis/            诊断组：诊断 + 严重度评估
│   │   ├── coordinator/          协调决策组：中枢/工单/安检/审批/回滚/收口
│   │   └── execution/            执行组：下发 + 反馈
│   ├── dataflows/             数据层 ★
│   │   ├── config.py             run 作用域配置（ContextVar）
│   │   ├── router.py             工具 → 类别 → 供应商链
│   │   ├── errors.py             错误分类（按行为分，不按供应商分）
│   │   ├── time_window.py        as-of 防未来信息
│   │   ├── symbols.py            路径安全
│   │   └── vendors/              synthetic（合成演示源）+ 6 个占位适配器
│   ├── skills/                技能包 ★
│   │   ├── perception/SKILL.md    感知作业手册（取数配方 / 对比口径 / 数据质量红旗）
│   │   ├── diagnosis/SKILL.md     诊断作业手册（假设清单 / 置信度口径 / 转人工条件）
│   │   ├── coordinator/SKILL.md   中枢作业手册（动作速查表 / 请求模板 / 预算纪律）
│   │   ├── execution/SKILL.md     执行作业手册（核对清单 / 指令配方 / 停止条件）
│   │   └── __init__.py            加载器（按 mtime 缓存，摘要进 checkpoint 签名）
│   └── memory/                记忆层 ★
│       ├── decision_log.py       追加式台账 + 历史教训检索
│       ├── settlement.py         现场回填结算
│       └── reflection.py         2-4 句复盘
└── tests/                     离线回归：框架、提示词、工具、知识库、构建与检索精度
```

---

## 四、图拓扑

```
                    ┌──────────────┐
   START ──────────▶│ 感知 Agent   │◀──── 中枢要求补采集
                    └──────┬───────┘
                    ┌──────▼───────┐
                    │ 感知工具     │（传感器/气象/影像/设备状态）
                    └──────┬───────┘
                    ┌──────▼───────┐
                    │ 异常初筛     │ 结构化：none/low/medium/high
                    └──────┬───────┘
              无异常且允许自动关单 │ 有异常
                    ┌──────────┴───────────┐
                    ▼                      ▼
              ┌──────────┐          ┌──────────────┐
              │ 收口关单 │          │ 诊断 Agent   │◀─── 中枢要求补诊断
              └────┬─────┘          └──────┬───────┘
                   │                ┌──────▼───────┐
                   │                │ 诊断工具     │（病虫害库/土壤/手册/方案）
                   │                └──────┬───────┘
                   │                ┌──────▼───────┐
                   │                │ 严重度评估   │
                   │                └──────┬───────┘
                   │                       ▼
                   │            ┌─────────────────────┐
                   │            │  协调决策中枢 ★     │◀──────┐
                   │            │  (深思考模型)       │       │
                   │            └──────────┬──────────┘       │
                   │        ┌──────────────┼──────────────┐   │
                   │        ▼              ▼              ▼   │
                   │   补采集/补诊断    出工单          收口  │
                   │                       ▼                 │
                   │              ┌────────────────┐         │
                   │              │ 工单规划       │◀─┐      │
                   │              └───────┬────────┘  │revise│
                   │              ┌───────▼────────┐  │      │
                   │              │ 安全检查       │──┘      │
                   │              └───────┬────────┘         │
                   │                 pass │  block           │
                   │              ┌───────▼────────┐         │
                   │              │ 人工审批门     │─────────┘ 驳回
                   │              └───────┬────────┘
                   │                 通过 │
                   │              ┌───────▼────────┐
                   │              │ 执行 Agent     │◀─┐
                   │              └───────┬────────┘  │
                   │              ┌───────▼────────┐  │
                   │              │ 执行工具       │──┘（灌溉/喷洒/农机/阀门/机械臂）
                   │              └───────┬────────┘
                   │              ┌───────▼────────┐
                   │              │ 执行反馈       │
                   │              └───────┬────────┘
                   │              失败 ┌───┴───┐ 成功
                   │                  ▼       │
                   │           ┌──────────┐   │
                   └──────────▶│ 回滚预案 │───┤（交回中枢重规划）
                               └──────────┘   ▼
                                        ┌────────────┐
                                        │ 最终决策单 │──▶ END
                                        └────────────┘
```

---

## 五、状态契约

顶层 `AgriState(MessagesState)` 承载案件上下文与四个子状态：

```python
AgriState
├── site_of_interest / site_type / as_of      案件标识（run 开始后只读）
├── site_context                              确定性解析的对象档案（锚定所有 Agent）
├── resource_context                          人力/机具/农资；未提供时写明未知
├── past_context                              历史决策与复盘教训
├── perception_state   { perception_report, anomaly_screen, anomaly_level,
│                        has_anomaly, perception_rounds, pending_requests }
├── diagnosis_state    { diagnosis_report, severity, confidence, rationale,
│                        recommended_checks, diagnosis_history,
│                        diagnosis_rounds, pending_request }
├── coordination_state { next_action, decision_reason, coordination_notes,
│                        work_order, safety_review, safety_verdict, approval,
│                        approved, rollback_plan, coordination_history,
│                        coordinator_rounds, safety_rounds, rollback_rounds }
├── execution_state    { dispatch_log, execution_status, execution_report,
│                        needs_followup }
└── final_decision / case_status
```

> ⚠️ **最容易踩的坑**：嵌套 TypedDict 在 LangGraph 里**没有 reducer**。
> 节点更新子状态时必须返回**完整字典**（本框架统一用 `{**old, "key": value}`），
> 只返回部分键会把其余键整体覆盖成缺失。

---

## 六、从 TradingAgents 提取的 11 个可复用模式

| # | 模式 | 为什么重要 | 本框架位置 |
| --- | --- | --- | --- |
| 1 | **角色工厂** `create_xxx(llm) -> node_fn` | 每个 Agent 可单独测试，不在使用中的角色不必实例化 | `agents/*/*.py` |
| 2 | **双模型分级** | 判断类节点用深思考模型，高频节点用快模型，成本与质量兼顾 | `execution_plan.NODE_SPECS.model` |
| 3 | **结构化输出 + 优雅回退** | 弱模型/供应商抖动不会中断流水线 | `agents/structured.py` |
| 4 | **回退语义必须保守** | 解析失败 → `unknown` → **按需要跟进处理**；绝不能让一次解析失败变成一次「正常」判定 | `anomaly_screen.py` / `safety_review.py` |
| 5 | **路由集中 + 路径表写全** | 条件边返回值未登记会让 LangGraph 在运行时崩，且崩溃点离原因很远 | `conditional_logic.py` + `setup.py` 的 `*_PATH_MAP` |
| 6 | **轮次预算三重闸门** | 提示词里的上限模型可以无视，路由不行，递归上限是最后保险丝 | `conditional_logic.py` + config |
| 7 | **数据源供应商路由** | 换数据源＝改配置，不动 Agent 代码 | `dataflows/router.py` |
| 8 | **错误按行为分类** | 类型数量 = 路由层不同的处理方式数量 | `dataflows/errors.py` |
| 9 | **as-of 防未来信息** | 回溯/复盘的结论不能被"事后才知道的信息"污染 | `dataflows/time_window.py` |
| 10 | **缺席显式化 / 未知即未知** | 「没产生报告」≠「结论为空」；「没给库存」≠「库存为零」 | `agents/context.py` |
| 11 | **记忆闭环** | 台账 → 现场回填 → 复盘 → 注入下一轮，系统才会越跑越准 | `memory/` |

另外两个工程习惯也一并保留：**checkpoint 的 thread 签名**（图形态变了不能误续）
与**交付物解析器**（`rating.py`：解析不出就标 `REVIEW`，不默认成某一档）。

---

## 七、农业场景特有的三处改造

**1. 执行会动物理世界 → 三层安全设计**

```
第一层（图）：  只有审批门的 approved 分支能进入执行节点
第二层（节点）：执行 Agent 内再校验一次 approved（图可以被重新接线，防呆不能只靠上游）
第三层（数据）：dry_run 时只留痕不下发；安全规则取不到时报错中断，绝不降级放行
```

配置默认就是**最保守**的：`require_human_approval=True` + `dry_run=True`。
要真正下发到设备，必须显式同时关掉两个开关，且会在台账里留下说明。

**2. 结算靠现场回填，不是固定持有期**

交易的结算是"等 N 个交易日读行情"，农业的结算是"作业做没做、复检指标如何"。
因此 `settle_pending(site, outcome_lookup=...)` 把结果来源做成**可注入**的：

```python
graph.settle_pending("FIELD-07", outcome_lookup=lambda site, as_of: {
    "resolved_date": "2026-10-03",
    "status": "success",
    "outcome": "作业按工单完成；7 天后复检病斑停止扩展，新叶正常。",
})
```

**拿不到结果就不结算**——用"大概没问题"补一条结果，会让整本台账的教训变成噪音，比不记更糟。

**3. 五档词汇从「评级」换成「处置等级」**

`立即处置 / 尽快处置 / 计划处置 / 观察 / 无需处置`（`Immediate…NoAction`），
首行 `**处置等级：** X` 是机器可读行，台账与报告都靠它定位结论。

---

## 八、怎么跑

```powershell
cd C:\Users\colorful\Desktop\多agent项目\AgriAgents

# 1) 依赖（虚拟环境已建好；若镜像源不通可加 --index-url https://pypi.org/simple）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2) 跑示例（显式选择离线桩模型，不需要 API key）
$env:AGRIAGENTS_LLM_PROVIDER = "stub"
.\.venv\Scripts\python.exe main.py

# 3) 框架自检
.\.venv\Scripts\python.exe -m pytest tests -q

# 4) 命令行
.\.venv\Scripts\python.exe -m cli.main --site FIELD-07 --type field --date 2026-09-26
.\.venv\Scripts\python.exe -m cli.main graph-nodes      # 打印节点清单
```

> Windows 控制台默认是 GBK 代码页，中文可能显示成乱码（**文件本身是 UTF-8，没有问题**）。
> 想看清控制台输出，先执行 `chcp 65001` 或设 `$env:PYTHONIOENCODING = "utf-8"`。

### 接入真实模型（当前已接 DeepSeek）

密钥只放在项目根目录的 `.env`（已被 `.gitignore` 忽略），**不要写进代码**：

```ini
AGRIAGENTS_LLM_PROVIDER=deepseek
AGRIAGENTS_DEEP_THINK_LLM=deepseek-v4-pro     # 协调决策 / 工单规划 / 回滚预案
AGRIAGENTS_QUICK_THINK_LLM=deepseek-flash     # 感知 / 诊断 / 执行 / 初筛 / 安检
DEEPSEEK_API_KEY=sk-...
```

四个 Agent 用的是同一个 AI（同一个 key），只是按角色分给了两个档位。
**想让四个 Agent 完全用同一个模型**，把两个键设成同一个模型名即可：

```ini
AGRIAGENTS_DEEP_THINK_LLM=deepseek-v4-pro
AGRIAGENTS_QUICK_THINK_LLM=deepseek-v4-pro
```

换 OpenAI 或本地端点：

```ini
AGRIAGENTS_LLM_PROVIDER=openai
AGRIAGENTS_DEEP_THINK_LLM=gpt-5.6-sol
AGRIAGENTS_QUICK_THINK_LLM=gpt-5.6-luna
OPENAI_API_KEY=sk-...
```

`openai_compatible` + `AGRIAGENTS_LLM_BACKEND_URL` 可接 vLLM / LM Studio / Ollama。

### DeepSeek 的三个实测约束（2026-09，v4-pro / flash）

接线时踩过，写在这里省得再踩一次：

| 现象 | 原因 | 框架的处理 |
| --- | --- | --- |
| 结构化输出报 400 `This response_format type is unavailable` | 不支持 `response_format=json_schema` | 结构化固定走 `function_calling` |
| 结构化输出报 400 `Thinking mode does not support this tool_choice` | thinking 模式与**强制 tool_choice** 互斥 | 结构化那一次调用强制关掉 thinking |
| 工具型 Agent 报 400 `The reasoning_content ... must be passed back` | thinking 模式要求回传上一轮的 `reasoning_content`，而 `langchain-openai` 明确不解析也不保留该字段 | **默认全局关闭 thinking** |

`json_mode` 虽然可用，但模型会把枚举取值翻成中文（`"medium"` → `"中"`），校验必失败，所以没走这条路。
开关在 `AGRIAGENTS_DEEPSEEK_THINKING`，默认 `false`——想打开必须先解决 `reasoning_content` 回传（框架外的工作）。

### 首次真实运行的观察

用 DeepSeek 跑通一次完整流程（FIELD-07，2026-09-26，约 150 秒 / 约 20 次模型调用）时，
模型发现**所有数据源都返回「未接入」的占位文本**，于是：

- 把初筛等级标为 `medium` 并在「待确认」里逐条说明缺哪些通道；
- 中枢在第 4 轮（触发轮次硬闸门）选择 `close`，明确写出"无法判断真实情况时应先接数据源，而不是凭猜测作业"；
- 最终决策单给出「无需处置」并注明需接入真实数据后重跑。

这正是框架想要的保守行为：**没有证据就不下结论、不发明作业方案**。
也说明要看到完整的"工单 → 安检 → 审批 → 执行"路径，必须接上真实数据源（或先用合成数据源练手）。

### 人工审批（中断 → 签字 → 续跑）

审批门用的是 LangGraph 的 `interrupt()`，**必须开启 checkpoint**：

```python
from langgraph.types import Command

config = DEFAULT_CONFIG.copy()
config["require_human_approval"] = True
config["checkpoint_enabled"] = True

graph = AgriAgentsGraph(config=config)
tid = graph.begin_checkpoint("GH-09", "2026-09-26")
try:
    init = graph.create_run_state("GH-09", "2026-09-26", "greenhouse")
    for chunk in graph.graph.stream(init, config={"configurable": {"thread_id": tid}}):
        last = chunk
    print(last["__interrupt__"])          # 审批载荷：工单 + 安全检查 + 待批问题
finally:
    graph.end_checkpoint()

# 人工签字后继续（可换一个进程、甚至换一台机器）
tid = graph.begin_checkpoint("GH-09", "2026-09-26")
try:
    result = graph.graph.invoke(
        Command(resume={"approved": True, "approver": "张工", "note": "注意安全间隔期"}),
        config={"configurable": {"thread_id": tid}},
    )
    print(result["case_status"], result["coordination_state"]["approval"])
finally:
    graph.end_checkpoint()
```

> 已实测：阶段一停在审批门并抛出完整工单载荷，阶段二带签字结果续跑至执行完成。

---

## 九、RAG 本地知识库

知识库已按番茄、黄瓜设施农业场景补齐：六源共 **51,124 条知识文档**，包含 CropDP 图谱、PlantInquiry 卡片、中英文问答及明确标识的演示地块/设备资料。支持自有 JSONL 文档导入。

诊断 Agent 可先用 `list_knowledge_bases()` 查看实际加载覆盖，再用八个知识工具进行定向查证。通用检索默认只返回短命中片段，只有片段不足以鉴别时才用 `get_knowledge_document(doc_id)` 精确读取一篇全文。处置方案按病害和作物绑定，土壤按地块编号、设备按型号/编号和故障码精确匹配；未知对象不借用邻近记录。每个结果保留来源与 doc_id。

```powershell
.\.venv\Scripts\python.exe tools/build_rag_index.py --all --probe
.\.venv\Scripts\python.exe tools/verify_kb_access.py --output-dir reports/kb/bm25
.\.venv\Scripts\python.exe tools/verify_kb_access.py --dense --output-dir reports/kb/dense
```

完整语料清单、演示档案、构建命令、失败恢复、自有文档格式和检索精度验收见 [知识库使用文档](docs/knowledge_base.md)。

---

## 十、接下来要填的内容（按优先级）

| 优先级 | 要做的事 | 改哪里 |
| --- | --- | --- |
| ✅ | ~~写四个 Agent 的系统提示词~~ 已完成（2026-09-26） | 见下方「已完成：四个 Agent 的系统提示词」 |
| ✅ | ~~让工具返回可读数据~~ 内置合成演示源（2026-09-26） | `dataflows/vendors/synthetic.py`；见下方「已完成：工具层」 |
| P0 | 接 1~2 个**真实**数据源（先接传感器或气象） | `dataflows/vendors/` 新增适配器 + `router.VENDOR_METHODS` 注册 + 改配置 |
| P0 | 扩充知识库（土壤档案、设备手册还是模板） | 加语料源：`agriagents/rag/sources/` 新增一个文件 + 在 `SOURCES` 注册一行 |
| P0 | 接入真实 LLM | 环境变量即可，`llm.py` 已有 openai / openai_compatible |
| P1 | 现场回填来源（作业记录 / 复检数据） | `memory/settlement.py::default_outcome_lookup` |
| P1 | 对象档案台账（作物、品种、播期、面积） | `agri_graph.resolve_site_context` 的 `identity` |
| P1 | 安全规则库（禁限用清单、安全间隔期） | `vendors/ops.py::check_safety_policy`（必须带版本号） |
| P2 | 执行网关（灌溉/喷洒/农机/阀门/机械臂） | `vendors/actuators.py`；先保持 `dry_run=True` |
| P2 | 更多 LLM 供应商（anthropic / google / qwen…） | 参考 TradingAgents 的 `llm_clients/` 每个供应商一个薄适配器 |
| P2 | 团队选择（按需启用感知/诊断） | `graph/setup.py`，参考 TradingAgents 的 `analyst_execution` |


### 已完成：四个 Agent 的系统提示词（2026-09-26）

四份提示词分别放在各自 Agent 文件里（`SYSTEM_PROMPT` 常量 + `build_system_prompt()` 注入本轮上下文），
随业务迭代，不需要动图与状态：

| Agent | 文件 | 核心约束 |
| --- | --- | --- |
| 感知 | `agents/perception/perception_agent.py` | 只报告观察事实；缺测即缺失；对比口径（阈值 > 参考基线 > 基线未知） |
| 诊断 | `agents/diagnosis/diagnosis_agent.py` | 假设 → 排除 → 收敛；三类病因显式处理；方案只能引用知识库，查不到转人工 |
| 协调决策 | `agents/coordinator/coordinator.py` | 四选一动作 + 安全优先 + 证据门槛 + 轮次预算纪律；不改写诊断结论 |
| 执行 | `agents/execution/executor.py` | 三条硬性前提；严格按单执行；参数缺失不猜；下发后回读；如实留痕 |

接线的同时修掉两处「只算不用」的缺陷：感知报告此前没有注入诊断提示词、工单正文没有注入
执行提示词（节点之间的消息会被清空，不注入＝模型根本看不到）。
`tests/test_prompts.py` 用录制型 LLM 盯住这几份提示词与上游证据的注入，共 9 项断言。

真实 DeepSeek（deepseek-v4-pro + deepseek-flash）跑过两次完整流程（2026-09-26，各约 2 分钟）：
模型在数据源全为占位桩时**拒绝编造结论**——诊断把「观测系统自身异常」单列为唯一有证据的
假设，中枢三轮 `perceive_more` 后按预算纪律转 `plan_work_order`，安全检查给出 `revise`，
执行 Agent 逐条核对硬性前提、如实记录未下发。第二次运行确认了演练（dry_run）语义：
演练放行可以走完下发-回读链路（只留痕、不下发物理动作），报告中明确标注「演练，未实际下发」。

第三次运行（接入工具层与技能包后）已经能走完完整的农艺链路：感知读到 8 天墒情/湿度/叶面湿润时长/
影像/设备状态序列 → 诊断命中知识库的「番茄晚疫病」并把 VALVE-11 的 E04 与风机离线列为并发因素 →
中枢连续两轮 `diagnose_more` 要求补检「叶背霉层颜色」→ 预算耗尽后转 `plan_work_order` →
安全检查给出 `revise`（逐条列出防护、泄压、用药二次审查等修正项）。
这次运行还暴露了一个收口语义缺陷：被预算兜底收口时，决策单会把中枢的处置预判渲染成
「立即处置」——一份从未执行的单子看起来像已定案。已改为强制收口一律产出 `REVIEW（待人工复核）`，
由 `tests/test_finalize.py` 守着。

### 已完成：工具层（内置合成演示源，2026-09-26）

`agriagents/dataflows/vendors/synthetic.py` 让 14 个工具第一次真正返回可读数据，
不需要任何外部账号即可把全流程跑通：

- **确定性**：读数由 `(对象, 通道, 日期)` 经 blake2s 派生，同一案件重跑结果完全一致，
  复盘与回归测试才有意义；
- **自曝身份**：每条返回都带 `[synthetic_demo]` 与"合成演示数据"字样，不冒充实测值；
- **演示场景**：墒情 22.1%→16.4% 持续失墒、湿度 80.8%→89.5%、叶面湿润 4.5→6.7 h/日、
  影像 15% 叶面积水渍状斑点、VALVE-11 报 E04、FAN-03 离线、次日 18 mm 降水、
  资源账本（2 人 / 3 号植保机 / 生物农药 X 12 L）；
- **安全规则带版本**：`synthetic-demo-rules v0.1`，会话含禁限用物资时明确返回"存在禁止性冲突"，
  裁决可指回规则版本；
- **执行网关写本地台账**：`dry_run` 只留痕；实发模式写入 `<cache>/actuator_ledger/<对象>.jsonl`
  并支持回读，绝不连接物理设备；
- **占位适配器不再静默降级**：`check_safety_policy` 的占位实现改为抛 `NoDataError`——
  安全规则取不到时必须中断，而不是返回一段"看起来审过了"的文本。

### 已完成：四个 Agent 的技能包（SKILL.md，2026-09-26）

每个 Agent 除了角色提示词，还带一份**作业手册**：提示词回答"你是谁、边界在哪"，
技能包回答"具体怎么做"（取数配方、请求模板、停止条件、常见坑）。

| 技能包 | 文件 | 内容要点 |
| --- | --- | --- |
| perception | `agriagents/skills/perception/SKILL.md` | 八个通道的取数配方、对比口径优先级、数据质量红旗、报告自检清单 |
| diagnosis | `agriagents/skills/diagnosis/SKILL.md` | 三类假设清单与工具配方、置信度口径、转人工条件、反例 |
| coordinator | `agriagents/skills/coordinator/SKILL.md` | 动作速查表、补采/补诊断请求模板、预算纪律、接口约定 |
| execution | `agriagents/skills/execution/SKILL.md` | 执行前核对清单、五类机构指令配方、回读留痕、立即停止条件 |

加载器 `agriagents/skills/__init__.py` 按文件 mtime + 大小缓存（改完立刻生效），
技能包摘要参与 checkpoint 签名——手册改了就不会复用到旧断点。


---

## 十一、自检与边界

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
# 覆盖框架流程、提示词证据注入、工具接线、技能加载与收口逻辑；
# RAG覆盖七个知识工具、作物/地块/设备精确匹配、中英术语、拒答负例、
# 多源构建、下载完整性、失败保留旧库、缓存刷新与向量身份一致性。
# 全库自然问句及来源覆盖验收见 docs/knowledge_base.md。
```

**明确不做的事**（框架层面的边界，避免被误用）：

- 不做"无人值守自动作业"：默认必须人工审批 + 干跑，物理执行永远有人的环节；
- 不发明农艺方案：处置方案只能引用知识库给出的内容，Agent 不得自行编造药剂与用量；
- 不猜结果：没有回填就不结算，没有证据就不下诊断结论；
- 不静默降级：安全规则取不到 → 中断整个 run；决策解析不出 → 记 `REVIEW` 而不是默认某一档。

---

## 十二、免责声明

本项目是**框架骨架 + 研究工具**，不构成农艺、植保、设备操作或投资建议。
把任何执行类工具接到真实设备之前，务必保持 `dry_run=True` 并完成现场安全评估。
