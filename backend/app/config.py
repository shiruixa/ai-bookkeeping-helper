"""全局配置：从 backend/.env 读取，未配置时给默认值。"""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]  # backend/
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"

# 允许用户从项目根目录放一个 API_data/令牌_模型.txt 作为兜底
TOKEN_FILE = PROJECT_ROOT / "API_data" / "令牌_模型.txt"


def _load_env() -> None:
    load_dotenv(BACKEND_DIR / ".env")
    # 若用户没有配 key，则从令牌文件读取
    if not os.environ.get("DEEPSEEK_API_KEY") and TOKEN_FILE.exists():
        lines = TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            os.environ.setdefault("DEEPSEEK_API_KEY", lines[0].strip())
        if len(lines) > 1:
            os.environ.setdefault("MODEL", lines[1].strip())


_load_env()

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://www.dmxapi.cn/v1")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("MODEL", "deepseek-v4-flash-guan")

DATA_DIR.mkdir(parents=True, exist_ok=True)
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", str(DATA_DIR / "ledger.bean")))
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR / "app.db")))

# 确认草稿超时（秒）
DRAFT_TTL_SECONDS = int(os.environ.get("DRAFT_TTL_SECONDS", "600"))
