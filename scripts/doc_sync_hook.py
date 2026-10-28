"""PostToolUse hook 入口：源码改动后自动刷新交接文档。

由 .claude/settings.local.json 的 hooks 调用。只响应 backend/app、backend/tests、
frontend、backend/requirements 的 Write/Edit；跳过文档自身（避免死循环）。

用法:  echo '<hook stdin json>' | .venv/Scripts/python.exe scripts/doc_sync_hook.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
SYNC = ROOT / "scripts" / "sync_docs.py"

# 命中这些路径的改动才触发同步
TRIGGERS = ("/backend/app/", "/backend/tests/", "/frontend/", "/backend/requirements")


def changed_file() -> str:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return ""
    ti = data.get("tool_input") or {}
    tr = data.get("tool_response") or {}
    return ti.get("file_path") or tr.get("filePath") or ""


def main() -> None:
    fp = changed_file().replace("\\", "/")
    if not fp or not any(t in fp for t in TRIGGERS):
        print(f"[doc_sync_hook] 跳过 {fp or '<无路径>'}")
        return
    if not VENV_PY.exists():
        print("[doc_sync_hook] 未找到虚拟环境 python")
        return
    try:
        subprocess.run([str(VENV_PY), str(SYNC)], cwd=str(ROOT), timeout=120)
    except Exception as exc:  # hook 不阻塞主流程
        print(f"[doc_sync_hook] 同步失败：{exc}")


if __name__ == "__main__":
    main()
