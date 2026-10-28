"""账本注册表测试：预置 / 幂等 / 遗留迁移 / 新建。"""

import json

import pytest

from app.core import books


def test_presets_created(tmp_books):
    ids = [b.id for b in books.list_books()]
    assert ids == ["company", "personal", "test"]
    names = {b.id: b.name for b in books.list_books()}
    assert names["company"] == "公司账本"
    assert names["test"] == "测试账本"


def test_ensure_registry_is_idempotent(tmp_books):
    """重复启动不能重复创建账本，也不能产生重复目录。"""
    books.ensure_registry()
    books.ensure_registry()
    assert len(books.list_books()) == 3
    # 每个账本的账本文件都存在
    for bid in ("company", "personal", "test"):
        assert books.ledger_path(bid).exists()
        assert books.db_path(bid).exists()
    assert len([p for p in books.BOOKS_DIR.iterdir() if p.is_dir()]) == 3


def test_registry_json_shape(tmp_books):
    reg = json.loads(books.registry_path().read_text(encoding="utf-8"))
    assert reg["version"] == 1
    assert reg["default"] == "company"
    assert {b["id"] for b in reg["books"]} == {"company", "personal", "test"}


def test_legacy_single_ledger_migrates_to_test_book(data_root):
    """遗留的单账本数据应被 move 进「测试账本」，且原文件不再存在。"""
    books.BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    legacy = data_root / "ledger.bean"
    legacy.write_text('option "title" "小店账本"\n', encoding="utf-8")
    (data_root / "app.db").write_bytes(b"")   # 假装有个旧库

    books.ensure_registry()

    assert not legacy.exists(), "遗留文件应该被移走，不能留两份"
    assert books.ledger_path("test").exists()
    assert "小店账本" in books.ledger_path("test").read_text(encoding="utf-8")
    # 其他两本不受影响
    assert "小店账本" not in books.ledger_path("company").read_text(encoding="utf-8")


def test_create_book_materializes_files(tmp_books):
    b = books.create_book("卷帘门分店", book_id="roller")
    assert b.name == "卷帘门分店"
    assert books.ledger_path("roller").exists()
    assert books.db_path("roller").exists()
    assert any(x.id == "roller" for x in books.list_books())
    # 新建的账本文件里标题应是它自己的名字，而不是写死的"小店账本"
    assert "卷帘门分店" in books.ledger_path("roller").read_text(encoding="utf-8")


def test_create_book_rejects_duplicate(tmp_books):
    with pytest.raises(ValueError):
        books.create_book("重复", book_id="company")


def test_get_book_unknown_returns_none(tmp_books):
    assert books.get_book("nope") is None
