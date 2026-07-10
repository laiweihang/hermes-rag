# models.py
"""SQLAlchemy ORM 模型定义 — 用户、对话、消息、反馈、技能、模型 Provider。

本模块同时承担三个职责：

1. ORM 类定义（User / Conversation / Message / Skill / Feedback / LlmProvider）。
2. 数据库引擎与 Session 工厂（``engine`` / ``SessionLocal``）。
3. 启动时自动初始化（``init_db``）：建表 + 在线 schema 迁移 +
   admin 账户 + 预置技能 seed + 默认 Provider 模板。

关于 SQLite + 多线程：
    FastAPI 的 sync 路由可能在不同线程间共享 Connection，因此
    ``check_same_thread=False`` 必须打开。代码层面通过
    ``SessionLocal()`` 每次创建新 session 来避免实际并发写冲突。

关于 schema 迁移：
    本项目没有引入 Alembic —— 字段变更很少，直接在 ``init_db`` 里用
    ``ALTER TABLE`` 做幂等迁移更轻量。每段迁移都做了"列存在性检查"，
    可重复执行。
"""

import json
import logging
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from config import SQLALCHEMY_DATABASE_URL

logger = logging.getLogger(__name__)

# ==========================================
# 数据库引擎 & 会话
# ==========================================

# echo=False：避免把所有 SQL 打到日志；调试时改 True 可排查慢查询。
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite 多线程访问必须关闭线程检查
    echo=False,
)

