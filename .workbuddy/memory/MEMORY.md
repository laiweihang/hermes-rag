# Hermes-RAG 项目记忆

## 项目概况
- RAG 全栈系统：FastAPI 后端 + React SPA 前端 + ChromaDB 向量库 + SQLite 关系库
- 核心特色：混合检索(BM25+语义)、LLM精排、查询重写(Simple+HyDE)、上下文感知分块(Anthropic风格)、规则引擎直答

## 技术栈
- 后端: FastAPI, SQLAlchemy, ChromaDB, LangChain, bcrypt+PyJWT, rank_bm25, jieba
- 前端: React SPA
- 外部API: LLM(DeepSeek/OpenAI兼容), Embedding(智谱/BGE), OCR(GLM-4V)

## 核心文件职责
- api.py: HTTP入口, 10章路由
- rag_engine.py: RAG核心大脑, 问答优先级: 规则→RAG→LLM直答
- retrieval.py: 混合检索, BM25Index单例+懒加载+失效重建
- reranker.py: LLM-as-reranker, 0-10分精排
- query_rewrite.py: Simple+HyDE双通道查询重写
- contextual_chunking.py: Anthropic风格上下文感知分块
- rule_engine.py: 关键词+正则零延迟直答
- ingest.py: 文档入库管道
- database.py: ChromaDB操作层, 稳定chunk ID(SHA256)
- models.py: SQLAlchemy ORM 8表, RetrievalSettings单行全局调参
- auth.py: JWT HS256认证
- config.py: 配置seed(运行时从DB读取)
- ocr_engine.py: 远程Vision API OCR
- utils.py: 多格式文档解析
- schemas.py: Pydantic请求/响应模型

## 2026-07-02
- 完成全项目架构分析, 输出3张SVG架构图(分层图、问答流程图、入库管道图)
- 产出完整分析文档 docs/架构分析-小白学习版.md
