"""validator 单元测试：金额 / 中文数字 / 日期解析。"""

from datetime import date

import pytest

from app.core.validator import format_amount, parse_amount, parse_date, is_future


@pytest.mark.parametrize("text,expected", [
    ("350", 350),
    ("350块", 350),
    ("350元", 350),
    ("三百五", 350),
    ("三百五十", 350),
    ("三百零五", 305),
    ("一千五", 1500),
    ("三万五", 35000),
    ("3万5", 35000),
    ("12块5毛", 12.5),
    ("12元5角", 12.5),
    ("5分", 0.05),
    ("5毛", 0.5),
    ("1,200", 1200),
    ("花了1500元进货", 1500),
    ("不是480", 480),
    ("今天买了300块的面粉", 300),
    ("利润五万", 50000),
    ("三点五", 3.5),
])
def test_parse_amount(text, expected):
    assert float(parse_amount(text)) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "你好", "abc", "0", "0块"])
def test_parse_amount_invalid(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_parse_amount_negative_treated_as_noise():
    # 负号不作为金额的一部分（记账金额一律为正数）
    assert float(parse_amount("-5")) == 5


def test_format_amount():
    from decimal import Decimal
    assert format_amount(Decimal("350")) == "350.00"
    assert format_amount(Decimal("1234567.5")) == "1,234,567.50"


@pytest.mark.parametrize("text,expected", [
    ("今天", date(2026, 8, 3)),
    ("昨天", date(2026, 8, 2)),
    ("前天", date(2026, 8, 1)),
    ("大前天", date(2026, 7, 31)),
    ("3天前", date(2026, 7, 31)),
    ("8月5号", date(2025, 8, 5)),   # 今年 8/5 未到 → 去年
    ("8月1号", date(2026, 8, 1)),
    ("5号", date(2026, 7, 5)),      # 本月5号未到 → 上月
    ("2026-8-3", date(2026, 8, 3)),
    ("上周三", date(2026, 7, 29)),  # 8/3 是周一
    ("上个月", date(2026, 7, 1)),
    ("8月3日", date(2026, 8, 3)),
])
def test_parse_date(text, expected):
    assert parse_date(text, date(2026, 8, 3)) == expected


def test_parse_date_none():
    assert parse_date("不知道哪天", date(2026, 8, 3)) is None


def test_is_future():
    assert is_future(date(2026, 8, 10), date(2026, 8, 3)) is True
    assert is_future(date(2026, 8, 3), date(2026, 8, 3)) is False
