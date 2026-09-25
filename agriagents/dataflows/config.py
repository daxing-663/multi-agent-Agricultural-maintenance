"""运行期配置作用域。

同一个进程里可能同时跑多个案件（多地块并发巡检），每个图绑定自己的配置，
它调用的数据工具读到的是**这次 run 的**数据源配置。

实现方式与 TradingAgents 一致：用 ``ContextVar`` 承载"进行中 run 的配置"，
LangGraph 会把上下文带进工具调用；没有 run 作用域时回落到进程级默认配置。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

import agriagents.default_config as default_config

_config: dict | None = None

_run_config: ContextVar[dict | None] = ContextVar("agriagents_run_config", default=None)


def initialize_config():
    """用默认值初始化进程级配置（幂等）。"""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)


def _merge(base: dict, config: dict) -> dict:
    """把 ``config`` 合并进 ``base``：字典型键做一层合并，标量直接替换。"""
    for key, value in deepcopy(config).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def set_config(config: dict):
    """更新进程级配置。

    ``data_vendors`` 这类字典型键做一层合并，因此
    ``{"data_vendors": {"sensor_data": "real_sensors"}}`` 不会丢掉其它类别。
    """
    initialize_config()
    _merge(_config, config)


@contextmanager
def run_config(config: dict):
    """在 with 块内，把所有配置读取指向 ``config``（覆盖在默认值之上）。"""
    token = _run_config.set(_merge(deepcopy(default_config.DEFAULT_CONFIG), config))
    try:
        yield
    finally:
        _run_config.reset(token)


def get_config() -> dict:
    """返回进行中 run 的配置，否则返回进程级配置。"""
    scoped = _run_config.get()
    if scoped is not None:
        return deepcopy(scoped)
    if _config is None:
        initialize_config()
    return deepcopy(_config)


initialize_config()
