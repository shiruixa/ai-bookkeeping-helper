"""长期压力测试：给一家「社区早餐店」灌 3 个月历史 + 本月对话记账 + 月末成本结转。

与 demo_simulation.py 的区别：
  - demo_simulation.py  演示脚本，8 步走完整链路，40 秒
  - stress_test.py      压力测试，约 250 笔流水，验证账本在高负载下依然自洽

覆盖到的链路：
  1. 批量导入（CSV → 列识别 → AI 批量分类 → 一次性落账，约 240 笔）
  2. 对话记账（真实 AI：采购/销售/费用/业主提款/纠错/查询）
  3. 月末成本结转（定期盘存法）
  4. 全部报表 + 会计恒等式校验

用法（需先启动后端）：
    python scripts/stress_test.py
    python scripts/stress_test.py --months 6      # 加大压力
"""

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BOOK = "test"
BOOK_DIR = ROOT / "backend" / "data" / "books" / BOOK
LINE = "─" * 68

# 商品/费用分类（会走 AI 批量分类映射到科目）
SALES_ITEMS = ["门锁", "水管", "电线", "油漆", "合页", "扳手", "螺丝"]
PURCHASE_ITEMS = ["螺丝", "合页", "PVC水管", "电线卷", "油漆桶", "水泥", "五金件"]
LABOR_ITEMS = ["人工费", "安装费"]        # 请工人的工钱（多样条目之一）

# 本月的对话记账（真实 AI，覆盖全部意图类型）
CHAT_SCRIPT: list[tuple[str, bool]] = [
    ("今天进了1200块PVC水管", True),
    ("今天进了600块螺丝", True),
    ("今天卖了1580块门锁", True),
    ("今天卖了920块电线", True),
    ("今天请师傅上门安装，人工费400", True),
    ("这个月房租交了4500", True),
    ("电费320", True),
    ("给两个员工发工资一共5600", True),
    ("我自己从店里拿了2000块当家用", True),
    # 按「日期 + 时刻 + 条目」精确定位纠错 —— 改的是昨天下午3点那笔耗材
    ("昨天下午3点那笔耗材记错了，是80块", True),
    ("这个月赚了多少", False),
]


def hr(title: str = "") -> None:
    print("\n" + LINE)
    if title:
        print(f"  {title}")
        print(LINE)


def reset_test_book() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core import books

    for p in BOOK_DIR.glob("*"):
        p.unlink()
    books._materialize(BOOK, "测试账本")


def build_history_csv(months: int, end: date) -> str:
    """生成一家早餐店的历史流水（微信账单格式），供批量导入。"""
    rnd = random.Random(20261011)          # 固定种子，结果可复现
    start = (end.replace(day=1) - timedelta(days=30 * months)).replace(day=1)
    last = end - timedelta(days=1)          # 历史一直生成到「昨天」，便于演示按时间纠错
    rows = ["交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,备注"]

    d = start
    while d <= last:
        # 每天两笔销售（早餐高峰）
        for _ in range(2):
            item = rnd.choice(SALES_ITEMS)
            amt = round(rnd.uniform(280, 900), 2)
            rows.append(f"{d} 0{rnd.randint(6,9)}:30:00,二维码收款,顾客,{item},收入,{amt},零钱,当日销售")
        # 每 3 天进一次货
        if d.day % 3 == 1:
            for _ in range(rnd.randint(1, 3)):
                item = rnd.choice(PURCHASE_ITEMS)
                amt = round(rnd.uniform(200, 900), 2)
                rows.append(f"{d} 07:10:00,商户消费,批发市场,{item},支出,{amt},零钱,进货")
        # 零星杂费（固定 15:00，方便演示「昨天下午3点那笔耗材」的精确定位）
        if rnd.random() < 0.25 or d == last:
            amt = round(rnd.uniform(20, 260), 2)
            rows.append(f"{d} 15:00:00,商户消费,杂货店,杂费,支出,{amt},零钱,耗材")
        # 请工人：安装/维修工钱
        if d.day % 7 == 3:
            amt = round(rnd.uniform(300, 900), 2)
            rows.append(f"{d} 16:30:00,商户消费,装修队,人工费,支出,{amt},银行卡,上门安装")
        # 每月固定支出
        if d.day == 1:
            rows.append(f"{d} 09:00:00,商户消费,房东,房租,支出,4500.00,银行卡,月租")
            rows.append(f"{d} 09:05:00,商户消费,物业,水电,支出,{round(rnd.uniform(260, 420), 2)},银行卡,水电费")
            rows.append(f"{d} 09:10:00,商户消费,员工,工资,支出,{round(rnd.uniform(5000, 6000), 2)},银行卡,发工资")
        d += timedelta(days=1)
    return "\n".join(rows) + "\n"


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=180, headers={"X-Book-Id": BOOK})

    def chat(self, message: str) -> dict:
        r = self.http.post(f"{self.base}/api/chat", json={"message": message})
        r.raise_for_status()
        return r.json()

    def get(self, path: str) -> dict:
        r = self.http.get(f"{self.base}{path}")
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self.http.post(f"{self.base}{path}", json=body)
        if r.status_code != 200:
            raise RuntimeError(r.json().get("detail", r.text))
        return r.json()


