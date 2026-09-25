"""供应商适配器包。

每个模块对应一类数据源，函数签名就是它对框架的**契约**：

- 入参已由工具层做过 as-of 夹紧，适配器只负责取数与格式化；
- 返回值为给 LLM 读的字符串：结构化、带单位、带时间戳、缺测要显式标注；
- 取不到数据时抛 ``dataflows.errors`` 里的类型化异常，不要返回空字符串——
  空字符串会被模型读成「没有异常」。

现在全部是占位实现（返回 ``[stub:...]`` 文本）。接真实数据源时：
新增一个模块 → 在 ``router.VENDOR_METHODS`` 注册 → 改配置里的 ``data_vendors``。
"""

from agriagents.dataflows.vendors import actuators, imagery, knowledge, ops, sensors, weather

__all__ = ["actuators", "imagery", "knowledge", "ops", "sensors", "weather"]
