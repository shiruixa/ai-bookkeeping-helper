"""构建参赛发布包到 Competition/。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts\\build_release.py

会自动排除：
- 密钥与私有数据：backend/.env、API_data/、backend/data/
- 运行环境与缓存：.venv/、__pycache__/、.pytest_cache/、*.pyc
- 参考资料与备份：Study/、_backup_before_fix/
- 内部材料：docs/参赛报名-填报内容.md
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Competition"

# 任意层级出现的目录名都排除
EXCLUDE_NAMES = {
    ".venv", "__pycache__", ".pytest_cache", ".git", ".claude",
    "Study", "_backup_before_fix", "Competition", "API_data",
}
# 相对路径前缀排除
EXCLUDE_PATH_PREFIXES = (
    "backend/data",
    "backend/.venv",
)
# 文件级排除
EXCLUDE_FILES = {
    "backend/.env",                    # 含真实 key
    "scripts/release_README.md",       # 仅作模板，会单独复制为 Competition/README.md
    ".DS_Store", "Thumbs.db",
}
# 内部材料：不随作品提交
EXCLUDE_PREFIXES = (
    "docs/参赛报名-填报内容.md",
)
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".mhtml", ".log", ".sqlite", ".db")


def ignored(rel: Path) -> bool:
    posix = rel.as_posix()
    if any(part in EXCLUDE_NAMES for part in rel.parts):
        return True
    if any(posix == p or posix.startswith(p + "/") for p in EXCLUDE_PATH_PREFIXES):
        return True
    if posix in EXCLUDE_FILES:
        return True
    if any(posix == p for p in EXCLUDE_PREFIXES):
        return True
    if rel.name.endswith(EXCLUDE_SUFFIXES):
        return True
    return False


def main() -> int:
    if OUT.exists():
        print(f"清空旧目录：{OUT}")
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    copied = 0
    for src in ROOT.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(ROOT)
        if ignored(rel):
            continue
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    print(f"已复制 {copied} 个文件 → {OUT}")

    # 数据目录占位（运行时自动生成账本，这里只保留空目录）
    (OUT / "backend" / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "backend" / "data" / ".gitkeep").write_text("", encoding="utf-8")

    # 用面向评审的发布说明替换根目录的开发版 README
    template = ROOT / "scripts" / "release_README.md"
    if template.exists():
        shutil.copy2(template, OUT / "README.md")
        print("已生成 Competition/README.md（发布说明）")

    # 安全检查：发布包里不能出现真实 key 文件
    leaks = []
    for forbidden in ("backend/.env", "API_data/令牌_模型.txt"):
        if (OUT / forbidden).exists():
            leaks.append(forbidden)
    if leaks:
        print("❌ 安全错误：发布包里出现了敏感文件：" + ", ".join(leaks))
        return 1
    print("✅ 安全检查通过：未包含 .env / 令牌文件")

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"发布包大小：{total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
