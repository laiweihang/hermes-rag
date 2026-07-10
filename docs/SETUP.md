# 环境配置教程

本文档面向 **拿到项目压缩包、从零搭建运行环境** 的同学，覆盖后端（FastAPI）、前端（Next.js）、模型 Provider（远程 OpenAI 兼容 API）三部分。

> 重要说明：项目早期文档（[README.md](../README.md) / [STARTUP.md](../STARTUP.md)）描述的是 **LM Studio 本地模型** 模式，但当前代码已全面改为 **远程 OpenAI 兼容 API** 模式（LLM / 嵌入 / OCR 全部走在线接口，API Key 在管理后台配置）。**以本文为准。**

---

## 1. 系统要求

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.10 ~ 3.12 | 后端运行环境（`pyproject.toml` 要求 ≥ 3.10） |
| Node.js | 20 LTS 或 22 LTS | 前端 Next.js 16 需要（CI 使用 Node 22） |
| npm | 随 Node 附带 | 前端包管理 |
| 磁盘 | ≥ 2GB | 依赖 + 向量库 |
| 网络 | 可访问模型 API | 远程 LLM / 嵌入服务（如 DeepSeek、智谱） |

可选：

- **Git**：克隆/版本管理（用压缩包则不需要）
- 远程模型 API 账号与 **API Key**（DeepSeek / 智谱 / OpenAI 等任一 OpenAI 兼容服务）

---

## 2. 解压与目录结构

将压缩包解压到任意 **不含空格和中文更稳妥** 的路径，例如 `D:\projects\hermes-rag`。

解压后核心结构：

```
hermes-rag/
├── api.py / rag_engine.py / ...   # 后端 Python 源码
├── pyproject.toml                 # 依赖声明（与 requirements.txt 一致）
├── requirements.txt               # Python 依赖清单
├── scripts/
│   ├── start-dev.ps1              # Windows 一键启动
│   └── start-dev.sh               # Linux/macOS 一键启动
├── frontend/                      # Next.js 前端（需自行 npm install）
├── docs/                          # 文档（含本教程）
├── demo/ evals/ tests/            # 演示语料 / 评测 / 测试
└── (data/ uploads/ logs/ 运行时自动创建)
```

> 压缩包 **不含** `.venv/`、`frontend/node_modules/`、`data/`、`uploads/`、`logs/` 等运行时目录，需按下文重新生成。

---

## 3. 后端环境配置

在 **项目根目录** 打开终端。

### 3.1 创建虚拟环境

**Windows (PowerShell)：**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> 若提示「禁止运行脚本」，先执行：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，再重新激活。

**Linux / macOS：**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

激活成功后命令行前缀会出现 `(.venv)`。

### 3.2 安装依赖

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> 等价方式：`pip install -e .`（读取 `pyproject.toml`，CI 用的就是这个）。

关键依赖：`fastapi`、`uvicorn`、`chromadb==0.4.24`、`langchain` 系列、`openai`、`rank_bm25`、`jieba`、`sqlalchemy` 等。

### 3.3 依赖坑：httpx 版本不兼容

若导入时报：

```
ImportError: cannot import name 'BaseTransport' from 'httpx'
```

说明 `httpx` 与 `openai`/`starlette` 版本不匹配，在 venv 内升级即可：

```powershell
pip install -U "httpx>=0.27" "openai>=1.30" "starlette>=0.37"
```

> 务必在 **venv 内** 安装，不要用系统/Anaconda 全局环境，否则极易踩版本冲突。

---

## 4. 前端环境配置

```powershell
cd frontend
npm install
cd ..
```

首次安装约需 1-3 分钟。若仅用于本地开发，无需额外配置；前端默认连接后端 `http://localhost:8000`。

如后端不在默认地址，在 `frontend/.env.local` 中设置：

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## 5. 环境变量配置（可选）

项目用 `python-dotenv` 读取根目录 `.env`（**不存在也能跑**，会用代码内置默认值）。读取优先级：`.env` 文件 → 进程环境变量 → 代码默认值。

在根目录新建 `.env`（可选）：

```env
# ===== 安全（生产务必修改）=====
JWT_SECRET_KEY=please-change-this-to-a-random-long-string
JWT_EXPIRY_MINUTES=30
CORS_ORIGINS=*

# ===== 检索 / 生成默认值（也可在管理后台调，无需重启）=====
RAG_RELEVANCE_THRESHOLD=0.5
CHUNK_SIZE=500
CHUNK_OVERLAP=100
GEN_TEMPERATURE=0.7

# ===== 可靠性 =====
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=3
MAX_UPLOAD_SIZE_BYTES=104857600

# ===== 数据库（默认 SQLite，无需改）=====
# SQLALCHEMY_DATABASE_URL=sqlite:///data/smartpolicy.db
```

