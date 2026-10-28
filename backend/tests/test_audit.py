"""审计溯源测试：用户原话 ↔ 账本分录 必须双向可查。"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.beancount_engine import BeancountEngine
from app.core.memory import Memory
from app.flows.chat import ChatFlow
from app.llm.schemas import CorrectIntent, Extraction, TransactionDraft


class FakeLLM:
    """只覆盖本测试用到的两句话，避免联网。"""

    def extract(self, user_text: str, today: str, mappings: dict):
        if "改成" in user_text:
            return Extraction(
                intent="correct",
                correct=CorrectIntent(target_keyword="面粉", new_amount=Decimal("480")),
            )
        if "面粉" in user_text:
            return Extraction(
                intent="record",
                transaction=TransactionDraft(
                    type="expense", amount=Decimal("300"), date=today,
                    category="面粉", account="Assets:Inventory",
                    cash_account="Assets:Cash", confidence="high",
                ),
            )
        raise AssertionError(f"FakeLLM 未覆盖输入: {user_text}")


@pytest.fixture()
def flow(tmp_path):
    engine = BeancountEngine(tmp_path / "ledger.bean")
    engine.ensure_exists()
    memory = Memory(tmp_path / "app.db")
    return ChatFlow(engine, memory, FakeLLM()), engine, memory


def test_commit_creates_audit_with_link(flow):
    """确认落账后，必须有审计记录，且分录上要带上对应的 ^link。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("确认")

    audits = memory.list_audits()
    assert len(audits) == 1
    a = audits[0]
    assert a["user_text"] == "今天买了300块面粉"     # 原始自然语言
    assert a["action"] == "record"
    assert a["status"] == "committed"
    assert a["amount"] == "300"
    assert set(a["accounts"]) == {"Assets:Inventory", "Assets:Cash"}
    assert a["entry_link"], "审计必须关联到分录"

    # 账本分录上确实带着这个 link
    rows = engine.transactions_since(date(2020, 1, 1))
    assert rows[0]["links"] == [a["entry_link"]]


def test_reverse_lookup_from_entry_to_utterance(flow):
    """反向溯源：拿分录上的 ^link 能查回当初那句话。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("确认")

    link = engine.transactions_since(date(2020, 1, 1))[0]["links"][0]
    found = memory.find_audits_by_link(link)
    assert len(found) == 1
    assert found[0]["user_text"] == "今天买了300块面粉"


def test_cancelled_draft_is_recorded(flow):
    """用户取消的草稿也要留痕，且不能挂到任何分录上。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("取消")

    audits = memory.list_audits()
    assert len(audits) == 1
    assert audits[0]["status"] == "cancelled"
    assert audits[0]["user_text"] == "今天买了300块面粉"
    assert audits[0]["entry_link"] is None
    assert engine.transactions_since(date(2020, 1, 1)) == []


def test_correction_appends_to_same_entry_trail(flow):
    """纠错要挂到「被改的那笔」上 —— 查一笔账能看到它被谁建的、被谁改的。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("确认")
    create_audit = memory.list_audits()[0]

    cf.handle("把今天那笔面粉改成480")
    cf.handle("确认")

    trail = memory.find_audits_by_link(create_audit["entry_link"])
    assert len(trail) == 2, "同一笔分录上应该有『创建』和『修改』两条记录"
    assert trail[0]["action"] == "record"
    assert trail[1]["action"] == "correct"
    assert trail[1]["user_text"] == "把今天那笔面粉改成480"
    assert engine.get_balances()["Assets:Inventory"] == Decimal("480")


def test_audit_records_edits(flow):
    """用户在确认态说过的话（如改金额）也要留在审计里。"""
    cf, engine, memory = flow
    cf.handle("今天买了300块面粉")
    cf.handle("不对，是350")
    cf.handle("确认")

    a = memory.list_audits()[0]
    assert a["user_text"] == "今天买了300块面粉"
    assert a["payload"].get("_edits") == ["不对，是350"]
    assert a["amount"] == "350"


def test_count_audits(flow):
    cf, engine, memory = flow
    assert memory.count_audits() == 0
    cf.handle("今天买了300块面粉")
    cf.handle("确认")
    assert memory.count_audits() == 1
