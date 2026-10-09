"""数据工具声明层：四个 Agent 能调用的全部取数/动作接口。

框架约定（沿用 TradingAgents 的做法）：

- **日期不由模型决定**。每个带日期的工具都从图状态注入 ``as_of``
  （``InjectedState("as_of")``），并由 ``as_of_window`` 夹紧到基准日期，
  保证回溯 run 永远看不到基准日之后才发生的信息。
- **工具不直连数据源**，只经 ``route_to_vendor`` 走供应商链；
  换数据源＝改配置，不动 Agent 代码。
- 现在全部是**框架占位实现**，返回带 ``[stub]`` 的说明字符串。
  接真实数据源时只替换 ``dataflows/vendors/`` 下的适配器，本文件签名保持不变。
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agriagents.dataflows.router import route_to_vendor
from agriagents.dataflows.time_window import as_of, as_of_window

# ---------------------------------------------------------------------------
# 感知 Agent 的工具
# ---------------------------------------------------------------------------


@tool
def get_sensor_readings(
    site_id: Annotated[str, "维护对象编号，例如 FIELD-07 / GH-03"],
    channel: Annotated[str, "传感器通道，例如 soil_moisture / air_temp / humidity / water_quality"],
    start_date: Annotated[str, "起始日期 YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期 YYYY-MM-DD"],
    as_of_date: Annotated[str, InjectedState("as_of")] = "",
) -> str:
    """读取指定通道的传感器时间序列（墒情、温湿度、水质等）。

    返回该通道在给定窗口内的读数序列与统计摘要。
    """
    start_date, end_date = as_of_window(start_date, end_date, as_of_date)
    return route_to_vendor("get_sensor_readings", site_id, channel, start_date, end_date)


@tool
def get_weather_forecast(
    site_id: Annotated[str, "维护对象编号"],
    start_date: Annotated[str, "预报起始日期 YYYY-MM-DD"],
    end_date: Annotated[str, "预报结束日期 YYYY-MM-DD"],
    as_of_date: Annotated[str, InjectedState("as_of")] = "",
) -> str:
    """读取作业窗口内的气象预报与历史实况（温度、降水、风、极端天气预警）。"""
    start_date, end_date = as_of_window(start_date, end_date, as_of_date)
    return route_to_vendor("get_weather_forecast", site_id, start_date, end_date)


@tool
def get_camera_snapshot(
    site_id: Annotated[str, "维护对象编号"],
    as_of_date: Annotated[str, InjectedState("as_of")] = "",
) -> str:
    """获取摄像头/无人机的影像观察结论（可见光与多光谱的判读结果，非原始图像）。"""
    return route_to_vendor("get_camera_snapshot", site_id, as_of(as_of_date, as_of_date))


@tool
def get_remote_sensing_index(
    site_id: Annotated[str, "维护对象编号"],
    index: Annotated[str, "遥感指数，例如 ndvi / ndwi / savi"],
    start_date: Annotated[str, "起始日期 YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期 YYYY-MM-DD"],
    as_of_date: Annotated[str, InjectedState("as_of")] = "",
) -> str:
    """读取田块尺度的遥感植被/水分指数序列，用于长势与胁迫判断。"""
    start_date, end_date = as_of_window(start_date, end_date, as_of_date)
    return route_to_vendor("get_remote_sensing_index", site_id, index, start_date, end_date)


@tool
def get_device_status(
    site_id: Annotated[str, "维护对象编号或机具编号"],
    as_of_date: Annotated[str, InjectedState("as_of")] = "",
) -> str:
    """读取设备/执行机构的运行状态与故障码（泵、阀、卷帘、灌溉机、农机）。"""
    return route_to_vendor("get_device_status", site_id, as_of(as_of_date, as_of_date))


# ---------------------------------------------------------------------------
# 诊断 Agent 的工具
# ---------------------------------------------------------------------------


@tool
def list_knowledge_bases() -> str:
    """列出实际加载的知识源、类型、数量及专用检索工具；首次诊断先查看覆盖范围。"""
    return route_to_vendor("list_knowledge_bases")


@tool
def search_knowledge_base(
    query: Annotated[str, "具体实体、症状或农艺问题"],
    source: Annotated[str, "可选，list_knowledge_bases 返回的 source，例如 cropdp/plantinquiry/qa_en/qa_zh/local"] = "",
    kind: Annotated[str, "可选，目录返回的 kind，例如 qa_pair/symptom/severity/diagnosis"] = "",
    top_k: Annotated[int, "最多返回多少条证据，范围1至10；默认3，仅在候选不足时增加"] = 3,
    crop: Annotated[str, "可选，按元数据精确限制适用作物；未知作物信息的条目不会进入结果"] = "",
    dataset: Annotated[str, "可选，目录中返回的具体 dataset 标识，可区分 qa_en 下的三个数据集"] = "",
) -> str:
    """按知识源/类型精确限定查证范围，返回 doc_id、出处与短命中片段，不默认返回全文。"""
    return route_to_vendor("search_knowledge_base", query, source, kind, top_k, crop, dataset)


@tool
def get_knowledge_document(
    doc_id: Annotated[str, "search_knowledge_base 返回的完整 doc_id，必须逐字传入"],
) -> str:
    """仅在短片段不足以完成鉴别时，按精确 doc_id 读取一篇知识文档全文。"""
    return route_to_vendor("get_knowledge_document", doc_id)


@tool
def query_agronomy_knowledge(
    question: Annotated[str, "要查询的农艺问题，尽量具体到作物、生育期与现象"],
) -> str:
    """查询农艺知识库（栽培管理、水肥策略、生育期判定）。"""
    return route_to_vendor("query_agronomy_knowledge", question)


@tool
def query_pest_disease_library(
    crop: Annotated[str, "作物名称"],
    symptom: Annotated[str, "观察到的症状描述，例如叶片黄化斑点、果实腐烂"],
) -> str:
    """查询病虫害图谱库：候选病害、鉴别要点、发生条件与防治建议。"""
    return route_to_vendor("query_pest_disease_library", crop, symptom)


@tool
def query_soil_reference(
    site_id: Annotated[str, "维护对象编号"],
) -> str:
    """查询该地块的土壤本底档案：质地、pH、有机质、历史检测与施肥记录。"""
    return route_to_vendor("query_soil_reference", site_id)


@tool
def query_equipment_manual(
    device_id: Annotated[str, "设备型号或编号"],
    fault_code: Annotated[str, "故障码或异常现象，例如 E04 或 出水压力不足"],
) -> str:
    """查询设备手册与故障处置指南（农机、泵阀、灌溉与环控设备）。"""
    return route_to_vendor("query_equipment_manual", device_id, fault_code)


@tool
def get_treatment_options(
    diagnosis: Annotated[str, "明确病害名称，优先使用病虫害工具返回的 disease 原值；不要附加整段诊断分析"],
    crop: Annotated[str, "诊断对象的作物名称，例如番茄或黄瓜；应始终提供以避免跨作物方案"] = "",
) -> str:
    """查询该诊断对应的可选处置方案及其约束（适用条件、成本、安全间隔期）。"""
    return route_to_vendor("get_treatment_options", diagnosis, crop=crop)


# ---------------------------------------------------------------------------
# 协调决策中枢的工具
# ---------------------------------------------------------------------------


@tool
def get_resource_inventory(
    site_id: Annotated[str, "维护对象编号"],
) -> str:
    """查询可用资源：人力排班、机具可用性、农资与备件库存。"""
    return route_to_vendor("get_resource_inventory", site_id)


@tool
def check_safety_policy(
    action: Annotated[str, "拟执行的作业动作描述"],
    materials: Annotated[str, "涉及的农资/药剂/能源清单，无则填「无」"],
) -> str:
    """对照安全与合规规则库，检查该作业是否存在禁用、限用或安全间隔期冲突。"""
    return route_to_vendor("check_safety_policy", action, materials)


# ---------------------------------------------------------------------------
# 执行 Agent 的工具
# ---------------------------------------------------------------------------

# 执行类工具是**唯一会改变物理世界**的接口。
# 框架层面强制两点：必须已通过人工审批（由审批门保证），且受 dry_run 开关约束。
ACTUATORS = ("irrigation", "spray", "machine", "valve", "arm")


@tool
def dispatch_command(
    site_id: Annotated[str, "维护对象编号"],
    actuator: Annotated[str, "执行机构类型：irrigation / spray / machine / valve / arm"],
    command: Annotated[str, "要下发的动作指令，例如 start / stop / set_duration"],
    params: Annotated[str, "指令参数（JSON 字符串），例如 {\"duration_min\": 20}"],
    work_order_id: Annotated[str, "对应的工单号，用于执行留痕"],
) -> str:
    """向执行机构下发控制指令，返回回执与任务编号。"""
    if actuator not in ACTUATORS:
        return f"未知执行机构 {actuator!r}；支持的机构为：{', '.join(ACTUATORS)}"
    return route_to_vendor("dispatch_command", site_id, actuator, command, params, work_order_id)


@tool
def get_execution_status(
    site_id: Annotated[str, "维护对象编号"],
    task_id: Annotated[str, "dispatch_command 返回的任务编号"],
) -> str:
    """查询已下发任务的执行状态与现场回执。"""
    return route_to_vendor("get_execution_status", site_id, task_id)
