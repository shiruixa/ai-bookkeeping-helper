"""可执行程序入口（PyInstaller 打包用）。

双击 exe 后：
  1. 在 exe 同目录创建 data/ 存放账本和数据库
  2. 启动本地服务
  3. 自动打开浏览器
  4. 关闭控制台窗口即停止服务

源码运行时也可以直接用：
    .venv\\Scripts\\python.exe scripts\\launcher.py
"""

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def _base_dir() -> Path:
    """exe 所在目录（打包后）或项目根目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _pick_port(host: str, start: int = 8000, tries: int = 12) -> int:
    """从 start 开始找一个空闲端口。"""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return start


def main() -> int:
    base = _base_dir()
    # 数据放在 exe 旁边（持久化），而不是 PyInstaller 的临时解压目录
    os.environ.setdefault("APP_DATA_DIR", str(base / "data"))
    (base / "data").mkdir(parents=True, exist_ok=True)

    # 源码运行时需要把 backend/ 加进搜索路径
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(base / "backend"))

    try:
        import uvicorn

        from app.main import app
    except Exception as exc:  # noqa: BLE001
        print("启动失败：", exc)
        print("\n请确认已安装依赖：pip install -r backend/requirements.txt")
        _pause()
        return 1

    host = "127.0.0.1"
    port = _pick_port(host)
    url = f"http://{host}:{port}"

    print("=" * 54)
    print("  一句话记账 · AI 复式记账助手")
    print("=" * 54)
    print(f"  地址：{url}")
    print(f"  数据：{base / 'data'}")
    print("  关闭此窗口即停止服务")
    print("=" * 54)

    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        print("\n服务异常退出：", exc)
        _pause()
        return 1
    return 0


def _pause() -> None:
    try:
        input("\n按回车键退出...")
    except EOFError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
