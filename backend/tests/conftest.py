"""共享测试夹具。

关键点：把账本数据根目录重定向到临时目录，任何测试都不碰 backend/data 下的真实数据。
`app.core.books` 里所有路径都是在函数内读模块级 BOOKS_DIR 的，所以 monkeypatch
这一个变量就能把整个数据目录搬走。
"""

import tempfile
from datetime import date
from pathlib import Path

import pytest

from app.core import books
from app.core.beancount_engine import BeancountEngine
from app.core.memory import Memory


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    """临时数据根目录；只重定向 BOOKS_DIR，BOOKS_DIR.parent 自动成为数据根。"""
    root = tmp_path / "data"
    monkeypatch.setattr(books, "BOOKS_DIR", root / "books")
    return root


@pytest.fixture()
def tmp_books(data_root):
    """已初始化好的三个账本（临时目录）。"""
    books.ensure_registry()
    return data_root / "books"


@pytest.fixture()
def book_engine_and_memory(tmp_path):
    """不依赖账本注册表的裸 engine + memory（沿用 test_record_flow 的做法）。"""
    engine = BeancountEngine(tmp_path / "ledger.bean")
    engine.ensure_exists()
    memory = Memory(tmp_path / "app.db")
    return engine, memory


@pytest.fixture()
def client(tmp_books, monkeypatch):
    """TestClient，数据落在临时账本目录；LLM 用假实现，测试不联网。"""
    from fastapi.testclient import TestClient

    from app import main
    from app.llm.schemas import Extraction

    # 清掉可能已缓存的真实账本资源
    main._resources.clear()

    def fake_extract(user_text, today, mappings):
        """固定返回闲聊意图：只验证路由/隔离，不测 AI。"""
        return Extraction(intent="smalltalk")

    monkeypatch.setattr(main.llm, "extract", fake_extract)

    with TestClient(main.app) as c:
        yield c

    main._resources.clear()
