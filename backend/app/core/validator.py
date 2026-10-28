"""硬校验器：金额/中文数字/日期解析与校验。

AI 输出永远不可直接信任，这里是规则兜底：
- 金额：支持中文数字（三百五→350）、单位（万/千/百/块/毛/角/分）、阿拉伯数字
- 日期：支持今天/昨天/前天/N天前/上周/8月3号/2026-8-3 等
- 所有解析失败都抛 ValueError，调用方进入澄清流程，绝不落账。
"""

import re
from datetime import date as date_cls, timedelta
from decimal import Decimal, InvalidOperation

# ---------- 中文数字 ----------

_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 10 ** 8}
_CURRENCY = {"块": 0, "元": 0, "块钱": 0, "毛": 0.1, "角": 0.1, "分": 0.01}

_CN_NUM_RE = re.compile(r"[0-9，,.\s零〇一二三四五六七八九十百千万亿两]")
_AMOUNT_RUN_RE = re.compile(r"[0-9，,.零〇一二三四五六七八九十百千万亿两点块元角毛分]+")
_CURRENCY_CHAR_RE = re.compile(r"[块元毛角分]")
_CN_UNIT_RE = re.compile(r"[十百千万亿]")

# 块/元 + 毛/角 + 分 的货币写法
_MONEY_RE = re.compile(
    r"([0-9，,零〇一二三四五六七八九十百千万亿两]+)?[块元]"
    r"(?:([0-9零〇一二三四五六七八九]{1,2})[毛角])?"
    r"(?:([0-9零〇一二三四五六七八九]{1,2})分)?"
)
# 只有毛/角/分，没有块/元（如 5分）
_MONEY_FRAC_RE = re.compile(
    r"([0-9零〇一二三四五六七八九]{1,2})[毛角](?:([0-9零〇一二三四五六七八九]{1,2})分)?"
    r"|([0-9零〇一二三四五六七八九]{1,2})分"
)


def _parse_cn_number(s: str) -> Decimal:
    """把中文数字串解析为 Decimal。仅处理纯数字表达，不含货币字。"""
    s = s.replace("，", "").replace(",", "").replace(" ", "")
    if not s:
        raise ValueError("空金额")
    # 纯阿拉伯数字（含小数点）
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", s):
        return Decimal(s)

    total = Decimal(0)
    section = Decimal(0)   # 当前万级小节的值
    num: int | None = None
    last_unit = 0
    zero_after_last_unit = False
    frac_digits: list[int] = []  # 小数部分
    in_frac = False

    for ch in s:
        if ch in ("点", "."):
            if num is not None:
                section += num  # 先提交点前的整数部分
                num = None
            in_frac = True
            continue
        if in_frac:
            # 小数模式：累积后续数字
            if ch in _DIGITS:
                frac_digits.append(_DIGITS[ch])
            else:
                raise ValueError(f"小数部分含非法字符: {ch!r}")
            continue
        if ch in ("零", "〇"):
            num = 0
            zero_after_last_unit = True
        elif ch in _DIGITS:
            if num is not None and last_unit == 0:
                num = num * 10 + _DIGITS[ch]  # 连续阿拉伯数字拼接
            else:
                num = _DIGITS[ch]
        elif ch in _UNITS:
            unit = _UNITS[ch]
            if unit in (10 ** 4, 10 ** 8):
                # 万/亿：把当前小节整体放大，不重复乘
                total = (total + section + (num or 0)) * unit
                section = Decimal(0)
            else:
                section += (num if num is not None else 1) * unit
            num = None
            last_unit = unit
            zero_after_last_unit = False
        else:
            raise ValueError(f"无法识别的数字字符: {ch!r}")

    # 口语省略：三百五 = 350（尾随数字 × 末位单位/10），中间出现"零"则不省略
    if num is not None and last_unit in (100, 1000, 10000) and not zero_after_last_unit:
        section += num * Decimal(last_unit // 10)
        num = None

    result = total + section + (num or 0)
    if frac_digits:
        result += Decimal(sum(d * (10 ** (len(frac_digits) - 1 - i)) for i, d in enumerate(frac_digits))) / Decimal(10 ** len(frac_digits))
    return result


def parse_amount(text: str) -> Decimal:
    """从文本中解析金额（正数）。找不到或非法抛 ValueError。"""
    if not text:
        raise ValueError("没有金额")

    runs = _AMOUNT_RUN_RE.findall(text)

    # 1) 优先含货币字（块/元/毛/角/分）的片段
    currency_runs = [r for r in runs if _CURRENCY_CHAR_RE.search(r)]
    if currency_runs:
        chosen = max(currency_runs, key=lambda r: len(r))
        # 12块5毛 / 350块 / 5分 / 12元5角
        m = _MONEY_RE.match(chosen)
        if m and m.group(1):
            value = _parse_cn_number(m.group(1))
            if m.group(2):
                value += _parse_cn_number(m.group(2)) * Decimal("0.1")
            if m.group(3):
                value += _parse_cn_number(m.group(3)) * Decimal("0.01")
            if value > 0:
                return value
        m = _MONEY_FRAC_RE.match(chosen)
        if m:
            value = Decimal(0)
            if m.group(1):  # X毛[Y分]
                value += _parse_cn_number(m.group(1)) * Decimal("0.1")
                if m.group(2):
                    value += _parse_cn_number(m.group(2)) * Decimal("0.01")
            elif m.group(3):  # X分
                value += _parse_cn_number(m.group(3)) * Decimal("0.01")
            if value > 0:
                return value
        # 兜底：纯块/元数值
        num = _parse_cn_number(re.sub(r"[块元毛角分]", "", chosen))
        if num > 0:
            return num
        raise ValueError(f"金额必须为正数: {text!r}")

    # 2) 含中文单位的片段（三百五 / 3万5）
    cn_unit_runs = [r for r in runs if _CN_UNIT_RE.search(r)]
    if cn_unit_runs:
        chosen = max(cn_unit_runs, key=lambda r: len(r))
        num = _parse_cn_number(chosen)
        if num <= 0:
            raise ValueError(f"金额必须为正数: {text!r}")
        return num

    # 3) 最大数值片段（"是480" / "付了350"）
    candidates = []
    for r in runs:
        try:
            v = _parse_cn_number(r)
            if v > 0:
                candidates.append(v)
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f"未找到有效金额: {text!r}")
    return max(candidates)


