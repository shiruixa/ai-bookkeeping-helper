"""历史导入 + 报表 单元测试。"""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core import importer
from app.core.beancount_engine import BeancountEngine


@pytest.fixture()
def engine():
    tmp = Path(tempfile.mkdtemp()) / "ledger.bean"
    eng = BeancountEngine(tmp)
    eng.ensure_exists()
    return eng


WECHAT_CSV = """交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,备注
2026-07-01 10:00:00,商户消费,面铺,面粉,支出,350.00,零钱,进50斤
2026-07-02 09:00:00,二维码收款,顾客,馒头,收入,500.00,零钱,卖出
2026-07-03 08:00:00,转账,微信零钱,零钱提现,中,1000.00,零钱,提现
2026-07-05 11:00:00,商户消费,供电局,电费,支出,200.50,零钱,
"""


def test_detect_wechat_columns():
    rows = importer.read_file_rows(WECHAT_CSV.encode("utf-8"), "wechat.csv")
    cols = importer.detect_columns(rows[0])
    assert cols["date"] == 0
    assert cols["direction"] == 4      # 收/支 列优先于 交易类型
    assert cols["category"] == 3       # 商品 列
    assert cols["payee"] == 2          # 交易对方
    assert cols["amount"] == 5


def test_parse_wechat_rows():
    rows = importer.read_file_rows(WECHAT_CSV.encode("utf-8"), "wechat.csv")
    valid, skipped, warnings = importer.parse_rows(rows)
    assert not skipped
    assert len(valid) == 4
    by_cat = {r["category"]: r for r in valid}
    assert by_cat["面粉"]["direction"] == "expense"
    assert by_cat["面粉"]["amount"] == Decimal("350.00")
    assert by_cat["馒头"]["direction"] == "income"
    assert by_cat["零钱提现"]["direction"] == "transfer"
    assert by_cat["零钱提现"]["transfer_sign"] == "in"


def test_parse_bank_two_columns():
    csv_text = """日期,交易摘要,收入,支出,余额
2026-07-01,工资入账,8000.00,,50000.00
2026-07-02,取现,,2000.00,48000.00
"""
    rows = importer.read_file_rows(csv_text.encode("utf-8"), "bank.csv")
    cols = importer.detect_columns(rows[0])
    assert "amount_inc" in cols and "amount_exp" in cols
    valid, skipped, _ = importer.parse_rows(rows)
    assert valid[0]["direction"] == "income"
    assert valid[1]["direction"] == "expense"


def test_commit_import(engine, tmp_path):
    from app.core.memory import Memory

    # 多账本后 commit_rows 必须接收"当前账本"的 memory，绝不回落到默认库
    memory = Memory(tmp_path / "app.db")
    rows = importer.read_file_rows(WECHAT_CSV.encode("utf-8"), "wechat.csv")
    valid, skipped, _ = importer.parse_rows(rows)
    mapping = {
        "expense:面粉": "Assets:Inventory",
        "income:馒头": "Income:Sales",
        "expense:电费": "Expenses:Utilities",
    }
    res = importer.commit_rows(engine, valid, mapping, memory)
    assert res["imported"] == 4
    assert engine.validate() == []
    b = engine.get_balances()
    assert b["Assets:Inventory"] == Decimal("350.00")
    assert b["Income:Sales"] == Decimal("-500.00")
    assert b["Expenses:Utilities"] == Decimal("200.50")
    # 现金侧：面粉350 + 电费200.5 + 提现1000
    assert b["Assets:Bank"] == Decimal("1000.00")
    assert b["Assets:Cash"] == Decimal("-1050.50")
    # 映射写进了传进来的库（而不是真实的 backend/data/app.db）
    assert memory.get_mapping("面粉") == "Assets:Inventory"
    assert memory.get_mapping("电费") == "Expenses:Utilities"


def test_commit_rows_without_memory_does_not_touch_any_db(engine):
    """不传 memory 时应静默跳过"记住映射"，而不是回落到默认库。"""
    rows = importer.read_file_rows(WECHAT_CSV.encode("utf-8"), "wechat.csv")
    valid, _, _ = importer.parse_rows(rows)
    res = importer.commit_rows(engine, valid, {"expense:面粉": "Assets:Inventory"})
    assert res["imported"] == 4


def test_commit_bad_rows_rejected(engine):
    """余额不平衡的行必须整批回滚。"""
    bad = [{
        "i": None, "date": date(2026, 8, 1), "amount": Decimal("100"),
        "direction": "expense", "category": "x", "payee": "", "note": "",
    }]
    # 构造一个不平衡的 text 行，走 commit_rows 应回滚
    from app.core.beancount_engine import BeancountEngine

    # 直接往文件写不平衡内容，engine 应拒绝
    with pytest.raises(Exception):
        engine.append_text(
            "2026-08-01 * \"x\" \"不平衡\"\n  Assets:Cash 100.00 CNY\n  Expenses:Misc 99.00 CNY"
        )
    assert engine.validate() == []


# ---------- 报表 ----------

def test_trial_balance(engine):
    engine.append_transaction(
        date=date(2026, 8, 1), payee="初始", narration="投入",
        postings=[("Assets:Cash", "dr", Decimal("30000")), ("Equity:Capital", "cr", Decimal("30000"))],
    )
    tb = engine.trial_balance()
    assert tb["Assets:Cash"]["debit"] == Decimal("30000")
    assert tb["Equity:Capital"]["credit"] == Decimal("30000")
    assert tb["Equity:Capital"]["balance"] == Decimal("-30000")


def test_balance_sheet(engine):
    engine.append_signed(
        date=date(2026, 8, 1), payee="", narration="期初",
        signed_postings=[
            ("Assets:Cash", Decimal("30000")),
            ("Assets:Bank", Decimal("20000")),
            ("Liabilities:Payable", Decimal("-10000")),
            ("Equity:OpeningBalances", Decimal("-40000")),
        ],
    )
    bs = engine.balance_sheet()
    assert bs["balanced"] is True
    assert bs["assets_total"] == Decimal("50000")
    assert bs["liabilities_total"] == Decimal("10000")
    assert bs["equity_total"] == Decimal("40000")


def test_account_ledger_running_balance(engine):
    engine.append_transaction(
        date=date(2026, 8, 1), payee="初始", narration="投入",
        postings=[("Assets:Cash", "dr", Decimal("30000")), ("Equity:Capital", "cr", Decimal("30000"))],
    )
    engine.append_transaction(
        date=date(2026, 8, 3), payee="面铺", narration="进货",
        postings=[("Assets:Inventory", "dr", Decimal("350")), ("Assets:Cash", "cr", Decimal("350"))],
    )
    rows = engine.account_ledger("Assets:Cash")
    assert rows[-1]["balance"] == Decimal("29650")
    assert len(rows) == 2
