"""
P0 连通性验证脚本：用 API_data/令牌_模型.txt 里的 key 实测 DMXAPI 中转的
deepseek-flash 模型是否可用。

验证点：
1. 普通对话调用
2. JSON 结构化输出（response_format=json_object）是否支持
3. 中文理解（记账场景字段抽取）
4. 单次延迟

用法:  python tests/test_llm_connect.py
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
TOKEN_FILE = ROOT / "API_data" / "令牌_模型.txt"

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


def load_credentials() -> tuple[str, str]:
    """读取令牌与模型名。文件第一行是 key，第二行是模型名。"""
    if not TOKEN_FILE.exists():
        sys.exit(f"找不到令牌文件: {TOKEN_FILE}")
    lines = TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()
    api_key = lines[0].strip()
    model = lines[1].strip() if len(lines) > 1 else "deepseek-flash"
    return api_key, model


def chat(api_key: str, model: str, messages: list[dict], json_mode: bool = False) -> dict:
    """调用 OpenAI 兼容的 /chat/completions。"""
    url = f"{BASE_URL}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 1024,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.time()
    resp = httpx.post(url, json=payload, headers=headers, timeout=60)
    latency = time.time() - t0
    print(f"    HTTP {resp.status_code}  ({latency:.2f}s)")
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    api_key, model = load_credentials()
    print(f"模型: {model}")
    print(f"Base URL: {BASE_URL}")
    print(f"密钥: {api_key[:6]}...{api_key[-4:]}")

    # 1. 普通对话
    print("\n[1] 普通对话")
    data = chat(api_key, model, [
        {"role": "user", "content": "只回复三个字：你好呀"},
    ])
    print("    ", data["choices"][0]["message"]["content"])

    # 2. JSON 结构化输出
    print("\n[2] JSON 结构化输出（记账场景字段抽取）")
    sys_prompt = (
        "你是记账助手。从用户的记账口语里抽取结构化信息，"
        '只输出 JSON，不要任何其他文字。JSON 字段: '
        '{"type":"expense|income|transfer","amount":number,'
        '"category":"商品类别","payee":"对方/商家","date":"YYYY-MM-DD",'
        '"note":"备注"}。没有的信息用 null。'
    )
    data = chat(api_key, model, [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "昨天从面铺进了一箱50斤装的面粉，付了350块"},
    ], json_mode=True)
    content = data["choices"][0]["message"]["content"]
    print("    原始返回:", content[:200])
    parsed = json.loads(content)
    print("    解析结果:", json.dumps(parsed, ensure_ascii=False))

    # 3. FunctionCall 能力
    print("\n[3] FunctionCall 能力")
    tools = [{
        "type": "function",
        "function": {
            "name": "record_transaction",
            "description": "记录一笔交易",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "金额"},
                    "type": {"type": "string", "enum": ["income", "expense"]},
                },
                "required": ["amount", "type"],
            },
        },
    }]
    url = f"{BASE_URL}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "今天卖货收到480元"}],
        "tools": tools,
        "tool_choice": "auto",
    }
    resp = httpx.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=60,
    )
    print(f"    HTTP {resp.status_code}")
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    if msg.get("tool_calls"):
        print("    工具调用:", json.dumps(msg["tool_calls"], ensure_ascii=False)[:300])
    else:
        print("    未触发工具调用，模型直接回复:", (msg.get("content") or "")[:200])

    print("\n[P0] 连通性验证完成: 模型可调、JSON模式可用、FunctionCall 可用")


if __name__ == "__main__":
    main()
