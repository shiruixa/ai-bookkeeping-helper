"""结构化输出模型（Pydantic）—— AI 返回 JSON 后的强校验层。"""

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TransactionDraft(BaseModel):
    """一笔待确认的交易草稿。"""
    type: Literal["expense", "income", "transfer"] = "expense"
    amount: Optional[Decimal] = None
    date: Optional[str] = None  # YYYY-MM-DD
    payee: Optional[str] = None
    category: Optional[str] = None
    account: Optional[str] = None  # AI 选定的科目代码
    account_alternatives: list[str] = Field(default_factory=list)
    cash_account: Optional[str] = None  # 现金/银行/微信/支付宝 侧科目
    note: Optional[str] = None
    confidence: Literal["high", "medium", "low"] = "medium"


class QueryIntent(BaseModel):
    report: Literal["today", "month", "balance", "category", "trend", "custom"] = "month"
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    category: Optional[str] = None
    account: Optional[str] = None


class CorrectIntent(BaseModel):
    target_date: Optional[str] = None
    target_keyword: Optional[str] = None
    target_time: Optional[str] = None  # 几点，如 "15" 或 "15:30"，用于精确定位
    new_amount: Optional[Decimal] = None
    new_category: Optional[str] = None
    delete: bool = False


class Extraction(BaseModel):
    intent: Literal["record", "query", "correct", "setup", "smalltalk"]
    transaction: Optional[TransactionDraft] = None
    query: Optional[QueryIntent] = None
    correct: Optional[CorrectIntent] = None
