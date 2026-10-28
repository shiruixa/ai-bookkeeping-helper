# 测试复现说明

本目录用于复现《压力测试与功能验证报告》中的结果。

---

## 前置条件

- Windows 10/11
- Python 3.11+
- 已安装依赖：

```cmd
cd /d <本目录的上一级>
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

---

## 方式一：自动化测试（推荐，无需联网）

**99 个用例全部不依赖网络和 API Key**，在临时目录里跑，不会碰你的真实账本。

```cmd
cd /d <本目录的上一级>\backend
..\.venv\Scripts\python.exe -m pytest tests/ -q
```

预期输出：

```
........................................................................ [ 72%]
...........................                                              [100%]
99 passed
```

测试覆盖：

| 文件 | 内容 |
|---|---|
| `test_validator.py` | 中文金额、日期解析 |
| `test_beancount_engine.py` | 借贷平衡、回滚、报表、冲销 |
| `test_record_flow.py` | 记账状态机、确认/取消、否定词 |
| `test_importer.py` | 微信/支付宝/银行账单解析与落账 |
| `test_books.py` | 多账本预置、幂等、迁移 |
| `test_multi_book_isolation.py` | 账本隔离 |
| `test_audit.py` | 审计溯源 |
| `test_cost_close.py` | 月末成本结转与冲回 |

---

## 方式二：8 步端到端演示（需要 API Key）

```cmd
REM 终端 1：启动服务
cd /d <本目录的上一级>
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend

REM 终端 2：跑演示
cd /d <本目录的上一级>
.venv\Scripts\python.exe scripts\demo_simulation.py
```

会依次演示：建账 → 采购 → 销售 → 房租 → 水电 → 工资 → 纠错 → 查询，
最后打印利润表、资产负债表、试算平衡表。

> 需要先在网页「设置 → API 设置」里配置接口地址、模型名和 API Key。

---

## 方式三：五金店压力测试（需要 API Key）

```cmd
REM 终端 1：启动服务（同上）
REM 终端 2：
cd /d <本目录的上一级>
.venv\Scripts\python.exe scripts\stress_test.py
```

预期结果：

```
第一步：批量导入历史流水（329 行原始数据）
  解析出 329 笔，跳过 0 行
  AI 分类映射 19 条
  落账：成功 329 笔，失败 0 笔

第二步：本月对话记账（真实 AI）

第三步：月末成本结转（定期盘存法）

第四步：全部报表
  账本总笔数：340
  净利润：23,696.96

第五步：一致性校验
  通过  试算平衡：借方 = 贷方
  通过  资产负债表：资产 = 负债 + 权益
  通过  利润表：净利 = 收入 − 支出
  通过  账本有实际数据（>200 笔）
  通过  账本隔离：company 为空
  通过  账本隔离：personal 为空
```

> ⚠️ 该脚本会**清空「测试账本」**后重新生成数据，不会影响公司/个人账本。

---

## 文件说明

| 文件 | 内容 |
|---|---|
| `压力测试与功能验证报告.md` | 完整测试报告：范围、结果、14 个缺陷及修复 |
| `README.md` | 本文件，复现步骤 |

---

## 说明

- 自动化测试（方式一）**不需要任何 API Key**，可离线运行，适合评审快速验证。
- 端到端测试（方式二、三）会真实调用大模型，产生少量 token 消耗。
- 所有测试数据都在临时目录或「测试账本」中，不会污染其他账本。
