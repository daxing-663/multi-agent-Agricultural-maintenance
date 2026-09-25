"""气象数据适配器（占位）。

TODO(内容): 接入气象服务（实况 + 预报 + 预警），注意区分
「预报」与「实况」两类数据——回溯场景只允许使用基准日当天已知的那一版预报。
"""

from __future__ import annotations

VENDOR_NAME = "stub_weather"
_PENDING = "未接入真实数据源（框架占位）。"


def get_weather_forecast(site_id: str, start_date: str, end_date: str) -> str:
    """读取窗口内的气象数据（温度、降水、风、湿度、极端天气预警）。"""
    return (
        f"[{VENDOR_NAME}] 气象 @ {site_id}，窗口 {start_date} ~ {end_date}：{_PENDING}\n"
        "契约：逐日给出温度区间、降水量、风力、预警等级，并标注数据是预报还是实况。"
    )
