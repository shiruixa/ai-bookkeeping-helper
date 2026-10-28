"""流程通用类型与常量。"""

from dataclasses import dataclass, field

# 会话状态 key（memory kv 里存当前流程状态）
FLOW_STATE = "flow_state"


@dataclass
class FlowReply:
    """一次对话的回复。"""
    text: str
    quick_replies: list[str] = field(default_factory=list)
    state: str = "idle"           # idle | awaiting_confirm
    kind: str | None = None       # record | correct
    draft_id: int | None = None


# 确认/修改/取消 关键词
CONFIRM_WORDS = ("确认", "确定", "对的", "没错", "可以", "嗯", "好", "好的", "行", "是的", "ok", "OK", "同意", "就这样")
CANCEL_WORDS = ("取消", "算了", "不记", "别记", "不要", "撤销", "放弃")
# 注意：「不行」「不好」这类否定必须放在 EDIT_WORDS 里，且 EDIT_WORDS 要先于
# CONFIRM_WORDS 判断 —— 否则子串匹配会命中「行」「好」，把否定当成确认直接落账。
EDIT_WORDS = (
    "不对", "错了", "不是", "修改", "改成", "改一下", "改成一", "其实是", "错啦",
    "记到", "记成", "放到", "金额", "是银行", "是现金", "走微信", "走支付宝",
    "用微信", "用支付宝", "删掉", "删除", "删了",
    "不行", "不好", "不可以", "不合适", "不对劲", "不满意",
)

# 紧跟在确认词前面的否定字：「不行」「不好」「别记」里的 行/好 不是确认
NEG_PREFIX = ("不", "别", "甭")

# 记账确认态里说这些 = 这笔不记了
DELETE_WORDS = ("删了", "删掉", "删除", "不要了")


def affirms(text: str) -> bool:
    """是否为「确认」，并排除否定式。

    CONFIRM_WORDS 含「好」「行」这类单字，直接用 `in` 判断会把「不行」「不好」
    判成确认。这里逐次查找确认词，只要它紧跟在否定字后面就跳过。
    """
    low = text.lower()
    for w in CONFIRM_WORDS:
        w_low = w.lower()
        start = 0
        while True:
            i = low.find(w_low, start)
            if i == -1:
                break
            if i == 0 or low[i - 1] not in NEG_PREFIX:
                return True
            start = i + 1  # 该位置是否定的，继续往后找
    return False
