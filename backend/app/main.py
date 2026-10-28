"""FastAPI 入口：/api/chat 对话、看账报表、导入迁移、期初余额、前端静态托管。

多账本：请求用 `X-Book-Id` 请求头（或 `?book=` 查询参数）声明当前账本，
每个账本一套独立的 engine/memory/chat_flow，进程内按 book_id 缓存。
"""

import threading
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.core import accounts, books as bookreg, importer
from app.core.beancount_engine import (
    COGS_ACCOUNT,
    INVENTORY_ACCOUNT,
    BeancountEngine,
    LedgerError,
)
from app.core.memory import Memory
from app.core.validator import format_amount
from app.flows.chat import ChatFlow
from app.llm.client import LLMClient

app = FastAPI(title="AI 记账助手", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 启动即建目录 / 迁移遗留数据 / 预置三个账本（幂等）
bookreg.ensure_registry()

# LLM 客户端无状态，全局共享一份即可，不需要按账本复制
llm = LLMClient(config.DEEPSEEK_BASE_URL, config.DEEPSEEK_API_KEY, config.MODEL)


class BookResources:
    """一个账本对应的一整套资源。"""

    def __init__(self, book_id: str):
        self.book_id = book_id
        book = bookreg.get_book(book_id)
        self.engine = BeancountEngine(
            bookreg.ledger_path(book_id),
            title=book.name if book else book_id,
        )
        self.engine.ensure_exists()
        self.memory = Memory(bookreg.db_path(book_id))
        self.chat_flow = ChatFlow(self.engine, self.memory, llm)


_resources: dict[str, BookResources] = {}
_res_lock = threading.Lock()


def get_resources(book_id: str) -> BookResources:
    with _res_lock:
        res = _resources.get(book_id)
        if res is None:
            res = BookResources(book_id)
            _resources[book_id] = res
        return res


def resolve_book_id(
    x_book_id: str | None = Header(default=None, alias="X-Book-Id"),
    book: str | None = Query(default=None),
) -> str:
    """解析当前账本。请求头优先，其次查询参数（导出链接带不了请求头），最后默认账本。"""
    bid = (x_book_id or book or bookreg.DEFAULT_BOOK_ID).strip()
    if bookreg.get_book(bid) is None:
        raise HTTPException(status_code=404, detail=f"账本不存在：{bid}")
    return bid


def get_book(book_id: str = Depends(resolve_book_id)) -> BookResources:
    return get_resources(book_id)


class ChatIn(BaseModel):
    message: str


@app.get("/api/books")
def books_list():
    """账本列表（给入口页 / 顶部切换条用）。

    刻意不依赖 resolve_book_id：即使前端存的 bookId 已失效，也必须能拉到列表自救。
    """
    reg = bookreg._load_registry()
    return {
        "default": reg.get("default", bookreg.DEFAULT_BOOK_ID),
        "books": [asdict(b) for b in bookreg.list_books()],
    }


class BookCreateIn(BaseModel):
    name: str


@app.post("/api/books")
def books_create(body: BookCreateIn):
    """新建账本（独立的一套账本文件 + 数据库）。"""
    try:
        book = bookreg.create_book(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return asdict(book)


@app.get("/api/health")
def health(book_id: str = Depends(resolve_book_id)):
    errors = get_resources(book_id).engine.validate()
    return {
        "status": "ok" if not errors else "ledger_error",
        "book_id": book_id,
        "model": config.MODEL,
        "base_url": config.DEEPSEEK_BASE_URL,
        "platform": _detect_platform(config.DEEPSEEK_BASE_URL),
        "ledger_errors": errors[:5],
        "api_key_configured": bool(config.DEEPSEEK_API_KEY),
    }


@app.post("/api/chat")
def chat(body: ChatIn, ctx: BookResources = Depends(get_book)):
    text = (body.message or "").strip()
    if not text:
        return {"reply": "请说点什么，比如“今天买了300块面粉”。", "quick_replies": [], "state": "idle"}
    ctx.memory.add_message("user", text)
    reply = ctx.chat_flow.handle(text)
    ctx.memory.add_message("assistant", reply.text)
    return {
        "reply": reply.text,
        "quick_replies": reply.quick_replies,
        "state": reply.state,
        "kind": reply.kind,
    }


# ---------- 看账：专业报表 ----------

@app.get("/api/accounts")
def accounts_list():
    """科目表（树）。"""
    tree = {}
    for kind, accs in accounts.TREE.items():
        tree[kind] = [{"code": a.code, "name": a.name, "aliases": list(a.aliases)} for a in accs]
    return tree


@app.get("/api/trial-balance")
def trial_balance(ctx: BookResources = Depends(get_book)):
    try:
        table = ctx.engine.trial_balance()
        return {"rows": [
            {
                "code": code, "name": accounts.get(code).name if code in accounts.all_codes() else code,
                "debit": str(r["debit"]), "credit": str(r["credit"]), "balance": str(r["balance"]),
            }
            for code, r in table.items()
        ]}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/income-statement")
def income_statement(period: str = "month", ctx: BookResources = Depends(get_book)):
    """利润表。period: month | last_month | week | today | all。"""
    today = date.today()
    d_from, d_to = _period_range(period, today)
    try:
        ie = ctx.engine.report_income_expense(d_from, d_to)
        # 注意：report_income_expense 已经把收入从「负余额」取正了，这里不能再取负。
        # 再取负会让「收入明细行」与「收入合计」符号相反，前端整张表自相矛盾。
        income = [{"code": k, "name": accounts.get(k).name, "amount": str(v)} for k, v in sorted(ie["income"].items()) if v]
        expense = [{"code": k, "name": accounts.get(k).name, "amount": str(v)} for k, v in sorted(ie["expense"].items()) if v]
        inc_total = sum(ie["income"].values())
        exp_total = sum(ie["expense"].values())
        return {
            "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
            "income": income, "income_total": str(inc_total),
            "expense": expense, "expense_total": str(exp_total),
            "net_profit": str(inc_total - exp_total),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/balance-sheet")
def balance_sheet(ctx: BookResources = Depends(get_book)):
    def lst(d: dict) -> list[dict]:
        return [{"code": k, "name": accounts.get(k).name, "amount": str(v)}
                for k, v in sorted(d.items())]

    try:
        bs = ctx.engine.balance_sheet()
        return {
            "assets": lst(bs["assets"]),
            "assets_total": str(bs["assets_total"]),
            "liabilities": lst(bs["liabilities"]),
            "liabilities_total": str(bs["liabilities_total"]),
            "equity": lst(bs["equity"]),
            "undistributed_profit": str(bs["undistributed_profit"]),
            "equity_total": str(bs["equity_total"]),
            "balanced": bs["balanced"],
            # 专业分组：按流动性列示（流动资产 / 非流动资产 / 流动负债 / 非流动负债）
            "current_assets": lst(bs["current_assets"]),
            "non_current_assets": lst(bs["non_current_assets"]),
            "current_liabilities": lst(bs["current_liabilities"]),
            "non_current_liabilities": lst(bs["non_current_liabilities"]),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/transactions")
def transactions(limit: int = 100, ctx: BookResources = Depends(get_book)):
    try:
        rows = ctx.engine.transactions_all(limit)
        return {"rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/account-ledger")
def account_ledger(account: str, limit: int = 100, ctx: BookResources = Depends(get_book)):
    if account not in accounts.all_codes():
        raise HTTPException(status_code=400, detail=f"没有这个科目: {account}")
    try:
        rows = ctx.engine.account_ledger(account, limit)
        return {"account": account, "name": accounts.get(account).name, "rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _period_range(period: str, today: date) -> tuple[date, date]:
    if period == "today":
        return today, today
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if period == "week":
        return today - timedelta(days=6), today
    if period == "all":
        return date(2000, 1, 1), date(9999, 12, 31)
    return today.replace(day=1), today


# ---------- 数据迁移：历史导入 ----------

class ImportPreviewIn(BaseModel):
    filename: str = ""
    data_b64: str = ""


@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...)):
    """上传文件 → 解析 + AI 分类映射建议 → 返回预览。不落账。"""
    data = await file.read()
    try:
        rows2d = importer.read_file_rows(data, file.filename or "")
        valid, skipped, warnings = importer.parse_rows(rows2d, file.filename or "")
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not valid:
        return {"rows": [], "mapping": {}, "skipped": skipped, "warnings": warnings,
                "note": "没有识别到可导入的记录，请检查文件格式。"}

    # 唯一 (方向, 分类)
    pairs = sorted({(r["direction"], r.get("category") or "其他") for r in valid})
    llm_map = {}
    try:
        llm_map = llm.map_categories(pairs)
    except Exception:
        llm_map = {}
    mapping = {}
    for direction, category in pairs:
        code = llm_map.get(f"{direction}:{category}")
        if not code:
            code = "Income:Sales" if direction == "income" else "Expenses:Misc"
        mapping[f"{direction}:{category}"] = code

    preview_rows = [{
        "i": r.get("i"), "date": (r["date"].isoformat() if r.get("date") else None),
        "amount": str(r["amount"]), "direction": r["direction"],
        "category": r.get("category"), "payee": r.get("payee"), "note": r.get("note"),
    } for r in valid[:10]]

    return {
        "rows": valid, "preview_rows": preview_rows, "mapping": mapping,
        "skipped": skipped, "warnings": warnings,
        "total": len(valid), "skip_count": len(skipped),
    }


class ImportCommitIn(BaseModel):
    rows: list[dict]
    mapping: dict[str, str]


@app.post("/api/import/commit")
def import_commit(body: ImportCommitIn, ctx: BookResources = Depends(get_book)):
    try:
        # 先落审计拿到 id，再把它作为 ^link 打到这一批导入的分录上
        audit_id = ctx.memory.add_audit(
            user_text=f"[批量导入] {len(body.rows)} 笔历史流水",
            action="import",
            payload={"mapping": body.mapping},
            narration=f"批量导入 {len(body.rows)} 笔",
            status="pending",
        )
        # 传 ctx.memory：分类映射必须写进"当前账本"的库
        result = importer.commit_rows(
            ctx.engine, body.rows, body.mapping, ctx.memory, link=f"import-{audit_id}",
        )
        ctx.memory.update_audit_status(
            audit_id,
            "committed" if result["imported"] else "failed",
            entry_link=f"import-{audit_id}" if result["imported"] else None,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------- 存货成本结转（定期盘存法） ----------

class CostCloseIn(BaseModel):
    date_from: str = ""
    date_to: str = ""
    ending_inventory: float


def _cost_close_range(body: CostCloseIn) -> tuple[date, date]:
    today = date.today()
    d_to = date.fromisoformat(body.date_to) if body.date_to else today
    d_from = date.fromisoformat(body.date_from) if body.date_from else d_to.replace(day=1)
    return d_from, d_to


@app.get("/api/cost-close/status")
def cost_close_status(ctx: BookResources = Depends(get_book)):
    """本月账上的存货情况（期初 + 本月进货 = 账上应有）。

    盘点其实就是拿这个「账上应有」和「仓库里实际有多少」去对——
    差额就是卖掉的货的成本。
    """
    d_from, d_to = _cost_close_range(CostCloseIn(ending_inventory=0))
    try:
        opening, net = ctx.engine.inventory_roll(d_from, d_to)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "opening": str(opening),
        "purchase_net": str(net),
        "available": str(opening + net),
        # 本月是否已经结转过 —— 前端据此提示，避免"结转后账上变了"看起来像 bug
        "last_close": ctx.engine.find_cost_close(d_from, d_to),
    }


@app.post("/api/cost-close/preview")
def cost_close_preview(body: CostCloseIn, ctx: BookResources = Depends(get_book)):
    """按期末盘点数试算本期销售成本 —— 只算不写。"""
    try:
        d_from, d_to = _cost_close_range(body)
        r = ctx.engine.inventory_cost_close(d_from, d_to, Decimal(str(body.ending_inventory)))
    except (ValueError, LedgerError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
        **{k: str(v) for k, v in r.items()},
        "entry": (
            f"{d_to.isoformat()} * \"\" \"月末成本结转\"\n"
            f"  {COGS_ACCOUNT:<28} {r['cost']:.2f} CNY\n"
            f"  {INVENTORY_ACCOUNT:<28} {-r['cost']:.2f} CNY"
        ),
    }


@app.post("/api/cost-close/commit")
def cost_close_commit(body: CostCloseIn, ctx: BookResources = Depends(get_book)):
    """确认结转：写入 借 营业成本 / 贷 存货。"""
    try:
        d_from, d_to = _cost_close_range(body)
        r = ctx.engine.inventory_cost_close(d_from, d_to, Decimal(str(body.ending_inventory)))
        reversal = r["cost"] < 0
        narration = f"{d_from.month}月成本结转" + ("冲回" if reversal else "")
        audit_id = ctx.memory.add_audit(
            user_text=f"[月末结转] 期末盘点存货 {body.ending_inventory}"
                      + ("（冲回上次多转的成本）" if reversal else ""),
            action="cost_close",
            payload={k: str(v) for k, v in r.items()},
            narration=narration,
            amount=str(r["cost"]),
            accounts=[COGS_ACCOUNT, INVENTORY_ACCOUNT],
            status="pending",
        )
        try:
            text = ctx.engine.append_cost_close(
                date=d_to, narration=narration, cost=r["cost"], links=f"close-{audit_id}",
            )
        except Exception:
            ctx.memory.update_audit_status(audit_id, "failed")
            raise
        ctx.memory.update_audit_status(audit_id, "committed", entry_link=f"close-{audit_id}")
    except (ValueError, LedgerError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # date_from/date_to 必须一并返回：前端结果面板要显示"结转期间"
    return {
        "ok": True,
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        **{k: str(v) for k, v in r.items()},
        "entry": text,
    }


# ---------- 期初余额 ----------

class OpeningBalanceIn(BaseModel):
    date: str
    cash: float = 0
    bank: float = 0
    inventory: float = 0
    receivables: float = 0
    fixed: float = 0
    payable: float = 0
    loan: float = 0


@app.post("/api/opening-balance")
def opening_balance(body: OpeningBalanceIn, ctx: BookResources = Depends(get_book)):
    """设置期初余额：中途接手账本时，把已有现金/银行/存货/负债结转进来。

    借方（资产增加）：现金/银行/存货/应收/固定资产
    贷方（负债增加）：应付/借款
    差额 → Equity:OpeningBalances 平账
    """
    try:
        d = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式不对，应为 YYYY-MM-DD")

    def q(v: float) -> Decimal:
        return (Decimal(str(v))).quantize(Decimal("0.01")) if v else Decimal("0")

    assets_dr = [
        ("Assets:Cash", q(body.cash)),
        ("Assets:Bank", q(body.bank)),
        ("Assets:Inventory", q(body.inventory)),
        ("Assets:Receivable", q(body.receivables)),
        ("Assets:Fixed", q(body.fixed)),
    ]
    liab_cr = [
        ("Liabilities:Payable", q(body.payable)),
        ("Liabilities:Loan", q(body.loan)),
    ]
    asset_total = sum(a for _, a in assets_dr)
    liab_total = sum(l for _, l in liab_cr)

    if asset_total == 0 and liab_total == 0:
        raise HTTPException(status_code=400, detail="还没有填写任何期初数字。")

    signed = []
    for acc, amt in assets_dr:
        if amt:
            signed.append((acc, amt))
    for acc, amt in liab_cr:
        if amt:
            signed.append((acc, -amt))
    # 平衡项
    opening = asset_total - liab_total
    if opening > 0:
        signed.append(("Equity:OpeningBalances", -opening))
    else:
        signed.append(("Equity:OpeningBalances", -opening))

    audit_id = ctx.memory.add_audit(
        user_text=f"[期初余额] 结转日 {body.date}",
        action="opening",
        payload=body.model_dump(),
        narration="期初余额结转",
        amount=str(opening),
        accounts=[a for a, _ in signed],
        status="pending",
    )
    try:
        ctx.engine.append_signed(
            date=d, payee="", narration="期初余额结转",
            signed_postings=signed, links=f"open-{audit_id}",
        )
    except Exception as exc:
        ctx.memory.update_audit_status(audit_id, "failed")
        raise HTTPException(status_code=400, detail=f"期初余额入账失败：{exc}")
    ctx.memory.update_audit_status(audit_id, "committed", entry_link=f"open-{audit_id}")

    return {
        "ok": True,
        "asset_total": str(asset_total),
        "liability_total": str(liab_total),
        "opening_balance": str(opening),
        "cash": str(asset_total),
    }


# ---------- 流水（低门槛的一条条视图） ----------

def _row_range(date_from: str, date_to: str, period: str) -> tuple[date | None, date | None]:
    today = date.today()
    if date_from or date_to:
        return (
            date.fromisoformat(date_from) if date_from else None,
            date.fromisoformat(date_to) if date_to else None,
        )
    if period == "all":
        return None, None
    d_from, d_to = _period_range(period, today)
    return d_from, d_to


class LedgerRowsIn(BaseModel):
    date_from: str = ""
    date_to: str = ""
    period: str = "month"


def _ledger_payload(engine: BeancountEngine, d_from, d_to) -> dict:
    rows = engine.ledger_rows(d_from, d_to)
    # 只统计真正的收入/支出：转账（现金↔银行）两边同时进出，计进去会把收支双双虚增
    total_in = sum((Decimal(r["income"]) for r in rows if r["kind"] == "income"), Decimal("0"))
    total_out = sum((Decimal(r["expense"]) for r in rows if r["kind"] == "expense"), Decimal("0"))
    return {
        "period": {"from": d_from.isoformat() if d_from else "", "to": d_to.isoformat() if d_to else ""},
        "rows": rows,
        "count": len(rows),
        "total_income": str(total_in),
        "total_expense": str(total_out),
        "net": str(total_in - total_out),
    }


@app.get("/api/ledger-rows")
def ledger_rows(
    period: str = "month",
    date_from: str = "",
    date_to: str = "",
    ctx: BookResources = Depends(get_book),
):
    """面向普通人的流水：日期 / 时间 / 干什么 / 收了多少 / 花了多少。"""
    try:
        d_from, d_to = _row_range(date_from, date_to, period)
        return _ledger_payload(ctx.engine, d_from, d_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/ledger-rows/export.csv")
def ledger_rows_export(
    period: str = "all",
    date_from: str = "",
    date_to: str = "",
    ctx: BookResources = Depends(get_book),
):
    """流水导出为 CSV（带 BOM，Excel 双击打开中文不乱码）。"""
    try:
        d_from, d_to = _row_range(date_from, date_to, period)
        data = _ledger_payload(ctx.engine, d_from, d_to)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    KIND = {"income": "收入", "expense": "支出", "transfer": "转账",
            "settle": "结转", "other": "其他"}
    lines = ["日期,时间,类型,摘要,对方,条目,收入,支出"]
    for r in data["rows"]:
        names = "、".join(accounts.get(a).name if a in accounts.all_codes() else a
                          for a in r["accounts"])
        cells = [
            r["date"], r["time"], KIND.get(r["kind"], r["kind"]),
            r["narration"], r["payee"], names,
            r["income"] if Decimal(r["income"]) else "",
            r["expense"] if Decimal(r["expense"]) else "",
        ]
        lines.append(",".join('"' + str(c).replace('"', '""') + '"' for c in cells))
    lines.append(f'"合计","","","","","","{data["total_income"]}","{data["total_expense"]}"')

    body = "﻿" + "\n".join(lines) + "\n"      # BOM：Excel 识别 UTF-8
    name = f"流水_{data['period']['from'] or '起'}_{data['period']['to'] or '今'}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ledger.csv"; '
                                        f"filename*=UTF-8''{quote(name)}"},
    )


# ---------- API 设置（大模型平台） ----------

class LLMSettingsIn(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""      # 留空 = 不修改（前端不回显 key）


def _detect_platform(base_url: str) -> str:
    u = (base_url or "").lower()
    if "workbuddy" in u:
        return "workbuddy"
    if "dmxapi" in u:
        return "DMXAPI"
    if "deepseek.com" in u:
        return "DeepSeek 官方"
    if "openai.com" in u:
        return "OpenAI"
    if "localhost" in u or "127.0.0.1" in u:
        return "本地服务"
    return "其他"


def _probe_llm(base_url: str, api_key: str, model: str) -> dict:
    """发一个最小请求，确认平台能通。错误码翻译成看得懂的话。"""
    import time as _time

    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
        "temperature": 0,
        "max_tokens": 16,
    }
    t0 = _time.time()
    try:
        r = httpx.post(url, json=payload,
                       headers={"Authorization": f"Bearer {api_key}"}, timeout=25)
    except Exception as exc:
        return {"ok": False, "error": f"请求失败：{exc}"}

    latency = round(_time.time() - t0, 2)
    if r.status_code == 401:
        return {"ok": False, "status": 401, "latency": latency,
                "error": "Key 无效，或者不属于这个平台"}
    if r.status_code == 403:
        return {"ok": False, "status": 403, "latency": latency,
                "error": "无权限（平台未开通 / 算力未领取）"}
    if r.status_code == 404:
        return {"ok": False, "status": 404, "latency": latency,
                "error": "地址不对，检查接口地址是否需要带 /v1"}
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "latency": latency,
                "error": r.text[:200]}

    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        content = ""
    return {
        "ok": True,
        "status": r.status_code,
        "latency": latency,
        "model": data.get("model", model),
        "reply": content.strip()[:40],
    }


def _write_env(updates: dict[str, str]) -> None:
    """更新 backend/.env 里指定的键，保留其他内容。"""
    import re

    path = config.BACKEND_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, seen = [], set()
    for line in lines:
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _apply_llm_settings(base_url: str, model: str, api_key: str) -> None:
    """热更新：改 config 和共享的 llm 实例，所有账本的 ChatFlow 立刻生效，不用重启。"""
    config.DEEPSEEK_BASE_URL = base_url
    config.MODEL = model
    config.DEEPSEEK_API_KEY = api_key
    llm.base_url = base_url.rstrip("/")
    llm.model = model
    llm.api_key = api_key


@app.get("/api/settings/llm")
def get_llm_settings():
    """当前使用的模型平台配置（key 只返回是否配置，绝不回显内容）。"""
    env_file = config.BACKEND_DIR / ".env"
    return {
        "base_url": config.DEEPSEEK_BASE_URL,
        "model": config.MODEL,
        "key_configured": bool(config.DEEPSEEK_API_KEY),
        "key_length": len(config.DEEPSEEK_API_KEY),
        "platform": _detect_platform(config.DEEPSEEK_BASE_URL),
        "env_file": str(env_file),
        "env_exists": env_file.exists(),
    }


@app.post("/api/settings/llm")
def save_llm_settings(body: LLMSettingsIn):
    base_url = (body.base_url or "").strip() or config.DEEPSEEK_BASE_URL
    model = (body.model or "").strip() or config.MODEL
    api_key = (body.api_key or "").strip() or config.DEEPSEEK_API_KEY

    if not api_key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="接口地址要以 http:// 或 https:// 开头")

    _write_env({
        "DEEPSEEK_BASE_URL": base_url,
        "DEEPSEEK_API_KEY": api_key,
        "MODEL": model,
    })
    _apply_llm_settings(base_url, model, api_key)
    return {
        "ok": True,
        "base_url": base_url,
        "model": model,
        "platform": _detect_platform(base_url),
        "message": "已保存并立即生效",
    }


@app.post("/api/settings/llm/test")
def test_llm_settings(body: LLMSettingsIn):
    """用表单里的值试连一次（不保存）。"""
    base_url = (body.base_url or "").strip() or config.DEEPSEEK_BASE_URL
    model = (body.model or "").strip() or config.MODEL
    api_key = (body.api_key or "").strip() or config.DEEPSEEK_API_KEY
    if not api_key:
        return {"ok": False, "error": "还没有配置 API Key"}
    result = _probe_llm(base_url, api_key, model)
    result["platform"] = _detect_platform(base_url)
    return result


# ---------- 审计溯源 ----------

@app.get("/api/audit")
def audit_list(limit: int = 100, offset: int = 0, ctx: BookResources = Depends(get_book)):
    """溯源列表：每一条都是「用户说了什么 → 做了什么改动 → 落到哪笔分录」。"""
    return {
        "total": ctx.memory.count_audits(),
        "rows": ctx.memory.list_audits(limit=limit, offset=offset),
    }


@app.get("/api/audit/by-link")
def audit_by_link(link: str, ctx: BookResources = Depends(get_book)):
    """反向溯源：给定账本分录上的 ^link，查出是哪句话记出来的、之后被改过几次。"""
    return {"link": link, "rows": ctx.memory.find_audits_by_link(link)}


# ---------- 账本导出 ----------

@app.get("/api/ledger")
def ledger(book_id: str = Depends(resolve_book_id)):
    return {"content": bookreg.ledger_path(book_id).read_text(encoding="utf-8")}


@app.get("/api/ledger/export")
def ledger_export(book_id: str = Depends(resolve_book_id)):
    """下载 .bean 账本文件（用于备份 / 交给会计）。文件名带账本 id，避免多账本下载混淆。"""
    return FileResponse(
        path=bookreg.ledger_path(book_id),
        filename=f"{book_id}.bean",
        media_type="text/plain",
    )


@app.get("/api/balances")
def balances(ctx: BookResources = Depends(get_book)):
    return {"balances": {k: str(v) for k, v in ctx.engine.get_balances().items()}}


# 前端静态托管
FRONTEND_DIR = config.PROJECT_ROOT / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
