"""DeepSeek / DMXAPI 兼容客户端。

- 走 OpenAI 兼容的 /chat/completions
- 抽取类调用优先 JSON 结构化输出，失败自动降级为文本解析
- 统一超时、错误处理
"""

import json
import re
import time

import httpx

from app.llm import prompts
from app.llm.schemas import Extraction


class LLMError(Exception):
    """AI 调用失败。"""


def extract_json(text: str) -> dict:
    """从模型输出里提取 JSON 对象（容忍多余文字 / 代码块）。"""
    t = text.strip()
    # 去掉 ```json ``` 围栏
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"输出中没有 JSON 对象: {text[:200]!r}")
    return json.loads(t[start : end + 1])


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(
        self,
        messages: list[dict],
        json_mode: bool = False,
        temperature: float = 0,
        max_tokens: int = 2048,
        max_retries: int = 2,
    ) -> str:
        """调用 chat/completions，返回 content 文本。"""
        url = f"{self.base_url}/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code == 400 and json_mode and attempt == 0:
                    # 部分中转不支持 response_format，降级重试
                    del payload["response_format"]
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                if content is None:
                    raise LLMError("模型没有返回内容")
                return content
            except (httpx.HTTPError, KeyError, LLMError) as exc:
                last_err = exc
                time.sleep(0.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败: {last_err}")

    def extract(self, user_text: str, today: str, mappings: dict[str, str]) -> Extraction:
        """意图 + 字段抽取（结构化 JSON → Pydantic 校验）。"""
        sys_prompt = prompts.extract_system_prompt(today, mappings)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_text},
        ]
        content = self.complete(messages, json_mode=True)
        data = extract_json(content)
        return Extraction.model_validate(data)

    def map_categories(self, pairs: list[tuple[str, str]]) -> dict[str, str]:
        """把历史账单里的 (方向, 分类) 批量映射到科目代码。

        返回 {f"{direction}:{category}": account_code}。direction 为 income/expense。
        """
        if not pairs:
            return {}
        from app.llm.prompts import ACCOUNT_TABLE

        lines = []
        for direction, category in pairs:
            lines.append(f"- 方向={direction}，分类={category}")
        prompt = f"""你是记账助手。下面是从别人记账系统导出的历史账单里的「方向+分类」，
请把每一条映射到我们科目表中合适的科目代码。
科目表：
{ACCOUNT_TABLE}

规则：
- 方向=income 的分类必须映射到 Income:* 开头的科目。
- 方向=expense 的分类映射到 Expenses:* 或 Assets:Inventory / Assets:Fixed。
- 映射不出来的用 Expenses:Misc。

**进货一律映射到 Assets:Inventory，不要用 Expenses:Misc。**
原材料、食材、商品、包装等"买进来先存着、以后才卖掉"的东西都属于进货。

参考示例（务必保持同一分类的映射稳定）：
- expense:面粉 → Assets:Inventory        （食材，进货）
- expense:蔬菜 → Assets:Inventory
- expense:猪肉 → Assets:Inventory
- expense:鸡蛋 → Assets:Inventory
- expense:食用油 → Assets:Inventory
- expense:包装袋 → Assets:Inventory
- expense:香烟 → Assets:Inventory        （待售商品）
- expense:螺丝 → Assets:Inventory        （五金件，进货）
- expense:水管 → Assets:Inventory
- expense:电线 → Assets:Inventory
- expense:油漆 → Assets:Inventory
- expense:冰箱 → Assets:Fixed            （设备）
- expense:房租 → Expenses:Rent
- expense:电费 → Expenses:Utilities
- expense:燃气 → Expenses:Utilities
- expense:工资 → Expenses:Salary
- expense:人工费 → Expenses:Salary       （请工人的工钱）
- expense:安装费 → Expenses:Salary
- expense:工钱 → Expenses:Salary
- expense:税费 → Expenses:Tax
- expense:快递 → Expenses:Misc           （一次性消耗，不存货）
- income:包子 → Income:Sales
- income:利息 → Income:Other

输出严格 JSON：{{"income:分类名": "Income:Sales", "expense:分类名": "Expenses:..."}}，键的冒号用英文冒号。

待映射：
{"\n".join(lines)}
"""
        try:
            content = self.complete(
                [{"role": "system", "content": prompt}], json_mode=True, max_tokens=2048,
            )
            data = extract_json(content)
            result: dict[str, str] = {}
            from app.core import accounts

            for key, code in data.items():
                direction = key.split(":", 1)[0] if ":" in key else ""
                if direction not in ("income", "expense"):
                    direction = "expense"
                    if any(kw in str(key) for kw in ("收", "income")):
                        direction = "income"
                category = key.split(":", 1)[1] if ":" in key else str(key)
                if code not in accounts.all_codes():
                    code = "Income:Sales" if direction == "income" else "Expenses:Misc"
                result[f"{direction}:{category}"] = code
            return result
        except Exception:
            # AI 失败时用别名兜底
            from app.core import accounts

            result = {}
            for direction, category in pairs:
                code = ""
                if direction == "income":
                    code = "Income:Sales"
                else:
                    for alias, acc in accounts.alias_map().items():
                        if alias in category:
                            code = acc
                            break
                    if not code:
                        code = "Expenses:Misc"
                result[f"{direction}:{category}"] = code
            return result

    def reply(self, messages: list[dict], temperature: float = 0.3) -> str:
        """自由对话（小对话 / 报表翻译成白话）。"""
        content = self.complete(messages, json_mode=False, temperature=temperature, max_tokens=1024)
        return content.strip()
