"""完整业务流程模拟：在「测试账本」上跑一个完整经营周期，最后打印所有会计表。

用途：
  1. 面试演示前自检 —— 一条命令验证全链路可用
  2. 展示多账本隔离 —— 跑完会断言公司账本/个人账本仍是空的

它会真实调用 AI（每步约 2-8 秒），全程约 40 秒。

用法：
    # 先启动后端：start.bat  或  cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
    python scripts/demo_simulation.py            # 跑前自动清空测试账本
    python scripts/demo_simulation.py --keep     # 保留测试账本已有数据，追加记账
"""

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BOOK = "test"                      # 本模拟一律作用在「测试账本」
BOOK_DIR = ROOT / "backend" / "data" / "books" / BOOK

# 完整的一个经营周期：(用户说的话, 是否紧接着说"确认")
SCRIPT: list[tuple[str, bool]] = [
    ("我开店投了3万现金", True),          # 建账 → 借 现金 / 贷 初始资本
    ("今天买了300块面粉", True),          # 采购 → 借 存货（资产，不是费用）
    ("今天卖了500块馒头", True),          # 销售 → 贷 销售收入
    ("这个月房租交了3000", True),         # 费用：租金
    ("电费花了200", True),                # 费用：水电
    ("给员工发了2000块工资", True),        # 费用：工资
    ("把今天那笔面粉改成480", True),       # 纠错 → 整账重写
    ("这个月赚了多少", False),            # 自然语言查询
]

LINE = "─" * 66


def hr(title: str = "") -> None:
    print("\n" + LINE)
    if title:
        print(f"  {title}")
        print(LINE)


