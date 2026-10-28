"""账本注册表：管理「公司 / 个人 / 测试」多账本。

为什么要多账本：会计的「会计主体假设」要求公司与个人的经济业务必须分开记录，
两本账混在一起本身就是错的（老板从店里拿钱自用应记「业主提款」，而不是并入个人账）。

路径约定：
    data/books/registry.json          # 账本目录（元数据，人类可读）
    data/books/<book_id>/ledger.bean  # 该账本的 Beancount 账本
    data/books/<book_id>/app.db       # 该账本的聊天记录/分类映射/草稿/会话状态

注册表用 JSON 而不是 SQLite：它是一份"跨账本的目录"，若存进某本账的 app.db
会造成"目录依赖某本账"的循环；而且 JSON 可手改、可备份、单写者无并发问题。

注意：本模块所有路径都从模块级 BOOKS_DIR 推导（包括遗留数据的 data 目录 = BOOKS_DIR.parent），
测试只要 monkeypatch 这一个变量就能把整个数据目录重定向到临时目录。
"""

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app import config

# 测试可 monkeypatch 这一个变量来重定向整个数据根目录
BOOKS_DIR = config.DATA_DIR / "books"

PRESETS: list[tuple[str, str, str]] = [
    ("company", "公司账本", ""),
    ("personal", "个人账本", ""),
    ("test", "测试账本", ""),
]
DEFAULT_BOOK_ID = "company"


@dataclass
class Book:
    id: str
    name: str
    emoji: str = "📒"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


# ---------- 路径 ----------

def _data_dir() -> Path:
    """数据根目录。生产环境 = backend/data；测试重定向 BOOKS_DIR 后自动跟随。"""
    return BOOKS_DIR.parent


def registry_path() -> Path:
    return BOOKS_DIR / "registry.json"


def book_dir(book_id: str) -> Path:
    return BOOKS_DIR / book_id


def ledger_path(book_id: str) -> Path:
    return book_dir(book_id) / "ledger.bean"


def db_path(book_id: str) -> Path:
    return book_dir(book_id) / "app.db"


# ---------- 注册表读写 ----------

def _load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"version": 1, "default": DEFAULT_BOOK_ID, "books": []}
    try:
        reg = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 目录损坏不该让整个应用起不来：退化为"空目录"重新预置
        return {"version": 1, "default": DEFAULT_BOOK_ID, "books": []}
    reg.setdefault("version", 1)
    reg.setdefault("default", DEFAULT_BOOK_ID)
    reg.setdefault("books", [])
    return reg


def _save_registry(reg: dict) -> None:
    """原子写：先写临时文件再 os.replace，避免写一半坏掉。"""
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------- 查询 ----------

def list_books() -> list[Book]:
    return [
        Book(id=b["id"], name=b.get("name", b["id"]),
             emoji=b.get("emoji", "📒"), created_at=b.get("created_at", ""))
        for b in _load_registry()["books"]
    ]


def get_book(book_id: str) -> Book | None:
    for b in list_books():
        if b.id == book_id:
            return b
    return None


# ---------- 创建 ----------

def _slugify(name: str) -> str:
    """英文名转 kebab-case；纯中文名返回空串（由调用方兜底）。"""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def _materialize(book_id: str, name: str | None = None) -> None:
    """确保该账本的目录 + 空账本 + 空 DB 都存在（幂等）。

    name 只影响"新建账本文件"时写入的 title；已存在的文件不会被改写。
    """
    from app.core.beancount_engine import BeancountEngine
    from app.core.memory import Memory

    book_dir(book_id).mkdir(parents=True, exist_ok=True)
    BeancountEngine(ledger_path(book_id), title=name or book_id).ensure_exists()   # 内部幂等
    Memory(db_path(book_id))                                                       # 建表


def create_book(name: str, book_id: str | None = None, emoji: str = "") -> Book:
    """新建账本。名称或 id 重复都会抛 ValueError。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("账本名称不能为空")
    if any(b.name == name for b in list_books()):
        raise ValueError(f"已经有叫「{name}」的账本了，换个名字吧")

    if book_id:
        if get_book(book_id) is not None:
            raise ValueError(f"账本已存在：{book_id}")
        bid = book_id
    else:
        # 中文名 slug 化后会是空串，这时按 book / book-2 / book-3 递增，
        # 避免第二本中文名账本直接撞 id 报错。
        base = _slugify(name) or "book"
        bid = base
        n = 2
        while get_book(bid) is not None:
            bid = f"{base}-{n}"
            n += 1
    reg = _load_registry()
    book = Book(id=bid, name=name, emoji=emoji)
    _materialize(bid, name)
    reg["books"].append(asdict(book))
    _save_registry(reg)
    return book


# ---------- 启动初始化 ----------

def ensure_registry() -> None:
    """幂等：迁移遗留单账本数据 → 预置三个账本 → 物化所有账本文件。可重复调用。"""
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    reg = _load_registry()

    # 1) 遗留单账本数据迁移到「测试账本」（它本来就是测试产生的）
    #    只在该账本还没有账本文件时执行一次，用 move 而非 copy，避免两份数据分叉。
    legacy_ledger = _data_dir() / "ledger.bean"
    if legacy_ledger.exists() and not ledger_path("test").exists():
        book_dir("test").mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_ledger), str(ledger_path("test")))
        legacy_db = _data_dir() / "app.db"
        if legacy_db.exists():
            shutil.move(str(legacy_db), str(db_path("test")))

    # 2) 预置（按稳定 id 判存在，天然幂等，重启不会重复创建）
    existing = {b["id"] for b in reg["books"]}
    for bid, name, emoji in PRESETS:
        if bid not in existing:
            reg["books"].append(asdict(Book(id=bid, name=name, emoji=emoji)))
    reg["default"] = reg.get("default") or DEFAULT_BOOK_ID
    _save_registry(reg)

    # 3) 物化所有账本（幂等）
    for b in reg["books"]:
        _materialize(b["id"], b.get("name"))
