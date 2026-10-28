# CLAUDE.md — 给接手本项目的 AI 的总纲

> 阅读本文件即可快速接手项目。更详细的开发经验与需求背景见 [docs/开发知识库.md](docs/开发知识库.md)。
> 本项目已完成 MVP 全流程（记账/看账/导入/期初/纠错），下一步是打磨与扩展。

## 项目一句话

给个体户小老板（尤其不懂专业记账的店长）用的 **自然语言 AI 记账助手**：
底层 Beancount 专业复式记账 → 中间层 DeepSeek V4 Flash（走 DMXAPI 中转）→ 上层自然语言聊天。
**AI 永不直接落账，必须过硬校验 + 用户确认。**

## 当前状态（已完成）

- ✅ P0–P7：对话记账（解析→确认→落账→反馈）、查询报表、纠错、前端 UI
- ✅ 看账：利润表 / 资产负债表（恒等式校验）/ 科目余额（试算平衡）/ 现金·银行明细账 / 导出 .bean
- ✅ 数据迁移：期初余额结转 + 微信/支付宝/银行 CSV·Excel 导入（AI 批量分类 + 预览确认）
- 后端可访问 `http://127.0.0.1:8000`（需先 `start.bat` 或 uvicorn 启动）

<!-- AUTO-SYNC:status -->
- 🔄 最后自动同步：2026-08-03 13:05（源码改动后自动刷新，勿手改此块）
- 🧪 测试用例数：**60**（`backend/tests/`，pytest 统计）
- 📡 API 路由（14 个）：
    `GET /api/health`
    `POST /api/chat`
    `GET /api/accounts`
    `GET /api/trial-balance`
    `GET /api/income-statement`
    `GET /api/balance-sheet`
    `GET /api/transactions`
    `GET /api/account-ledger`
    `POST /api/import/preview`
    `POST /api/import/commit`
    `POST /api/opening-balance`
    `GET /api/ledger`
    `GET /api/ledger/export`
    `GET /api/balances`
<!-- /AUTO-SYNC:status -->

## 环境与启动（Windows + Git Bash）

- Python 3.12.9，虚拟环境在项目根 `.venv`，**Python 一律用 `../.venv/Scripts/python.exe`**
- 已装：beancount 3.2.3、fastapi、uvicorn、openai、httpx、pydantic、python-dotenv、pytest、**python-multipart**（文件上传）、**openpyxl**（Excel 导入）
- 启动：`cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 测试：`cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`
- 一键脚本：根目录 `start.bat`
- 数据文件（运行时生成，已 gitignore）：`backend/data/ledger.bean` + `backend/data/app.db`
- 本机有代理 `7897`（仅影响联网调试，代码未引用）

## API 配置（敏感，勿写入文档/代码）

- 模型名：`deepseek-flash`，Base URL：`https://api.deepseek.com`（DMXAPI 中转，OpenAI 兼容）
- key 在 `API_data/令牌_模型.txt`（第一行 key，第二行模型名），**已 gitignore，勿提交**
- `backend/app/config.py` 自动读取该文件；可用 `backend/.env` 覆盖（见 `.env.example`）
- 实测：JSON 抽取延迟约 8s，AI 批量分类约 5s；DMXAPI 支持 `response_format=json_object` 和 FunctionCall

## 架构与目录

```
backend/app/
  main.py          FastAPI 入口：/api/chat、/api/accounts、/api/trial-balance、
                   /api/income-statement、/api/balance-sheet、/api/transactions、
                   /api/account-ledger、/api/import/preview、/api/import/commit、
                   /api/opening-balance、/api/ledger/export、/api/balances
  config.py        配置（读 .env 或 API_data/令牌_模型.txt）
  llm/             client.py（DeepSeek 兼容客户端+批量分类）、prompts.py（few-shot 提示词）、schemas.py（Pydantic）
  core/            beancount_engine.py（引擎封装）、accounts.py（科目表）、
                   validator.py（金额/日期硬校验）、memory.py（SQLite）、importer.py（导入解析）
  flows/           chat.py（状态机协调）、record.py（记账）、query.py（报表）、correct.py（纠错）、base.py（常量）
frontend/          原生 HTML/CSS/JS 四页：对话 / 看账 / 导入 / 期初（移动端优先、大字体）
backend/tests/     pytest（validator / beancount_engine / record_flow / importer / llm_connect）
```