def set_opening_balance(c: Client, months: int) -> None:
    """接手这家店时的家底（否则现金会一路走负）。"""
    hr("第零步：期初余额结转")
    start = (date.today().replace(day=1) - timedelta(days=30 * months)).replace(day=1)
    d = start - timedelta(days=1)
    r = c.post("/api/opening-balance", {
        "date": d.isoformat(),
        "cash": 8000, "bank": 45000, "inventory": 6000,
        "payable": 3000,
    })
    print(f"  结转日 {d.isoformat()}")
    print(f"  现金 8,000.00　银行 45,000.00　存货 6,000.00　应付 3,000.00")
    print(f"  差额走期初余额科目：{float(r['opening_balance']):,.2f}")


def import_history(c: Client, csv_text: str) -> None:
    hr(f"第一步：批量导入历史流水（{len(csv_text.splitlines()) - 1} 行原始数据）")
    r = httpx.post(
        f"{c.base}/api/import/preview",
        files={"file": ("history.csv", csv_text.encode("utf-8"), "text/csv")},
        headers={"X-Book-Id": BOOK},
        timeout=180,
    )
    r.raise_for_status()
    d = r.json()
    print(f"  解析出 {d['total']} 笔，跳过 {d['skip_count']} 行")
    print(f"  AI 分类映射 {len(d['mapping'])} 条：")
    for k, v in sorted(d["mapping"].items()):
        print(f"    {k:<22} → {v}")

    res = c.post("/api/import/commit", {"rows": d["rows"], "mapping": d["mapping"]})
    print(f"\n  落账：成功 {res['imported']} 笔，失败 {res['failed']} 笔")
    if res["failed"]:
        print(f"  ⚠️ 失败样例：{res['errors'][:3]}")


def run_chat_month(c: Client) -> None:
    hr("第二步：本月对话记账（真实 AI）")
    for i, (msg, confirm) in enumerate(CHAT_SCRIPT, 1):
        d = c.chat(msg)
        text = (d.get("reply") or "").split("\n")[0]
        print(f"  [{i:>2}] {msg}")
        print(f"       {text}")
        if confirm and d.get("state") == "awaiting_confirm":
            c.chat("确认")
        elif confirm:
            print("       ⚠️ 未进入待确认态，跳过")


def close_cost(c: Client) -> None:
    hr("第三步：月末成本结转（定期盘存法）")
    # 先用 0 盘存探一次，拿到"可供结转存货"
    probe = c.post("/api/cost-close/preview", {"ending_inventory": 0})
    available = float(probe["available"])
    if available <= 0:
        print(f"  可供结转存货为 {available}，无需结转")
        return
    ending = round(available * 0.3, 2)     # 假设盘下来还剩三成存货
    prev = c.post("/api/cost-close/preview", {"ending_inventory": ending})
    print(f"  期初存货      {float(prev['opening']):>14,.2f}")
    print(f"  本期存货净增  {float(prev['purchase_net']):>14,.2f}")
    print(f"  可供结转存货  {float(prev['available']):>14,.2f}")
    print(f"  期末盘存      {float(prev['ending']):>14,.2f}")
    print(f"  本期销售成本  {float(prev['cost']):>14,.2f}")
    done = c.post("/api/cost-close/commit", {"ending_inventory": ending})
    print(f"\n  已结转：借 营业成本 / 贷 存货  {float(done['cost']):,.2f} 元")


