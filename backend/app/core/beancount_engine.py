"""Beancount 记账引擎封装。

职责:
- 加载/校验 .bean 账本（loader 报错即失败，不落账）
- 余额查询（按账户累加）
- 追加交易/开科目（写文本 -> 重载校验 -> 失败回滚）
- 报表查询（beancount.query 同 bean-query 引擎）

设计原则：所有写操作必须通过 reload + 校验，校验不过就回滚。
"""

import threading
from collections import defaultdict
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation
from pathlib import Path

from beancount import loader
from beancount.api import format_entry
from beancount.core import data

CURRENCY = "CNY"
OPEN_DATE = "2016-01-01"  # open 指令的固定日期，早于所有业务
INVENTORY_ACCOUNT = "Assets:Inventory"
COGS_ACCOUNT = "Expenses:COGS"
CASH_ACCOUNTS = ("Assets:Cash", "Assets:Bank")   # 现金/银行：判断"钱进钱出"的口径

# 资产负债表的专业分组（流动资产/非流动资产、流动负债/非流动负债）
CURRENT_ASSETS = ("Assets:Cash", "Assets:Bank", "Assets:Inventory", "Assets:Receivable")
NON_CURRENT_ASSETS = ("Assets:Fixed",)
CURRENT_LIABILITIES = ("Liabilities:Payable",)
NON_CURRENT_LIABILITIES = ("Liabilities:Loan",)


class LedgerError(Exception):
    """账本校验失败。"""


