"""检查项目实际在调用哪个大模型平台。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\check_llm.py
    .venv\\Scripts\\python.exe scripts\\check_llm.py --no-call    # 只看配置，不发请求

它会做三件事：
  1. 打印当前生效的 base_url / model / key 是否配置（绝不打印 key 内容）
  2. 告诉你配置是从哪里来的（.env / 环境变量 / API_data 令牌文件）
  3. 真实发一次最小请求，确认这个平台能通、模型名叫什么、延迟多少

注意：会真实消耗一次极小的 API 调用（几个 token）。
"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402

LINE = "─" * 62


def hr(title: str = "") -> None:
    print("\n" + LINE)
    if title:
        print(f"  {title}")
        print(LINE)


def describe_source() -> list[str]:
    """推断配置来自哪里，按优先级：环境变量 > backend/.env > 令牌文件 > 代码默认值。"""
    out = []
    env_file = config.BACKEND_DIR / ".env"
    out.append(f"backend/.env          {'存在' if env_file.exists() else '不存在'}")
    out.append(f"环境变量 DEEPSEEK_BASE_URL  {'已设置' if os.environ.get('DEEPSEEK_BASE_URL') else '未设置'}")
    out.append(f"环境变量 MODEL              {'已设置' if os.environ.get('MODEL') else '未设置'}")
    out.append(f"环境变量 DEEPSEEK_API_KEY   {'已设置' if os.environ.get('DEEPSEEK_API_KEY') else '未设置'}")
    out.append(f"API_data/令牌_模型.txt      {'存在' if config.TOKEN_FILE.exists() else '不存在'}")
    return out


def detect_platform(base_url: str) -> str:
    u = base_url.lower()
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
    return "其他 / 未知"


def live_call(timeout: int = 30) -> bool:
    """发一个最小请求，确认平台可用。"""
    import httpx

    url = f"{config.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": config.MODEL,
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
        "temperature": 0,
        "max_tokens": 16,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    print(f"  请求地址: {url}")
    t0 = time.time()
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        print(f"  ❌ 超时（{timeout}s）—— 网络不通，或平台地址写错")
        return False
    except httpx.HTTPError as exc:
        print(f"  ❌ 请求失败：{exc}")
        return False

    elapsed = time.time() - t0
    print(f"  HTTP 状态: {resp.status_code}　耗时 {elapsed:.2f}s")

    if resp.status_code == 401:
        print("  ❌ 401：key 无效，或者不属于这个平台")
        return False
    if resp.status_code == 403:
        print("  ❌ 403：没有权限（可能是平台未开通/算力未领取）")
        return False
    if resp.status_code == 404:
        print("  ❌ 404：地址不对，检查 base_url 是否要带 /v1")
        return False
    if resp.status_code >= 400:
        print(f"  ❌ 失败，返回：{resp.text[:300]}")
        return False

    data = resp.json()
    returned_model = data.get("model", "(未返回)")
    content = ""
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        pass
    print(f"  平台返回的模型名: {returned_model}")
    print(f"  模型回复: {(content or '(空)').strip()[:40]}")
    print("  ✅ 调用成功")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-call", action="store_true", help="只看配置，不发真实请求")
    args = ap.parse_args()

    platform = detect_platform(config.DEEPSEEK_BASE_URL)

    hr("当前生效的 LLM 配置")
    print(f"  base_url : {config.DEEPSEEK_BASE_URL}")
    print(f"  model    : {config.MODEL}")
    print(f"  key      : {'已配置' if config.DEEPSEEK_API_KEY else '未配置'}"
          f"（长度 {len(config.DEEPSEEK_API_KEY)}，内容不显示）")
    print(f"  平台判断 : {platform}")

    hr("配置来源（优先级从高到低）")
    print("  1. 环境变量  >  2. backend/.env  >  3. API_data/令牌_模型.txt  >  4. 代码默认值")
    for line in describe_source():
        print("  " + line)

    hr("平台判断")
    if platform == "workbuddy":
        print("  ✅ 正在使用 workbuddy")
    elif platform == "DMXAPI":
        print("  ⚠️ 正在使用 DMXAPI —— 不是 workbuddy")
        print("     参赛要求：仅限使用中国移动 workbuddy 联名版")
    else:
        print(f"  ❓ 当前平台：{platform}")
        print("     如果这已经是 workbuddy 的接口地址，可以忽略提示")

    if args.no_call:
        hr("跳过真实调用（--no-call）")
        return 0

    hr("真实调用测试")
    if not config.DEEPSEEK_API_KEY:
        print("  ❌ 没有配置 key，无法调用")
        return 1
    return 0 if live_call() else 1


if __name__ == "__main__":
    raise SystemExit(main())
