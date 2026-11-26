"""时间轴与半开区间。

资料中所有起止日期都是 ISO 字符串，归属与有效期统一按半开区间
``[begin, end)`` 判断：生效日当天有效，结束日当天不再有效。
空值（``None``）表示该侧无界，``classrooms.switch_lesson`` 的空值
另有语义（本学期未声明可切换课次），在本模块之外解释。
"""

from datetime import date, datetime


def parse_day(value):
    """把 ISO 日期字符串解析为 :class:`date`，原样透传 None。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"无法识别的日期: {value!r}")
    return datetime.strptime(value, "%Y-%m-%d").date()


def on_day(value, day):
    """把入参统一成 date，便于在领域代码里比较。"""
    if not isinstance(day, date):
        day = parse_day(day)
    if isinstance(value, date):
        return day
    return day


def within(day, begin, end):
    """``begin <= day < end``，None 表示该侧无界。"""
    day = parse_day(day) if not isinstance(day, date) else day
    left = parse_day(begin)
    right = parse_day(end)
    if left is not None and day < left:
        return False
    if right is not None and day >= right:
        return False
    return True


def active_window(begin, end, day):
    """记录是否在半开窗口内，返回布尔值。"""
    return within(day, begin, end)


def earlier(a, b):
    """两个 ISO 日期/None 中较早的一个，None 视为无界靠后。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if parse_day(a) <= parse_day(b) else b
