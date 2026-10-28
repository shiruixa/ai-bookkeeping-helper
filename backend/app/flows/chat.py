"""会话协调：消息进来 → 处理待确认态 / 抽取意图 → 路由到各流程。"""

from datetime import date as date_cls

from app.flows.base import (
    CANCEL_WORDS,
    DELETE_WORDS,
    EDIT_WORDS,
    FLOW_STATE,
    FlowReply,
    affirms,
)
from app.flows.correct import CorrectFlow
from app.flows.query import QueryFlow
from app.flows.record import RecordFlow, is_amount_only
from app.llm.client import LLMError
from app.core.validator import parse_amount


class ChatFlow:
    def __init__(self, engine, memory, llm):
        self.engine = engine
        self.memory = memory
        self.llm = llm
        self.record = RecordFlow(engine, memory, llm)
        self.query = QueryFlow(engine, memory, llm)
        self.correct = CorrectFlow(engine, memory, llm)

    # ---------- 入口 ----------

    def handle(self, text: str) -> FlowReply:
        today = date_cls.today()
        state = self.memory.get_state(FLOW_STATE)
        if state and state.get("state") == "awaiting_confirm":
            reply = self._handle_pending(text, state, today)
            if reply is not None:
                return reply
            # None = 用户说的是新指令，覆盖旧草稿
            self.memory.clear_state(FLOW_STATE)
        return self._handle_new(text, today)

    # ---------- 待确认态 ----------

    def _handle_pending(self, text: str, state: dict, today: date_cls) -> FlowReply | None:
        kind = state.get("kind")
        payload = state.get("payload") or {}

        # 记账草稿态说「删了/不要了」= 这笔不记了（先于关键词分类，避免被「可以删了」里的「可以」判成确认）
        if kind == "record" and any(w in text for w in DELETE_WORDS):
            self._audit_dropped(kind, payload)
            self.memory.clear_state(FLOW_STATE)
            return FlowReply(text="好的，这笔先不记了。")

        cls = self._classify(text, payload)

        if cls == "cancel":
            self._audit_dropped(kind, payload)
            self.memory.clear_state(FLOW_STATE)
            return FlowReply(text="好的，这笔先不记了。")

        if cls == "edit":
            if kind == "record":
                updated = self.record.apply_edit(dict(payload), text, today)
                if updated:
                    self.memory.set_state(FLOW_STATE, {
                        "state": "awaiting_confirm", "kind": "record", "payload": updated,
                    })
                    return self.record.to_confirm(updated)
                # 草稿还挂着，state 必须如实报 awaiting_confirm，否则前端会收起「确认/取消」按钮
                return FlowReply(
                    text="没听懂要改什么。可以直接说，比如：金额是480 / 记到银行 / 是现金。",
                    quick_replies=["确认", "取消"],
                    state="awaiting_confirm", kind="record",
                )
            if kind == "correct":
                updated = self._apply_correct_edit(dict(payload), text)
                if updated:
                    self.memory.set_state(FLOW_STATE, {
                        "state": "awaiting_confirm", "kind": "correct", "payload": updated,
                    })
                    return FlowReply(
                        text=self._correct_changes_text(updated),
                        quick_replies=["确认", "取消"],
                        state="awaiting_confirm", kind="correct",
                        draft_id=self.memory.save_draft(updated),
                    )
                return FlowReply(
                    text="没听懂要改什么。可以直接说，比如：金额改成480 / 分类改成房租。",
                    quick_replies=["确认", "取消"],
                    state="awaiting_confirm", kind="correct",
                )

        if cls == "confirm":
            if kind == "record":
                reply = self.record.commit(payload, today)
            elif kind == "correct":
                reply = self.correct.commit(payload, today)
            else:
                reply = FlowReply(text="收到。")
            self.memory.clear_state(FLOW_STATE)
            return reply

        return None  # 新指令

    def _audit_dropped(self, kind: str | None, payload: dict) -> None:
        """用户取消的草稿也要留痕 —— 溯源要能回答「当时我本来想记什么」。"""
        if not payload:
            return
        self.memory.add_audit(
            user_text=payload.get("_source") or "",
            action=kind or "record",
            payload={k: v for k, v in payload.items() if k != "_source"},   # 保留 _edits：修改痕迹也是溯源内容,
            amount=payload.get("amount") or payload.get("new_amount"),
            accounts=[a for a in (payload.get("dr_account"), payload.get("cr_account")) if a],
            status="cancelled",
        )

    def _classify(self, text: str, payload: dict) -> str:
        t = text.strip()
        low = t.lower()
        if any(w in t for w in CANCEL_WORDS):
            return "cancel"
        if any(w in t for w in EDIT_WORDS):
            return "edit"
        # 单独的数字/金额 → 视为改金额（如 "480" / "是480"）
        try:
            amt = parse_amount(t)
            if is_amount_only(t):
                pending = payload.get("amount") or payload.get("new_amount")
                if pending is None or str(amt) != str(pending):
                    return "edit"
        except ValueError:
            pass
        if affirms(low):
            return "confirm"
        return "new"

    def _apply_correct_edit(self, payload: dict, text: str) -> dict | None:
        """修改待确认的纠错：金额 / 删除标记。"""
        changed = False
        try:
            amt = parse_amount(text)
            if payload.get("new_amount") is None or amt != parse_amount(str(payload["new_amount"])):
                payload["new_amount"] = str(amt)
                payload["delete"] = False
                changed = True
        except ValueError:
            pass
        if any(w in text for w in DELETE_WORDS):
            payload["delete"] = True
            changed = True
        return payload if changed else None

    def _correct_changes_text(self, payload: dict) -> str:
        if payload.get("delete"):
            return "我将【删除】这笔（追加冲销分录）。\n\n【确认】或【取消】"
        changes = []
        if payload.get("new_amount"):
            from app.core.validator import format_amount
            changes.append(f"金额改为 {format_amount(parse_amount(str(payload['new_amount'])))} 元")
        if payload.get("new_category"):
            changes.append(f"分类改为「{payload['new_category']}」")
        return "我将修改：" + "，".join(changes) + "\n\n【确认】或【取消】"

    # ---------- 新指令 ----------

    def _handle_new(self, text: str, today: date_cls) -> FlowReply:
        mappings = self.memory.all_mappings()
        try:
            ex = self.llm.extract(text, today.isoformat(), mappings)
        except LLMError:
            return FlowReply(text="AI 服务暂时连不上，请稍后再试，或直接用“确认/取消”继续。")
        except Exception:
            return FlowReply(text="没完全听懂。可以这样跟我说：\n· “今天买了300块面粉”\n· “这个月赚了多少”")

        if ex.intent in ("record", "setup"):
            payload = self.record.build_draft(ex, text, today)
            if payload is None:
                return self.record.clarify_amount()
            self.memory.set_state(FLOW_STATE, {
                "state": "awaiting_confirm", "kind": "record", "payload": payload,
            })
            return self.record.to_confirm(payload)

        if ex.intent == "query":
            return self.query.handle(ex.query, today)

        if ex.intent == "correct":
            reply, payload = self.correct.build_correction(ex.correct, today, text)
            if reply is None:
                return FlowReply(
                    text="没找到你说的那笔。可以告诉我大概哪天、记的是什么，比如“昨天那笔面粉”。"
                )
            if payload is not None:
                self.memory.set_state(FLOW_STATE, {
                    "state": "awaiting_confirm", "kind": "correct", "payload": payload,
                })
            return reply

        # smalltalk
        return FlowReply(
            text="我是您的记账小助手，把平时收支说给我听就行：\n"
                 "· “今天卖了500块馒头”\n"
                 "· “房租交了3000”\n"
                 "· “这个月赚了多少”\n"
                 "· “我还有多少现金”\n"
                 "· “把昨天的面粉改成480”",
            quick_replies=["这个月赚了多少", "我还有多少现金"],
        )
