"""Beancount 引擎封装单元测试。"""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data

from app.core.beancount_engine import BeancountEngine, LedgerError


@pytest.fixture()
def engine():
    tmp = Path(tempfile.mkdtemp()) / "ledger.bean"
    eng = BeancountEngine(tmp)
    eng.ensure_exists()
    return eng


def test_empty_ledger_valid(engine):
    assert engine.validate() == []


def test_append_and_balances(engine):
    engine.append_transaction(
        date=date(2026, 8, 1), payee="初始资金", narration="开店投入",
        postings=[("Assets:Cash", "dr", Decimal("30000")), ("Equity:Capital", "cr", Decimal("30000"))],
    )
    engine.append_transaction(
        date=date(2026, 8, 3), payee="面铺", narration="采购面粉",
        postings=[("Assets:Inventory", "dr", Decimal("350")), ("Assets:Cash", "cr", Decimal("350"))],
    )
    b = engine.get_balances()
    assert b["Assets:Cash"] == Decimal("29650")
    assert b["Assets:Inventory"] == Decimal("350")
    assert b["Equity:Capital"] == Decimal("-30000")


def test_unbalanced_rejected(engine):
    """借贷不平衡必须被拒绝并回滚。"""
    with pytest.raises(LedgerError):
        engine.append_transaction(
            date=date(2026, 8, 3), payee="x", narration="不平衡",
            postings=[("Assets:Cash", "dr", Decimal("100")), ("Expenses:Misc", "cr", Decimal("99"))],
        )
    assert engine.validate() == []  # 回滚后账本仍健康
    assert len(engine.transactions_since(date(2026, 1, 1))) == 0


def test_report_income_expense(engine):
    engine.append_transaction(
        date=date(2026, 8, 1), payee="初始", narration="投入",
        postings=[("Assets:Cash", "dr", Decimal("10000")), ("Equity:Capital", "cr", Decimal("10000"))],
    )
    engine.append_transaction(
        date=date(2026, 8, 2), payee="客", narration="卖货",
        postings=[("Assets:Cash", "dr", Decimal("500")), ("Income:Sales", "cr", Decimal("500"))],
    )
    engine.append_transaction(
        date=date(2026, 8, 3), payee="房东", narration="房租",
        postings=[("Expenses:Rent", "dr", Decimal("2000")), ("Assets:Cash", "cr", Decimal("2000"))],
    )
    ie = engine.report_income_expense(date(2026, 8, 1), date(2026, 8, 31))
    assert ie["income"]["Income:Sales"] == Decimal("500")
    assert ie["expense"]["Expenses:Rent"] == Decimal("2000")


def test_find_and_rewrite(engine):
    # find_transactions 默认只搜最近 N 天，日期必须用「今天」而不是写死的日期，
    # 否则测试会随日历漂移出搜索窗口而变红（曾经的坑）。
    engine.append_transaction(
        date=date.today(), payee="面铺", narration="采购面粉",
        postings=[("Assets:Inventory", "dr", Decimal("350")), ("Assets:Cash", "cr", Decimal("350"))],
    )
    hits = engine.find_transactions("面粉")
    assert len(hits) == 1
    entry, idx = hits[0]

    # 改为 480
    new_postings = []
    for p in entry.postings:
        if p.units is None:
            continue
        sign = 1 if p.units.number > 0 else -1
        new_postings.append(data.Posting(p.account, data.Amount(Decimal("480") * sign, "CNY"), p.cost, p.price, p.flag, p.meta))
    entries, _ = engine.load_or_raise()
    entries[idx] = data.Transaction(
        meta=entry.meta, date=entry.date, flag=entry.flag, payee=entry.payee,
        narration=entry.narration, tags=entry.tags, links=entry.links, postings=new_postings,
    )
    engine.save_entries(entries)
    assert engine.validate() == []
    assert engine.get_balances()["Assets:Inventory"] == Decimal("480")


def test_reversal(engine):
    engine.append_transaction(
        date=date.today(), payee="面铺", narration="采购面粉",
        postings=[("Assets:Inventory", "dr", Decimal("350")), ("Assets:Cash", "cr", Decimal("350"))],
    )
    entry, _ = engine.find_transactions("面粉")[0]
    rev = [(p.account, -p.units.number) for p in entry.postings if p.units]
    engine.append_signed(date=date.today(), payee="", narration="冲销", signed_postings=rev)
    b = engine.get_balances()
    assert b["Assets:Inventory"] == Decimal("0")
    assert b["Assets:Cash"] == Decimal("0")