# 全局 Session 工厂；autoflush=False 让我们显式控制何时 flush，
# 避免在多步操作中触发预期外的部分写入。
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# 启用 SQLite 外键约束（默认关闭）
@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite 默认不启用外键约束，需要手动开启。

    没有这段，``ondelete="CASCADE"`` 等约束会被静默忽略 —— 例如删除
    User 时其 conversations 不会被自动级联清理，造成孤儿数据。
    """
    import sqlite3

    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ==========================================
# ORM 模型
# ==========================================


class User(Base):
    """用户表。

    role 取值仅 "user" / "admin"。删除 User 会级联清理其所有对话与反馈
    （cascade + ondelete=CASCADE 双重保险）。
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # username 加唯一索引：登录走该字段查询，唯一约束既保数据正确又加速。
    username = Column(String(64), unique=True, nullable=False, index=True)
    # bcrypt 哈希结果约 60 字符；预留到 128 应对未来算法升级。
    hashed_password = Column(String(128), nullable=False)
    role = Column(String(16), default="user", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ORM 级 cascade + DB 级 ondelete 双保险，确保孤儿数据不会残留。
    conversations = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan"
    )
    feedbacks = relationship(
        "Feedback", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"


class Conversation(Base):
    """对话表。

    skill_id / provider_id 用 SET NULL 而非 CASCADE：技能或模型被删除
    时对话本身仍保留，仅丢失关联设置（前端会回退默认）。
    """

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 用户删除 → 对话级联消失（用户隐私要求）。
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # 技能/Provider 仅是配置项，删除后保留对话记录。
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="SET NULL"), nullable=True)
    provider_id = Column(Integer, ForeignKey("llm_providers.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(256), default="新对话")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # 关系
    user = relationship("User", back_populates="conversations")
    skill = relationship("Skill")
    provider = relationship("LlmProvider")
    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Conversation(id={self.id}, title='{self.title}')>"


class Message(Base):
    """消息表"""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(String(16), nullable=False)  # "user" / "assistant"
    content = Column(Text, nullable=False)
    # sources 用 JSON 字符串而不是关联表：检索结果是冻结快照（即使后续
    # 文档被删，老对话也能展示当时的引用），且无关联查询需求。
    sources = Column(Text, default=None)
    # rule_matched 不为 NULL 表示该回答来自规则引擎（而非 LLM）。
    rule_matched = Column(String(128), default=None)
    # query_rewrite：JSON 字符串，记录本轮检索是否触发了查询重写以及
    # LLM 实际改写后的查询。仅在 assistant 消息上写入；普通对话路径为 NULL。
    # 字段形如 {"original": "...", "simple": "..."|null, "hyde": "..."|null,
    #          "bm25_query": "...", "vector_query": "..."}
    query_rewrite = Column(Text, default=None)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 关系
    conversation = relationship("Conversation", back_populates="messages")
    feedbacks = relationship(
        "Feedback", back_populates="message", cascade="all, delete-orphan"
    )

    def get_sources(self):
        """解析 sources JSON 字段。"""
        if self.sources:
            try:
                return json.loads(self.sources)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def set_sources(self, sources_list):
        """将来源列表序列化为 JSON 字符串。"""
        if sources_list:
            self.sources = json.dumps(sources_list, ensure_ascii=False)
        else:
            self.sources = None

    def __repr__(self):
        return f"<Message(id={self.id}, role='{self.role}')>"


class Skill(Base):
    """技能场景表"""

    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    system_prompt = Column(Text, nullable=False)
    # deprecated: 仅保留以兼容老 SQLite，rag_engine 不读、API 不返回。
    # 下次做迁移脚本时一并清理。
    rules = Column(Text, default=None)
    icon = Column(String(16), default=None)
    # auto_detect_patterns 用 JSON 文本存储关键词列表；DEFAULT '[]'
    # 而非 NULL，让 get_patterns() 不必处理 None 分支。
    auto_detect_patterns = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def get_patterns(self):
        """解析 auto_detect_patterns JSON 字段。"""
        if self.auto_detect_patterns:
            try:
                return json.loads(self.auto_detect_patterns)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def set_patterns(self, patterns_list):
        """将模式列表序列化为 JSON 字符串。"""
        if patterns_list:
            self.auto_detect_patterns = json.dumps(patterns_list, ensure_ascii=False)
        else:
            self.auto_detect_patterns = "[]"

    def __repr__(self):
        return f"<Skill(id={self.id}, name='{self.name}')>"


class Feedback(Base):
    """反馈表"""

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating = Column(String(16), nullable=False)  # "up" / "down"
    comment = Column(Text, default=None)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 关系
    message = relationship("Message", back_populates="feedbacks")
    user = relationship("User", back_populates="feedbacks")

    def __repr__(self):
        return f"<Feedback(id={self.id}, rating='{self.rating}')>"


class LlmProvider(Base):
    """模型提供者配置表（涵盖 LLM / 嵌入 / OCR 三类远程 API）"""

    __tablename__ = "llm_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    # model_type：模型类型，决定调用方式
    #   "llm"        — 对话生成（chat completions）
    #   "embedding"  — 文本嵌入（embeddings）
    #   "ocr"        — 图像 OCR（vision chat completions）
    model_type = Column(String(16), nullable=False, default="llm")
    # provider_type：仅作前端展示标签（"remote" 在线 / "local" 本地中转），
    # 不再影响后端调用逻辑——所有请求都按标准 OpenAI 兼容协议直连。
    provider_type = Column(String(16), nullable=False, default="remote")
    base_url = Column(String(512), nullable=False)
    api_key = Column(String(512), nullable=False)
    model_name = Column(String(128), nullable=False)
    max_tokens = Column(Integer, default=4096)
    timeout_seconds = Column(Integer, default=120)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<LlmProvider(id={self.id}, name='{self.name}', "
            f"type='{self.model_type}', model='{self.model_name}')>"
        )


class RetrievalSettings(Base):
    """混合检索全局参数表（仅一行，约定 id=1）。

    管理员可通过 ``/api/admin/retrieval`` 修改；请求级 ``RetrievalParams``
    可在单次问答中覆盖这里的字段。设计成 ORM 表而非纯 .env 配置是因为
    "调参验证"需要不重启服务即可生效。
    """

    __tablename__ = "retrieval_settings"

    id = Column(Integer, primary_key=True, autoincrement=False)
    # 融合策略：weighted / rrf / semantic / bm25
    mode = Column(String(16), nullable=False, default="weighted")
    # weighted 模式 semantic 通道权重；bm25 权重 = 1 - alpha
    alpha = Column(Float, nullable=False, default=0.5)
    # RRF 常数
    rrf_k = Column(Integer, nullable=False, default=60)
    # 召回阶段两路各取多少
    bm25_top_k = Column(Integer, nullable=False, default=20)
    vector_top_k = Column(Integer, nullable=False, default=20)
    # 融合后保留的最终 K
    final_top_k = Column(Integer, nullable=False, default=5)
    # semantic 通道相关度阈值（ChromaDB 归一化分数 0~1）
    semantic_threshold = Column(Float, nullable=False, default=0.5)
    # 一键禁用 BM25（强制只走语义）—— 调试时方便对照
    enable_bm25 = Column(Boolean, nullable=False, default=True)
    # ----- Rerank（LLM-as-reranker）-----
    # 全局开关；关掉后行为完全等同未引入 rerank 时
    rerank_enabled = Column(Boolean, nullable=False, default=True)
    # 精排后真正喂给 LLM 的最终条数；与 final_top_k（精排输入数）解耦
    rerank_top_n = Column(Integer, nullable=False, default=5)
    # 指定 reranker Provider；NULL = 与主 LLM 同一个
    rerank_provider_id = Column(Integer, nullable=True, default=None)
    # ----- Contextual Retrieval（Anthropic 风格上下文感知分块）-----
    # 入库期决策：开启后每个切片入库前调一次 LLM 生成 50-100 字上下文，
    # 前置到 page_content 再做嵌入；显著提升小片段在长文档中的检索精度，
    # 但入库耗时增加 N 倍（N = 切片数）。仅对新入库 / 重新入库的文档生效。
    contextual_chunking_enabled = Column(Boolean, nullable=False, default=False)
    # 指定上下文生成 Provider；NULL = 与主 LLM 同一个
    contextual_chunking_provider_id = Column(Integer, nullable=True, default=None)
    # ----- Query Rewriting（查询重写）-----
    # 简单重写：把口语化 / 模糊查询用 LLM 改写为更结构化、含关键术语的检索查询。
    query_rewrite_simple_enabled = Column(Boolean, nullable=False, default=False)
    # HyDE：让 LLM 先写一段「假设性答案」，用其语义近似真实答案的特性提升向量召回。
    query_rewrite_hyde_enabled = Column(Boolean, nullable=False, default=False)
    # 两个查询重写功能共用的 Provider；NULL = 与主 LLM 同一个
    query_rewrite_provider_id = Column(Integer, nullable=True, default=None)
    # ----- 分块（入库期，仅对新入库 / 重新入库文档生效）-----
    # 切片大小（字符数）。改动后需对旧文档「重新入库」或「重建向量库」才能全部生效。
    chunk_size = Column(Integer, nullable=False, default=500)
    # 相邻切片重叠字符数，防止把同一句话切碎丢失上下文。
    chunk_overlap = Column(Integer, nullable=False, default=100)
    # 切分策略：recursive（递归字符）/ markdown（标题感知）/ character（单一分隔符）
    # / token（按 token 切，适配模型上下文窗口）。
    splitter_strategy = Column(String(16), nullable=False, default="recursive")
    # 自定义分隔符列表（JSON 文本，如 ["\n\n", "。", "！"]）；NULL = 用策略内置默认。
    chunk_separators = Column(Text, nullable=True, default=None)
    # ----- 生成参数（问答期，对所有 RAG / 直答路径生效）-----
    # 默认采样温度；请求级 temperature 可覆盖。
    gen_temperature = Column(Float, nullable=False, default=0.7)
    # 核采样 top_p；NULL = 不显式传（用模型默认）。
    gen_top_p = Column(Float, nullable=True, default=None)
    # 单次生成最大 token；NULL = 用 Provider.max_tokens。
    gen_max_tokens = Column(Integer, nullable=True, default=None)
    # 存在惩罚 / 频率惩罚（-2.0 ~ 2.0），抑制重复。
    gen_presence_penalty = Column(Float, nullable=False, default=0.0)
    gen_frequency_penalty = Column(Float, nullable=False, default=0.0)
    # 停止序列（JSON 文本数组）；NULL = 不设置。
    gen_stop = Column(Text, nullable=True, default=None)
    # 发送给 LLM 的参考资料最大字符数（上下文窗口预算）。
    max_context_length = Column(Integer, nullable=False, default=8000)
    # 拼装 LLM 上下文时回溯的最近会话条数。
    max_history_messages = Column(Integer, nullable=False, default=10)
    # ----- Prompt 与拒答（问答期可编辑，无需改代码重启）-----
    # RAG / 直答系统提示词；NULL 或空 = 用代码内置默认（SYSTEM_PROMPT_*）。
    system_prompt_rag = Column(Text, nullable=True, default=None)
    system_prompt_direct = Column(Text, nullable=True, default=None)
    # 检索无命中时的拒答文案；NULL 或空 = 用内置默认。
    no_answer_text = Column(Text, nullable=True, default=None)
    # 检索无命中时是否回退到「纯 LLM 直答」而非拒答（False = 严格拒答，防幻觉）。
    allow_fallback_to_direct = Column(Boolean, nullable=False, default=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return (
            f"<RetrievalSettings(mode='{self.mode}', alpha={self.alpha}, "
            f"final_top_k={self.final_top_k})>"
        )


class KnowledgeDocument(Base):
    """知识库文档处理状态。

    Chroma 保存切片，本表保存文件级生命周期，便于展示 queued/processing/
    ready/failed 状态、失败原因和内容哈希。
    """

    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(512), unique=True, nullable=False, index=True)
    content_sha256 = Column(String(64), nullable=False, default="")
    status = Column(String(16), nullable=False, default="queued", index=True)
    chunk_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    uploaded_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


# ==========================================
# 数据库初始化
# ==========================================


def init_db(bind=None):
    """创建所有表（如果不存在），处理 migration，并自动创建 admin 账户和预置技能。

    每次启动 FastAPI 都会调用本函数（``api.py`` 的 startup 事件）。
    所有步骤设计为幂等：首次启动建表 + 写 seed；后续启动只校验 schema。

    Args:
        bind: 可传入自定义 engine 用于单元测试；生产代码用全局 engine。
    """
    target_engine = bind or engine
    logger.info("🗄️ 正在初始化 SQLite 数据库...")
    Base.metadata.create_all(bind=target_engine)

    # 迁移：如果 users 表缺少 role 列，则添加
    try:
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(target_engine)
        if "users" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("users")]
            if "role" not in columns:
                with target_engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT 'user' NOT NULL"))
                    conn.commit()
                logger.info("🔄 已为 users 表添加 role 列")
    except Exception as e:
        logger.warning(f"⚠️ 迁移检查跳过: {e}")

    # 迁移：如果 conversations 表缺少 skill_id 列，则添加
    try:
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(target_engine)
        if "conversations" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("conversations")]
            if "skill_id" not in columns:
                with target_engine.connect() as conn:
                    conn.execute(text("ALTER TABLE conversations ADD COLUMN skill_id INTEGER REFERENCES skills(id) ON DELETE SET NULL"))
                    conn.commit()
                logger.info("🔄 已为 conversations 表添加 skill_id 列")
    except Exception as e:
        logger.warning(f"⚠️ conversations 迁移检查跳过: {e}")

    # 迁移：如果 conversations 表缺少 provider_id 列，则添加
    try:
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(target_engine)
        if "conversations" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("conversations")]
            if "provider_id" not in columns:
                with target_engine.connect() as conn:
                    conn.execute(text("ALTER TABLE conversations ADD COLUMN provider_id INTEGER REFERENCES llm_providers(id) ON DELETE SET NULL"))
                    conn.commit()
                logger.info("🔄 已为 conversations 表添加 provider_id 列")
    except Exception as e:
        logger.warning(f"⚠️ conversations.provider_id 迁移检查跳过: {e}")

    # 迁移：如果 llm_providers 表缺少 model_type 列，则添加并把存量记录全部标为 'llm'
    try:
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(target_engine)
        if "llm_providers" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("llm_providers")]
            if "model_type" not in columns:
                with target_engine.connect() as conn:
                    conn.execute(text(
                        "ALTER TABLE llm_providers ADD COLUMN model_type VARCHAR(16) "
                        "NOT NULL DEFAULT 'llm'"
                    ))
                    conn.execute(text(
                        "UPDATE llm_providers SET model_type='llm' WHERE model_type IS NULL OR model_type=''"
                    ))
                    conn.commit()
                logger.info("🔄 已为 llm_providers 表添加 model_type 列（默认 'llm'）")
    except Exception as e:
        logger.warning(f"⚠️ llm_providers.model_type 迁移检查跳过: {e}")

    # 自动创建 admin 账户
    _ensure_admin_account(target_engine)
    # 自动创建预置技能
    init_skills(target_engine)
    # 自动创建默认 LLM Provider
    _ensure_default_provider(target_engine)
    # 迁移：retrieval_settings 表加 rerank 三列（旧库升级路径）
    try:
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(target_engine)
        if "retrieval_settings" in inspector.get_table_names():
            columns = {col["name"] for col in inspector.get_columns("retrieval_settings")}
            with target_engine.connect() as conn:
                if "rerank_enabled" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "rerank_enabled BOOLEAN NOT NULL DEFAULT 1"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 rerank_enabled 列")
                if "rerank_top_n" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "rerank_top_n INTEGER NOT NULL DEFAULT 5"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 rerank_top_n 列")
                if "rerank_provider_id" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "rerank_provider_id INTEGER"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 rerank_provider_id 列")
                if "contextual_chunking_enabled" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "contextual_chunking_enabled BOOLEAN NOT NULL DEFAULT 0"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 contextual_chunking_enabled 列")
                if "contextual_chunking_provider_id" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "contextual_chunking_provider_id INTEGER"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 contextual_chunking_provider_id 列")
                if "query_rewrite_simple_enabled" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "query_rewrite_simple_enabled BOOLEAN NOT NULL DEFAULT 0"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 query_rewrite_simple_enabled 列")
                if "query_rewrite_hyde_enabled" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "query_rewrite_hyde_enabled BOOLEAN NOT NULL DEFAULT 0"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 query_rewrite_hyde_enabled 列")
                if "query_rewrite_provider_id" not in columns:
                    conn.execute(text(
                        "ALTER TABLE retrieval_settings ADD COLUMN "
                        "query_rewrite_provider_id INTEGER"
                    ))
                    logger.info("🔄 已为 retrieval_settings 添加 query_rewrite_provider_id 列")
                # 分块 / 生成 / Prompt 可调字段（旧库升级路径）。
                # 每列 (name, DDL 片段)；逐个检测后补齐，幂等。
                _extra_cols = [
                    ("chunk_size", "chunk_size INTEGER NOT NULL DEFAULT 500"),
                    ("chunk_overlap", "chunk_overlap INTEGER NOT NULL DEFAULT 100"),
                    ("splitter_strategy",
                     "splitter_strategy VARCHAR(16) NOT NULL DEFAULT 'recursive'"),
                    ("chunk_separators", "chunk_separators TEXT"),
                    ("gen_temperature", "gen_temperature FLOAT NOT NULL DEFAULT 0.7"),
                    ("gen_top_p", "gen_top_p FLOAT"),
                    ("gen_max_tokens", "gen_max_tokens INTEGER"),
                    ("gen_presence_penalty",
                     "gen_presence_penalty FLOAT NOT NULL DEFAULT 0.0"),
                    ("gen_frequency_penalty",
                     "gen_frequency_penalty FLOAT NOT NULL DEFAULT 0.0"),
                    ("gen_stop", "gen_stop TEXT"),
                    ("max_context_length",
                     "max_context_length INTEGER NOT NULL DEFAULT 8000"),
                    ("max_history_messages",
                     "max_history_messages INTEGER NOT NULL DEFAULT 10"),
                    ("system_prompt_rag", "system_prompt_rag TEXT"),
                    ("system_prompt_direct", "system_prompt_direct TEXT"),
                    ("no_answer_text", "no_answer_text TEXT"),
                    ("allow_fallback_to_direct",
                     "allow_fallback_to_direct BOOLEAN NOT NULL DEFAULT 0"),
                ]
                for _col_name, _col_ddl in _extra_cols:
                    if _col_name not in columns:
                        conn.execute(text(
                            f"ALTER TABLE retrieval_settings ADD COLUMN {_col_ddl}"
                        ))
                        logger.info(f"🔄 已为 retrieval_settings 添加 {_col_name} 列")
                conn.commit()
    except Exception as e:
        logger.warning(f"⚠️ retrieval_settings.rerank_* / contextual_chunking_* / query_rewrite_* / chunk_* / gen_* / prompt 迁移检查跳过: {e}")

    # 迁移：messages 表加 query_rewrite 列（旧库升级路径）
    try:
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(target_engine)
        if "messages" in inspector.get_table_names():
            columns = {col["name"] for col in inspector.get_columns("messages")}
            if "query_rewrite" not in columns:
                with target_engine.connect() as conn:
                    conn.execute(text(
                        "ALTER TABLE messages ADD COLUMN query_rewrite TEXT"
                    ))
                    conn.commit()
                logger.info("🔄 已为 messages 添加 query_rewrite 列")
    except Exception as e:
        logger.warning(f"⚠️ messages.query_rewrite 迁移检查跳过: {e}")

    # 自动写入混合检索默认设置
    _ensure_retrieval_settings(target_engine)
    _mark_interrupted_ingestion_jobs(target_engine)
    logger.info("✅ 数据库表创建完成")


