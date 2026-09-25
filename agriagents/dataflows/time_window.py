"""as-of 时间窗口：防未来信息泄漏的统一实现。

农业场景里这一点同样关键：8 月 20 日回看 7 月的处置是否合理时，
**不能**让模型看到 8 月才发布的病虫害预警或 8 月才测到的土壤数据，
否则复盘结论会被"事后诸葛亮"污染，学到的教训也是假的。

规则（与 TradingAgents 的 date_window 一致）：

- 所有时间戳归一到 UTC；
- 窗口上界取 ``end`` 之后那天的零点，且为开区间，恰好落在边界的数据不会漏进来；
- 无时间戳的条目，只有窗口一直延伸到"现在"时才保留——回溯场景无法证明它不是未来的。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def to_utc(dt: datetime) -> datetime:
    """把 datetime 归一到 UTC；无时区的按 UTC 处理。"""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def get_current_date() -> str:
    """今天是哪天，YYYY-MM-DD。"""
    return date.today().strftime("%Y-%m-%d")


def in_window(pub_dt: datetime | None, start_dt: datetime, end_dt: datetime) -> bool:
    """条目是否落在半开窗口 ``[start, end + 1 天)`` 内。

    ``pub_dt`` 为 None 表示无时间戳：只有窗口延伸到当下时才保留。
    """
    end = to_utc(end_dt)
    if pub_dt is not None:
        return to_utc(start_dt) <= to_utc(pub_dt) < end + timedelta(days=1)
    return end >= datetime.now(timezone.utc) - timedelta(days=1)


def as_of(value: str, as_of_date: str = "") -> str:
    """把单个日期夹紧到基准日期：不得超过 ``as_of_date``。"""
    if not as_of_date or not value:
        return value
    return min(str(value), str(as_of_date))


def as_of_window(start_date: str, end_date: str, as_of_date: str = "") -> tuple[str, str]:
    """把窗口夹紧到基准日期；夹紧后若窗口倒挂，收敛为一个单日窗口。"""
    start = as_of(start_date, as_of_date)
    end = as_of(end_date, as_of_date)
    if start and end and start > end:
        start = end
    return start, end


def coverage_gap(dates, start_date: str, end_date: str, source: str) -> str | None:
    """某数据源没有完整观测该窗口时，返回说明文本；确实观测过则返回 None。

    厂商接口常常"无论你要哪段都只给最近的几条"。此时"窗口内没有查到"
    不能读成"当时没有发生"——那是在宣称一个没人看到的空白。
    """
    now = datetime.now(timezone.utc)
    oldest = min((to_utc(d) for d in dates if d is not None), default=now)
    if datetime.strptime(end_date, "%Y-%m-%d").date() > now.date():
        reason = "窗口延伸到今天之后"
    elif oldest.date() > datetime.strptime(start_date, "%Y-%m-%d").date():
        reason = "该数据源只提供最近的数据"
    else:
        return None
    return f"（{source} 未覆盖 {start_date} ~ {end_date} 全窗口：{reason}，"
    f"因此「没有记录」不等于「没有发生」。）"
