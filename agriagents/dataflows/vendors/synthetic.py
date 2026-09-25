"""合成演示数据源：把全流程"喂饱"，但绝不冒充真实数据。

为什么需要它：四个 Agent 只有在工具能返回**可读的数值与现象**时，才能走出
「感知 → 诊断 → 工单 → 安检 → 审批 → 执行 → 反馈」的完整路径。真实数据源
接入之前，本模块提供确定性的合成数据做端口联调与回归测试。

三条纪律：

1. **确定性**：同一个 (对象, 通道, 日期) 永远得到同一个值——不读时钟、
   不依赖进程级随机状态。复盘、回归测试与"同一案件重跑"才有意义。
2. **自曝身份**：每条返回文本都带 ``[synthetic_demo]`` 与"合成演示数据"字样，
   下游 Agent 与人工读者都能看出这不是实测值。
3. **只给数据不给结论**：参考区间、规则条款一律标注"演示口径"，
   诊断与处置结论仍由 Agent 依据这些数据自行得出。

演示场景（锚定 ``DEMO_ANCHOR = 2026-09-26``，围绕它构造一周过程）：

- 土壤墒情一路缓降到 16% 上下（偏低），空气湿度升到 88% 上下（叶面易结露）；
- 影像判读：下部叶片出现水渍状暗绿斑点，约 15% 叶面积；
- 滴灌阀门 VALVE-11 报 E04（开阀无流量反馈），3 号风机通信中断；
- 基准日当天发布的短期预报：次日 18 mm 降水、风力 3 级；
- 资源账本：2 人可调度、3 号植保机可用、"叶面肥 Y"库存不足。

接入真实数据源时：在 ``router.VENDOR_METHODS`` 注册新供应商、改 ``data_vendors``
配置即可；本模块保留用于回归测试，不必删除。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from agriagents.dataflows.config import get_config
from agriagents.dataflows.errors import NoDataError
from agriagents.dataflows.symbols import safe_site_component

VENDOR_NAME = "synthetic_demo"
SCENARIO_VERSION = "synthetic-demo v1"
SAFETY_RULES_VERSION = "synthetic-demo-rules v0.1（演示用，不得用于真实作业）"
DEMO_ANCHOR = date(2026, 9, 26)

_MAX_WINDOW_DAYS = 62

# ---- 演示地块档案（真实台账接入后替换 lookup_site_profile）------------------
_DEMO_SITES: dict[str, dict] = {
    "FIELD-07": {
        "name": "东地块（演示档案）",
        "crop": "番茄",
        "area": "1.2 亩",
        "planting": "2026-05-12 定植（演示）",
    },
    "GH-03": {
        "name": "3 号棚（演示档案）",
        "crop": "黄瓜",
        "area": "0.6 亩",
        "planting": "2026-08-02 定植（演示）",
    },
}


def lookup_site_profile(site_id: str) -> dict:
    """返回演示地块档案；未知编号返回空字典（失败必须 fail-open）。"""
    return dict(_DEMO_SITES.get(str(site_id).strip().upper(), {}))


# ---- 数值通道：确定性序列 ----------------------------------------------------
# channel: (单位, 锚定日数值, 每日斜率, 季节振幅, 季节相位(年内第几天), 噪声幅度, 小数位)
_CHANNELS: dict[str, tuple] = {
    "soil_moisture": ("%", 16.6, -0.75, 3.0, 105, 0.25, 1),
    "air_temp": ("°C", 27.6, -0.50, 9.0, 200, 0.45, 1),
    "humidity": ("%", 88.0, 1.10, 12.0, 15, 1.60, 1),
    "water_quality": ("mS/cm(EC)", 1.78, 0.020, 0.30, 130, 0.030, 2),
    "leaf_wetness": ("h/日", 6.5, 0.35, 2.5, 20, 0.40, 1),
}

_GENERIC_CHANNEL = ("单位未知", 20.0, 0.0, 2.0, 100, 0.50, 2)


def _seed(site_id: str, channel: str, day: date) -> int:
    key = f"{site_id}|{channel}|{day.isoformat()}".encode("utf-8")
    return int.from_bytes(hashlib.blake2s(key, digest_size=8).digest(), "big")


def _rng(site_id: str, channel: str, day: date) -> random.Random:
    return random.Random(_seed(site_id, channel, day))


def _channel_value(site_id: str, channel: str, day: date) -> tuple[float, str]:
    """确定性通道读数：锚定值 + 斜率 + 季节项 + 噪声。"""
    unit, anchor_value, slope, amp, phase, noise_amp, digits = _CHANNELS.get(
        channel, _GENERIC_CHANNEL
    )
    doy = day.timetuple().tm_yday
    anchor_doy = DEMO_ANCHOR.timetuple().tm_yday
    seasonal = amp * (
        math.sin(2 * math.pi * (doy - phase) / 365.25)
        - math.sin(2 * math.pi * (anchor_doy - phase) / 365.25)
    )
    offset = (day - DEMO_ANCHOR).days
    offset = max(-_MAX_WINDOW_DAYS, min(_MAX_WINDOW_DAYS, offset))
    noise = _rng(site_id, channel, day).uniform(-1.0, 1.0) * noise_amp
    return round(anchor_value + slope * offset + seasonal + noise, digits), unit


def _parse_window(start_date: str, end_date: str) -> list[date]:
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except ValueError as exc:
        raise NoDataError(f"窗口 {start_date} ~ {end_date}", VENDOR_NAME, f"日期格式非法：{exc}")
    if end < start:
        raise NoDataError(f"窗口 {start_date} ~ {end_date}", VENDOR_NAME, "结束日期早于开始日期")
    days = (end - start).days + 1
    if days > _MAX_WINDOW_DAYS:
        raise NoDataError(
            f"窗口 {start_date} ~ {end_date}", VENDOR_NAME,
            f"窗口 {days} 天超过演示源上限 {_MAX_WINDOW_DAYS} 天",
        )
    return [start + timedelta(days=i) for i in range(days)]


def _header(title: str) -> str:
    return (
        f"[{VENDOR_NAME}] {title}\n"
        f"（合成演示数据（{SCENARIO_VERSION}）：由程序按确定性规则生成，**不是真实测值**，"
        "仅用于把流程跑通；接入真实数据源后本段自然消失。）"
    )


# ---------------------------------------------------------------------------
# 感知类工具
# ---------------------------------------------------------------------------


def get_sensor_readings(site_id: str, channel: str, start_date: str, end_date: str) -> str:
    """读取一个传感器通道的时间序列（确定性合成）。"""
    days = _parse_window(start_date, end_date)
    unit, *_ = _CHANNELS.get(channel, _GENERIC_CHANNEL)
    values = [_channel_value(site_id, channel, day) for day in days]
    lines = [
        _header(f"传感器通道 {channel} @ {site_id}"),
        f"窗口：{start_date} ~ {end_date}（{len(days)} 天，全部有读数，无缺测）",
        "",
        "读数（日期 数值 单位）：",
    ]
    lines += [f"{day.isoformat()}  {value} {unit}" for day, (value, _) in zip(days, values)]
    numbers = [value for value, _ in values]
    latest = numbers[-1]
    mean = sum(numbers) / len(numbers)
    lines += [
        "",
        f"统计：最新 {latest} {unit}；窗口均值 {round(mean, 2)} {unit}；"
        f"窗口区间 {min(numbers)} ~ {max(numbers)} {unit}",
        "演示参考区间（仅用于演示对比，非农艺标准）："
        + _reference_band(channel, unit),
    ]
    if channel not in _CHANNELS:
        lines.append("说明：该通道未在演示场景中建模，返回通用合成序列。")
    return "\n".join(lines)


def _reference_band(channel: str, unit: str) -> str:
    band = {
        "soil_moisture": "20.0 ~ 30.0 %（10cm 层体积含水率）",
        "air_temp": "18.0 ~ 32.0 °C（日均）",
        "humidity": "50.0 ~ 80.0 %",
        "water_quality": "EC 1.2 ~ 1.8 mS/cm",
        "leaf_wetness": "0 ~ 4.0 h/日",
    }.get(channel)
    return band or f"未建模（{unit}）"


def get_device_status(site_id: str, as_of_date: str) -> str:
    """设备/执行机构状态与故障码（演示场景固定含一处阀门告警）。"""
    return "\n".join([
        _header(f"设备状态 @ {site_id}，基准日 {as_of_date}"),
        "设备清单（编号 | 类型 | 在线 | 故障码 | 最近动作）：",
        f"- PUMP-02 | 灌溉泵 | 在线 | 无 | {as_of_date} 06:05 启动，06:41 停止（手动）",
        f"- VALVE-11 | 滴灌阀门（A 组） | 在线 | **E04 开阀无流量反馈** | {as_of_date} 06:12 开阀失败，已自动回关",
        "- FAN-03 | 环控风机 | **离线**（通信中断 3 天） | 通信超时 | 2026-09-23 18:55 停止",
        "- SENSOR-10 | 土壤墒情探头（10cm） | 在线 | 无 | 每小时上报，最近一次成功",
        "",
        "说明：E04 与风机离线的处置要点见设备手册工具（演示场景已内置对应条目）。",
    ])


def get_weather_forecast(site_id: str, start_date: str, end_date: str) -> str:
    """气象实况 + 基准日当天发布的短期预报（确定性合成）。"""
    days = _parse_window(start_date, end_date)
    lines = [
        _header(f"气象 @ {site_id}"),
        f"窗口：{start_date} ~ {end_date}（{len(days)} 天）",
        "",
        "实况（基准日及以前）：",
    ]
    total_rain = 0.0
    for day in days:
        rng = _rng(site_id, "weather", day)
        rain = round(rng.choice([0.0, 0.0, 0.0, 0.6, 0.2]), 1)
        total_rain += rain
        low = 20 + rng.randint(0, 3)
        high = 28 + rng.randint(0, 3)
        wind = rng.choice([2, 2, 3])
        lines.append(
            f"{day.isoformat()}  气温 {low}~{high} °C，降水 {rain} mm，风力 {wind} 级，"
            "湿度 80%~92%，无预警"
        )
    next_day = date.fromisoformat(str(end_date)) + timedelta(days=1)
    day_after = next_day + timedelta(days=1)
    lines += [
        f"窗口累计降水：{round(total_rain, 1)} mm（对滴灌的补充有限）",
        "",
        "基准日当天发布的短期预报（仅作排程参考，标注为预报值）：",
        f"- {next_day.isoformat()}：中雨 18 mm，气温 20~26 °C，风力 3 级，无预警",
        f"- {day_after.isoformat()}：阵雨 6 mm，气温 19~25 °C，风力 3~4 级",
        "- 提示：风力 ≥4 级或 24 小时内 ≥10 mm 降水时，喷施类作业应改期（演示口径）。",
    ]
    return "\n".join(lines)


def get_camera_snapshot(site_id: str, as_of_date: str) -> str:
    """影像判读结论（演示场景：下部叶片水渍状斑点）。"""
    ratio = 10 + _rng(site_id, "vision", DEMO_ANCHOR).randint(0, 8)
    return "\n".join([
        _header(f"影像判读 @ {site_id}，基准日 {as_of_date}"),
        f"拍摄：{as_of_date} 07:40，东侧田头俯拍（可见光，4160×3120，已存证）",
        "判读结论：",
        f"- 下部叶片出现**暗绿色水渍状不规则斑点**，多自叶缘与叶尖起始，"
        f"受影响的叶面积约 {ratio}%",
        "- 相邻 3~5 株可见相似症状，呈点片状分布；顶部新叶与果实暂未见异常",
        "- 未见虫体、虫粪、蜜露或明显咬食痕迹",
        "- 叶面在 07:40 仍有结露（与高湿读数一致）",
        "边界说明：以上为合成演示的判读结论，不含原始影像；"
        "真实接入时本层应返回模型判读结果与影像存档编号。",
    ])


def get_remote_sensing_index(site_id: str, index: str, start_date: str, end_date: str) -> str:
    """遥感指数序列（演示：NDVI 缓降、NDWI 走低）。"""
    days = _parse_window(start_date, end_date)
    anchor_value = {"ndvi": 0.63, "ndwi": 0.10}.get(str(index).lower(), 0.5)
    slope = {"ndvi": -0.010, "ndwi": -0.008}.get(str(index).lower(), -0.005)
    lines = [
        _header(f"遥感指数 {index} @ {site_id}"),
        f"窗口：{start_date} ~ {end_date}（{len(days)} 天，逐日值，重访日无数据时以前值补齐）",
        "",
    ]
    for day in days:
        value = anchor_value + slope * max(
            -_MAX_WINDOW_DAYS, min(_MAX_WINDOW_DAYS, (day - DEMO_ANCHOR).days)
        )
        noise = _rng(site_id, f"rs_{index}", day).uniform(-1, 1) * 0.01
        lines.append(f"{day.isoformat()}  {round(value + noise, 3)}")
    lines += [
        "",
        "相对历史同期（演示口径）：NDVI 低于近三年同期均值约 0.09，NDWI 低于同期均值约 0.05；"
        "趋势连续下行，非单日波动。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 资源与安全规则
# ---------------------------------------------------------------------------


def get_resource_inventory(site_id: str) -> str:
    """人力 / 机具 / 农资库存（演示账本）。"""
    return "\n".join([
        _header(f"资源账本 @ {site_id}"),
        "- 人力：2 人（上午可调度；下午需支援其他地块）",
        "- 机具：3 号植保机（可用，药箱 400 L）；滴灌阀门 A 组（含告警阀 VALVE-11）；"
        "背负式喷雾器 2 台",
        "- 农资：生物农药 X 12 L（演示）；叶面肥 Y 库存不足（0.5 L，需补货）",
        "- 备件：电磁阀线圈 ×1（适配 VALVE-11）",
        "",
        "说明：以上数量为演示值；真实接入时应返回带批次与有效期的库存台账。",
    ])


_FORBIDDEN = ("毒死蜱", "百草枯", "甲胺磷", "克百威")
_INTERVAL_RULES = {
    "生物农药 X": "施药后须 ≥3 天方可采收（演示口径，真实间隔期以登记标签为准）",
}


def check_safety_policy(action: str, materials: str) -> str:
    """对照演示规则库检查作业方案；含禁止性冲突时必须明确说出。"""
    action = str(action or "")
    materials = str(materials or "")
    hits: list[str] = []
    blocked: list[str] = []

    for name in _FORBIDDEN:
        if name in materials:
            blocked.append(f"R-101 禁限用物资：{name} 属禁止使用清单（演示清单），不得用于任何作物")
    for name, rule in _INTERVAL_RULES.items():
        if name in materials:
            hits.append(f"R-201 安全间隔期：{name} —— {rule}")
    if any(word in action for word in ("喷", "施药", "喷雾")):
        hits.append("R-301 气象条件：风力 ≥4 级或 24 小时内 ≥10 mm 降水预报时禁止喷施（演示口径）")
        hits.append("R-401 人员防护：喷施作业须佩戴口罩、护目镜、长袖长裤，作业后清洗暴露部位")
    if any(word in action for word in ("灌溉", "滴灌", "浇水")) and "药" in materials:
        hits.append("R-202 顺序约束：同一天内不得先施药后灌溉（药液会被冲刷），灌溉应在施药前完成")
    if any(word in action for word in ("检修", "带电", "电气", "拆")):
        hits.append("R-501 设备检修：涉及电气部件必须断电挂牌上锁（LOTO）并验电后作业；"
                    "管路检修前须关闭支路阀门并泄压")
    if any(word in action for word in ("清园", "整枝", "摘除", "拔除")):
        hits.append("R-601 病残体处置：摘除的病叶病果须装袋带出田块销毁，不得就地丢弃")

    lines = [
        _header("安全规则检查"),
        f"规则库版本：{SAFETY_RULES_VERSION}",
        f"检查对象：动作={action or '（未填写）'}；涉及物资={materials or '无'}",
        "",
        "命中条款：" if (hits or blocked) else "命中条款：无（本次动作未触发演示规则库中的条款）",
    ]
    lines += [f"- {item}" for item in blocked + hits]
    if blocked:
        lines += ["", f"结论：**存在禁止性冲突**（{len(blocked)} 条），不得执行。" ]
    elif hits:
        lines += ["", "结论：无禁止性冲突；但须逐条落实上方控制措施后方可执行。"]
    else:
        lines += ["", "结论：无禁止性冲突，未命中额外控制措施。"]
    lines += ["", "说明：本规则库为演示版本，条款不构成农艺或合规依据。"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 执行网关（本地台账，不连接任何物理设备）
# ---------------------------------------------------------------------------


def _ledger_path(site_id: str) -> Path:
    cache_dir = Path(str(get_config().get("data_cache_dir") or "."))
    target = cache_dir / "actuator_ledger"
    target.mkdir(parents=True, exist_ok=True)
    return target / f"{safe_site_component(site_id)}.jsonl"


def _append(record: dict) -> None:
    with _ledger_path(record["site_id"]).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _task_id(site_id: str, work_order_id: str, actuator: str, command: str, params: str) -> str:
    key = f"{site_id}|{work_order_id}|{actuator}|{command}|{params}".encode("utf-8")
    return "SYN-" + hashlib.blake2s(key, digest_size=4).hexdigest().upper()


def dispatch_command(
    site_id: str, actuator: str, command: str, params: str, work_order_id: str
) -> str:
    """合成执行网关：``dry_run`` 只留痕；实发模式只写本地台账（无物理设备）。"""
    dry_run = bool(get_config().get("dry_run", True))
    record = {
        "site_id": site_id,
        "actuator": actuator,
        "command": command,
        "params": params,
        "work_order_id": work_order_id,
        "mode": "dry-run" if dry_run else "live",
        "dispatched": not dry_run,
    }
    if dry_run:
        _append(record)
        return (
            f"[{VENDOR_NAME}][dry-run] 已记录但**未下发**："
            f"工单={work_order_id} 对象={site_id} 机构={actuator} 指令={command} 参数={params}"
        )
    task_id = _task_id(site_id, work_order_id, actuator, command, params)
    record["task_id"] = task_id
    _append(record)
    return (
        f"[{VENDOR_NAME}] 已下发到**合成执行网关**（无物理设备，仅本地台账）："
        f"task_id={task_id} 工单={work_order_id} 对象={site_id} 机构={actuator} "
        f"指令={command} 参数={params}"
    )


def get_execution_status(site_id: str, task_id: str) -> str:
    """查询合成网关的执行状态。"""
    path = _ledger_path(site_id)
    if not path.exists():
        return f"[{VENDOR_NAME}] {site_id} 暂无下发记录（台账为空）。"
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("task_id") != task_id:
            continue
        if record.get("mode") == "dry-run":
            return f"[{VENDOR_NAME}] {task_id} 为演练记录，未实际下发，无现场回执。"
        return (
            f"[{VENDOR_NAME}] {task_id} 回执：合成网关已受理并执行完成"
            f"（{record.get('actuator')} / {record.get('command')}）；"
            "现场效果需按验收标准复检后回填。"
        )
    return f"[{VENDOR_NAME}] 未找到任务 {task_id} 的下发记录。"