def reset_test_book() -> None:
    """把测试账本清空，给演示一个干净起点。

    引擎每次操作都重新读文件、Memory 每次调用都新开连接，
    所以直接删文件再重建即可，不需要重启后端。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core import books

    for p in BOOK_DIR.glob("*"):
        p.unlink()
    books._materialize(BOOK, "测试账本")
    print(f"已清空测试账本：{BOOK_DIR}")


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=120, headers={"X-Book-Id": BOOK})

    def chat(self, message: str) -> dict:
        r = self.http.post(f"{self.base}/api/chat", json={"message": message})
        r.raise_for_status()
        return r.json()

    def get(self, path: str) -> dict:
        r = self.http.get(f"{self.base}{path}")
        r.raise_for_status()
        return r.json()


def run_conversation(c: Client) -> int:
    hr("第一步：自然语言记账（真实调用 AI）")
    ok = 0
    for i, (message, do_confirm) in enumerate(SCRIPT, 1):
        d = c.chat(message)
        reply = (d.get("reply") or "").replace("\n", "\n      ")
        print(f"\n[{i}] 用户：{message}")
        print(f"    助手：{reply}")
        print(f"    状态：{d.get('state')}")

        if do_confirm:
            if d.get("state") != "awaiting_confirm":
                print("    ⚠️ 没有进入待确认态，跳过确认（AI 未产出草稿）")
                continue
            d2 = c.chat("确认")
            print(f"    助手：{(d2.get('reply') or '').replace(chr(10), chr(10) + '      ')}")
            ok += 1
        else:
            ok += 1
    print(f"\n共完成 {ok}/{len(SCRIPT)} 步")
    return ok


def show_balances(c: Client) -> None:
    hr("账户余额")
    bals = c.get("/api/balances")["balances"]
    if not bals:
        print("  （空）")
    for k, v in sorted(bals.items()):
        print(f"  {k:<28} {float(v):>12,.2f}")


def show_income(c: Client) -> None:
    hr("利润表（本月）")
    d = c.get("/api/income-statement?period=month")
    print(f"  {'项目':<20}{'金额（元）':>14}")
    for r in d["income"]:
        print(f"  {r['name']:<20}{float(r['amount']):>14,.2f}")
    print(f"  {'收入合计':<20}{float(d['income_total']):>14,.2f}")
    for r in d["expense"]:
        print(f"  {r['name']:<20}{-float(r['amount']):>14,.2f}")
    print(f"  {'支出合计':<20}{-float(d['expense_total']):>14,.2f}")
    print(f"  {'净利润':<20}{float(d['net_profit']):>14,.2f}")


def show_balance_sheet(c: Client) -> None:
    hr("资产负债表")
    d = c.get("/api/balance-sheet")
    for label, rows, total in (
        ("资产", d["assets"], d["assets_total"]),
        ("负债", d["liabilities"], d["liabilities_total"]),
    ):
        print(f"  【{label}】")
        for r in rows:
            print(f"    {r['name']:<18}{float(r['amount']):>14,.2f}")
        print(f"    {'合计':<18}{float(total):>14,.2f}")
    print("  【权益】")
    for r in d["equity"]:
        print(f"    {r['name']:<18}{float(r['amount']):>14,.2f}")
    print(f"    {'未分配利润':<18}{float(d['undistributed_profit']):>14,.2f}")
    print(f"    {'合计':<18}{float(d['equity_total']):>14,.2f}")
    mark = "✅ 资产 = 负债 + 权益，账目平衡" if d["balanced"] else "⚠️ 不平衡"
    print(f"\n  {mark}")


def show_trial_balance(c: Client) -> None:
    hr("科目余额表（试算平衡）")
    rows = c.get("/api/trial-balance")["rows"]
    print(f"  {'科目':<14}{'借方':>13}{'贷方':>13}{'余额':>13}")
    td = tc = tb = 0.0
    for r in rows:
        d_, c_, b_ = float(r["debit"]), float(r["credit"]), float(r["balance"])
        td += d_; tc += c_; tb += b_
        print(f"  {r['name']:<14}{d_:>13,.2f}{c_:>13,.2f}{b_:>13,.2f}")
    print(f"  {'合计':<14}{td:>13,.2f}{tc:>13,.2f}{tb:>13,.2f}")
    print(f"\n  借贷合计相等？{'✅ 是' if abs(td - tc) < 0.005 else '❌ 否'}   "
          f"余额之和为 0？{'✅ 是' if abs(tb) < 0.005 else '❌ 否'}")


def show_account_ledger(c: Client) -> None:
    hr("现金明细账")
    rows = c.get("/api/account-ledger?account=Assets:Cash")["rows"]
    if not rows:
        print("  （无）")
    for r in rows:
        print(f"  {r['date']}  {r['narration']:<16}{float(r['amount']):>12,.2f}"
              f"   余额 {float(r['balance']):>12,.2f}")


def show_ledger_file() -> None:
    hr("账本原文（Beancount .bean，可直接交给会计）")
    text = (BOOK_DIR / "ledger.bean").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("2016-01-01 open"):
            continue                      # 开科目指令太长，跳过
        print("  " + line)


def verify_isolation(c: Client) -> bool:
    hr("多账本隔离验证")
    good = True
    for other in ("company", "personal"):
        path = ROOT / "backend" / "data" / "books" / other / "ledger.bean"
        text = path.read_text(encoding="utf-8")
        has_txn = any(l[:2].isdigit() and "*" in l for l in text.splitlines())
        mark = "❌ 有交易（隔离失败）" if has_txn else "✅ 无交易（干净）"
        print(f"  {other:<10}{mark}")
        good = good and not has_txn
    return good


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--keep", action="store_true", help="不清空测试账本")
    args = ap.parse_args()

    if not args.keep:
        reset_test_book()

    c = Client(args.base)
    try:
        c.get("/api/books")
    except Exception as e:
        print(f"❌ 连不上后端 {args.base}：{e}\n   请先启动：start.bat")
        return 1

    books_list = c.get("/api/books")["books"]
    print("账本列表：" + "、".join(f"{b['emoji']}{b['name']}({b['id']})" for b in books_list))
    print(f"本次模拟使用：🧪 测试账本（{BOOK}）")

    run_conversation(c)
    show_balances(c)
    show_income(c)
    show_balance_sheet(c)
    show_trial_balance(c)
    show_account_ledger(c)
    show_ledger_file()
    ok = verify_isolation(c)

    hr("模拟结束")
    print("  测试账本已跑完一个完整经营周期。" + ("三本账本隔离正常。" if ok else "⚠️ 隔离异常！"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
