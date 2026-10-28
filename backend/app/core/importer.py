"""历史数据迁移导入：解析外部记账（微信/支付宝/银行/Excel/其他记账App）的流水。

流程：
1. 读文件（CSV/Excel），识别列（日期/金额/方向/分类/对方/备注）
2. 逐行校验，产出 row 列表与 skipped
3. AI 把唯一 (方向, 分类) 批量映射到科目（caller 调 llm.map_categories）
4. commit_rows() 批量生成 Beancount 分录，整体追加 + 校验回滚

注意：转账类（自己账户之间）识别为 Assets:Cash ↔ Assets:Bank 互转，不产生收入支出。
"""

import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.core.beancount_engine import BeancountEngine, LedgerError

# 标准列 -> 分优先级的表头别名组（每组里按表头顺序扫描，前面组优先）
COLUMN_ALIASES: dict[str, list[tuple]] = {
    "date": (("交易时间",), ("日期", "时间", "发生时间", "交易日期", "记账日期", "时间戳", "Date")),
    "direction": (("收/支",), ("收/支金额",), ("收支", "收付", "资金流向"), ("交易类型", "类型", "借贷", "类别")),
    "amount": (("金额(元)", "金额（元）"), ("金额", "交易金额", "发生额", "实付金额", "Amount")),
    "income": (("收入金额", "收入"),),
    "expense": (("支出金额", "支出"),),
    "category": (("商品名称", "商品说明"), ("商品",), ("分类", "类别", "交易分类", "类目", "摘要", "商品名称", "category")),
    "payee": (("交易对方",), ("对方名称",), ("对方", "商户", "商家", "收款方", "付款方", "交易伙伴", "payee")),
    "note": (("备注",), ("备注/说明",), ("说明", "描述", "注释", "名称", "note")),
}

CASH_HINTS = ("微信", "支付宝", "银行", "银行卡", "零钱", "钱包", "余额")

TRANSFER_HINTS = ("转账", "提现", "转入", "转出", "调拨", "互转", "充值")

MAX_ROWS = 8000


class ImportError_(ValueError):
    """导入解析错误。"""


# ---------- 文件读取 ----------

def read_file_rows(data: bytes, filename: str) -> list[list]:
    """把上传文件读成二维行。支持 CSV / Excel(.xlsx/.xls)。"""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise ImportError_("读取 Excel 需要安装 openpyxl：pip install openpyxl") from exc
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([("" if v is None else v) for v in row])
        return rows
    if name.endswith(".csv"):
        text = _decode_csv(data)
        return list(csv.reader(io.StringIO(text)))
    raise ImportError_("只支持 CSV / Excel 文件（.csv/.xlsx/.xls）")