def show_reports(c: Client) -> dict:
    hr("第四步：全部报表")
    book_id = BOOK
    txns = c.get("/api/transactions?limit=5000")["rows"]
    print(f"  账本总笔数：{len(txns)}")

    ie = c.get("/api/income-statement?period=all")
    print(f"\n  【利润表·开业至今】")
    for r in ie["income"]:
        print(f"    {r['name']:<12}{float(r['amount']):>14,.2f}")
    print(f"    {'收入合计':<12}{float(ie['income_total']):>14,.2f}")
    for r in ie["expense"]:
        print(f"    {r['name']:<12}{-float(r['amount']):>14,.2f}")
    print(f"    {'支出合计':<12}{-float(ie['expense_total']):>14,.2f}")
    print(f"    {'净利润':<12}{float(ie['net_profit']):>14,.2f}")

    bs = c.get("/api/balance-sheet")
    print(f"\n  【资产负债表】")
    for label, rows, total in (("资产", bs["assets"], bs["assets_total"]),
                               ("负债", bs["liabilities"], bs["liabilities_total"])):
        for r in rows:
            print(f"    {r['name']:<12}{float(r['amount']):>14,.2f}")
        print(f"    {label + '合计':<12}{float(total):>14,.2f}")
    for r in bs["equity"]:
        print(f"    {r['name']:<12}{float(r['amount']):>14,.2f}")
    print(f"    {'未分配利润':<12}{float(bs['undistributed_profit']):>14,.2f}")
    print(f"    {'权益合计':<12}{float(bs['equity_total']):>14,.2f}")
    right = float(bs["liabilities_total"]) + float(bs["equity_total"])
    print(f"    资产合计 {float(bs['assets_total']):,.2f}  vs  负债和权益合计 {right:,.2f}")

    tb = c.get("/api/trial-balance")["rows"]
    debit = sum(float(r["debit"]) for r in tb)
    credit = sum(float(r["credit"]) for r in tb)
    print(f"\n  【试算平衡】借方 {debit:,.2f} / 贷方 {credit:,.2f} / 差额 {debit - credit:,.2f}")

    return {
        "txn_count": len(txns),
        "balanced_sheet": bs["balanced"],
        "debit": debit, "credit": credit,
        "net_profit": float(ie["net_profit"]),
        "income": float(ie["income_total"]),
        "expense": float(ie["expense_total"]),
    }


def check(summary: dict) -> bool:
    hr("第五步：一致性校验")
    checks = [
        ("试算平衡：借方 = 贷方", abs(summary["debit"] - summary["credit"]) < 0.005),
        ("资产负债表：资产 = 负债 + 权益", summary["balanced_sheet"]),
        ("利润表：净利 = 收入 − 支出",
         abs(summary["net_profit"] - (summary["income"] - summary["expense"])) < 0.005),
        ("账本有实际数据（>200 笔）", summary["txn_count"] > 200),
    ]
    ok = True
    for name, passed in checks:
        print(f"  {'　通过' if passed else '　失败'}   {name}")
        ok = ok and passed

    for other in ("company", "personal"):
        p = ROOT / "backend" / "data" / "books" / other / "ledger.bean"
        has = any(l[:2].isdigit() and "*" in l for l in p.read_text(encoding="utf-8").splitlines())
        print(f"  {'　失败' if has else '　通过'}   账本隔离：{other} {'竟有交易' if has else '为空'}")
        ok = ok and not has
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--months", type=int, default=3, help="生成多少个月的历史流水")
    args = ap.parse_args()

    reset_test_book()
    c = Client(args.base)
    try:
        c.get("/api/books")
    except Exception as e:
        print(f"连不上后端 {args.base}：{e}\n请先启动：start.bat")
        return 1

    csv_text = build_history_csv(args.months, date.today())
    set_opening_balance(c, args.months)
    import_history(c, csv_text)
    run_chat_month(c)
    close_cost(c)
    summary = show_reports(c)
    ok = check(summary)

    hr("压力测试结束")
    print(f"  账本：{summary['txn_count']} 笔流水，全部校验{'通过' if ok else '有失败项'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
