# Python 基础：那些命令到底在干什么

> 从你天天敲的那两条命令讲起，把 Python 项目的运行机制一次讲透。
> 所有例子都是本项目实测输出。

---

## 一、先把启动命令解剖开

```cmd
cd /d D:\Workbench\VScode\Bookkeeping_AI_helper
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```

| 片段 | 是什么 | 作用 |
|---|---|---|
| `cd /d ...` | cmd 命令 | 切换工作目录（`/d` 表示允许跨盘符） |
| `.venv\Scripts\python.exe` | **解释器路径** | 用**哪个** Python 来运行 |
| `-m uvicorn` | 参数 + 模块名 | 把 `uvicorn` 这个包**当程序运行** |
| `app.main:app` | 模块:变量 | 告诉 uvicorn：**程序在哪** |
| `--host 127.0.0.1` | uvicorn 的参数 | 只监听本机 |
| `--port 8000` | uvicorn 的参数 | 用 8000 端口 |
| `--app-dir backend` | uvicorn 的参数 | 把 `backend/` 加进模块搜索路径 |

**前四段是 Python 的通用知识，后三段是 uvicorn 自己的参数。**

---

## 二、核心概念 ①：解释器路径

Python 代码不是"双击就运行"的，得有个**解释器**去读它。你的电脑上可能装了不止一个 Python。

实测你机器上就有两个：

```
PATH 里的 python   →  D:\TOOLS\Python\python-3.12.9\python
项目里的 python    →  D:\Workbench\...\Bookkeeping_AI_helper\.venv\Scripts\python.exe
```

它们**装的东西是分开的**。实测：

```
系统 python 找 fastapi → D:\TOOLS\Python\python-3.12.9\Lib\site-packages\fastapi\...
项目 python 找 fastapi → D:\Workbench\...\.venv\Lib\site-packages\fastapi\...
                                    ↑ 完全不同的两套
```

### 为什么不直接敲 `python`？

因为 `python` 是靠**环境变量 PATH** 找的——谁在前用谁。这在你自己电脑上可能碰巧是对的，
换台电脑、或者系统 Python 升级了，就可能变成另一个解释器，然后：

```
ModuleNotFoundError: No module named 'fastapi'
```

**写全路径 `.venv\Scripts\python.exe` 就是明确说"用这个，别猜"。**

---

## 三、核心概念 ②：虚拟环境（.venv）

### 它解决什么问题

假设你有两个项目：A 要 `fastapi 1.0`，B 要 `fastapi 2.0`。
如果都装在系统 Python 里，就会打架。

**虚拟环境 = 给每个项目一个独立的"包仓库"。**

```
Bookkeeping_AI_helper/
  .venv/                    ← 这个项目专用的 Python + 第三方包
    Scripts/python.exe      ← 专用解释器
    Lib/site-packages/      ← 专用包仓库（fastapi、beancount…都在这）
  backend/
  frontend/
```

### 它怎么来的

```cmd
python -m venv .venv         ← 创建（只做一次）
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
                             ← 往这个环境里装依赖
```

`requirements.txt` 就是"这个项目需要哪些包"的清单：

```
beancount>=3.2
fastapi>=0.141
pydantic>=2.0
...
```

### 为什么要提交 requirements.txt 但不提交 .venv

`.venv` 里有几千个文件、几百 MB，而且是**从 requirements.txt 可以完整重建**的。
所以 `.gitignore` 里排除了 `.venv/`，别人拿到项目只要：

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

**这样"怎么装"比"装了什么"更值得保存。**

---

## 四、核心概念 ③：`-m` 是什么意思

```cmd
.venv\Scripts\python.exe -m uvicorn ...
.venv\Scripts\python.exe -m pytest ...
```

`-m` = **module**。意思不是"运行某个文件"，而是**"把一个模块当程序运行"**。

### 和直接写文件名有什么区别

```cmd
python scripts/stress_test.py     ← 直接指定文件路径
python -m pytest                  ← 让 Python 自己去包里找入口
```

`-m` 的好处：**不用知道这个包装在哪个目录**。pip 装的东西路径可能变，但模块名永远不变。

### 为什么推荐 `-m pip`

```cmd
pip install xxx                        ← 可能装到别的 Python 里
python -m pip install xxx              ← 明确"用当前这个 python 的 pip"
.venv\Scripts\python.exe -m pip install xxx   ← 最明确
```

**`python -m pip` 永远比裸 `pip` 安全**，因为 pip 和 python 一定是配套的。

---

## 五、核心概念 ④：`app.main:app` 这个冒号

这是 uvicorn 的写法，意思是 **"模块:变量"**。

实测验证（`importlib` 内部就是这么干的）：

```python
import importlib
mod = importlib.import_module('app.main')   # 冒号左边：模块
obj = getattr(mod, 'app')                    # 冒号右边：模块里的变量
```

输出：

```
左边 app.main  →  文件: .../backend/app/main.py
右边 app       →  对象: FastAPI → AI 记账助手
```

所以 **`app.main:app` 的意思是**：

> 去 `app/main.py` 这个文件里，找那个名叫 `app` 的变量。

打开 `main.py` 第 21 行，确实有：

```python
app = FastAPI(title="AI 记账助手", version="0.3.0")
```

**这个 `app` 变量就是你整个 Web 应用的入口。**

---

## 六、核心概念 ⑤：为什么必须 `--app-dir backend`

Python 找模块时，会按一个**搜索路径列表**（`sys.path`）逐个目录去找。

实测：**在项目根目录**运行：

```cmd
> python -c "import app"
ModuleNotFoundError: No module named 'app'
```

**在 backend 目录**运行：