def _decode_csv(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("latin1", errors="replace")


# ---------- 列识别 ----------

def detect_columns(header: list) -> dict[str, int]:
    """返回 {标准列名: 列下标}。按别名组优先级 + 表头顺序分配，列不重复。"""
    mapping: dict[str, int] = {}
    cells = [str(c).strip() for c in header]
    used: set[int] = set()

    def pick(std: str) -> None:
        for group in COLUMN_ALIASES.get(std, ()):
            for i, cell in enumerate(cells):
                if i in used:
                    continue
                for a in group:
                    if cell == a or (a in cell or cell in a):
                        mapping[std] = i
                        used.add(i)
                        return

    for std in ("date", "direction", "amount", "category", "payee", "note"):
        pick(std)
    if "amount" not in mapping:
        # 收入/支出两列（银行导出常见）
        pick("income")
        pick("expense")
        if "income" in mapping and "expense" in mapping:
            mapping["amount_inc"] = mapping.pop("income")
            mapping["amount_exp"] = mapping.pop("expense")
    return mapping


def parse_amount_cell(v) -> Decimal | None:
    """解析单个金额单元格。返回正数 Decimal；解析失败返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        d = Decimal(str(v)).quantize(Decimal("0.01"))
        return abs(d) if d else None
    s = str(v).replace("¥", "").replace("￥", "").replace(",", "").replace("，", "").strip()
    if not s or s in ("-", "/"):
        return None
    if s.endswith("%"):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        try:
            from app.core.validator import parse_amount

            d = parse_amount(s)
        except ValueError:
            return None
    if d == 0:
        return None
    return abs(d.quantize(Decimal("0.01")))


def parse_date_cell(v) -> date | None:
    """解析日期单元格（含 Excel 序列号、带时间戳的字符串）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and 20000 < v < 80000:
        return date(1899, 12, 30) + timedelta(days=int(v))
    # datetime 是 date 的子类，必须先判 datetime —— 否则 Excel 的日期时间单元格
    # 会原样返回 datetime，isoformat() 带上 "T10:00:00"，把账本日期写坏。
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_time_cell(v) -> str:
    """从日期单元格里抠出 HH:MM；抠不出返回空串。

    微信/支付宝账单的「交易时间」列是 "2026-07-01 10:00:00"，
    带上时刻后，用户可以「昨天下午3点那笔记错了」精确定位。
    """
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    m = re.search(r"(\d{1,2}):(\d{2})", str(v))
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    return ""


def detect_direction(v, amount: Decimal) -> str | None:
    """判断方向：income / expense。None = 跳过（不计收支）。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "/"):
        return None
    if s in ("收入", "收", "入", "income", "I", "+", "贷"):
        return "income"
    if s in ("支出", "支", "出", "expense", "E", "-", "借", "消费", "付款"):
        return "expense"
    if "不计收支" in s or "不计" in s or "冻结" in s:
        return None
    # 转账 / 调拨：自己账户之间
    if any(k in s for k in ("转账", "转出", "转入", "调拨", "互转", "银行卡", "零钱提现")):
        return "transfer"
    if "收" in s:
        return "income"
    if "支" in s or "付" in s or "消费" in s:
        return "expense"
    # 数字标识 1=收入 2=支出 等
    if s in ("1", "2", "3", "4"):
        return "income" if s in ("1",) else "expense"
    return None


# ---------- 行解析 ----------

def parse_rows(rows2d: list[list], filename: str = "") -> tuple[list[dict], list[dict], list[str]]:
    """解析二维行 → (valid_rows, skipped, warnings)。

    valid_rows: [{i, date, amount, direction, category, payee, note}]
    skipped:    [{row_no, reason, raw}]
    warnings:   表头识别提示
    """
    if not rows2d:
        raise ImportError_("文件是空的")
    header = rows2d[0]
    cols = detect_columns(header)
    if "date" not in cols and "amount" not in cols and "amount_inc" not in cols:
        raise ImportError_(
            "没认出表头。至少要有「日期」和「金额」两列。\n"
            "支持的列名：日期/交易时间、金额/收入/支出、收支/类型、分类、对方、备注。"
        )
    warnings = []
    missing = [s for s in ("date", "amount", "direction") if s not in cols and "amount_inc" not in cols]
    if "direction" in missing:
        warnings.append("没找到「收支/类型」列，将按金额正负判断（正=收入，负=支出）。")
    if "date" not in cols:
        warnings.append("没找到「日期」列，导入的账将记到今天。")

    valid, skipped = [], []
    for idx, raw in enumerate(rows2d[1:], start=2):
        if not any(str(c).strip() for c in raw):
            continue
        row_no = idx  # 表格里的行号（含表头）
        try:
            row = _parse_one(raw, cols)
        except Exception as exc:
            skipped.append({"row_no": row_no, "reason": str(exc), "raw": [str(c)[:30] for c in raw]})
            continue
        if row is None:
            continue  # 空行/不计收支
        valid.append(row)
        if len(valid) >= MAX_ROWS:
            skipped.append({"row_no": row_no, "reason": "超过单次导入上限", "raw": []})
            break
    return valid, skipped, warnings


def _parse_one(raw: list, cols: dict) -> dict | None:
    def cell(std: str):
        i = cols.get(std)
        return raw[i] if i is not None and i < len(raw) else None

    # 方向
    direction = None
    if "direction" in cols:
        direction = detect_direction(cell("direction"), None)
    elif "amount_inc" in cols:
        inc = parse_amount_cell(cell("amount_inc"))
        exp = parse_amount_cell(cell("amount_exp"))
        if inc and exp:
            direction = None  # 两列都有值 → 异常
        elif inc:
            direction = "income"
        elif exp:
            direction = "expense"

    # 金额
    if "amount_inc" in cols:
        amount = parse_amount_cell(cell("amount_inc")) or parse_amount_cell(cell("amount_exp"))
        if amount is None:
            raise ValueError("金额无法识别")
    else:
        raw_amt = cell("amount")
        amount = parse_amount_cell(raw_amt)
        if amount is None:
            raise ValueError("金额无法识别")
        if direction is None:
            s = str(raw_amt)
            direction = "income" if s.strip().startswith(("+", "收")) else (
                "expense" if s.strip().startswith(("-",)) else None
            )

    date_cell = cell("date")
    d = parse_date_cell(date_cell) if date_cell not in (None, "") else None
    t = parse_time_cell(date_cell)

    category = str(cell("category") or "").strip()
    payee = str(cell("payee") or "").strip()
    note = str(cell("note") or "").strip()

    if direction is None:
        # 没方向也没符号 → 可能是转账（微信/支付宝的"中/不计收支"）
        hint = payee + note + category
        if any(k in hint for k in TRANSFER_HINTS):
            direction = "transfer"
        else:
            raise ValueError("分不清收入还是支出")

    # 转账类：微信↔银行卡 等自己账户互转
    if direction == "transfer":
        hint = (payee + note + category)
        if any(k in hint for k in ("微信", "零钱", "钱包")):
            other = "Assets:Bank"
        else:
            other = "Assets:Cash"
        sign = "in" if any(k in hint for k in ("转入", "提现", "充值")) else "out"
        return {
            "i": None, "date": d, "time": t, "amount": amount, "direction": "transfer",
            "transfer_sign": sign, "transfer_other": other,
            "category": category or "转账", "payee": payee, "note": note,
        }

    return {
        "i": None, "date": d, "time": t, "amount": amount, "direction": direction,
        "category": category or "其他", "payee": payee, "note": note,
    }


# ---------- 批量落账 ----------

def build_transaction_text(row: dict, account_code: str, link: str = "") -> str | None:
    """把一行生成 Beancount 分录文本。account_code 是业务侧科目。link 用于溯源。"""
    from app.core.beancount_engine import BeancountEngine

    d = row["date"]
    if isinstance(d, str):
        d = parse_date_cell(d) or date.today()
    elif d is None:
        d = date.today()
    amount = row["amount"]
    direction = row["direction"]

    # 现金侧：对方/备注提到微信支付宝银行 → 银行存款
    hint = (row.get("payee") or "") + (row.get("note") or "")
    cash_side = "Assets:Bank" if any(k in hint for k in CASH_HINTS) else "Assets:Cash"

    if direction == "transfer":
        # 自账户互转：dr 进来的，cr 出去的
        other = row.get("transfer_other", "Assets:Bank")
        if row.get("transfer_sign") == "in":
            dr, cr = other, cash_side if other != cash_side else "Assets:Cash"
        else:
            dr, cr = cash_side, other
        if dr == cr:
            return None
        return BeancountEngine._format_transaction(
            date=d, payee=row.get("payee") or "", narration=row.get("category") or "转账",
            postings=[(dr, "dr", amount), (cr, "cr", amount)], links=link,
            time_str=row.get("time") or "",
        )

    narration = row.get("category") or (row.get("note") or "")
    note = row.get("note")
    if note and note != narration:
        narration = f"{narration} {note}".strip()
    if direction == "income":
        postings = [(cash_side, "dr", amount), (account_code, "cr", amount)]
    else:
        postings = [(account_code, "dr", amount), (cash_side, "cr", amount)]
    return BeancountEngine._format_transaction(
        date=d, payee=row.get("payee") or "", narration=narration, postings=postings,
        links=link, time_str=row.get("time") or "",
    )


def commit_rows(
    engine: BeancountEngine,
    rows: list[dict],
    mapping: dict[str, str],
    memory=None,
    link: str = "",
) -> dict:
    """批量落账。mapping: {f"{direction}:{category}": account_code}。返回统计。

    memory: 当前账本的 Memory 实例（用于记住分类映射）。传 None 时跳过"记住映射"，
    绝不回落到默认库 —— 多账本下写错库会让 A 账本的分类习惯污染 B 账本。
    """
    if not rows:
        return {"imported": 0, "failed": 0, "errors": []}
    texts, errors = [], []
    for row in rows:
        key = f"{row['direction']}:{row.get('category') or '其他'}"
        code = mapping.get(key) or mapping.get(row["direction"] + ":其他") or "Expenses:Misc"
        try:
            t = build_transaction_text(row, code, link)
            if t:
                texts.append(t)
        except Exception as exc:
            errors.append({"row": row.get("i"), "reason": str(exc)})
    if texts:
        try:
            engine.append_text("\n".join(texts))
        except LedgerError as exc:
            return {"imported": 0, "failed": len(texts), "errors": [{"reason": f"账本校验失败，全部回滚：{exc}"}]}
    # 记住映射（必须写进"当前账本"的库）
    if memory is not None:
        for key, code in mapping.items():
            if ":" in key:
                _, category = key.split(":", 1)
                if category and category != "其他":
                    memory.save_mapping(category, code)
    return {"imported": len(texts), "failed": len(errors), "errors": errors}
