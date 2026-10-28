"""科目表（Chart of Accounts）—— 个体户简化版，但保持复式记账的专业性。

每个科目包含:
- code: Beancount 账户全名（Assets:xxx）
- name: 中文显示名
- aliases: 口语关键词（用于 AI 账户映射，以及给 AI 的提示词）
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    kind: str  # asset | liability | equity | income | expense
    aliases: tuple[str, ...] = field(default_factory=tuple)
    # 是否允许用户直接记账（income/expense 全部允许，Equity:Capital 只建账用）
    bookable: bool = True


CHART: list[Account] = [
    # 资产
    Account("Assets:Cash", "现金", "asset", ("现金", "零钱", "钱包", "现钞")),
    Account("Assets:Bank", "银行存款", "asset", ("银行", "银行卡", "微信", "支付宝", "存款")),
    Account("Assets:Inventory", "存货", "asset", ("存货", "库存", "商品", "原材料", "进货")),
    Account("Assets:Fixed", "固定资产", "asset", ("固定资产", "设备", "冰箱", "柜台", "三轮车")),
    Account("Assets:Receivable", "应收", "asset", ("应收", "欠款", "赊账", "白条")),
    # 负债
    Account("Liabilities:Payable", "应付", "liability", ("应付", "欠", "赊购", "进货款未付")),
    Account("Liabilities:Loan", "借款", "liability", ("借款", "贷款", "借的")),
    # 权益
    Account("Equity:Capital", "初始资本", "equity", ("初始", "本钱", "投入", "启动资金"), bookable=False),
    Account("Equity:Drawings", "业主提款", "equity", ("提款", "自己拿", "家用", "生活费")),
    Account("Equity:OpeningBalances", "期初余额", "equity", ("期初", "接手", "结转", "之前"), bookable=False),
    # 收入
    Account("Income:Sales", "销售收入", "income", ("销售", "卖货", "收入", "营业额", "收款", "赚")),
    Account("Income:Other", "其他收入", "income", ("其他收入", "补贴", "退款", "返利", "利息")),
    # 费用
    Account("Expenses:COGS", "营业成本", "expense", ("成本", "进货成本", "成本价")),
    Account("Expenses:Rent", "租金", "expense", ("租金", "房租", "店租", "摊位费")),
    Account("Expenses:Utilities", "水电", "expense", ("水电", "电费", "水费", "燃气")),
    Account("Expenses:Salary", "工资", "expense", ("工资", "人工", "请人", "员工")),
    Account("Expenses:Tax", "税费", "expense", ("税费", "税", "发票税", "社保")),
    Account("Expenses:Misc", "杂费", "expense", ("杂费", "杂项", "其他费用", "快递", "打包", "耗材")),
]

# 按 kind 索引
BY_CODE: dict[str, Account] = {a.code: a for a in CHART}
BY_KIND: dict[str, list[Account]] = {}
for a in CHART:
    BY_KIND.setdefault(a.kind, []).append(a)

# 展示用的树（Fava / 前端渲染用）
TREE: dict[str, list[Account]] = {
    "资产": BY_KIND["asset"],
    "负债": BY_KIND["liability"],
    "权益": BY_KIND["equity"],
    "收入": BY_KIND["income"],
    "费用": BY_KIND["expense"],
}


def get(code: str) -> Account:
    return BY_CODE[code]


def all_codes() -> list[str]:
    return list(BY_CODE.keys())


def bookable_codes() -> list[str]:
    return [a.code for a in CHART if a.bookable]


def alias_map() -> dict[str, str]:
    """口语关键词 -> 账户代码。用于关键词兜底匹配。"""
    m: dict[str, str] = {}
    for a in CHART:
        for alias in a.aliases:
            m[alias] = a.code
    return m


def prompt_table() -> str:
    """给 AI 看的科目表文本（含中文名与常用口语别名）。"""
    lines = []
    for a in CHART:
        line = f"- {a.code}  {a.name}"
        if a.aliases:
            line += " （" + "、".join(a.aliases) + "）"
        if not a.bookable:
            line += " [仅建账用]"
        lines.append(line)
    return "\n".join(lines)
