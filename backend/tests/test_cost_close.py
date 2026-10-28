"""月末成本结转（定期盘存法）测试，含「盘点数比账上多」的冲回场景。"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.beancount_engine import BeancountEngine, LedgerError


@pytest.fixture()
def engine(tmp_path):
    eng = BeancountEngine(tmp_path / "ledger.bean")
    eng.ensure_exists()
    # 期初：仓库里有 10,000 的货
    eng.append_signed(
        date=date(2026, 6, 30), payee="", narration="期初",
        signed_postings=[
            ("Assets:Inventory", Decimal("10000")),
            ("Equity:OpeningBalances", Decimal("-10000")),
        ],
    )
    return eng


def _buy(eng, day, amount):
    eng.append_transaction(
        date=date(2026, 7, day), payee="批发", narration="进货",
        postings=[("Assets:Inventory", "dr", Decimal(amount)),
                  ("Assets:Cash", "cr", Decimal(amount))],
    )


def test_cost_is_opening_plus_purchases_minus_counted(engine):
    """本期销售成本 = 上月结余 + 本月进货 − 期末盘点。"""
    _buy(engine, 1, "5000")
    r = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal("9000"))
    assert r["opening"] == Decimal("10000")
    assert r["purchase_net"] == Decimal("5000")
    assert r["available"] == Decimal("15000")
    assert r["cost"] == Decimal("6000")       # 15000 - 9000
    assert r["is_reversal"] is False


def test_second_close_is_incremental(engine):
    """连续结转：第二次只算「上次之后」这一段，不会重复计成本。"""
    _buy(engine, 1, "5000")
    # 第一次：账上 15,000，数出 12,000 → 成本 3,000，账上变 12,000
    r1 = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal("12000"))
    assert r1["cost"] == Decimal("3000")
    engine.append_cost_close(date(2026, 7, 15), "7月成本结转", r1["cost"])

    # 第二次：账上 12,000，数出 9,000 → 本次成本 3,000（不是整月的 6,000）
    r2 = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal("9000"))
    assert r2["available"] == Decimal("12000")
    assert r2["cost"] == Decimal("3000")
    engine.append_cost_close(date(2026, 7, 25), "7月成本结转", r2["cost"])

    # 累计 = 15,000 − 9,000，与一次算完完全一致
    assert engine.get_balances()["Assets:Inventory"] == Decimal("9000")
    assert engine.get_balances()["Expenses:COGS"] == Decimal("6000")


def test_counting_more_than_book_gives_reversal(engine):
    """盘点数比账上多 = 之前转多了 → 返回负数成本，标记为冲回，而不是报错。"""
    _buy(engine, 1, "5000")
    r = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal("12000"))
    engine.append_cost_close(date(2026, 7, 10), "7月成本结转", r["cost"])
    assert engine.get_balances()["Assets:Inventory"] == Decimal("12000")

    # 发现实际有 14,000（上次估低了 2,000）
    r2 = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal("14000"))
    assert r2["is_reversal"] is True
    assert r2["cost"] == Decimal("-2000")

    engine.append_cost_close(date(2026, 7, 20), "7月成本结转冲回", r2["cost"])
    # 冲回后存货回到 14,000，成本相应减少
    assert engine.get_balances()["Assets:Inventory"] == Decimal("14000")
    assert engine.get_balances()["Expenses:COGS"] == Decimal("1000")   # 15000−14000
    assert engine.validate() == []


def test_zero_cost_is_rejected(engine):
    """盘点数和账上一致 → 没有可结转的，写入应被拒绝。"""
    _buy(engine, 1, "5000")
    r = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal("15000"))
    assert r["cost"] == Decimal("0")
    with pytest.raises(LedgerError):
        engine.append_cost_close(date(2026, 7, 31), "7月成本结转", r["cost"])


def test_find_cost_close_aggregates(engine):
    """find_cost_close 要给出"转了几次 / 累计多少"，前端据此区分"本次"和"本月"。"""
    assert engine.find_cost_close(date(2026, 7, 1), date(2026, 7, 31)) is None

    _buy(engine, 1, "5000")
    for i, (counted, day) in enumerate((("13000", 10), ("11000", 20)), start=1):
        r = engine.inventory_cost_close(date(2026, 7, 1), date(2026, 7, 31), Decimal(counted))
        # links 要和生产路径一致（main.py 传 close-<audit_id>），find_cost_close 靠它识别
        engine.append_cost_close(date(2026, 7, day), "7月成本结转", r["cost"], links=f"close-{i}")

    info = engine.find_cost_close(date(2026, 7, 1), date(2026, 7, 31))
    assert info["count"] == 2
    # 这些字段是给 JSON 用的字符串，比较时转回 Decimal
    assert Decimal(info["total_cost"]) == Decimal("4000")     # 2000 + 2000
    assert Decimal(info["cost"]) == Decimal("2000")           # 最近一次
    assert info["date"] == "2026-07-20"
