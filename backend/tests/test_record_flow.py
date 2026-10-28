"""记账状态机流程测试（用 FakeLLM 模拟 AI，不联网）。"""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.beancount_engine import BeancountEngine
from app.core.memory import Memory
from app.core.validator import parse_amount
from app.flows.chat import ChatFlow
from app.flows.base import FLOW_STATE
from app.llm.schemas import Extraction, TransactionDraft


class FakeLLM:
    """按关键词返回预设抽取结果，模拟 deepseek-v4-flash。"""

    def __init__(self, today: str):
        self.today = today

    def extract(self, user_text: str, today: str, mappings: dict[str, str]) -> Extraction:
        t = user_text
        if "面粉" in t:
            return Extraction(
                intent="record",
                transaction=TransactionDraft(
                    type="expense", amount=Decimal("300"), date=today,
                    category="面粉", account="Assets:Inventory",
                    account_alternatives=["Expenses:COGS"],
                    cash_account="Assets:Cash", confidence="high",
                ),
            )
        if "馒头" in t:
            return Extraction(
                intent="record",
                transaction=TransactionDraft(
                    type="income", amount=Decimal("500"), date=today,
                    category="馒头", account="Income:Sales",
                    account_alternatives=[], cash_account="Assets:Cash", confidence="high",
                ),
            )
        if "赚" in t or "赚了多少" in t:
            return Extraction(intent="query", query=None)
        if "家用" in t or "拿了" in t:
            # 提款：AI 常把"钱在动"判成 transfer，这里刻意复现这个行为
            return Extraction(
                intent="record",
                transaction=TransactionDraft(
                    type="transfer", amount=Decimal("2000"), date=today,
                    category="家用", account="Equity:Drawings",
                    cash_account="Assets:Cash", confidence="high",
                ),
            )
        raise AssertionError(f"FakeLLM 未覆盖输入: {t}")

    def reply(self, messages, temperature=0.3):
        return "好的"


@pytest.fixture()
def flow():
    tmp = Path(tempfile.mkdtemp())
    engine = BeancountEngine(tmp / "ledger.bean")
    engine.ensure_exists()
    memory = Memory(tmp / "app.db")
    llm = FakeLLM(date.today().isoformat())
    cf = ChatFlow(engine, memory, llm)
    return cf, engine, memory


def test_record_confirm_commit(flow):
    cf, engine, memory = flow
    today = date.today()

    # 1. 记录 → 进入确认态
    r1 = cf.handle("今天买了300块面粉")
    assert r1.state == "awaiting_confirm"
    assert r1.kind == "record"
    assert "300.00" in r1.text
    assert "存货" in r1.text

    # 2. 未确认，账本没变化
    assert len(engine.transactions_since(date(2020, 1, 1))) == 0

    # 3. 确认 → 落账
    r2 = cf.handle("确认")
    assert r2.state == "idle"
    txns = engine.transactions_since(date(2020, 1, 1))
    assert len(txns) == 1
    assert engine.get_balances()["Assets:Inventory"] == Decimal("300")
    assert engine.get_balances()["Assets:Cash"] == Decimal("-300")


def test_record_cancel(flow):
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    r = cf.handle("取消")
    assert "不记" in r.text
    assert len(engine.transactions_since(date(2020, 1, 1))) == 0
    assert memory.get_state(FLOW_STATE) is None


def test_record_edit_amount(flow):
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    r = cf.handle("不对，是480")
    assert r.state == "awaiting_confirm"
    assert "480.00" in r.text
    # 确认后金额为 480
    cf.handle("确认")
    assert engine.get_balances()["Assets:Inventory"] == Decimal("480")


def test_edit_by_plain_number(flow):
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    r = cf.handle("480")   # 直接说数字 = 改金额
    assert r.state == "awaiting_confirm"
    assert "480.00" in r.text


def test_new_message_overrides_pending(flow):
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    # 待确认时来了新指令，旧草稿作废
    cf.handle("今天卖了500块馒头")
    cf.handle("确认")
    txns = engine.transactions_since(date(2020, 1, 1))
    assert len(txns) == 1
    assert engine.get_balances()["Income:Sales"] == Decimal("-500")


def test_mapping_remembered(flow):
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("确认")
    assert memory.get_mapping("面粉") == "Assets:Inventory"


# ---------- 回归测试：借贷方向由科目性质决定，不信 AI 的 type ----------

def test_owner_drawing_reduces_cash(flow):
    """老板从店里拿钱家用 —— 现金必须减少。

    曾经的真实 bug：AI 把"钱在动"判成 transfer，代码照 transfer 处理成
    「借 现金 / 贷 业主提款」，结果拿了 2000 块家用，现金反而 +2000。
    """
    cf, engine, memory = flow
    r = cf.handle("我自己从店里拿了2000块家用")
    assert r.state == "awaiting_confirm"
    assert "业主提款" in r.text

    cf.handle("确认")
    b = engine.get_balances()
    assert b["Assets:Cash"] == Decimal("-2000"), "提款后现金必须是减少的"
    assert b["Equity:Drawings"] == Decimal("2000"), "业主提款记借方（正数）"


def test_income_increases_cash(flow):
    """收入方向不能被方向规则改坏：借 现金 / 贷 收入。"""
    cf, engine, memory = flow
    cf.handle("今天卖了500块馒头")
    cf.handle("确认")
    b = engine.get_balances()
    assert b["Assets:Cash"] == Decimal("500")
    assert b["Income:Sales"] == Decimal("-500")


def test_purchase_decreases_cash(flow):
    """采购方向：借 存货 / 贷 现金。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("确认")
    b = engine.get_balances()
    assert b["Assets:Inventory"] == Decimal("300")
    assert b["Assets:Cash"] == Decimal("-300")


# ---------- 回归测试：否定词绝不能落账 ----------

@pytest.mark.parametrize("word", ["不行", "不好", "不可以", "不对劲"])
def test_negative_reply_never_commits(flow, word):
    """曾经的真实 bug：CONFIRM_WORDS 里的「行」「好」用子串匹配，
    导致「不行」「不好」被判成确认，错账直接落进账本。
    """
    cf, engine, memory = flow
    r1 = cf.handle("今天买了300块面粉")
    assert r1.state == "awaiting_confirm"

    r2 = cf.handle(word)
    assert r2.state == "awaiting_confirm", f"「{word}」被误判成确认"
    assert len(engine.transactions_since(date(2020, 1, 1))) == 0, f"「{word}」竟然落账了"


@pytest.mark.parametrize("word", ["可以删了", "删了", "不要了"])
def test_delete_words_cancel_pending_record(flow, word):
    """「可以删了」曾被「可以」命中判成确认；在记账草稿态应等于取消。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    r = cf.handle(word)
    assert "不记" in r.text
    assert memory.get_state(FLOW_STATE) is None
    assert len(engine.transactions_since(date(2020, 1, 1))) == 0


def test_confirm_still_works(flow):
    """加了否定判断后，正常的确认不能被打断。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    for word in ("确认", "好的", "嗯", "对的", "没错"):
        if word != "确认":
            cf.handle("今天买了300块面粉")  # 重新起一笔，保证每轮都从既正确又合法的确认词开始
        r = cf.handle(word)
        assert r.state == "idle", f"「{word}」应当被识别为确认"
    assert len(engine.transactions_since(date(2020, 1, 1))) == 5