def format_amount(d: Decimal) -> str:
    """统一金额展示：两位小数，千分位分隔。"""
    return f"{d:,.2f}"


# ---------- 日期 ----------

_WEEKDAY = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def _parse_iso(s: str) -> date_cls | None:
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s.strip())
    if m:
        y, mo, d = map(int, m.groups())
        return date_cls(y, mo, d)
    return None


def parse_date(text: str, today: date_cls) -> date_cls | None:
    """解析相对/绝对日期；无法解析返回 None（不抛错，让 AI 澄清）。"""
    if not text:
        return None
    t = text.strip()

    # 绝对日期
    iso = _parse_iso(t)
    if iso:
        return iso

    # 今天/昨天/前天/大前天（大前天要在大前天检查前，避免"前天"误匹配）
    if "大前天" in t:
        return today - timedelta(days=3)
    if "今天" in t or "今日" in t:
        return today
    if "昨天" in t or "昨日" in t:
        return today - timedelta(days=1)
    if "前天" in t:
        return today - timedelta(days=2)

    # N天前
    m = re.search(r"(\d+|[一二三四五六七八九十]+)\s*天?前", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _parse_cn_number(m.group(1))
        return today - timedelta(days=int(n))

    # 上周X / 上星期X / 上礼拜X
    m = re.search(r"(?:上周|上星期|上礼拜|上个礼拜)([一二三四五六日天])", t)
    if m:
        target = _WEEKDAY[m.group(1)]
        last_week_monday = today - timedelta(days=today.weekday() + 7)
        return last_week_monday + timedelta(days=target - 1)
    if re.search(r"(?:上周|上星期|上礼拜|上个礼拜|上周)", t):
        return today - timedelta(days=7)

    # 上个月（返回上个月 1 号）
    if "上个月" in t or "上月" in t:
        prev = today.replace(day=1) - timedelta(days=1)
        return prev.replace(day=1)

    # 8月3号 / 8月3日 / 3号 / 3日
    m = re.search(r"(?:(\d{1,2}|[一二三四五六七八九十]{1,3})月)?(\d{1,2}|[一二三四五六七八九十]{1,3})[号日]", t)
    if m:
        month_s, day_s = m.groups()
        day = int(day_s) if day_s.isdigit() else int(_parse_cn_number(day_s))
        if month_s:
            month = int(month_s) if month_s.isdigit() else int(_parse_cn_number(month_s))
            d = date_cls(today.year, month, day)
            if d > today:
                d = date_cls(today.year - 1, month, day)
            return d
        d = date_cls(today.year, today.month, day)
        if d > today:
            # 这个月没有这天 → 上个月
            prev = today.replace(day=1) - timedelta(days=1)
            try:
                return date_cls(prev.year, prev.month, day)
            except ValueError:
                return None
        return d

    return None


def is_future(d: date_cls, today: date_cls) -> bool:
    return d > today


# ---------- 时刻 ----------

_TIME_RE = re.compile(
    r"(上午|中午|下午|晚上|早上|凌晨)?\s*"
    r"([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[点時时]"
    r"(半|([0-9]{1,2}|[一二三四五六七八九十]{1,3})分)?"
)
_PM = ("下午", "晚上")
_AM = ("上午", "早上", "凌晨")


def parse_time_of_day(text: str) -> int | None:
    """从口语里解析出「几点」（0-23）。解析不出来返回 None。

    用于按时间精确定位一笔账，比如「昨天下午3点那笔水管记错了」。
    下午/晚上 的 1-11 点会 +12；中午12点保持12。
    """
    if not text:
        return None
    m = _TIME_RE.search(text)
    if not m:
        return None
    period, hour_s = m.group(1), m.group(2)
    try:
        hour = int(hour_s) if hour_s.isdigit() else int(_parse_cn_number(hour_s))
    except ValueError:
        return None
    if not 0 <= hour <= 24:
        return None

    if period in _PM and hour < 12:
        hour += 12
    elif period == "中午" and hour < 12:
        hour = 12 if hour <= 1 else hour
    elif period in _AM and hour == 12:
        hour = 0

    # 只说「3点」不带时段：早 6 点到晚 11 点之间，按最合理的理解
    return hour % 24 if hour == 24 else hour


def parse_time_hm(text: str) -> str | None:
    """解析成 "HH:MM"；分钟解析不出来就补 ":00"。"""
    hour = parse_time_of_day(text)
    if hour is None:
        return None
    m = _TIME_RE.search(text)
    minute = 0
    if m and m.group(3) == "半":
        minute = 30
    elif m and m.group(4):
        g = m.group(4)
        try:
            minute = int(g) if g.isdigit() else int(_parse_cn_number(g))
        except ValueError:
            minute = 0
    if not 0 <= minute <= 59:
        minute = 0
    return f"{hour:02d}:{minute:02d}"
