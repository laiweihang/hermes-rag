# 赫尔墨斯 Hermes RAG

基于 RAG（检索增强生成）的智能知识问答系统，支持多技能场景、混合检索、文档管理等功能。

## 快速开始

### 系统要求

| 组件 | 版本 |
|------|------|
| Python | 3.10 ~ 3.12 |
| Node.js | 20 LTS 或 22 LTS |
| 磁盘 | ≥ 2GB |

### 后端

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 前端

```powershell
cd frontend
npm install
cd ..
```

### 启动

```powershell
.\scripts\start-dev.ps1
```

启动后访问：
- 前端页面：http://localhost:3000
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

### 配置模型

首次启动后，用默认管理员 `admin` / `123456` 登录 → 进入管理后台 `/admin` → 模型管理，填写 LLM / 嵌入 / OCR 的 API Key（支持 DeepSeek、智谱、OpenAI 等兼容接口）。

## 功能特性

- **RAG 问答**：基于知识库文档的智能问答，支持流式输出
- **多技能场景**：财务分析、会议纪要、政策文档、发票助手等预设技能，可自定义
- **混合检索**：加权融合 / RRF / 纯语义 / 纯关键词四种策略 + LLM 精排
- **规则引擎**：高频问题毫秒级响应，不依赖大模型
- **文档管理**：上传 PDF / DOCX / TXT / 图片，自动切片与向量嵌入
- **多轮对话**：上下文连贯，支持对话导出
- **检索调参**：可视化面板实时对比检索效果
- **多用户**：用户隔离，数据独立

## 项目结构

```
hermes-rag/
├── api.py / rag_engine.py      # 后端 Python 源码
├── frontend/                    # Next.js 前端
├── scripts/                     # 启动脚本
│   ├── start-dev.ps1            # Windows 启动
│   └── start-dev.sh             # Linux/macOS 启动
├── docs/                        # 详细文档
├── demo/                        # 演示语料
├── evals/                       # 评测数据
└── tests/                       # 测试
```

## 详细文档

- [环境搭建](docs/SETUP.md)
- [使用指南](docs/USAGE.md)
- [演示流程](docs/DEMO.md)
- [混合检索说明](docs/HYBRID_RETRIEVAL.md)
- [评测说明](docs/EVAL.md)
- [测试说明](docs/TESTING.md)