class BeancountEngine:
    def __init__(self, ledger_path: Path | str, title: str = "小店账本"):
        self.path = Path(ledger_path)
        # 账本标题（多账本时各账本不同）。去掉引号，避免拼坏 bean 文本。
        self.title = (title or "账本").replace('"', "")
        self._lock = threading.Lock()

    # ---------- 初始化 ----------

    def ensure_exists(self) -> None:
        """若账本文件不存在，生成包含 open 指令的空账本。"""
        if self.path.exists():
            self.ensure_open_instructions()
            return
        from app.core import accounts

        lines = [
            f'option "title" "{self.title}"',
            'option "operating_currency" "CNY"',
            "",
        ]
        for code in accounts.all_codes():
            lines.append(f'{OPEN_DATE} open {code} CNY')
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def ensure_open_instructions(self) -> None:
        """补齐缺失的 open 指令（新增科目 / 老账本迁移时用）。"""
        from app.core import accounts

        entries, _, _ = self.load()
        existing = {e.account for e in entries if isinstance(e, data.Open)}
        missing = [c for c in accounts.all_codes() if c not in existing]
        if missing:
            self.append_text("\n".join(f"{OPEN_DATE} open {c} CNY" for c in missing))

    # ---------- 读取 ----------

    def load(self) -> tuple[list, list[dict], dict]:
        """返回 (entries, errors, options_map)。errors 非空说明账本有问题。"""
        entries, errors, options_map = loader.load_file(str(self.path))
        return entries, errors, options_map

    def load_or_raise(self) -> tuple[list, dict]:
        """加载并校验；有错误直接抛 LedgerError。"""
        entries, errors, options_map = self.load()
        if errors:
            msgs = [f"{e.source.message if getattr(e, 'source', None) else e}" for e in errors]
            raise LedgerError("账本校验失败: " + "; ".join(msgs[:5]))
        return entries, options_map

    def validate(self) -> list[str]:
        """返回错误信息列表；空列表 = 账本健康。"""
        _, errors, _ = self.load()
        return [str(e) for e in errors]

    def get_balances(self) -> dict[str, Decimal]:
        """按账户累加余额。返回 {account: Decimal}。"""
        entries, errors, _ = self.load()
        if errors:
            raise LedgerError("账本存在校验错误，无法计算余额")
        balances: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if posting.units is None:
                    continue  # padding 之类的占位
                units = posting.units
                if units.currency != CURRENCY:
                    continue
                balances[posting.account] += units.number
        return dict(balances)

    # ---------- 写入 ----------

    def append_text(self, text: str) -> None:
        """把账本文本追加到文件末尾，然后重载校验；失败则回滚。"""
        text = text.strip()
        if not text:
            return
        original = self.path.read_text(encoding="utf-8")
        with self._lock:
            if not original.endswith("\n"):
                original += "\n"
            self.path.write_text(original + text + "\n", encoding="utf-8")
            _, errors, _ = self.load()
            if errors:
                # 回滚
                self.path.write_text(original, encoding="utf-8")
                raise LedgerError("账本校验失败: " + "; ".join(str(e) for e in errors[:5]))

    def append_transaction(
        self,
        *,
        date: date_cls,
        payee: str,
        narration: str,
        postings: list[tuple[str, str, Decimal]],
        tags: str = "",
        links: str = "",
        time_str: str = "",
    ) -> str:
        """追加一笔交易。

        postings: [(account, 'dr'|'cr', amount), ...]  amount 为正数
        dr=借方(资产/费用增加), cr=贷方(权益/负债/收入增加)。
        注意 Beancount 不区分借贷方向，统一用正负号表示余额方向，
        这里按复式约定换算：贷方记负数。
        links: Beancount ^link 标记，用于溯源到产生它的那条审计记录。
        """
        text = self._format_transaction(date, payee, narration, postings, tags, links, time_str)
        self.append_text(text)
        return text

    def append_signed(
        self,
        *,
        date: date_cls,
        payee: str,
        narration: str,
        signed_postings: list[tuple[str, Decimal]],
        tags: str = "",
        links: str = "",
    ) -> str:
        """追加一笔交易（带符号金额，用于冲销等场景）。"""
        text = self._format_signed(date, payee, narration, signed_postings, tags, links)
        self.append_text(text)
        return text

    def append_open(self, account: str) -> None:
        """开设新科目。"""
        self.append_text(f"{OPEN_DATE} open {account} CNY")

    # ---------- 格式化 ----------

    @staticmethod
    def _fmt(amount: Decimal) -> str:
        return f"{amount:.2f}"

    @staticmethod
    def _format_signed(
        date: date_cls,
        payee: str,
        narration: str,
        signed_postings: list[tuple[str, Decimal]],
        tags: str = "",
        links: str = "",
        time_str: str = "",
    ) -> str:
        """生成账本文本。signed_postings: (account, 带符号金额)。

        links 会写成 Beancount 的 ^link 标记，用于把分录和审计记录串起来。
        time_str 写成分录的元数据（如 time: "15:30"），用于按时间精确定位一笔账。
        """
        lines = []
        payee_part = f' "{payee}"' if payee else ""
        tag_part = f" #{tags}" if tags else ""
        link_part = f" ^{links}" if links else ""
        lines.append(f'{date.isoformat()} *{payee_part} "{narration}"{tag_part}{link_part}')
        if time_str:
            lines.append(f'  time: "{time_str}"')
        for account, number in signed_postings:
            lines.append(f"  {account:<28} {BeancountEngine._fmt(number)} {CURRENCY}")
        return "\n".join(lines)

    @staticmethod
    def _format_transaction(
        date: date_cls,
        payee: str,
        narration: str,
        postings: list[tuple[str, str, Decimal]],
        tags: str = "",
        links: str = "",
        time_str: str = "",
    ) -> str:
        """生成账本文本。postings 为 (account, 'dr'|'cr', 正数金额)。"""
        signed = [(acc, amt if d == "dr" else -amt) for acc, d, amt in postings]
        return BeancountEngine._format_signed(
            date, payee, narration, signed, tags, links, time_str)

    @staticmethod
    def parse_amount(s: str) -> Decimal:
        """解析金额字符串为 Decimal；非法抛 ValueError。"""
        cleaned = s.replace(",", "").replace("，", "").strip()
        try:
            d = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"金额无法解析: {s!r}") from exc
        if d <= 0:
            raise ValueError(f"金额必须为正数: {s!r}")
        return d

    # ---------- 报表聚合（MVP 用 Python 直接聚合，不依赖 bean-query 引擎） ----------

    def sum_by_account(
        self,
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
        account_prefix: str | None = None,
    ) -> dict[str, Decimal]:
        """按账户汇总交易金额（含起止，默认全量）。可用于收支/分类汇总。

        注意：Beancount 里收入/权益/负债是负数余额，费用/资产是正数余额。
        返回原始方向，由调用方决定展示（如收入取反）。
        """
        entries, errors, _ = self.load()
        if errors:
            raise LedgerError("账本存在校验错误，无法汇总")
        totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            if date_from and entry.date < date_from:
                continue
            if date_to and entry.date > date_to:
                continue
            for posting in entry.postings:
                if posting.units is None:
                    continue
                units = posting.units
                if units.currency != CURRENCY:
                    continue
                if account_prefix and not posting.account.startswith(account_prefix):
                    continue
                totals[posting.account] += units.number
        return dict(totals)

    def report_income_expense(
        self,
        date_from: date_cls,
        date_to: date_cls,
    ) -> dict[str, dict[str, Decimal]]:
        """区间利润表：{income: {account: 正数金额}, expense: {account: 正数金额}}。"""
        raw = self.sum_by_account(date_from, date_to)
        result = {"income": {}, "expense": {}}
        for account, amount in raw.items():
            if account.startswith("Income:"):
                result["income"][account] = -amount  # 收入为负余额，取反成正数
            elif account.startswith("Expenses:"):
                result["expense"][account] = amount
        return result

    def daily_totals(
        self,
        date_from: date_cls,
        date_to: date_cls,
    ) -> dict[str, dict[str, Decimal]]:
        """按天汇总收入/支出。返回 {date: {"income": Decimal, "expense": Decimal}}。"""
        entries, errors, _ = self.load()
        if errors:
            raise LedgerError("账本存在校验错误，无法汇总")
        out: dict[str, dict[str, Decimal]] = {}
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            if entry.date < date_from or entry.date > date_to:
                continue
            day = out.setdefault(entry.date.isoformat(), {"income": Decimal("0"), "expense": Decimal("0")})
            for posting in entry.postings:
                if posting.units is None:
                    continue
                units = posting.units
                if units.currency != CURRENCY:
                    continue
                if posting.account.startswith("Income:"):
                    day["income"] += -units.number
                elif posting.account.startswith("Expenses:"):
                    day["expense"] += units.number
        return out

    # ---------- 存货成本结转（定期盘存法） ----------

    def inventory_roll(self, date_from: date_cls, date_to: date_cls) -> tuple[Decimal, Decimal]:
        """返回 (期初存货, 本期存货净增)。

        存货的滚动关系：期初 + 本期净增 − 期末盘存 = 本期销售成本。
        用"净增"而不是"只看借方"，是为了同时涵盖进货、退货、以及上一次结转。
        """
        entries, errors, _ = self.load()
        if errors:
            raise LedgerError("账本存在校验错误，无法核算存货")
        opening = Decimal("0")
        net = Decimal("0")
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if posting.account != INVENTORY_ACCOUNT or posting.units is None:
                    continue
                if posting.units.currency != CURRENCY:
                    continue
                if entry.date < date_from:
                    opening += posting.units.number
                elif entry.date <= date_to:
                    net += posting.units.number
        return opening, net

    def inventory_cost_close(
        self, date_from: date_cls, date_to: date_cls, ending_inventory: Decimal,
    ) -> dict:
        """定期盘存法：按期末盘点数倒推本期销售成本。

        期中不逐笔结转成本（小店做不到），期末盘一次货即可：
            本期销售成本 = 期初存货 + 本期存货净增 − 期末盘点
        然后生成结转分录：借 营业成本 / 贷 存货。
        """
        ending_inventory = Decimal(ending_inventory).quantize(Decimal("0.01"))
        if ending_inventory < 0:
            raise LedgerError("期末盘点数不能是负数")
        opening, net = self.inventory_roll(date_from, date_to)
        available = opening + net
        cost = (available - ending_inventory).quantize(Decimal("0.01"))
        # cost < 0 不报错：意思是"盘点数比账上还多"，也就是之前把成本转多了
        # （多半是上次盘点数估低了），本次要把它冲回来。会计上叫存货盘盈的调整。
        return {
            "opening": opening,
            "purchase_net": net,
            "available": available,
            "ending": ending_inventory,
            "cost": cost,
            "is_reversal": cost < 0,
        }

    def find_cost_close(self, date_from: date_cls, date_to: date_cls) -> dict | None:
        """区间内的成本结转情况：最近一次 + 累计。

        一个月可以结转多次（每次数出来的数都在上一次的基础上往下调），
        所以除了"最近一次转了多少"，还要给出"本月累计转了多少"，
        否则用户会以为第二次是在重算整月。
        """
        entries, _ = self.load_or_raise()
        last = None
        total = Decimal("0")
        count = 0
        for e in entries:
            if not isinstance(e, data.Transaction):
                continue
            if not (date_from <= e.date <= date_to):
                continue
            links = sorted(e.links) if e.links else []
            if not any(l.startswith("close-") for l in links):
                continue
            for p in e.postings:
                if p.account == COGS_ACCOUNT and p.units is not None:
                    total += p.units.number
                    count += 1
                    last = {
                        "date": e.date.isoformat(),
                        "cost": str(p.units.number),
                        "narration": e.narration or "",
                    }
        if last is None:
            return None
        return {**last, "count": count, "total_cost": str(total)}

    def append_cost_close(
        self, date: date_cls, narration: str, cost: Decimal, links: str = "",
    ) -> str:
        """写入成本结转分录：借 营业成本 / 贷 存货。

        cost 为负数时方向自动反转，即「借 存货 / 贷 营业成本」——
        用于把之前多转的成本冲回来（上次盘点数估低了的情况）。
        """
        if cost == 0:
            raise LedgerError("本次没有可结转的成本（盘点数和账上一致）")
        return self.append_signed(
            date=date, payee="", narration=narration, links=links,
            signed_postings=[(COGS_ACCOUNT, cost), (INVENTORY_ACCOUNT, -cost)],
        )

    # ---------- 专业报表（试算平衡 / 资产负债表 / 明细账） ----------

    def trial_balance(self) -> dict[str, dict[str, Decimal]]:
        """试算平衡表：每账户 借方合计 / 贷方合计 / 期末余额。

        Beancount 里借方=正数余额，贷方=负数余额。此表即「科目余额表」，
        是传统记账的核心表。
        """
        entries, errors, _ = self.load()
        if errors:
            raise LedgerError("账本存在校验错误，无法生成试算平衡表")
        table: dict[str, dict[str, Decimal]] = {}
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if posting.units is None:
                    continue
                units = posting.units
                if units.currency != CURRENCY:
                    continue
                row = table.setdefault(
                    posting.account,
                    {"debit": Decimal("0"), "credit": Decimal("0"), "balance": Decimal("0")},
                )
                n = units.number
                if n > 0:
                    row["debit"] += n
                else:
                    row["credit"] += -n
                row["balance"] += n
        return table

    def balance_sheet(self) -> dict:
        """资产负债表（累计口径）。

        由于收入和费用未结转损益，权益含「未分配利润」项 = 累计收入 - 累计支出，
        使 资产 = 负债 + 权益 恒等成立。这是传统会计「期末编制」时的简化处理。
        """
        balances = self.get_balances()
        assets = {k: v for k, v in balances.items() if k.startswith("Assets:")}
        liabilities = {k: -v for k, v in balances.items() if k.startswith("Liabilities:")}
        equity = {k: -v for k, v in balances.items() if k.startswith("Equity:")}

        ie = self.report_income_expense(date_cls(2000, 1, 1), date_cls(9999, 12, 31))
        net_profit = sum(ie["income"].values()) - sum(ie["expense"].values())

        assets = {k: v for k, v in assets.items() if v}
        liabilities = {k: v for k, v in liabilities.items() if v}
        equity = {k: v for k, v in equity.items() if v}
        assets_total = sum(assets.values())
        liabilities_total = sum(liabilities.values())
        equity_total = sum(equity.values()) + net_profit

        def group(src: dict, codes: tuple) -> dict:
            return {k: v for k, v in src.items() if k in codes}

        return {
            "assets": assets,
            "assets_total": assets_total,
            "liabilities": liabilities,
            "liabilities_total": liabilities_total,
            "equity": equity,
            "undistributed_profit": net_profit,
            "equity_total": equity_total,
            "balanced": abs(assets_total - liabilities_total - equity_total) < Decimal("0.005"),
            # 专业分组：资产负债表按流动性列示
            "current_assets": group(assets, CURRENT_ASSETS),
            "non_current_assets": group(assets, NON_CURRENT_ASSETS),
            "current_liabilities": group(liabilities, CURRENT_LIABILITIES),
            "non_current_liabilities": group(liabilities, NON_CURRENT_LIABILITIES),
        }

    def account_ledger(self, account: str, limit: int = 100) -> list[dict]:
        """某科目明细账：逐笔 + 余额累进。"""
        entries, errors, _ = self.load()
        if errors:
            raise LedgerError("账本存在校验错误，无法生成明细账")
        rows = []
        running = Decimal("0")
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if posting.account != account or posting.units is None:
                    continue
                units = posting.units
                if units.currency != CURRENCY:
                    continue
                running += units.number
                rows.append({
                    "date": entry.date.isoformat(),
                    "narration": entry.narration or "",
                    "payee": entry.payee or "",
                    "amount": units.number,
                    "balance": running,
                })
        return rows[-limit:]

    def transactions_all(self, limit: int = 200) -> list[dict]:
        """全部交易流水（倒序），供「明细账」总览。"""
        entries, _ = self.load_or_raise()
        txns = [e for e in entries if isinstance(e, data.Transaction)]
        txns.sort(key=lambda e: e.date, reverse=True)
        return [self._entry_to_dict(e) for e in txns[:limit]]

    # ---------- 交易检索 ----------

    def ledger_rows(
        self,
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
        limit: int = 3000,
    ) -> list[dict]:
        """面向普通人的流水：每笔只说「收了多少钱 / 花了多少钱 / 干什么用的」。

        收入/支出按「现金·银行科目的进出」判定 —— 这是店主最直观的口径，
        不需要看懂借贷。现金↔银行互转两边相抵，单列为 transfer，不虚增收支。
        """
        entries, _ = self.load_or_raise()
        rows = []
        for e in entries:
            if not isinstance(e, data.Transaction):
                continue
            if date_from and e.date < date_from:
                continue
            if date_to and e.date > date_to:
                continue
            cash_in = Decimal("0")
            cash_out = Decimal("0")
            others: list[str] = []
            for p in e.postings:
                if p.units is None or p.units.currency != CURRENCY:
                    continue
                if p.account in CASH_ACCOUNTS:
                    if p.units.number > 0:
                        cash_in += p.units.number
                    else:
                        cash_out += -p.units.number
                else:
                    others.append(p.account)
            links = sorted(e.links) if e.links else []

            # 期初结转 / 成本结转不是经营收支：期初结转会让现金科目大幅增加，
            # 若按"钱进来"算成收入，普通人会误以为这个月赚了这么多。
            settle = any(l.split("-")[0] in ("open", "close") for l in links)
            if settle:
                kind = "settle"
                cash_in = Decimal("0")
                cash_out = Decimal("0")
            elif cash_in and cash_out:
                kind = "transfer"
            elif cash_in:
                kind = "income"
            elif cash_out:
                kind = "expense"
            else:
                kind = "other"

            rows.append({
                "date": e.date.isoformat(),
                "time": (e.meta or {}).get("time") or "",
                "narration": e.narration or "",
                "payee": e.payee or "",
                "kind": kind,
                "income": str(cash_in),
                "expense": str(cash_out),
                "accounts": others,
                "links": links,
            })
        rows.sort(key=lambda r: (r["date"], r["time"]))
        return rows[-limit:]

    def transactions_since(self, since: date_cls) -> list[dict]:
        """返回 since 日期之后的交易（含），供纠错/记忆使用。"""
        entries, _ = self.load_or_raise()
        result = []
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            if entry.date < since:
                continue
            result.append(self._entry_to_dict(entry))
        return result

    def find_transactions(
        self,
        keyword: str | None = None,
        target_date: date_cls | None = None,
        since_days: int = 30,
        hour: int | None = None,
    ) -> list[tuple[object, int]]:
        """按关键词 + 日期 + 时刻 检索交易，返回 [(entry, index_in_entries)]，按日期倒序。

        关键词匹配 narration/payee/科目中文名。用于纠错定位。
        hour: 只保留该小时内的分录（依赖分录上的 time 元数据）。
        """
        from datetime import timedelta

        from app.core import accounts

        entries, _ = self.load_or_raise()
        cutoff = date_cls.today() - timedelta(days=since_days)
        kw_lower = keyword.strip().lower() if keyword else ""
        # 关键词可能命中科目中文名
        kw_account_codes = {
            a.code for a in accounts.CHART if kw_lower and kw_lower in a.name.lower()
        }
        hits = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, data.Transaction):
                continue
            if entry.date < cutoff:
                continue
            if target_date and entry.date != target_date:
                continue
            if hour is not None:
                t = (entry.meta or {}).get("time")
                try:
                    if t is None or int(str(t).split(":")[0]) != hour:
                        continue
                except (ValueError, TypeError):
                    continue
            if kw_lower:
                hay = (entry.narration or "").lower() + " " + (entry.payee or "").lower()
                acc_match = any(p.account in kw_account_codes for p in entry.postings)
                if kw_lower not in hay and not acc_match:
                    continue
            hits.append((entry, i))
        hits.sort(key=lambda t: t[0].date, reverse=True)
        return hits

    # ---------- 全量重写（纠错时用） ----------

    def save_entries(self, entries: list) -> None:
        """把 entries 全部重写回账本文件（保留 open 指令与 option）。"""
        open_lines = []
        txn_blocks = []
        for entry in entries:
            if isinstance(entry, data.Open):
                # format_entry 自带尾换行，必须 strip 掉再 join，
                # 否则每条之间会多出一个空行（整本账变得很脏）。
                open_lines.append(format_entry(entry).strip())
            elif isinstance(entry, data.Transaction):
                txn_blocks.append(format_entry(entry).strip())
        content = [
            f'option "title" "{self.title}"',
            'option "operating_currency" "CNY"',
            "",
            *open_lines,
            "",
            "\n\n".join(txn_blocks),   # 交易之间空一行，便于阅读
        ]
        text = "\n".join(content).strip() + "\n"
        with self._lock:
            original = self.path.read_text(encoding="utf-8")
            self.path.write_text(text, encoding="utf-8")
            _, errors, _ = self.load()
            if errors:
                self.path.write_text(original, encoding="utf-8")
                raise LedgerError("账本重写校验失败: " + "; ".join(str(e) for e in errors[:5]))

    @staticmethod
    def _entry_to_dict(entry) -> dict:
        postings = []
        for p in entry.postings:
            if p.units is None:
                continue
            postings.append({
                "account": p.account,
                "amount": float(p.units.number),
            })
        return {
            "date": entry.date.isoformat(),
            "time": (entry.meta or {}).get("time") or "",
            "payee": entry.payee or "",
            "narration": entry.narration or "",
            "postings": postings,
            # ^link：这条分录是由哪条审计记录产生的（可反向溯源到用户原话）
            "links": sorted(entry.links) if getattr(entry, "links", None) else [],
        }


# 供调试用
def dump_entries(entries) -> str:
    return "\n".join(format_entry(e) for e in entries)