def _mark_interrupted_ingestion_jobs(target_engine):
    """进程重启后，内存后台任务无法恢复，将悬空任务标记为失败以便重试。"""
    _Session = sessionmaker(bind=target_engine)
    db = _Session()
    try:
        rows = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.status.in_(["queued", "processing"])
        ).all()
        for row in rows:
            row.status = "failed"
            row.error_message = "服务重启导致入库任务中断，请点击重新入库"
        if rows:
            db.commit()
            logger.warning(f"⚠️ 已标记 {len(rows)} 个中断的入库任务为 failed")
    finally:
        db.close()


def _ensure_retrieval_settings(target_engine):
    """确保 RetrievalSettings 单行存在；首次启动按 config.HYBRID_* 写入。

    幂等：以 id=1 为定位键；后续启动若已存在则不覆盖管理员修改后的值。
    """
    from config import (
        CHUNK_OVERLAP,
        CHUNK_SIZE,
        CONTEXTUAL_CHUNKING_DEFAULT_ENABLED,
        GEN_DEFAULT_FREQUENCY_PENALTY,
        GEN_DEFAULT_MAX_TOKENS,
        GEN_DEFAULT_PRESENCE_PENALTY,
        GEN_DEFAULT_TEMPERATURE,
        GEN_DEFAULT_TOP_P,
        HYBRID_ALPHA,
        HYBRID_BM25_TOP_K,
        HYBRID_DEFAULT_MODE,
        HYBRID_RRF_K,
        HYBRID_VECTOR_TOP_K,
        MAX_CONTEXT_LENGTH,
        MAX_HISTORY_MESSAGES,
        QUERY_REWRITE_HYDE_DEFAULT_ENABLED,
        QUERY_REWRITE_SIMPLE_DEFAULT_ENABLED,
        RAG_RELEVANCE_THRESHOLD,
        RERANK_DEFAULT_ENABLED,
        RERANK_DEFAULT_PROVIDER_ID,
        RERANK_DEFAULT_TOP_N,
        SPLITTER_DEFAULT_STRATEGY,
        TOP_K,
    )

    _Session = sessionmaker(bind=target_engine)
    db = _Session()
    try:
        existing = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
        if existing:
            logger.info("⚙️ 检索设置已存在，跳过 seed")
            return
        row = RetrievalSettings(
            id=1,
            mode=HYBRID_DEFAULT_MODE,
            alpha=HYBRID_ALPHA,
            rrf_k=HYBRID_RRF_K,
            bm25_top_k=HYBRID_BM25_TOP_K,
            vector_top_k=HYBRID_VECTOR_TOP_K,
            final_top_k=TOP_K,
            semantic_threshold=RAG_RELEVANCE_THRESHOLD,
            enable_bm25=True,
            rerank_enabled=RERANK_DEFAULT_ENABLED,
            rerank_top_n=RERANK_DEFAULT_TOP_N,
            rerank_provider_id=RERANK_DEFAULT_PROVIDER_ID,
            contextual_chunking_enabled=CONTEXTUAL_CHUNKING_DEFAULT_ENABLED,
            contextual_chunking_provider_id=None,
            query_rewrite_simple_enabled=QUERY_REWRITE_SIMPLE_DEFAULT_ENABLED,
            query_rewrite_hyde_enabled=QUERY_REWRITE_HYDE_DEFAULT_ENABLED,
            query_rewrite_provider_id=None,
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            splitter_strategy=SPLITTER_DEFAULT_STRATEGY,
            chunk_separators=None,
            gen_temperature=GEN_DEFAULT_TEMPERATURE,
            gen_top_p=GEN_DEFAULT_TOP_P,
            gen_max_tokens=GEN_DEFAULT_MAX_TOKENS,
            gen_presence_penalty=GEN_DEFAULT_PRESENCE_PENALTY,
            gen_frequency_penalty=GEN_DEFAULT_FREQUENCY_PENALTY,
            gen_stop=None,
            max_context_length=MAX_CONTEXT_LENGTH,
            max_history_messages=MAX_HISTORY_MESSAGES,
            system_prompt_rag=None,
            system_prompt_direct=None,
            no_answer_text=None,
            allow_fallback_to_direct=False,
        )
        db.add(row)
        db.commit()
        logger.info(
            f"⚙️ 已写入默认检索设置 mode={HYBRID_DEFAULT_MODE} "
            f"alpha={HYBRID_ALPHA} top_k={TOP_K}"
        )
    except Exception as e:
        logger.error(f"❌ 写入默认检索设置失败: {e}")
        db.rollback()
    finally:
        db.close()


