"""数据源错误分类。

统一一套继承体系，让路由层按**行为**而不是按供应商来反应：
凡是"供应商无法返回可用数据"的情况都派生自 ``VendorError``，
路由层只捕获基类。新增供应商只需抛这些异常（或它们的细分子类），
不需要加新的 ``except`` 分支。

    VendorError
    ├── NoDataError             无可用数据（空结果或数据过期）→ 试下一家
    ├── VendorRateLimitError    限流/暂时不可用             → 试下一家
    └── VendorNotConfiguredError 缺密钥/缺配置              → 视为该家不可用

类型数量 = 路由层**不同的处理方式**数量，而不是人类能描述的原因数量。
"""

from __future__ import annotations


class VendorError(Exception):
    """供应商无法返回可用数据的基类。"""


class NoDataError(VendorError):
    """该对象在此窗口内没有可用数据（空结果或数据过期）。"""

    def __init__(self, subject: str, vendor: str = "", detail: str = ""):
        self.subject = subject
        self.vendor = vendor
        self.detail = detail
        msg = f"无可用数据：{subject!r}"
        if vendor:
            msg += f"（供应商 {vendor}）"
        if detail:
            msg += f"：{detail}"
        super().__init__(msg)


class VendorRateLimitError(VendorError):
    """限流或暂时不可用；换下一家即可，不必中止整个 run。"""


class VendorNotConfiguredError(VendorError):
    """供应商未配置（缺 API key / 缺端点），等同于该家不可用。"""
