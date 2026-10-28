"""多账本隔离测试：不同 X-Book-Id 的请求必须落到完全不同的账本与数据库。"""

from datetime import date
from decimal import Decimal

from app.core import books
from app.core.beancount_engine import BeancountEngine
from app.core.memory import Memory


def test_books_endpoint_lists_three(client):
    d = client.get("/api/books").json()
    assert d["default"] == "company"
    assert [b["id"] for b in d["books"]] == ["company", "personal", "test"]


def test_books_endpoint_works_without_book_header(client):
    """即使本地存的 bookId 失效了，也必须能拉到账本列表自救。"""
    r = client.get("/api/books", headers={"X-Book-Id": "nope"})
    assert r.status_code == 200


def test_unknown_book_404(client):
    r = client.get("/api/balances", headers={"X-Book-Id": "nope"})
    assert r.status_code == 404
    assert "账本不存在" in r.json()["detail"]


def test_chat_writes_to_the_right_book(client):
    r = client.post("/api/chat", json={"message": "你好"}, headers={"X-Book-Id": "test"})
    assert r.status_code == 200

    # 只有测试账本的库里有聊天记录
    assert len(Memory(books.db_path("test")).recent_messages()) == 2   # user + assistant
    assert Memory(books.db_path("company")).recent_messages() == []
    assert Memory(books.db_path("personal")).recent_messages() == []


def test_balances_are_isolated(client):
    """在测试账本记一笔，公司账本的余额必须还是 0。"""
    eng = BeancountEngine(books.ledger_path("test"))
    eng.append_transaction(
        date=date.today(), payee="", narration="卖货",
        postings=[("Assets:Cash", "dr", Decimal("500")), ("Income:Sales", "cr", Decimal("500"))],
    )

    test_bal = client.get("/api/balances", headers={"X-Book-Id": "test"}).json()["balances"]
    company_bal = client.get("/api/balances", headers={"X-Book-Id": "company"}).json()["balances"]

    assert float(test_bal.get("Assets:Cash")) == 500
    assert company_bal == {}


def test_export_filename_carries_book_id(client):
    r = client.get("/api/ledger/export?book=test")
    assert r.status_code == 200
    assert "test.bean" in r.headers["content-disposition"]


def test_export_accepts_query_param_fallback(client):
    """导出链接是 <a href>，带不了请求头，必须支持 ?book= 兜底。"""
    r = client.get("/api/ledger/export?book=personal")
    assert r.status_code == 200
    assert "personal.bean" in r.headers["content-disposition"]


def test_ledger_content_is_per_book(client):
    eng = BeancountEngine(books.ledger_path("personal"))
    eng.append_transaction(
        date=date.today(), payee="", narration="个人开销",
        postings=[("Expenses:Misc", "dr", Decimal("88")), ("Assets:Cash", "cr", Decimal("88"))],
    )

    personal = client.get("/api/ledger?book=personal").json()["content"]
    company = client.get("/api/ledger?book=company").json()["content"]

    assert "个人开销" in personal
    assert "个人开销" not in company


def test_health_reports_current_book(client):
    d = client.get("/api/health", headers={"X-Book-Id": "test"}).json()
    assert d["status"] == "ok"
    assert d["book_id"] == "test"
