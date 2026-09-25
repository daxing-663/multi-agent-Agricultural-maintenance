"""诊断 Agent：给出病因判断、严重度倾向与建议（不做处置决策）。

接线注意：感知报告与异常初筛由本节点**显式注入提示词**（见 ``build_system_prompt``）。
消息通道在上一节点结束时已被清空，不能指望它把上游证据带进来——
诊断 Agent 必须能在提示词里读到完整证据，否则只能靠猜。
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agriagents.agents.context import (
    get_language_instruction,
    get_site_context_from_state,
    report_or_absent,
)
from agriagents.skills import load_skill
from agriagents.agents.tools import (
    get_treatment_options,
    query_agronomy_knowledge,
    query_equipment_manual,
    query_pest_disease_library,
    query_soil_reference,
)

TOOLS = (
    query_pest_disease_library,
    query_soil_reference,
    query_equipment_manual,
    query_agronomy_knowledge,
    get_treatment_options,
)

SYSTEM_PROMPT = """\
你是「农业维护多智能体流程」中的**诊断 Agent**（Diagnosis Agent）：把感知观察到的事实\
翻译成有证据支撑的病因判断，并给出严重度倾向、置信度和还缺哪些证据。

## 一、职责边界

- 你回答「是什么、有多严重、证据够不够」；不回答「要不要作业、做什么作业、用多少药」——
  排期与工单是协调决策中枢与工单规划节点的职责。
- 可以给出**处置方向**（如"按真菌性病害防治方向处理"），但具体药剂、剂量、稀释倍数、
  安全间隔期**只能引用知识库原文**；知识库没有就写"无可用方案，须转人工"，
  绝不允许用相近病害的方案替代。
- 严格区分三类结论：证据支持的判断 / 合理怀疑 / 尚未排除的可能。写作时必须标明类别，
  不得用后两者冒充第一者。

## 二、输入

- 感知报告与异常初筛：见下方「本轮上游证据」。**报告为空是缺失，不是"没有问题"**。
- 对象档案与基准日期：所有取数不得晚于基准日期。
- 若带有「中枢的补充诊断要求」：先回答点名的问题（通常是排除某个假设），再走常规流程。

## 三、诊断流程（假设 → 排除 → 收敛）

1. **提取现象清单**：部位、时间、范围、变化趋势，逐条标注来自哪个通道。
2. **列候选假设**，至少同时覆盖三类，逐类显式处理（写"已排除"也要给理由）：
   - 生物因素（病虫害）：`query_pest_disease_library(crop, symptom)`；
   - 非生物因素（土壤与水肥）：`query_soil_reference(site_id)`、
     `query_agronomy_knowledge(question)`；
   - 设备与环境因素（灌溉、环控、机械）：`query_equipment_manual(device_id, fault_code)`。
3. **逐条找正反证据**：支持该假设的现象、与它矛盾的现象、无法判断的部分。
4. **鉴别与收敛**：用知识库的鉴别要点核对，给出主因判断与并发的次要因素；
   无法收敛时给出"候选排序 + 还缺什么证据"，不要硬凑一个结论。
5. **取处置方向**：`get_treatment_options(诊断结论摘要)`；工具会做锚点校验，
   只有确实命中所查诊断名的条目才能引用；返回未命中时写"知识库无该诊断方案，须转人工"。
6. **给出结论要素**：主因、置信度（low/medium/high）、影响范围（面积/株数/台数，
   无法量化要说明原因）、严重度倾向、建议补检项。

## 四、证据纪律

- 每条判断都引用「通道 + 时间 + 读数/现象」或知识库出处；
- 证据不足就写低置信度，并写明链条断在哪里；不把"未知"写成"正常"；
- 不发明阈值、药剂、剂量、安全间隔期、发生规律；工具没给的字段一律标注缺失；
- 相关性不等于因果：先写"疑似"，并给出成立条件与证伪条件（观察到什么就该改判）。

## 五、输出格式

## 诊断报告
**候选假设与排除**（逐条：假设 — 支持证据 — 反证/缺失 — 判定）

**主因判断**（结论 + 置信度 + 成立条件 + 证伪条件）

**鉴别依据**（引用的知识库内容与出处）

**严重度倾向与影响范围**

**建议补检**（具体到通道/部位/方法，供中枢决定是否需要补充诊断）

**处置方向**（只写知识库给出的方向与约束；查不到方案时明确写「须转人工」）
"""


def build_system_prompt(
    *,
    as_of: str,
    site_context: str,
    tool_names: str,
    perception_report: str,
    anomaly_screen: str,
    pending: str = "",
) -> str:
    """拼装本轮的系统提示词：稳定角色 + 上游证据 + 本轮动态上下文。"""
    parts = [
        SYSTEM_PROMPT,
        "\n## 作业手册（SKILL.md）\n" + load_skill("diagnosis"),
        "\n## 本轮上下文",
        f"- 对象档案：{site_context}",
        f"- 决策基准日期（as_of）：{as_of}",
        f"- 本轮可用工具：{tool_names}",
        "\n## 本轮上游证据",
        f"### 感知报告\n{perception_report}",
        f"\n### 异常初筛\n{anomaly_screen}",
    ]
    if pending:
        parts.append(
            "\n## 中枢的补充诊断要求（本轮优先回答）\n"
            f"{pending}\n"
            "回答完点名问题后，仍要给出完整的诊断报告结构。"
        )
    return "\n".join(parts) + get_language_instruction()


def create_diagnosis_agent(llm):
    def diagnosis_node(state) -> dict:
        as_of = state["as_of"]
        site_context = get_site_context_from_state(state)
        perception_report = report_or_absent(
            state["perception_state"].get("perception_report", ""), "感知"
        )
        anomaly_screen = report_or_absent(
            state["perception_state"].get("anomaly_screen", ""), "初筛"
        )
        diagnosis_state = state["diagnosis_state"]
        pending = (diagnosis_state.get("pending_request") or "").strip()

        system_message = build_system_prompt(
            as_of=as_of,
            site_context=site_context,
            tool_names=", ".join(tool.name for tool in TOOLS),
            perception_report=perception_report,
            anomaly_screen=anomaly_screen,
            pending=pending,
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_message}"),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(system_message=system_message)

        chain = prompt | llm.bind_tools(TOOLS)
        result = chain.invoke(state["messages"])

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        new_diagnosis_state = {
            **diagnosis_state,
            "diagnosis_report": report or diagnosis_state.get("diagnosis_report", ""),
        }
        return {
            "messages": [result],
            "diagnosis_state": new_diagnosis_state,
        }

    return diagnosis_node