> 注意：**模型 API Key 不在 `.env` 配置**，而是在管理后台填写（见第 7 节）。完整可调变量见 [config.py](../config.py) 与 [docs/TESTING.md](TESTING.md)。

---

## 6. 启动服务

### 方式一：一键启动（推荐）

**Windows：**

```powershell
.\scripts\start-dev.ps1
```

**Linux / macOS：**

```bash
bash scripts/start-dev.sh
```

输出：

```
Starting API      -> http://127.0.0.1:8000
Starting frontend -> http://localhost:3000
Press Ctrl+C to stop both.
```

后端日志写入 `logs/api.log` 与 `logs/api.err.log`。

自定义端口：

```powershell
$env:API_PORT="9000"; $env:FRONTEND_PORT="4000"; .\scripts\start-dev.ps1
```

### 方式二：分别启动

**终端 1 — 后端**（在根目录、已激活 venv）：

```powershell
uvicorn api:app --host 0.0.0.0 --port 8000
```

**终端 2 — 前端**：

```powershell
cd frontend
npm run dev -- --port 3000
```

### 首次启动自动初始化

后端首次启动会自动：

- 创建 SQLite 数据库与表（`data/smartpolicy.db`）
- 创建默认管理员：**`admin` / `123456`**
- 写入预置技能与默认检索设置
- 创建 **占位的 Provider 模板**（LLM / 嵌入 / OCR 各一，API Key 为空）

访问地址：

- 前端：http://localhost:3000
- 后端 Swagger：http://localhost:8000/docs

---

## 7. 配置模型 Provider（关键步骤）

系统采用 **远程 OpenAI 兼容 API**，首次启动后必须填写 API Key 才能问答。

1. 浏览器打开 http://localhost:3000，用 `admin` / `123456` 登录
2. 进入 **管理后台 `/admin`** → **模型管理**
3. 会看到三个占位 Provider，分别编辑并填入真实 `API Key`：

| 类型 | 默认 base_url | 默认模型 | 用途 |
|------|---------------|----------|------|
| `llm` | `https://api.deepseek.com/v1` | `deepseek-chat` | 对话生成 |
| `embedding` | `https://open.bigmodel.cn/api/paas/v4` | `embedding-3` | 文本嵌入 |
| `ocr` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4v-flash` | 扫描件/图片 OCR |

> 也可改 `base_url` / `model_name` 切换到 OpenAI、通义千问等其他 OpenAI 兼容服务。`embedding` Provider 必填，否则文档无法入库；`ocr` 仅处理扫描件/图片时需要。

4. 保存后即可在首页对话、在文档管理页上传文档。

---

## 8. 验证安装

| 检查项 | 操作 | 期望 |
|--------|------|------|
| 后端存活 | 访问 http://localhost:8000/health | 返回正常 JSON |
| 前端存活 | 访问 http://localhost:3000 | 显示登录页 |
| 登录 | `admin` / `123456` | 进入主界面 |
| Provider | 管理后台填好 Key | 对话能正常回答 |
| 文档入库 | 上传 `demo/teaching/01_hr_handbook.md` | 状态变 ready，有切片数 |
| 问答 | 问「工作日加班几倍工资？」 | 返回答案 + 参考来源 |

可选自检（在 venv 内）：

```powershell
pytest tests/test_eval_metrics.py -q     # 不依赖外部服务的纯逻辑测试
```

更完整的测试流程见 [docs/TESTING.md](TESTING.md)。

---

## 9. 常见问题

### 启动报 `address already in use` / 端口占用

```powershell
# Windows：查占用并结束
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

或改用其它端口启动（见 6.1）。

### `python` 命令找不到

确认 Python 已加入 PATH；Windows 可改用 `py -3` 创建 venv：`py -3 -m venv .venv`。

### PowerShell 无法激活 venv

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 前端启动报 Node 版本过低

升级到 Node 20 LTS 或 22 LTS（Next.js 16 要求）。

### 对话报「未配置可用的 LLM Provider」

回到管理后台 → 模型管理，确认 `llm` 类型 Provider 已填 API Key 且为启用/默认。

### 文档上传后一直 failed

多为 `embedding` Provider 未配置或 Key 无效；检查嵌入 Provider 的 base_url / Key / 模型名。

### 重置到全新状态

停止服务后删除运行时目录即可（会丢失所有数据）：

```powershell
Remove-Item -Recurse -Force data, uploads, logs
```

---

## 10. 卸载 / 迁移

- **数据**：全部在 `data/`（SQLite + 向量库）与 `uploads/`，拷走这两个目录即可迁移知识库。
- **环境**：删除 `.venv/` 和 `frontend/node_modules/` 即可清理依赖，源码不受影响。

---

## 附：最小命令清单

```powershell
# 1. 后端
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 前端
cd frontend; npm install; cd ..

# 3. 启动
.\scripts\start-dev.ps1

# 4. 浏览器 http://localhost:3000 → admin/123456 → 管理后台填 API Key
```