```cmd
> python -c "import app"
成功: ...\backend\app\__init__.py
```

### 原因

```
Bookkeeping_AI_helper/        ← 根目录：这里没有 app/ 这个文件夹
  backend/
    app/                      ← app 包在这里
      main.py
```

`import app` 的意思是"找一个叫 `app` 的文件夹（包）"。
Python 默认会在**当前工作目录**找。你在根目录，根目录下没有 `app/`，就找不到。

`--app-dir backend` 就是告诉 uvicorn：

> 先把 `backend/` 加进搜索路径，再去找 `app.main`。

**这就是为什么这条参数不能省。** 省了就报 `ModuleNotFoundError: No module named 'app'`。

---

## 七、核心概念 ⑥：包（package）是什么

```
backend/app/
  __init__.py        ← 有这个文件，就是一个"包"
  main.py
  config.py
  llm/
    __init__.py
    client.py
  core/
    beancount_engine.py
```

| 写法 | 含义 |
|---|---|
| `app` | 包（文件夹） |
| `app.main` | `app/` 下的 `main.py` 模块 |
| `app.core.beancount_engine` | `app/core/beancount_engine.py` |
| `from app.core import accounts` | 从那个模块里导入 `accounts` |
| `app.main:app` | `app/main.py` 里的 `app` 变量 |

**规律：点号 = 目录层级。** `app.core.books` 读作"app 目录下 core 目录下的 books 模块"。

---

## 八、`--host` 和 `--port` 是什么

```cmd
--host 127.0.0.1 --port 8000
```

**启动一个 Web 服务 = 在你的电脑上开一扇"门"，等人来访问。**

| 参数 | 含义 |
|---|---|
| `--port 8000` | 门牌号。访问 `http://127.0.0.1:8000` 就敲这扇门 |
| `--host 127.0.0.1` | **只允许本机访问**（最安全） |
| `--host 0.0.0.0` | **允许同 WiFi 下所有设备访问**（手机能用） |

**完整地址 = 协议 + 主机 + 端口 + 路径**
```
http://127.0.0.1:8000/api/health
  ↑      ↑       ↑      ↑
 协议   主机    端口    接口路径
```

### 常见问题

| 现象 | 原因 |
|---|---|
| `Address already in use` | 端口被占了（上次没关干净） |
| 手机连不上 | 用了 `127.0.0.1`，要改 `0.0.0.0` + 用电脑的局域网 IP |

**端口被占用的处理**：

```cmd
netstat -ano | findstr :8000     ← 查谁占了
taskkill /F /PID <那个PID>        ← 杀掉
```

---

## 九、其他常用命令速查

| 命令 | 意思 |
|---|---|
| `python -m pytest tests/ -q` | 跑测试，`-q` = quiet（少输出） |
| `python -m pytest tests/ -v` | 详细输出，每个用例一行 |
| `python -m pytest tests/test_books.py` | 只跑某个文件 |
| `python -m pytest -k "audit"` | 只跑名字含 audit 的用例 |
| `python scripts/stress_test.py` | 跑普通脚本 |
| `python -c "print(1)"` | 直接执行一段代码（`-c` = command） |
| `pip list` | 看装了什么包 |
| `pip install -r requirements.txt` | 按清单装依赖 |

---

## 十、Git Bash 和 cmd 的路径差异

同一个文件，两种写法：

| | cmd | Git Bash |
|---|---|---|
| 分隔符 | `.venv\Scripts\python.exe` | `.venv/Scripts/python.exe` |
| 上一级 | `..\backend` | `../backend` |
| 绝对路径 | `D:\Workbench\...` | `/d/Workbench/...` |
| 切换盘符 | `cd /d D:\...` | `cd /d/...` 或直接 `cd "D:/..."` |

**在 cmd 里就要用反斜杠**，否则会报"不是内部或外部命令"。

---

## 十一、完整对照：三条命令的完整读法

### ① 启动服务

```cmd
cd /d D:\Workbench\VScode\Bookkeeping_AI_helper
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```

> 切到项目根目录 → 用**本项目的** Python → 运行 **uvicorn** 这个包 →
> 去 `app/main.py` 里找 `app` 变量 → 把 `backend/` 加进搜索路径 →
> 在 8000 端口开一扇只允许本机访问的门

### ② 跑测试

```cmd
cd backend
..\.venv\Scripts\python.exe -m pytest tests/ -q
```

> 切到 backend 目录 → 用上一级目录里的**本项目** Python → 运行 **pytest** →
> 跑 `tests/` 下的所有 `test_*.py` → 少输出

### ③ 跑脚本

```cmd
.venv\Scripts\python.exe scripts\stress_test.py
```

> 用本项目的 Python → 直接执行 `scripts/stress_test.py` 这个**文件**

**区别**：`-m xxx` 是"运行一个包"，`xxx.py` 是"运行一个文件"。

---

## 十二、一张图总结

```
                 ┌──────────────────────────────┐
   你敲的命令  →  │  .venv\Scripts\python.exe    │  ← 用哪个解释器
                 └───────────┬──────────────────┘
                             │
                 ┌───────────▼──────────────────┐
                 │  -m uvicorn                  │  ← 运行哪个包
                 └───────────┬──────────────────┘
                             │
                 ┌───────────▼──────────────────┐
                 │  app.main:app                │  ← 程序入口在哪
                 │  = app/main.py 里的 app 变量  │
                 └───────────┬──────────────────┘
                             │
                 ┌───────────▼──────────────────┐
                 │  --app-dir backend           │  ← 去哪找 app 包
                 │  --host / --port             │  ← 门开在哪
                 └──────────────────────────────┘
```

**理解了这四层，任何 Python 项目的启动命令你都能读懂了。**