## 核心业务流程

1. **记账状态机**（`flows/chat.py`）：用户说话 → `llm.extract` 出结构化 JSON（intent/transaction）→ `record.build_draft` 硬校验 → 待确认态（存 SQLite draft）→ 用户「确认/改/取消」→ 落账 → 反馈余额。**没有确认绝不落账。**
2. **确认态关键词分类**（`chat._classify`）：`确认/取消/改`；纯数字 = 改金额；改科目用「记到银行」「是现金」。
3. **分类映射记忆**（SQLite `mappings` 表）：用户确认「面粉→存货」后下次免确认；导入的分类也记入。
4. **纠错**：`find_transactions` 按关键词+日期定位 → 改金额/改分类重写该分录（`save_entries` 整账重写+校验回滚）；删除走**冲销分录**不物理删。
5. **查询**：预置报表模板 + Python 聚合（`report_income_expense`/`daily_totals`），**不靠 AI 写 SQL**。
6. **导入**（`core/importer.py`）：CSV/Excel → 列识别（微信/支付宝/银行三种格式都有别名规则）→ `llm.map_categories` 批量分类 → 预览 → `commit_rows` 整体追加+校验回滚；转账识别为互转不误计收支。
7. **期初**：`/api/opening-balance` 填资产/负债 → 差额走 `Equity:OpeningBalances` 平账。

## 科目表（backend/app/core/accounts.py）

资产：Cash现金 Bank银行(含微信/支付宝) Inventory存货 Fixed固定资产 Receivable应收
负债：Payable应付 Loan借款
权益：Capital初始资本 Drawings业主提款 OpeningBalances期初余额
收入：Sales销售收入 Other其他收入
费用：COGS营业成本 Rent租金 Utilities水电 Salary工资 Tax税费 Misc杂费

## 开发经验与坑（务必遵守）

1. **Beancount 3 API 差异（最易踩坑）**：
   - `Posting` 没有 `.position`，金额在 `posting.units`（`Amount`），判断 `posting.units is None` 跳过 padding
   - 格式化条目用 `from beancount.api import format_entry`，返回 **str**；`beancount.parser.printer.print_entry` 在 v3 **返回 None**（会炸）
   - 借贷方向：Beancount 用正负号表示（资产/费用借方=正，收入/权益/负债贷方=负）；复式恒等式 = 全账本余额之和为 0
   - `loader.load_file()` 返回 `(entries, errors, options_map)`，errors 非空=账本不健康；所有写操作先写文件→重载校验→失败回滚
2. **Windows 编码**：控制台中文乱码是 GBK 问题，脚本前加 `PYTHONIOENCODING=utf-8`；读写文件全用 `encoding="utf-8"`
3. **金额/日期解析**（`validator.py`）：支持中文数字（三百五→350）、单位（万/千/块/毛/分）、相对日期（昨天/上周三/8月5号）；未来日期需确认
4. **AI 输出永远不可直接信任**：Pydantic `model_validate` 强校验 + 硬校验兜底，解析失败走澄清流程
5. **补科目**：新增科目后 `engine.ensure_exists()` 会自动补 open 指令（老账本迁移安全）
6. **SQLite**：`Memory` 类带线程锁；账本在 .bean 文件，DB 只存聊天/映射/草稿元数据

## 下一步计划（按优先级）

1. 真实用户试用一周，收集口语识别准确率 → 调 few-shot 提示词（`llm/prompts.py`）
2. **行业拓展接口**（用户明确要的）：如「卷帘门行业」等垂直场景，需在科目表 + 提示词 + 分类映射上做成可配置/可插拔
3. 微信小程序前端（核心已解耦，只需重写 frontend）
4. 语音输入（老年店长打字困难）
5. 多用户/多租户、草稿箱找回、报表导出 Excel/PDF
