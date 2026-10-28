"""交接文档自动同步：测试数 + API 路由 + 时间戳。

由 scripts/doc_sync_hook.py（PostToolUse hook）在每次源码改动后自动调用。
幂等：只替换文档里 <!-- AUTO-SYNC:status --> ... <!-- /AUTO-SYNC:status --> 之间的内容，
其余部分原样保留（静态经验、需求、坑等仍靠人工维护）。

用法:  python scripts/sync_docs.py
"""

import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

# 需要自动刷新的交接文档
DOCS = [
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "开发知识库.md",
]

# 页面目录说明也在 README 里，一并维护
README = ROOT / "README.md"


def count_tests() -> int:
    """用 pytest --collect-only 精确统计用例数；失败则退回正则统计。

    注意：测试模块 `from app...` 依赖 backend/ 在 sys.path，必须 cwd=backend。
    """
    try:
        r = subprocess.run(
            [str(VENV_PY), "-m", "pytest", "tests", "--collect-only", "-q",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=str(ROOT / "backend"), capture_output=True, text=True, timeout=120,
        )
        n = sum(1 for line in r.stdout.splitlines() if "::" in line)
        if n:
            return n
    except Exception:
        pass
    # 正则回退
    n = 0
    for f in (ROOT / "backend" / "tests").glob("*.py"):
        txt = f.read_text(encoding="utf-8")
        n += len(re.findall(r"^def test_", txt, re.M))
    return n


def api_routes() -> list[str]:
    """从 main.py 提取 API 路由。"""
    text = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    pairs = re.findall(r'@app\.(get|post|put|delete)\(\s*"([^"]+)"', text)
    return [f"`{m.upper()} {path}`" for m, path in pairs]


def replace_block(content: str, marker: str, block: str) -> str:
    """只替换 marker 块之间的内容；没有 marker 则原样返回。"""
    start = f"<!-- AUTO-SYNC:{marker} -->"
    end = f"<!-- /AUTO-SYNC:{marker} -->"
    if start not in content or end not in content:
        return content
    pre, _ = content.split(start, 1)
    _, post = content.split(end, 1)
    return pre + start + "\n" + block.rstrip() + "\n" + end + post


def build_status() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tests = count_tests()
    routes = api_routes()
    lines = [
        f"- 🔄 最后自动同步：{now}（源码改动后自动刷新，勿手改此块）",
        f"- 🧪 测试用例数：**{tests}**（`backend/tests/`，pytest 统计）",
        f"- 📡 API 路由（{len(routes)} 个）：",
    ]
    lines += [f"    {r}" for r in routes]
    return "\n".join(lines)


def main() -> None:
    status = build_status()
    for p in DOCS:
        if not p.exists():
            continue
        c = p.read_text(encoding="utf-8")
        nc = replace_block(c, "status", status)
        if nc != c:
            p.write_text(nc, encoding="utf-8")
            print(f"[sync] 已更新 {p.relative_to(ROOT)}")
        else:
            print(f"[sync] 无变化 {p.relative_to(ROOT)}（未找到 marker 或内容相同）")


if __name__ == "__main__":
    main()
