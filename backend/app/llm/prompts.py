"""提示词：意图抽取 / 查询翻译 / 小对话。集中管理，便于调优。"""

import json

from app.core import accounts

# 科目表（给 AI 用）
ACCOUNT_TABLE = accounts.prompt_table()

# 输出 JSON 结构说明
JSON_SCHEMA_HINT = """\
输出严格 JSON，不要任何多余文字。结构如下：
{
  "intent": "record | query | correct | setup | smalltalk",
  "transaction": {   // 仅 intent=record/setup 时
    "type": "expense | income | transfer",
    "amount": 数字(元),
    "date": "YYYY-MM-DD",
    "payee": "对方/商家，没有就 null",
    "category": "口语分类，如 面粉、房租、水电",
    "account": "你选定的科目代码(见科目表)",
    "account_alternatives": ["备选科目代码"],
    "cash_account": "支出/收入走现金还是银行/微信/支付宝，代码",
    "note": "备注",
    "confidence": "high | medium | low"
  },
  "query": {   // 仅 intent=query 时
    "report": "today | month | balance | category | trend | custom",
    "date_from": "YYYY-MM-DD 或 null",
    "date_to": "YYYY-MM-DD 或 null",
    "category": "查询的商品/分类关键词，或 null",
    "account": "科目代码，或 null"
  },
  "correct": {   // 仅 intent=correct 时
    "target_date": "要改的账日期或 null",
    "target_keyword": "要改的账关键词，如 面粉",
    "target_time": "要改的账是几点，如 \"15\" 或 \"15:30\"；没提到时刻就 null",
    "new_amount": 新金额或 null,
    "new_category": "新分类或 null",
    "delete": true/false
  }
}
"""

EXAMPLES = [
    (
        "今天买了300块的面粉",
        {
            "intent": "record",
            "transaction": {
                "type": "expense", "amount": 300, "date": "{today}",
                "payee": None, "category": "面粉",
                "account": "Assets:Inventory", "account_alternatives": ["Expenses:COGS"],
                "cash_account": "Assets:Cash", "note": None, "confidence": "high",
            },
            "query": None, "correct": None,
        },
    ),
    (
        "上午卖了500块馒头",
        {
            "intent": "record",
            "transaction": {
                "type": "income", "amount": 500, "date": "{today}",
                "payee": None, "category": "馒头",
                "account": "Income:Sales", "account_alternatives": [],
                "cash_account": "Assets:Cash", "note": "上午", "confidence": "high",
            },
            "query": None, "correct": None,
        },
    ),
    (
        "微信收了一笔货款1280",
        {
            "intent": "record",
            "transaction": {
                "type": "income", "amount": 1280, "date": "{today}",
                "payee": None, "category": "货款",
                "account": "Income:Sales", "account_alternatives": [],
                "cash_account": "Assets:Bank", "note": "微信收款", "confidence": "high",
            },
            "query": None, "correct": None,
        },
    ),
    (
        "这个月赚了多少",
        {
            "intent": "query",
            "transaction": None,
            "query": {"report": "month", "date_from": None, "date_to": None,
                      "category": None, "account": None},
            "correct": None,
        },
    ),
    (
        "今天卖了多少",
        {
            "intent": "query",
            "transaction": None,
            "query": {"report": "today", "date_from": None, "date_to": None,
                      "category": None, "account": None},
            "correct": None,
        },
    ),
    (
        "我还有多少现金",
        {
            "intent": "query",
            "transaction": None,
            "query": {"report": "balance", "date_from": None, "date_to": None,
                      "category": None, "account": "Assets:Cash"},
            "correct": None,
        },
    ),
    (
        "近7天收支怎么样",
        {
            "intent": "query",
            "transaction": None,
            "query": {"report": "trend", "date_from": None, "date_to": None,
                      "category": None, "account": None},
            "correct": None,
        },
    ),
    (
        "面粉这个月花了多少",
        {
            "intent": "query",
            "transaction": None,
            "query": {"report": "category", "date_from": None, "date_to": None,
                      "category": "面粉", "account": None},
            "correct": None,
        },
    ),
    (
        "昨天的面粉那笔记错了，是480",
        {
            "intent": "correct",
            "transaction": None,
            "query": None,
            "correct": {"target_date": "{yesterday}", "target_keyword": "面粉",
                        "new_amount": 480, "new_category": None, "delete": False},
        },
    ),
    (
        "昨天下午3点那笔耗材记错了，是80块",
        {
            "intent": "correct",
            "transaction": None,
            "query": None,
            "correct": {"target_date": "{yesterday}", "target_keyword": "耗材",
                        "target_time": "15", "new_amount": 80,
                        "new_category": None, "delete": False},
        },
    ),
    (
        "把昨天那笔房租删了",
        {
            "intent": "correct",
            "transaction": None,
            "query": None,
            "correct": {"target_date": "{yesterday}", "target_keyword": "房租",
                        "new_amount": None, "new_category": None, "delete": True},
        },
    ),
]


def extract_system_prompt(today: str, mappings: dict[str, str]) -> str:
    mapping_lines = "\n".join(f"- {k} -> {v}" for k, v in mappings.items()) or "（暂无）"
    today_d = today
    # 示例里的相对日期换算（today 为 YYYY-MM-DD）
    from datetime import date as _date, timedelta
    d = _date.fromisoformat(today_d)
    params = {
        "today": today_d,
        "yesterday": (d - timedelta(days=1)).isoformat(),
    }
    example_lines = "\n".join(
        f"用户: {u}\n助手: {json.dumps({k: (v.format(**params) if isinstance(v, str) else ({kk: (vv.format(**params) if isinstance(vv, str) else vv) for kk, vv in v.items()} if isinstance(v, dict) else v)) for k, v in e.items()}, ensure_ascii=False)}"
        for u, e in EXAMPLES
    )
    return f"""\
你是中文记账助手，帮助个体户小老板把日常口语记账为专业复式账。
今天日期是 {today}。所有相对日期（昨天/前天/3号/上个月）都要换算成具体日期。

科目表（账户代码必须来自这里）：
{ACCOUNT_TABLE}

用户确认过的分类映射（优先采用）：
{mapping_lines}

{JSON_SCHEMA_HINT}

判断用户意图：
- record：要记一笔收支/转账。金额或分类没给全时，missing 信息也返回，但 confidence 标 low。
- query：在问报表/余额/收入支出。
- correct：在改或删已记的账。
- setup：首次建账，提到 初始/本钱/投入/启动资金/开店。按 transfer 处理，category=初始资本，account=Equity:Capital。
- smalltalk：打招呼或其他不记账的话。

金额一律换算成元。说话含糊（如"几百块""大概"）时 confidence=low，amount 给 null。
注意：收入类不要漏 payee 为 null 的情况。

参考示例：
{example_lines}

现在抽取下面用户的话：
"""
