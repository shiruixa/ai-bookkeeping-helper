"""记账流程：解析 → 澄清 → 确认 → 落账 → 反馈。

核心约束：AI 产出的凭证必须经过硬校验 + 用户确认才能落账。
"""

import re
from datetime import date as date_cls, datetime, timedelta
from decimal import Decimal

from app.core import accounts
from app.core.validator import format_amount, is_future, parse_amount, parse_date
from app.flows.base import FlowReply

CASH_TERMS = {
    "银行": "Assets:Bank",
    "银行卡": "Assets:Bank",
    "微信": "Assets:Bank",     # 电子钱包归入银行存款（简化）
    "支付宝": "Assets:Bank",
    "现金": "Assets:Cash",
    "零钱": "Assets:Cash",
}

SETUP_WORDS = ("初始", "本钱", "投入", "启动资金", "开店", "入资")
CASH_CODES = ("Assets:Cash", "Assets:Bank")


class RecordFlow:
    def __init__(self, engine, memory, llm):
        self.engine = engine
        self.memory = memory
        self.llm = llm

    # ---------- 解析与草稿 ----------

    def _resolve_code(self, term: str | None, default: str) -> str:
        """把中文/代码解析成科目代码。"""
        if not term:
            return default
        term = term.strip()
        if term in accounts.all_codes():
            return term
        for kw, code in CASH_TERMS.items():
            if kw in term:
                return code
        for alias, code in accounts.alias_map().items():
            if alias in term:
                return code
        # 尝试科目中文名
        for a in accounts.CHART:
            if a.name in term:
                return a.code
        return default

    def _resolve_business(self, category: str | None, txn_account: str | None,
                          default: str = "Expenses:Misc") -> str:
        """确定"业务侧"科目。优先级：用户记忆映射 > AI 选定 > 别名兜底。"""
        if category:
            mapped = self.memory.get_mapping(category)
            if mapped and mapped in accounts.all_codes():
                return mapped
        if txn_account and txn_account in accounts.all_codes():
            return txn_account
        if category:
            for alias, code in accounts.alias_map().items():
                if alias in category:
                    return code
        if category:
            for a in accounts.CHART:
                if a.name in category:
                    return a.code
        return default

    @staticmethod
    def _direction(biz: str, cash: str) -> tuple[str, str]:
        """按「业务侧科目的性质」决定借贷方向，而不是信任 AI 给的 type。

        为什么不信 AI：说"我自己拿了2000块家用"，AI 常判成 transfer（钱在动），
        若照 transfer 处理成「借 现金 / 贷 业主提款」，现金反而变多 —— 方向反了。
        按科目性质判断才可靠：
            收入/负债/初始资本 → 钱流进来（借 现金）
            费用/资产/业主提款 → 钱流出去（贷 现金）
        """
        acc = accounts.get(biz) if biz in accounts.all_codes() else None
        kind = acc.kind if acc else "expense"
        if kind in ("income", "liability"):
            return cash, biz
        if kind == "equity" and biz == "Equity:Capital":
            return cash, biz
        return biz, cash          # expense / asset / equity(Drawings)

    def build_draft(self, ex, text: str, today: date_cls) -> dict | None:
        """从抽取结果构建确认草稿。缺关键信息返回 None（需澄清）。"""
        txn = ex.transaction or type("T", (), {
            "amount": None, "date": None, "payee": None, "category": None,
            "account": None, "cash_account": None, "note": None, "confidence": "low",
        })()

        # 金额
        amount = None
        if txn.amount and txn.amount > 0:
            amount = txn.amount
        else:
            try:
                amount = parse_amount(text)
            except ValueError:
                amount = None
        if amount is None:
            return None  # 需要澄清金额

        # 日期
        d: date_cls | None = None
        if txn.date:
            try:
                d = date_cls.fromisoformat(txn.date)
            except ValueError:
                d = None
        if d is None:
            d = parse_date(text, today) or today

        # 类型与科目
        type_ = getattr(txn, "type", "expense") or "expense"
        if ex.intent == "setup" or (
            type_ == "transfer"
            and txn.category
            and any(w in txn.category for w in SETUP_WORDS)
        ):
            kind = "setup"
            biz = "Equity:Capital"
            cash = self._resolve_code(getattr(txn, "cash_account", None), "Assets:Cash")
        else:
            kind = type_  # expense | income | transfer
            biz_default = "Income:Sales" if kind == "income" else "Expenses:Misc"
            biz = self._resolve_business(
                getattr(txn, "category", None), getattr(txn, "account", None), biz_default,
            )
            cash = self._resolve_code(getattr(txn, "cash_account", None), "Assets:Cash")

        if kind == "transfer" and biz == cash:
            # 真正的转账（现金↔银行）：不能两边同一个科目
            cash = "Assets:Cash" if biz == "Assets:Bank" else "Assets:Bank"
        dr_account, cr_account = self._direction(biz, cash)

        category = (getattr(txn, "category", None) or "").strip() or None
        note = (getattr(txn, "note", None) or "").strip() or None
        payee = (getattr(txn, "payee", None) or "").strip() or None
        confidence = getattr(txn, "confidence", "medium") or "medium"

        return {
            "kind": "record",
            "type": kind,
            "amount": str(amount),
            "date": d.isoformat(),
            "payee": payee,
            "category": category,
            "note": note,
            "confidence": confidence,
            "dr_account": dr_account,
            "cr_account": cr_account,
            # 溯源用：用户原话（下划线开头 = 元数据，不参与记账）
            "_source": text,
            # 记账时刻：用于「昨天下午3点那笔记错了」这类精确定位
            "_time": datetime.now().strftime("%H:%M"),
            "postings": [
                [dr_account, "dr", str(amount)],
                [cr_account, "cr", str(amount)],
            ],
        }

    # ---------- 确认展示 ----------

    def _account_name(self, code: str) -> str:
        return accounts.get(code).name if code in accounts.all_codes() else code

    def confirm_text(self, payload: dict) -> str:
        amount = format_amount(Decimal(payload["amount"]))
        d = date_cls.fromisoformat(payload["date"])
        kind = payload["type"]
        category = payload.get("category")
        note = payload.get("note")
        payee = payload.get("payee")

        if kind == "setup":
            cash = self._account_name(payload["dr_account"])
            head = f"{d.month}月{d.day}日 开店投入 {amount}元（存入{cash}）"
        elif kind == "expense":
            what = category or note or "一笔支出"
            dr = self._account_name(payload["dr_account"])
            cr = self._account_name(payload["cr_account"])
            head = f"{d.month}月{d.day}日 {what} {amount}元\n    {cr} → {dr}"
        elif kind == "income":
            what = category or note or "一笔收入"
            dr = self._account_name(payload["dr_account"])
            cr = self._account_name(payload["cr_account"])
            head = f"{d.month}月{d.day}日 {what} {amount}元\n    {dr} ← {cr}"
        elif "Equity:Drawings" in (payload["dr_account"], payload["cr_account"]):
            # 业主提款：钱从店里拿走，AI 常判成 transfer，这里单独显示才不误导
            cash = self._account_name(payload["cr_account"])
            head = f"{d.month}月{d.day}日 业主提款 {amount}元（从{cash}拿走）"
        else:  # transfer
            dr = self._account_name(payload["dr_account"])
            cr = self._account_name(payload["cr_account"])
            head = f"{d.month}月{d.day}日 转账 {amount}元\n    {cr} → {dr}"
        lines = [head]
        if payee:
            lines.append(f"对方：{payee}")
        if note:
            lines.append(f"备注：{note}")
        lines.append("")
        lines.append("记这笔对吗？【确认】或直接修改（如：金额是480 / 记到银行）")
        return "\n".join(lines)

    def to_confirm(self, payload: dict) -> FlowReply:
        draft_id = self.memory.save_draft(payload)
        return FlowReply(
            text=self.confirm_text(payload),
            quick_replies=["确认", "取消"],
            state="awaiting_confirm",
            kind="record",
            draft_id=draft_id,
        )

    def clarify_amount(self) -> FlowReply:
        return FlowReply(
            text="这笔记了多少钱呢？直接告诉我数字就行，比如“300块”。",
            quick_replies=["300块", "500块", "1000块"],
        )

    # ---------- 落账 ----------

    def commit(self, payload: dict, today: date_cls) -> FlowReply:
        amount = Decimal(payload["amount"])
        d = date_cls.fromisoformat(payload["date"])
        postings = [(p[0], p[1], Decimal(p[2])) for p in payload["postings"]]
        category = payload.get("category")
        note = payload.get("note")
        payee = payload.get("payee") or ""
        kind = payload["type"]

        if kind == "setup":
            narration = "开店投入初始资金"
        else:
            narration = (category or note or ("收入" if kind == "income" else "支出"))

        # 溯源：先落一条审计（pending），拿到 id 作为分录的 ^link，写完再置为 committed。
        # 顺序很重要 —— 先写账本再建审计的话，就没法把 link 写进分录了。
        audit_id = self.memory.add_audit(
            user_text=payload.get("_source") or "",
            action="record",
            intent="record",
            payload={k: v for k, v in payload.items() if k != "_source"},   # 保留 _edits：修改痕迹也是溯源内容,
            narration=narration,
            amount=str(amount),
            accounts=[payload["dr_account"], payload["cr_account"]],
            status="pending",
        )
        link = f"chat-{audit_id}"
        try:
            self.engine.append_transaction(
                date=d, payee=payee, narration=narration, postings=postings,
                links=link, time_str=payload.get("_time", ""),
            )
        except Exception:
            self.memory.update_audit_status(audit_id, "failed")
            raise
        self.memory.update_audit_status(audit_id, "committed", entry_link=link)

        # 记住分类映射（用户已确认）：业务侧科目
        if kind == "expense":
            biz = payload["dr_account"]
        else:  # income / transfer / setup
            biz = payload["cr_account"]
        if category and category != "初始资本":
            self.memory.save_mapping(category, biz)

        return self.feedback(payload, today)

    def feedback(self, payload: dict, today: date_cls) -> FlowReply:
        balances = self.engine.get_balances()
        # 直接认出哪一侧是现金/银行科目，而不是按 type 猜（提款等场景 type 不可靠）
        dr, cr = payload["dr_account"], payload["cr_account"]
        cash_code = cr if cr in CASH_CODES else (dr if dr in CASH_CODES else cr)
        cash_bal = balances.get(cash_code, Decimal("0"))
        m_from = today.replace(day=1)
        ie = self.engine.report_income_expense(m_from, today)
        inc = sum(ie["income"].values())
        exp = sum(ie["expense"].values())
        lines = [
            f"记好啦。{self._account_name(cash_code)}余额 {format_amount(cash_bal)} 元。",
            f"本月收入 {format_amount(inc)} 元，支出 {format_amount(exp)} 元。",
        ]
        return FlowReply(text="\n".join(lines))

    # ---------- 修改待确认草稿 ----------

    def apply_edit(self, payload: dict, text: str, today: date_cls) -> dict | None:
        """根据用户修改文字更新草稿。返回新 payload 或 None（没看出改什么）。"""
        changed = False
        try:
            amt = parse_amount(text)
            if amt != Decimal(payload["amount"]):
                payload["amount"] = str(amt)
                changed = True
        except ValueError:
            pass
        d = parse_date(text, today)
        if d and d.isoformat() != payload["date"]:
            payload["date"] = d.isoformat()
            changed = True
        # 现金/银行侧
        for kw, code in CASH_TERMS.items():
            if kw in text:
                if payload["type"] in ("expense", "transfer", "setup"):
                    payload["cr_account"] = code
                else:
                    payload["dr_account"] = code
                changed = True
                break
        # 重新计算 postings（金额可能变了）
        amt = Decimal(payload["amount"])
        dr = payload["dr_account"]
        cr = payload["cr_account"]
        payload["postings"] = [[dr, "dr", str(amt)], [cr, "cr", str(amt)]]
        if changed:
            # 溯源：把用户的修改也留痕，审计里能看到「原话 → 改了什么」
            payload.setdefault("_edits", []).append(text)
        return payload if changed else None


def is_amount_only(text: str) -> bool:
    """这条消息是否主要是金额（用于确认态快速改金额）。"""
    try:
        parse_amount(text)
    except ValueError:
        return False
    # 去掉数字后的剩余字符
    rest = re.sub(r"[0-9，,.\s零〇一二三四五六七八九十百千万亿两点块元角毛分]", "", text)
    return len(rest) <= 2
