"""纠错流程：定位 → 确认 → 修正（改金额/改分类/冲销删除）。

- 改金额/改分类：定位原 entry，重建后整账本重写（保留审计记录）
- 删除：追加一笔方向相反的冲销分录（不物理删除，保持可追溯）
"""

from datetime import date as date_cls
from decimal import Decimal

from beancount.core import data

from app.core import accounts
from app.core.validator import format_amount, parse_amount, parse_time_of_day
from app.flows.base import FlowReply

CASH_CODES = {"Assets:Cash", "Assets:Bank"}


class CorrectFlow:
    def __init__(self, engine, memory, llm):
        self.engine = engine
        self.memory = memory
        self.llm = llm

    def _acct(self, code: str) -> str:
        return accounts.get(code).name if code in accounts.all_codes() else code

    # ---------- 定位 ----------

    def build_correction(
        self, c, today: date_cls, source_text: str = "",
    ) -> tuple[FlowReply | None, dict | None]:
        """返回 (回复, 确认payload)。payload 为 None 时表示无需进入确认态。"""
        target_date = None
        if c and c.target_date:
            try:
                target_date = date_cls.fromisoformat(c.target_date)
            except ValueError:
                pass
        keyword = (c and c.target_keyword or "").strip() or None

        # 时刻：优先用 AI 抽出来的；没抽到就从原话里用规则解析。
        # 「昨天下午3点那笔水管」这种说法要能精确定位到那一笔。
        hour = None
        t = (c.target_time or "").strip() if c else ""
        if t:
            hour = parse_time_of_day(f"{t}点") if ":" not in t else int(t.split(":")[0])
        if hour is None and source_text:
            hour = parse_time_of_day(source_text)

        hits = self.engine.find_transactions(keyword, target_date, since_days=60, hour=hour)
        time_missed = False
        if not hits and hour is not None:
            # 带时刻没找到 → 放宽到整天，并告诉用户时刻没对上
            hits = self.engine.find_transactions(keyword, target_date, since_days=60)
            time_missed = bool(hits)
        if not hits:
            return None, None
        entry, idx = hits[0]

        # 新分类 → 新科目
        new_account = None
        if c and c.new_category:
            cat = c.new_category.strip()
            mapped = self.memory.get_mapping(cat)
            if mapped:
                new_account = mapped
            else:
                for alias, code in accounts.alias_map().items():
                    if alias in cat:
                        new_account = code
                        break
            if not new_account:
                return self._ask_category(cat), None

        payload = {
            "kind": "correct",
            "entry_index": idx,
            "new_amount": str(c.new_amount) if (c and c.new_amount) else None,
            "new_category": c.new_category.strip() if (c and c.new_category) else None,
            "new_account": new_account,
            "delete": bool(c and c.delete),
            "_source": source_text,          # 溯源：用户原话
        }
        return self._confirm_text(entry, payload, time_missed), payload

    def _ask_category(self, cat: str) -> FlowReply:
        opts = ", ".join(a.code for a in accounts.CHART if a.bookable)
        return FlowReply(
            text=f"“{cat}”归到哪个科目？可选：{opts}\n直接回复科目名或编号即可。",
        )

    def _confirm_text(self, entry, payload: dict, time_missed: bool = False) -> FlowReply:
        orig = self.engine._entry_to_dict(entry)
        lines = ["你是指这笔吗？" + ("（这个时间点没找到，是最接近的一笔）" if time_missed else "")]
        when = orig["date"] + (f" {orig['time']}" if orig.get("time") else "")
        who = " ".join(x for x in (orig.get("payee") or "", orig["narration"] or "") if x)
        lines.append(f"{when}　{who}".rstrip())
        for p in orig["postings"]:
            lines.append(f"    {self._acct(p['account'])} {format_amount(abs(Decimal(str(p['amount']))))} 元")
        if payload["delete"]:
            lines.append("我将【删除】这笔（追加冲销分录）。")
        else:
            changes = []
            if payload["new_amount"]:
                changes.append(f"金额改为 {format_amount(Decimal(payload['new_amount']))} 元")
            if payload["new_category"]:
                changes.append(f"分类改为「{payload['new_category']}」")
            lines.append("我将修改：" + "，".join(changes) if changes else "（未发现要改什么）")
        lines.append("")
        lines.append("【确认】或【取消】")
        draft_id = self.memory.save_draft(payload)
        return FlowReply(
            text="\n".join(lines),
            quick_replies=["确认", "取消"],
            state="awaiting_confirm",
            kind="correct",
            draft_id=draft_id,
        )

    # ---------- 执行 ----------

    def commit(self, payload: dict, today: date_cls) -> FlowReply:
        entries, _ = self.engine.load_or_raise()
        idx = int(payload["entry_index"])
        if idx >= len(entries):
            return FlowReply(text="要改的那笔找不到了，账本可能已变化。")
        entry = entries[idx]

        # 溯源：被改动的那笔分录自带的 ^link，挂到这条审计上，
        # 这样查那笔账时能同时看到「谁建的 + 谁改的」。
        orig_link = sorted(entry.links)[0] if getattr(entry, "links", None) else None

        if payload.get("delete"):
            rev = [
                (p.account, -p.units.number) for p in entry.postings if p.units is not None
            ]
            audit_id = self.memory.add_audit(
                user_text=payload.get("_source") or "",
                action="correct",
                payload={"op": "delete", "target": self.engine._entry_to_dict(entry)},
                entry_link=orig_link,
                narration=f"冲销: {entry.narration or '一笔账'}",
                accounts=[p.account for p in entry.postings if p.units is not None],
                status="pending",
            )
            try:
                self.engine.append_signed(
                    date=today,
                    payee=entry.payee or "",
                    narration=f"冲销: {entry.narration or '一笔账'}",
                    signed_postings=rev,
                    links=f"chat-{audit_id}",
                )
            except Exception:
                self.memory.update_audit_status(audit_id, "failed")
                raise
            self.memory.update_audit_status(audit_id, "committed", entry_link=f"chat-{audit_id}")
            return FlowReply(text="已删除这笔，并保留了冲销记录（可查）。")

        # 改金额/改分类：重建 entry 后整账本重写
        new_amount = Decimal(payload["new_amount"]) if payload.get("new_amount") else None
        new_account = payload.get("new_account")
        new_postings = []
        for p in entry.postings:
            if p.units is None:
                continue
            acct = p.account
            if new_account and p.account not in CASH_CODES:
                acct = new_account
            num = new_amount if new_amount is not None else p.units.number
            sign = 1 if p.units.number > 0 else -1
            new_postings.append(
                data.Posting(acct, data.Amount(num * sign, "CNY"), p.cost, p.price, p.flag, p.meta)
            )
        new_entry = data.Transaction(
            meta=entry.meta, date=entry.date, flag=entry.flag,
            payee=entry.payee, narration=entry.narration,
            tags=entry.tags, links=entry.links, postings=new_postings,
        )
        self.memory.add_audit(
            user_text=payload.get("_source") or "",
            action="correct",
            payload={
                "op": "modify",
                "new_amount": payload.get("new_amount"),
                "new_category": payload.get("new_category"),
                "before": self.engine._entry_to_dict(entry),
                "after": self.engine._entry_to_dict(new_entry),
            },
            entry_link=orig_link,
            narration=new_entry.narration or "",
            amount=payload.get("new_amount"),
            accounts=[p.account for p in new_postings],
            status="committed",
        )
        entries[idx] = new_entry
        self.engine.save_entries(entries)

        # 记住新分类映射
        if payload.get("new_category") and new_account:
            self.memory.save_mapping(payload["new_category"], new_account)

        return FlowReply(text="改好了。你可以问我“余额”或“这个月赚了多少”来核对。")
