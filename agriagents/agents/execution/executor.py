"""执行 Agent：把审批通过的工单下发到执行机构，并回传现场反馈。

安全设计分三层，缺一不可：

1. **图上**：只有审批门的 approved 分支能进入本节点；
2. **节点内**：再校验一次 ``approved``——图是可以被重新接线的，防呆不能只靠上游；
3. **供应商层**：``dry_run`` 时只留痕不下发（见 ``vendors/actuators.py``）。

未获批准时本节点不抛异常，而是返回一个 ``failed`` 的执行结果交给中枢处理：
让流程显式地"知道有一单被拒绝了"，比抛异常中断整条流水线更有用。

接线注意：工单正文、审批记录与执行模式由本节点**显式注入提示词**——
消息通道在审批门之前已清空过，执行 Agent 必须直接在提示词里读到工单。
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agriagents.agents.context import get_language_instruction, get_site_context_from_state
from agriagents.agents.tools import dispatch_command, get_execution_status
from agriagents.skills import load_skill
from agriagents.dataflows.config import get_config

TOOLS = (dispatch_command, get_execution_status)

SYSTEM_PROMPT = """\
你是「农业维护多智能体流程」中的**执行 Agent**（Executor）：把**已批准**的维护工单\
逐条翻译成执行机构指令并下发，回收执，如实报告实际发生了什么。

你只负责"照单执行与留痕"：工单让你做什么就做什么，不多、不少、不发挥。

## 一、硬性前提（任一条不满足即停手）

1. 审批记录必须是「通过」或「演练模式（dry_run）放行」；
   若为「驳回」或记录缺失，不得下发任何指令；
2. 本次对象的编号必须与工单一致；
3. 工单任务与参数必须齐全、无歧义。

任一条不满足：不下发任何指令，把原因写进执行记录并上报，等待中枢处理。

## 二、演练模式（dry_run）

演练模式**仍要走完下发与回读流程**——`dispatch_command` 只留痕、不产生物理动作，
这正是用来把全链路跑通的。但每一处记录都必须写明「演练，未实际下发」，
严禁把演练表述成已执行。

## 三、执行纪律

- **严格按单执行**：不增删动作、不修改用量与参数、不调整时间窗；工单没写的动作一律不做；
- **参数缺失不猜**：缺少设备编号、用量、时长、阈值等必需参数时，该条停止执行，
  写明"因缺参数未执行"；不要用经验默认值补齐；
- **逐条推进**：一次只推进一步；上一条未确认成功，不开始下一条；
- **下发后必须回读**：每次 `dispatch_command` 之后调用 `get_execution_status` 确认；
  失败立即停止后续动作，不要把后续动作继续发出去；
- **重试有度**：同一指令最多重试一次，并在记录里写明重试次数与原因；
- **如实留痕**：多做了、少做了、参数不同、未取得回执，都要写清楚；
  严禁把"计划"写成"已执行"，严禁把演练写成实发；
- **异常即上报**：现场情况与工单前提冲突（设备离线、天气不满足、人员未到位等）时，
  停止执行并把事实回报给中枢，不自行改方案、不自行换参数。

## 四、职责边界

不做农艺判断、不解释病因、不重新规划、不修改工单、不经手审批；
一切超出工单范围的情况都上报，不自行处置。

## 五、输出格式

## 执行记录
**工单号 / 对象 / 执行模式（实发 / 演练）/ 审批记录**

### 指令下发台账
逐条：任务 — 执行机构 — 指令与参数 — 回执状态 — 时间

### 未执行项与原因

### 与工单的偏差

### 现场反馈
只写观察到的事实；没有拿到回执就写"未取得回执"

### 需要中枢跟进的问题
"""


def build_system_prompt(
    *,
    as_of: str,
    site_context: str,
    tool_names: str,
    work_order: str,
    approval: str,
    dry_run: bool,
    require_human_approval: bool,
) -> str:
    """拼装本轮的系统提示词：稳定角色 + 工单/审批/执行模式。"""
    if dry_run:
        mode = "演练（dry_run）：指令只留痕、不下发物理动作，报告中必须写明这是演练"
    else:
        mode = "实发：指令会真正下发到执行机构"
    if approval:
        approval_note = approval
    elif dry_run:
        approval_note = "（演练模式：未取得人工审批，按 dry_run 放行——只可留痕，不得产生物理动作。）"
    elif require_human_approval:
        approval_note = "（审批记录缺失：按未批准处理，不得下发。）"
    else:
        approval_note = "（受控自动化放行：无人工审批记录，风险由调用方承担。）"

    return "\n".join([
        SYSTEM_PROMPT,
        "\n## 作业手册（SKILL.md）\n" + load_skill("execution"),
        "\n## 本轮输入",
        f"- 对象档案：{site_context}",
        f"- 决策基准日期（as_of）：{as_of}",
        f"- 本轮可用工具：{tool_names}",
        f"- 执行模式：{mode}",
        f"- 审批记录：{approval_note}",
        "\n## 待执行工单\n"
        + (work_order or "（没有工单：缺失即不允许下发任何指令。）"),
    ]) + get_language_instruction()


def create_executor(llm):
    def executor_node(state) -> dict:
        coordination_state = state["coordination_state"]
        execution_state = state["execution_state"]

        if not coordination_state.get("approved"):
            return {
                "execution_state": {
                    **execution_state,
                    "dispatch_log": "未获批准：执行 Agent 拒绝下发任何指令。",
                    "execution_status": "failed",
                    "execution_report": "工单未通过审批门，未产生任何物理动作。",
                    "needs_followup": False,
                }
            }

        as_of = state["as_of"]
        site_context = get_site_context_from_state(state)
        work_order = coordination_state.get("work_order", "")
        config = get_config()

        system_message = build_system_prompt(
            as_of=as_of,
            site_context=site_context,
            tool_names=", ".join(tool.name for tool in TOOLS),
            work_order=work_order,
            approval=coordination_state.get("approval", ""),
            dry_run=config.get("dry_run", True),
            require_human_approval=config.get("require_human_approval", True),
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_message}"),
            MessagesPlaceholder(variable_name="messages"),
        ])
        prompt = prompt.partial(system_message=system_message)

        chain = prompt | llm.bind_tools(TOOLS)
        result = chain.invoke(state["messages"])

        log = execution_state.get("dispatch_log", "")
        if len(result.tool_calls) == 0:
            log = f"{log}\n{result.content}".strip()

        return {
            "messages": [result],
            "execution_state": {
                **execution_state,
                "dispatch_log": log,
            },
        }

    return executor_node
