"""全局配置：从 .env 读取，未配置时给默认值。

同时支持两种运行方式：
- 源码运行：数据在 backend/data，配置在 backend/.env
- PyInstaller 打包的 exe：数据在 exe 同目录的 data/，配置在 exe 同目录的 .env，
  前端等只读资源从打包解压目录（_MEIPASS）读取
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 是否运行在 PyInstaller 打包的 exe 里
FROZEN = bool(getattr(sys, "frozen", False))

BACKEND_DIR = Path(__file__).resolve().parents[1]  # backend/

if FROZEN:
    # exe 所在目录：放持久化的账本/数据库/.env（_MEIPASS 是临时目录，重启会丢）
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    # 打包进去的只读资源（frontend 等）解压在 _MEIPASS
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    ENV_FILE = PROJECT_ROOT / ".env"
    DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
else:
    PROJECT_ROOT = BACKEND_DIR.parent
    RESOURCE_DIR = PROJECT_ROOT
    ENV_FILE = BACKEND_DIR / ".env"
    DEFAULT_DATA_DIR = BACKEND_DIR / "data"

# 允许用环境变量覆盖数据目录（打包启动器会设置成 exe 同目录）
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", str(DEFAULT_DATA_DIR)))

# 允许用户从项目根目录放一个 API_data/令牌_模型.txt 作为兜底
TOKEN_FILE = PROJECT_ROOT / "API_data" / "令牌_模型.txt"


def _load_env() -> None:
    load_dotenv(ENV_FILE)
    # 若用户没有配 key，则从令牌文件读取
    if not os.environ.get("DEEPSEEK_API_KEY") and TOKEN_FILE.exists():
        lines = TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            os.environ.setdefault("DEEPSEEK_API_KEY", lines[0].strip())
        if len(lines) > 1:
            os.environ.setdefault("MODEL", lines[1].strip())


_load_env()

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("MODEL", "deepseek-flash")

DATA_DIR.mkdir(parents=True, exist_ok=True)
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", str(DATA_DIR / "ledger.bean")))
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR / "app.db")))

# 确认草稿超时（秒）
DRAFT_TTL_SECONDS = int(os.environ.get("DRAFT_TTL_SECONDS", "600"))
