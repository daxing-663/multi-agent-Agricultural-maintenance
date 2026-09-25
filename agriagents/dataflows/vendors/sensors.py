"""传感器读数与设备状态适配器（占位）。

TODO(内容): 接入真实来源，例如
- 物联网平台（MQTT / HTTP 网关）的土壤墒情、气象站、水质探头；
- 环境控制柜的运行状态与故障码；
- 农机 CAN 总线 / 车载终端。
"""

from __future__ import annotations

VENDOR_NAME = "stub_sensors"
_PENDING = "未接入真实数据源（框架占位）。"


def get_sensor_readings(site_id: str, channel: str, start_date: str, end_date: str) -> str:
    """读取一个传感器通道的时间序列。"""
    return (
        f"[{VENDOR_NAME}] 通道 {channel} @ {site_id}，窗口 {start_date} ~ {end_date}：{_PENDING}\n"
        "契约：返回按时间排序的读数列表（时间、数值、单位、缺测标记）与区间统计。"
    )


def get_device_status(site_id: str, as_of_date: str) -> str:
    """读取设备/执行机构的运行状态与故障码。"""
    return (
        f"[{VENDOR_NAME}] 设备状态 @ {site_id}，基准日 {as_of_date}：{_PENDING}\n"
        "契约：返回设备清单（编号、类型、在线状态、故障码、最近动作）。"
    )