def _ensure_admin_account(target_engine):
    """确保 admin 账户存在。

    首次启动用默认凭据 ``admin/123456`` 创建；生产部署后管理员应立即在
    前端修改密码。后续启动检测到已存在则跳过，不覆盖修改后的密码。
    """
    from auth import hash_password

    _Session = sessionmaker(bind=target_engine)
    db = _Session()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                hashed_password=hash_password("123456"),
                role="admin",
            )
            db.add(admin)
            db.commit()
            logger.info("👤 已自动创建 admin 账户")
        else:
            logger.info("👤 admin 账户已存在，跳过创建")
    except Exception as e:
        logger.error(f"❌ 创建 admin 账户失败: {e}")
        db.rollback()
    finally:
        db.close()


def _ensure_default_provider(target_engine):
    """确保至少存在一份占位的远程 Provider 模板（LLM/嵌入/OCR 各一）。

    所有 Provider 均为远程 OpenAI 兼容 API；api_key 留空，需用户在
    管理后台填写真实凭据后才能使用。已存在记录则不重复创建。
    """
    _Session = sessionmaker(bind=target_engine)
    db = _Session()
    try:
        templates = [
            {
                "name": "DeepSeek Chat",
                "model_type": "llm",
                "provider_type": "remote",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "",
                "model_name": "deepseek-chat",
                "max_tokens": 4096,
                "timeout_seconds": 120,
                "is_default": True,
            },
            {
                # 国内用户更易拿到 Key 且 OpenAI 兼容；如需切换为 OpenAI 直接改 base_url 即可。
                "name": "智谱 Embedding",
                "model_type": "embedding",
                "provider_type": "remote",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key": "",
                "model_name": "embedding-3",
                "max_tokens": 8192,
                "timeout_seconds": 60,
                "is_default": True,
            },
            {
                "name": "智谱 GLM-4V (OCR)",
                "model_type": "ocr",
                "provider_type": "remote",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key": "",
                "model_name": "glm-4v-flash",
                "max_tokens": 4096,
                "timeout_seconds": 120,
                "is_default": True,
            },
        ]

        for t in templates:
            existing = db.query(LlmProvider).filter(
                LlmProvider.model_type == t["model_type"]
            ).first()
            if existing:
                # 安全迁移：仅当占位记录未填 API Key 且 base_url 仍是旧默认值时
                # 才将其升级为新模板，避免覆盖用户已配置的内容。
                obsolete_combos = {
                    ("embedding", "https://api.openai.com/v1", "text-embedding-3-small"),
                }
                if (
                    not (existing.api_key or "").strip()
                    and (
                        existing.model_type,
                        (existing.base_url or "").rstrip("/"),
                        existing.model_name or "",
                    )
                    in obsolete_combos
                ):
                    existing.name = t["name"]
                    existing.base_url = t["base_url"]
                    existing.model_name = t["model_name"]
                    existing.max_tokens = t["max_tokens"]
                    existing.timeout_seconds = t["timeout_seconds"]
                    logger.info(
                        f"🔁 检测到未配置的旧 {existing.model_type} 占位 Provider，"
                        f"已升级为模板: {t['name']}"
                    )
                else:
                    logger.info(f"🤖 已存在 {t['model_type']} Provider，跳过模板创建")
                continue
            provider = LlmProvider(**t, is_active=True)
            db.add(provider)
            logger.info(f"🤖 已创建 {t['model_type']} Provider 模板: {t['name']}")
        db.commit()
    except Exception as e:
        logger.error(f"❌ 创建默认 Provider 失败: {e}")
        db.rollback()
    finally:
        db.close()


