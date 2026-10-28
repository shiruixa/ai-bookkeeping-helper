"""查询报表流程：NL → 模板报表 → 白话播报。

MVP 用预置模板 + Python 格式化，AI 只负责选模板填参数，避免幻觉。
"""

from datetime import date as date_cls, timedelta
from decimal import Decimal

from app.core import accounts
from app.core.validator import format_amount
from app.flows.base import FlowReply


class QueryFlow:
    def __init__(self, engine, memory, llm):
        self.engine = engine
        self.memory = memory
        self.llm = llm

    def _acct(self, code: str) -> str:
        return accounts.get(code).name if code in accounts.all_codes() else code

    def handle(self, q, today: date_cls) -> FlowReply:
        report = (q.report or "month") if q else "month"
        handler = getattr(self, f"_r_{report}", self._r_month)
        try:
            return handler(q, today)
        except Exception as exc:  # 账本有问题时兜底
            return FlowReply(text=f"查询出问题了：{exc}")

    # ---------- 各模板 ----------

    def _r_balance(self, q, today: date_cls) -> FlowReply:
        balances = self.engine.get_balances()
        lines = ["【当前余额】"]
        cash = balances.get("Assets:Cash", Decimal("0"))
        bank = balances.get("Assets:Bank", Decimal("0"))
        inv = balances.get("Assets:Inventory", Decimal("0"))
        lines.append(f"· 现金 {format_amount(cash)} 元")
        lines.append(f"· 银行/微信/支付宝 {format_amount(bank)} 元")
        lines.append(f"· 存货 {format_amount(inv)} 元")
        ar = balances.get("Assets:Receivable", Decimal("0"))
        ap = -balances.get("Liabilities:Payable", Decimal("0"))
        if ar:
            lines.append(f"· 别人欠我 {format_amount(ar)} 元")
        if ap:
            lines.append(f"· 我欠别人 {format_amount(ap)} 元")
        m_from = today.replace(day=1)
        ie = self.engine.report_income_expense(m_from, today)
        inc = sum(ie["income"].values())
        exp = sum(ie["expense"].values())
        lines.append("")
        lines.append(f"本月收入 {format_amount(inc)} 元，支出 {format_amount(exp)} 元。")
        return FlowReply(text="\n".join(lines))

    def _r_today(self, q, today: date_cls) -> FlowReply:
        ie = self.engine.report_income_expense(today, today)
        inc = sum(ie["income"].values())
        exp = sum(ie["expense"].values())
        if not inc and not exp:
            return FlowReply(text=f"今天（{today.month}月{today.day}日）还没有记收支。")
        lines = [f"今天（{today.month}月{today.day}日）："]
        lines.append(f"· 收入 {format_amount(inc)} 元")
        lines.append(f"· 支出 {format_amount(exp)} 元")
        if inc or exp:
            lines.append(f"· 结余 {format_amount(inc - exp)} 元")
        return FlowReply(text="\n".join(lines))

    def _r_month(self, q, today: date_cls) -> FlowReply:
        m_from = today.replace(day=1)
        ie = self.engine.report_income_expense(m_from, today)
        inc = sum(ie["income"].values())
        exp = sum(ie["expense"].values())
        lines = [f"本月（{today.year}年{today.month}月1日至今）："]
        lines.append(f"· 收入合计 {format_amount(inc)} 元")
        lines.append(f"· 支出合计 {format_amount(exp)} 元")
        lines.append(f"· 结余 {format_amount(inc - exp)} 元")
        lines.append("")
        # 支出大头
        if ie["expense"]:
            lines.append("支出最多：")
            for acct, amt in sorted(ie["expense"].items(), key=lambda x: -x[1])[:3]:
                if amt > 0:
                    lines.append(f"  {self._acct(acct)} {format_amount(amt)} 元")
        return FlowReply(text="\n".join(lines))

    def _r_category(self, q, today: date_cls) -> FlowReply:
        kw = (q.category or "").strip()
        date_from, date_to = self._resolve_range(q, today)
        if not kw:
            return FlowReply(text="您想查哪一类花的钱？比如“面粉花了多少”。")
        totals = self.engine.sum_by_account(date_from, date_to)
        hit_lines = []
        total = Decimal("0")
        for acct, amt in totals.items():
            label = f"{self._acct(acct)}"
            acc_kw = any(a in kw for a in (label, acct, acct.split(":")[-1]))
            if acc_kw:
                hit_lines.append(f"· {label} {format_amount(amt if acct.startswith(('Assets','Expenses')) else -amt)} 元")
                total += amt
        # 按摘要里的关键词粗筛
        if not hit_lines:
            entries = self.engine.transactions_since(date_from)
            matched = [e for e in entries if kw and kw in (e["narration"] + e.get("payee", ""))]
            for e in matched:
                # 取借方合计（业务金额，income/expense 均以流入流出额为金额）
                amt = sum(p["amount"] for p in e["postings"] if p["amount"] > 0)
                if amt:
                    hit_lines.append(f"· {e['date']} {e['narration']} {format_amount(Decimal(str(amt)))} 元")
        if not hit_lines:
            return FlowReply(text=f"没查到「{kw}」相关的记录。")
        head = f"【{kw} 相关】" + ("\n" if len(hit_lines) > 1 else " ")
        return FlowReply(text=head + "\n".join(hit_lines[:10]))

    def _r_trend(self, q, today: date_cls) -> FlowReply:
        d_from = today - timedelta(days=6)
        daily = self.engine.daily_totals(d_from, today)
        lines = ["【近7天收支】"]
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            key = d.isoformat()
            item = daily.get(key, {"income": Decimal("0"), "expense": Decimal("0")})
            mark = "今天" if i == 0 else f"{d.month}/{d.day}"
            lines.append(
                f"{mark}: 收 {format_amount(item['income'])} / 支 {format_amount(item['expense'])}"
            )
        return FlowReply(text="\n".join(lines))

    def _resolve_range(self, q, today: date_cls) -> tuple[date_cls, date_cls]:
        d_from = None
        d_to = None
        if q and q.date_from:
            try:
                d_from = date_cls.fromisoformat(q.date_from)
            except ValueError:
                pass
        if q and q.date_to:
            try:
                d_to = date_cls.fromisoformat(q.date_to)
            except ValueError:
                pass
        if d_to is None:
            d_to = today
        if d_from is None:
            d_from = today.replace(day=1)
        return d_from, d_to
