"""影像与遥感适配器（占位）。

TODO(内容): 接入摄像头抓拍、无人机巡飞影像、卫星遥感指数服务。
框架约定：本层返回**判读结论或指数序列**，不返回原始图像字节流；
视觉模型推理放在本模块内部完成，对上层只暴露文本结果。
"""

from __future__ import annotations

VENDOR_NAME = "stub_imagery"
_PENDING = "未接入真实数据源（框架占位）。"


def get_camera_snapshot(site_id: str, as_of_date: str) -> str:
    """获取影像判读结论（可见光 / 多光谱）。"""
    return (
        f"[{VENDOR_NAME}] 影像 @ {site_id}，基准日 {as_of_date}：{_PENDING}\n"
        "契约：返回拍摄时间、视角、发现的异常区域及其位置描述；无异常也要明确说无异常。"
    )


def get_remote_sensing_index(site_id: str, index: str, start_date: str, end_date: str) -> str:
    """获取田块尺度的遥感指数序列。"""
    return (
        f"[{VENDOR_NAME}] 遥感指数 {index} @ {site_id}，窗口 {start_date} ~ {end_date}：{_PENDING}\n"
        "契约：返回指数序列与相对历史同期的偏离程度（这是判断胁迫的关键）。"
    )
