"""SQLite 存储：聊天记录、分类映射记忆、确认草稿、状态。

单用户验证期，数据量小，直接用 sqlite3。账本本体在 .bean 文件，
这里只存"聊天/流程"相关的元数据。
"""

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config


class Memory:
    def __init__(self, db_path: Path | str | None = None):
        self.path = str(db_path or config.DB_PATH)
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS messages(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        role TEXT NOT NULL,          -- user | assistant
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS mappings(
                        category TEXT PRIMARY KEY,   -- 口语分类，如 "面粉"
                        account_code TEXT NOT NULL,  -- 映射到的科目，如 Assets:Inventory
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS drafts(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payload_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS kv(
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    -- 审计溯源：记录「用户原话 → 记账改动 → 账本分录」的完整链条。
                    -- entry_link 与账本分录上的 ^link 对应，可双向追溯。
                    CREATE TABLE IF NOT EXISTS audits(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        user_text TEXT NOT NULL,      -- 用户原话
                        action TEXT NOT NULL,         -- record/correct/import/cost_close/opening
                        intent TEXT,                  -- AI 判定的意图
                        payload_json TEXT,            -- 解析出的结构化草稿
                        entry_link TEXT,              -- 账本分录的 ^link，如 chat-42
                        narration TEXT,               -- 分录摘要
                        amount TEXT,                  -- 金额
                        accounts_json TEXT,           -- 涉及科目
                        status TEXT NOT NULL          -- committed/cancelled/failed
                    );
                    CREATE INDEX IF NOT EXISTS idx_audits_link ON audits(entry_link);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ---------- 聊天记录 ----------

    def add_message(self, role: str, content: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO messages(role, content, created_at) VALUES (?,?,?)",
                    (role, content, datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()

    def recent_messages(self, limit: int = 20) -> list[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in reversed(rows)]
            finally:
                conn.close()

    # ---------- 分类映射记忆 ----------

    def get_mapping(self, category: str) -> str | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT account_code FROM mappings WHERE category=?",
                    (category.strip(),),
                ).fetchone()
                return row["account_code"] if row else None
            finally:
                conn.close()

    def save_mapping(self, category: str, account_code: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO mappings(category, account_code, created_at) VALUES (?,?,?)",
                    (category.strip(), account_code, datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()

    def all_mappings(self) -> dict[str, str]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT category, account_code FROM mappings").fetchall()
                return {r["category"]: r["account_code"] for r in rows}
            finally:
                conn.close()

    # ---------- 确认草稿 ----------

    def save_draft(self, payload: dict, ttl: int | None = None) -> int:
        """保存草稿，返回草稿 id。ttl 秒后过期。"""
        now = time.time()
        ttl = ttl or config.DRAFT_TTL_SECONDS
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO drafts(payload_json, created_at, expires_at) VALUES (?,?,?)",
                    (json.dumps(payload, ensure_ascii=False), now, now + ttl),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def get_draft(self, draft_id: int) -> dict | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT id, payload_json, created_at, expires_at FROM drafts WHERE id=?",
                    (draft_id,),
                ).fetchone()
                if not row:
                    return None
                if row["expires_at"] < time.time():
                    conn.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
                    conn.commit()
                    return None
                payload = json.loads(row["payload_json"])
                payload["draft_id"] = row["id"]
                return payload
            finally:
                conn.close()

    def delete_draft(self, draft_id: int) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
                conn.commit()
            finally:
                conn.close()

    def expire_drafts(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM drafts WHERE expires_at < ?", (time.time(),))
                conn.commit()
            finally:
                conn.close()

    # ---------- 审计溯源 ----------

    def add_audit(
        self,
        *,
        user_text: str,
        action: str,
        intent: str | None = None,
        payload: dict | None = None,
        entry_link: str | None = None,
        narration: str | None = None,
        amount: str | None = None,
        accounts: list[str] | None = None,
        status: str = "committed",
    ) -> int:
        """记录一条溯源：用户说了什么 → 做了哪类操作 → 落到哪笔分录。"""
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO audits(created_at, user_text, action, intent, payload_json,"
                    " entry_link, narration, amount, accounts_json, status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        user_text,
                        action,
                        intent,
                        json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                        entry_link,
                        narration,
                        amount,
                        json.dumps(accounts or [], ensure_ascii=False),
                        status,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def update_audit_status(
        self, audit_id: int, status: str, entry_link: str | None = None,
    ) -> None:
        """收尾审计记录。entry_link 用于把「先建审计、后写分录」产生出来的 link 回填。"""
        with self._lock:
            conn = self._conn()
            try:
                if entry_link is None:
                    conn.execute("UPDATE audits SET status=? WHERE id=?", (status, audit_id))
                else:
                    conn.execute(
                        "UPDATE audits SET status=?, entry_link=? WHERE id=?",
                        (status, entry_link, audit_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def list_audits(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """倒序列出溯源记录（最新在前）。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM audits ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return [self._audit_row(r) for r in rows]
            finally:
                conn.close()

    def find_audits_by_link(self, entry_link: str) -> list[dict]:
        """按账本分录的 ^link 反查：这笔分录是哪句话记出来的。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM audits WHERE entry_link=? ORDER BY id",
                    (entry_link,),
                ).fetchall()
                return [self._audit_row(r) for r in rows]
            finally:
                conn.close()

    @staticmethod
    def _audit_row(row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "null")
        item["accounts"] = json.loads(item.pop("accounts_json") or "[]")
        return item

    def count_audits(self) -> int:
        with self._lock:
            conn = self._conn()
            try:
                return int(conn.execute("SELECT COUNT(*) AS n FROM audits").fetchone()["n"])
            finally:
                conn.close()

    # ---------- 会话状态（kv） ----------

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
                conn.commit()
            finally:
                conn.close()

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
                if not row:
                    return default
                return json.loads(row["value"])
            finally:
                conn.close()

    def clear_state(self, key: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM kv WHERE key=?", (key,))
                conn.commit()
            finally:
                conn.close()
