"""供应商路由：工具名 → 类别 → 供应商链。

仿 TradingAgents 的 ``dataflows/router.py``：

- 配置里的值是**确切的**供应商链，工具按顺序回退，不做隐式兜底；
  想要"优先 A、失败换 B"就写 ``"a,b"``；
- 路由按**行为**捕获异常：限流/未配置/无数据都只是"换下一家"；
- 核心数据（读数、影像、状态、知识、资源、安全规则、执行网关）全部失败时
  必须抛错让 run 显式中断；只有可选增强数据允许降级为哨兵文本。
"""

from __future__ import annotations

import logging

from agriagents.dataflows.config import get_config
from agriagents.dataflows.errors import (
    NoDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from agriagents.dataflows.vendors import (
    actuators,
    imagery,
    knowledge,
    knowledge_catalog,
    local_rag,
    ops,
    sensors,
    synthetic,
    weather,
)

logger = logging.getLogger(__name__)

# 类别定义：既是文档，也是配置校验的依据
DATA_CATEGORIES: dict[str, dict] = {
    "sensor_data": {
        "description": "田间传感器读数（墒情、温湿度、水质…）",
        "tools": ["get_sensor_readings"],
    },
    "weather_data": {
        "description": "气象实况与预报",
        "tools": ["get_weather_forecast"],
    },
    "imagery_data": {
        "description": "摄像头 / 无人机 / 卫星遥感",
        "tools": ["get_camera_snapshot", "get_remote_sensing_index"],
    },
    "device_telemetry": {
        "description": "设备运行状态与故障码",
        "tools": ["get_device_status"],
    },
    "agronomy_knowledge": {
        "description": "农艺/病虫害/土壤/设备手册知识库",
        "tools": [
            "list_knowledge_bases",
            "search_knowledge_base",
            "get_knowledge_document",
            "query_agronomy_knowledge",
            "query_pest_disease_library",
            "query_soil_reference",
            "query_equipment_manual",
            "get_treatment_options",
        ],
    },
    "resource_data": {
        "description": "人力、机具、农资库存",
        "tools": ["get_resource_inventory"],
    },
    "safety_policy": {
        "description": "安全与合规规则库",
        "tools": ["check_safety_policy"],
    },
    "actuator_gateway": {
        "description": "执行机构网关（会改变物理世界）",
        "tools": ["dispatch_command", "get_execution_status"],
    },
}

# 工具名 → 类别
TOOL_CATEGORY: dict[str, str] = {
    tool: category
    for category, spec in DATA_CATEGORIES.items()
    for tool in spec["tools"]
}

# 工具名 → {供应商名: 实现}
# 接真实数据源时在这里注册，例如 "get_sensor_readings": {"real_sensors": sensors.get_sensor_readings}
VENDOR_METHODS: dict[str, dict] = {
    "list_knowledge_bases": {"local_rag": knowledge_catalog.list_knowledge_bases},
    "search_knowledge_base": {"local_rag": knowledge_catalog.search_knowledge_base},
    "get_knowledge_document": {"local_rag": knowledge_catalog.get_knowledge_document},
    "get_sensor_readings": {
        "synthetic_demo": synthetic.get_sensor_readings,
        "stub_sensors": sensors.get_sensor_readings,
    },
    "get_device_status": {
        "synthetic_demo": synthetic.get_device_status,
        "stub_sensors": sensors.get_device_status,
    },
    "get_weather_forecast": {
        "synthetic_demo": synthetic.get_weather_forecast,
        "stub_weather": weather.get_weather_forecast,
    },
    "get_camera_snapshot": {
        "synthetic_demo": synthetic.get_camera_snapshot,
        "stub_imagery": imagery.get_camera_snapshot,
    },
    "get_remote_sensing_index": {
        "synthetic_demo": synthetic.get_remote_sensing_index,
        "stub_imagery": imagery.get_remote_sensing_index,
    },
    "query_agronomy_knowledge": {
        "local_rag": local_rag.query_agronomy_knowledge,
        "stub_knowledge": knowledge.query_agronomy_knowledge,
    },
    "query_pest_disease_library": {
        "local_rag": local_rag.query_pest_disease_library,
        "stub_knowledge": knowledge.query_pest_disease_library,
    },
    "query_soil_reference": {
        "local_rag": local_rag.query_soil_reference,
        "stub_knowledge": knowledge.query_soil_reference,
    },
    "query_equipment_manual": {
        "local_rag": local_rag.query_equipment_manual,
        "stub_knowledge": knowledge.query_equipment_manual,
    },
    "get_treatment_options": {
        "local_rag": local_rag.get_treatment_options,
        "stub_knowledge": knowledge.get_treatment_options,
    },
    "get_resource_inventory": {
        "synthetic_demo": synthetic.get_resource_inventory,
        "stub_ops": ops.get_resource_inventory,
    },
    "check_safety_policy": {
        "synthetic_demo": synthetic.check_safety_policy,
        "stub_ops": ops.check_safety_policy,
    },
    "dispatch_command": {
        "synthetic_demo": synthetic.dispatch_command,
        "stub_actuators": actuators.dispatch_command,
    },
    "get_execution_status": {
        "synthetic_demo": synthetic.get_execution_status,
        "stub_actuators": actuators.get_execution_status,
    },
}

# 可选增强数据：全部供应商失败时降级为哨兵，不中断 run。
# 注意 check_safety_policy **不在**此列——安全规则取不到时必须中断，不能降级放行。
OPTIONAL_TOOLS: frozenset[str] = frozenset({
    "get_remote_sensing_index",
    "get_treatment_options",
})


def resolve_vendor_chain(tool_name: str, config: dict | None = None) -> list[str]:
    """解析某工具应当使用的供应商顺序。

    优先级：``tool_vendors`` 的工具级覆盖 > ``data_vendors`` 的类别默认。
    """
    config = config or get_config()
    override = (config.get("tool_vendors") or {}).get(tool_name)
    if override:
        return [v.strip() for v in str(override).split(",") if v.strip()]
    category = TOOL_CATEGORY.get(tool_name)
    if category is None:
        raise ValueError(f"未注册的工具：{tool_name}")
    raw = (config.get("data_vendors") or {}).get(category, "")
    return [v.strip() for v in str(raw).split(",") if v.strip()]


def route_to_vendor(method: str, *args, **kwargs) -> str:
    """按供应商链执行 ``method``，返回第一条成功的文本结果。"""
    chain = resolve_vendor_chain(method)
    failures: list[str] = []

    for vendor in chain:
        impl = VENDOR_METHODS.get(method, {}).get(vendor)
        if impl is None:
            failures.append(f"{vendor}: 未提供 {method} 实现")
            continue
        try:
            return impl(*args, **kwargs)
        except (VendorRateLimitError, VendorNotConfiguredError, NoDataError) as exc:
            logger.debug("供应商 %s 无法提供 %s：%s", vendor, method, exc)
            failures.append(f"{vendor}: {exc}")

    detail = "；".join(failures) or "未配置任何供应商"
    if method in OPTIONAL_TOOLS:
        logger.warning("可选数据 %s 全部供应商不可用：%s", method, detail)
        return f"（可选数据源不可用：{method}。{detail}）"
    raise NoDataError(f"{method}{args[:1] if args else ''}", detail=detail)