# 预置技能定义
# 这里定义的是系统首次启动时写入数据库的"种子技能"。管理员之后可以
# 在管理后台修改、删除或新增技能，本列表的修改不会回写已存在的技能
# 记录（init_skills 仅在 name 不存在时才插入）。
_PRESET_SKILLS = [
    {
        "name": "财务分析",
        "description": "专业财务数据分析与报表解读，帮助理解财务报表、利润分析、资产负债等财务相关内容。",
        "system_prompt": "你是一个专业的财务分析专家。请根据参考资料，用简练的中文回答用户关于财务报表、利润分析、资产负债、营收成本等方面的问题。回答时注意数据准确性，必要时给出计算过程。",
        "icon": "📊",
        "auto_detect_patterns": ["财务", "报表", "利润", "资产", "负债", "营收", "成本"],
    },
    {
        "name": "会议纪要助手",
        "description": "会议记录智能整理与要点提取，自动识别会议文档并帮助提炼议程要点、决议事项、待办跟进和参会人观点。",
        "system_prompt": (
            "你是一个专业的会议纪要整理助手。请根据参考资料，用简练的中文帮助用户：\n"
            "1. 提取会议基本信息（时间、地点、参会人）；\n"
            "2. 按议题分类整理讨论内容；\n"
            "3. 明确列出决议事项和责任人；\n"
            "4. 汇总待办事项与截止时间。\n"
            "回答时注意条理清晰，使用编号列表，突出关键结论。"
        ),
        "icon": "📝",
        "auto_detect_patterns": ["会议", "纪要", "议程", "决议", "讨论", "参会", "待办", "议题", "记录"],
    },
    {
        "name": "本地政策文档",
        "description": "政策法规解读与查询，帮助理解和查询各类政策、规定、条例等文档内容。",
        "system_prompt": "你是一个智能政策问答助手。请根据参考资料，用简练的中文回答用户关于政策法规、规定条例、管理办法等方面的问题。回答时注意引用具体条款，确保准确性。",
        "icon": "📋",
        "auto_detect_patterns": ["政策", "规定", "条例", "办法", "通知", "制度"],
    },
    {
        "name": "发票助手",
        "description": "发票信息提取与核验，自动识别发票类文档并帮助提取关键字段、核对金额、整理报销明细。",
        "system_prompt": (
            "你是一个专业的发票处理助手。请根据参考资料，用简练的中文帮助用户：\n"
            "1. 提取发票关键信息（发票号码、开票日期、购买方、销售方）；\n"
            "2. 列出商品/服务明细、单价、数量和金额；\n"
            "3. 核对税额、价税合计等关键数字；\n"
            "4. 如有多张发票，汇总合计金额。\n"
            "回答时注意数据准确性，使用表格或列表格式呈现，方便核对。"
        ),
        "icon": "🧾",
        "auto_detect_patterns": ["发票", "invoice", "税额", "价税", "开票", "报销", "增值税", "专票", "普票"],
    },
    {
        # 改编自 https://github.com/Norman-bury/research-writing-skill
        # 原仓库是 19 个子技能的多文件 bundle，本项目只支持单 prompt，
        # 这里把 SKILL.md 的核心原则与路径拆解精炼为一个 system_prompt。
        "name": "科研写作助手",
        "description": (
            "面向本科与研究生论文写作的执行型 Skill，覆盖头脑风暴、章节写作、"
            "文献综述、LaTeX 输出。改编自 "
            "https://github.com/Norman-bury/research-writing-skill"
        ),
        "system_prompt": (
            "你是一个专业的科研写作助手。请根据参考资料，用简练的中文帮助用户"
            "完成论文写作任务。遵循以下四条原则：\n"
            "1. 流程优于即兴：开始写作前先与用户确认论文类型（学位论文 / "
            "期刊论文 / 综述）和章节结构，避免一上来就堆砌内容；\n"
            "2. 证据优于声称：所有引用必须可追溯到参考资料中的原文；"
            "若资料中无依据，明确告诉用户「资料中未涉及」而不是编造文献；\n"
            "3. 简洁优于复杂：去 AI 化写作，避免「值得注意的是」「综上所述」"
            "等机械表达，使用学术风格的主动句；\n"
            "4. 确认优于假设：每完成一章或一节，主动请用户确认再继续下一段。\n\n"
            "根据用户问题选择路径：\n"
            "- 头脑风暴：先问清楚研究主题、目标读者、字数要求，再给章节大纲；\n"
            "- 章节写作：按用户给定的大纲逐节展开，每节先列要点再展开成文；\n"
            "- 文献综述：按主题归类、按时间排序，标注每条文献的核心观点；\n"
            "- LaTeX 输出：用标准模板，公式用 align/equation 环境，表用 booktabs。\n\n"
            "（完整工作流详见 docs/SKILLS.md「research-writing demo」一节。）"
        ),
        "icon": "📚",
        "auto_detect_patterns": [
            "论文", "学位论文", "毕业论文", "硕士论文", "博士论文",
            "文献综述", "literature review", "摘要", "abstract",
            "引言", "方法", "实验", "结果", "讨论", "参考文献",
            "LaTeX", "投稿", "审稿", "Introduction", "Methods",
        ],
    },
]


def init_skills(target_engine=None):
    """初始化预置技能。如果技能不存在则创建。

    幂等：以 name 为唯一键判断；管理员重命名内置技能后再次启动不会
    被覆盖，但会因 name 变化而重新创建一份同名旧技能 —— 实践中很少
    出现，因此未做更复杂的标识。
    """
    _engine = target_engine or engine
    _Session = sessionmaker(bind=_engine)
    db = _Session()
    try:
        for skill_data in _PRESET_SKILLS:
            existing = db.query(Skill).filter(Skill.name == skill_data["name"]).first()
            if not existing:
                skill = Skill(
                    name=skill_data["name"],
                    description=skill_data["description"],
                    system_prompt=skill_data["system_prompt"],
                    icon=skill_data["icon"],
                    auto_detect_patterns=json.dumps(
                        skill_data["auto_detect_patterns"], ensure_ascii=False
                    ),
                )
                db.add(skill)
                logger.info(f"🎯 已创建预置技能: {skill_data['name']}")
        db.commit()
    except Exception as e:
        logger.error(f"❌ 创建预置技能失败: {e}")
        db.rollback()
    finally:
        db.close()
