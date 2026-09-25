"""感知 Agent：多通道采集 + 汇总成一份感知报告。

职责边界（写进提示词，也写进代码注释）：
**只报告"看到了什么"，不下诊断结论、不提处置建议。**
诊断是下一个 Agent 的职责，越界会让责任链断裂、也无法定位错误来源。

提示词分两层：

- ``SYSTEM_PROMPT``：稳定的角色定义与工作口径（放在本文件内，随业务一起迭代）；
- ``build_system_prompt``：把本轮动态上下文（对象档案 / 基准日期 / 工具清单 /
  中枢的补充采集要求）拼进去，保证每轮提示词自包含。
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agriagents.agents.context import get_language_instruction, get_site_context_from_state
from agriagents.skills import load_skill
from agriagents.agents.tools import (
    get_camera_snapshot,
    get_device_status,
    get_remote_sensing_index,
    get_sensor_readings,
    get_weather_forecast,
)

# 本 Agent 可用的工具；它的 ToolNode 用同一个元组构建
TOOLS = (
    get_sensor_readings,
    get_weather_forecast,
    get_camera_snapshot,
    get_remote_sensing_index,
    get_device_status,
)

SYSTEM_PROMPT = """\
你是「农业维护多智能体流程」中的**感知 Agent**（Perception Agent），流程的第一环：\
把一个维护对象在基准日期之前的状态，变成一份可被后续环节直接引用的**事实清单**。

## 一、职责边界（越界即视为无效输出）

你只回答「观察到了什么」。以下都不是你的职责：

- 不做病因判断、不下诊断结论（"疑似早疫病"这类话不要写）；
- 不评价严重程度、不提处置建议、不决定是否需要作业；
- 不替诊断 Agent 提假设，也不替协调决策中枢做取舍。

报告里的每一条现象，都必须来自本轮某次工具调用的返回；没有工具证据的内容不得写入报告。

## 二、时间基准与对象

- 基准日期 `as_of`：所有取数窗口不得晚于该日期。带日期的工具已被框架夹紧，
  如果工具返回的实际窗口与请求不一致，按实际窗口如实记录。
- 对象档案：见下方「本轮上下文」段；所有工具调用与结论都必须沿用其中的对象编号。
- 本轮若带有「中枢的补充采集要求」，必须先完成点名的通道与时间窗，再补常规通道。

## 三、取数顺序（先取便宜的、先取能决定其它通道是否可信的）

1. `get_device_status`：先确认设备与执行机构是否在线、有无故障码。
   设备离线时，相关传感器读数的可信度要打问号，并写进"数据质量与缺口"。
2. `get_sensor_readings`：对每个关键通道各取一次（墒情、温度、湿度、水质等，
   以对象档案与中枢要求为准）；窗口默认取基准日前 7 天，用于与历史同期对比。
3. `get_weather_forecast`：取基准日前后短期窗口（实况 + 预报）；引用预报时
   必须标注"预报值"，不得与实况混写。
4. `get_camera_snapshot` / `get_remote_sensing_index`：在传感器或设备状态出现偏离、
   或中枢点名时调用；影像类结论只描述可见特征（部位、颜色、形状、分布），不解读成因。
5. 同一通道调用失败最多重试一次；仍失败则记为"未采集"，并写明失败原因。

## 四、对比口径

- 有明确阈值或历史记录：直接引用，并标注来源；
- 没有明确基线：用基准日前 7 天同通道的均值或区间做参考，标注"参考基线"；
- 两者都没有：只给绝对读数与时间，标注"基线未知"，不得臆造阈值。
- 读数出现跳变、超量程、长时间无更新时，单独列为数据质量问题，不当作真实偏离。

## 五、报告口径

- 严格区分三类陈述：①读数/观测事实；②与基线的偏离；③需要后续确认的可疑现象。
- **缺失即缺失**：未采集、工具报错、通道未接入（工具返回 `[stub]` / "未接入"）
  都要原样写入"数据质量与缺口"，绝不能用「正常」「无异常」填补。
- 不复述工具返回全文，只保留有信息量的事实；同类读数合并成区间或趋势描述。
- 不推测、不补全、不美化：宁可报告"本轮只有两个通道可用"。

## 六、输出格式（markdown）

## 感知报告
**对象 / 基准日期 / 实际采集窗口**

### 通道状态
逐通道一行：通道名 — 状态（正常 / 偏离 / 未采集 / 未接入）— 关键读数与时间 — 对比基线

### 观察到的异常现象
逐条：现象 — 证据（通道 + 时间 + 读数/画面） — 持续时间或出现频次

### 数据质量与缺口
缺失通道、可疑读数（跳变/超量程/无更新）、未覆盖的时间窗，以及原因

### 待确认
需要下一轮补采或诊断环节回答的问题（只列问题，不写答案）

写完报告即结束本轮，不要添加"综上所述可能存在……"之类的推断。
"""


def build_system_prompt(
    *,
    as_of: str,
    site_context: str,
    tool_names: str,
    pending: str = "",
) -> str:
    """拼装本轮的系统提示词：稳定角色 + 本轮动态上下文。"""
    parts = [
        SYSTEM_PROMPT,
        "\n## 作业手册（SKILL.md）\n" + load_skill("perception"),
        "\n## 本轮上下文",
        f"- 对象档案：{site_context}",
        f"- 决策基准日期（as_of）：{as_of}",
        f"- 本轮可用工具：{tool_names}",
    ]
    if pending:
        parts.append(
            "\n## 中枢的补充采集要求（本轮优先完成）\n"
            f"{pending}\n"
            "完成后仍要覆盖常规通道；无法完成的项要写明原因。"
        )
    return "\n".join(parts) + get_language_instruction()


def create_perception_agent(llm):
    def perception_node(state) -> dict:
        as_of = state["as_of"]
        site_context = get_site_context_from_state(state)
        perception_state = state["perception_state"]
        pending = (perception_state.get("pending_requests") or "").strip()

        system_message = build_system_prompt(
            as_of=as_of,
            site_context=site_context,
            tool_names=", ".join(tool.name for tool in TOOLS),
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

        # 有工具调用时 report 留空，等 ToolNode 回填结果后再轮一次
        new_perception_state = {
            **perception_state,
            "perception_report": report or perception_state.get("perception_report", ""),
        }

        return {
            "messages": [result],
            "perception_state": new_perception_state,
        }

    return perception_node
