"""Pydantic 请求 / 响应模型集中定义。

定位：把所有 HTTP 入参与出参的形状放在一处，便于 FastAPI 自动生成
Swagger 文档（``/docs``），同时让前端 TypeScript 类型可以反推。

命名约定：
    - ``XxxCreate``  → POST 入参（创建/写）
    - ``XxxUpdate``  → PUT/PATCH 入参（部分更新）
    - ``XxxOut``     → 单条响应
    - ``XxxListResponse`` → 列表响应（包一层带 ``total``/``items``）

ORM 模型转 Pydantic：使用 ``ConfigDict(from_attributes=True)`` 让 Pydantic
能直接从 SQLAlchemy ORM 实例的属性取值，避免手工 ``.dict()`` 转换。
时间字段统一使用 ``str`` 而非 ``datetime``，由 ORM 在 ``to_dict()`` 中
预格式化为 ISO8601，避免时区歧义。
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Literal, Optional


# ==========================================
# 混合检索（BM25 + 语义）调参 Schema
# ==========================================


class RetrievalParams(BaseModel):
    """单次问答可覆盖的混合检索参数。所有字段可选 —— 未传则使用 DB
    中的全局默认。设计为 Optional 是为了方便前端做"调参实验"：
    只发想测的字段，其余沿用默认。"""

    mode: Optional[Literal["weighted", "rrf", "semantic", "bm25"]] = Field(
        default=None, description="融合策略"
    )
    alpha: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="weighted 模式 semantic 权重；bm25 权重 = 1-alpha",
    )
    rrf_k: Optional[int] = Field(default=None, ge=1, description="RRF 常数")
    bm25_top_k: Optional[int] = Field(default=None, ge=1, description="BM25 召回数")
    vector_top_k: Optional[int] = Field(default=None, ge=1, description="语义召回数")
    final_top_k: Optional[int] = Field(default=None, ge=1, description="最终保留数")
    semantic_threshold: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="语义通道相关度阈值"
    )
    # ----- Rerank -----
    rerank_enabled: Optional[bool] = Field(
        default=None, description="本次问答是否启用 LLM 精排"
    )
    rerank_top_n: Optional[int] = Field(
        default=None, ge=1, description="精排后真正喂给 LLM 的最终条数"
    )


class RetrievalSettingsOut(BaseModel):
    """``GET /api/admin/retrieval`` 响应：DB 中持久化的全局参数。"""

    mode: str
    alpha: float
    rrf_k: int
    bm25_top_k: int
    vector_top_k: int
    final_top_k: int
    semantic_threshold: float
    enable_bm25: bool
    rerank_enabled: bool = True
    rerank_top_n: int = 5
    rerank_provider_id: Optional[int] = None
    contextual_chunking_enabled: bool = False
    contextual_chunking_provider_id: Optional[int] = None
    query_rewrite_simple_enabled: bool = False
    query_rewrite_hyde_enabled: bool = False
    query_rewrite_provider_id: Optional[int] = None
    # ----- 分块 -----
    chunk_size: int = 500
    chunk_overlap: int = 100
    splitter_strategy: str = "recursive"
    chunk_separators: Optional[List[str]] = None
    # ----- 生成 -----
    gen_temperature: float = 0.7
    gen_top_p: Optional[float] = None
    gen_max_tokens: Optional[int] = None
    gen_presence_penalty: float = 0.0
    gen_frequency_penalty: float = 0.0
    gen_stop: Optional[List[str]] = None
    max_context_length: int = 8000
    max_history_messages: int = 10
    # ----- Prompt 与拒答 -----
    system_prompt_rag: Optional[str] = None
    system_prompt_direct: Optional[str] = None
    no_answer_text: Optional[str] = None
    allow_fallback_to_direct: bool = False
    updated_at: str


class RetrievalSettingsUpdate(BaseModel):
    """``PUT /api/admin/retrieval`` 入参。任何字段为 None 表示不修改。

    例外：文本 / 列表型字段（``system_prompt_*`` / ``no_answer_text`` /
    ``chunk_separators`` / ``gen_stop``）传入空字符串 ``""`` 或空数组 ``[]``
    表示「重置为内置默认」，由 API 层转成 NULL 落库。
    """

    mode: Optional[Literal["weighted", "rrf", "semantic", "bm25"]] = None
    alpha: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    rrf_k: Optional[int] = Field(default=None, ge=1)
    bm25_top_k: Optional[int] = Field(default=None, ge=1)
    vector_top_k: Optional[int] = Field(default=None, ge=1)
    final_top_k: Optional[int] = Field(default=None, ge=1)
    semantic_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    enable_bm25: Optional[bool] = None
    rerank_enabled: Optional[bool] = None
    rerank_top_n: Optional[int] = Field(default=None, ge=1)
    rerank_provider_id: Optional[int] = Field(
        default=None, description="0 或 None 表示与主 LLM 同 Provider"
    )
    contextual_chunking_enabled: Optional[bool] = None
    contextual_chunking_provider_id: Optional[int] = Field(
        default=None, description="0 或 None 表示与主 LLM 同 Provider"
    )
    query_rewrite_simple_enabled: Optional[bool] = None
    query_rewrite_hyde_enabled: Optional[bool] = None
    query_rewrite_provider_id: Optional[int] = Field(
        default=None, description="0 或 None 表示与主 LLM 同 Provider"
    )
    # ----- 分块 -----
    chunk_size: Optional[int] = Field(default=None, ge=50, le=4000)
    chunk_overlap: Optional[int] = Field(default=None, ge=0, le=2000)
    splitter_strategy: Optional[Literal["recursive", "markdown", "character", "token"]] = None
    chunk_separators: Optional[List[str]] = Field(
        default=None, description="空数组 [] 表示重置为策略内置默认"
    )
    # ----- 生成 -----
    gen_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    gen_top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    gen_max_tokens: Optional[int] = Field(default=None, ge=1, le=131072)
    gen_presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    gen_frequency_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    gen_stop: Optional[List[str]] = Field(
        default=None, description="空数组 [] 表示清空停止序列"
    )
    max_context_length: Optional[int] = Field(default=None, ge=500, le=200000)
    max_history_messages: Optional[int] = Field(default=None, ge=0, le=100)
    # ----- Prompt 与拒答 -----
    system_prompt_rag: Optional[str] = Field(
        default=None, description="空字符串表示重置为内置默认"
    )
    system_prompt_direct: Optional[str] = Field(
        default=None, description="空字符串表示重置为内置默认"
    )
    no_answer_text: Optional[str] = Field(
        default=None, description="空字符串表示重置为内置默认"
    )
    allow_fallback_to_direct: Optional[bool] = None


class RetrievalPreviewRequest(BaseModel):
    """``POST /api/admin/retrieval/preview`` 入参：跑一次检索看 ranking 但不调 LLM。"""

    query: str = Field(..., min_length=1)
    params: Optional[RetrievalParams] = Field(
        default=None,
        description="可选；不传则使用全局默认。可只覆盖部分字段做 A/B 对比。",
    )


class RetrievalPreviewItem(BaseModel):
    """preview 返回的单条片段：把两路 rank 与归一化分数都带回前端，
    便于人肉判断哪种 mode 更合理。"""

    id: str
    source: str
    content: str   # 截断 200 字预览
    bm25_rank: Optional[int] = None
    bm25_norm: Optional[float] = None
    sem_rank: Optional[int] = None
    sem_norm: Optional[float] = None
    fused_score: float
    # rerank：original_rank 是融合后的位次（1-based），rerank_score
    # 是 LLM 给出的 0-10 分；rerank 关闭时两者都为 None。
    original_rank: Optional[int] = None
    rerank_score: Optional[float] = None


class RetrievalPreviewResponse(BaseModel):
    mode: str
    used_params: RetrievalSettingsOut
    items: List[RetrievalPreviewItem]


class QueryRequest(BaseModel):
    """``POST /query`` 同步问答入参。"""
    question: str = Field(..., description="用户提出的问题")
    # top_k 可由请求覆盖 config.TOP_K；ge=1 保证至少返回 1 条参考。
    # 注意：与 ``retrieval.final_top_k`` 同义；两者都传时以 ``retrieval`` 优先。
    top_k: Optional[int] = Field(default=None, ge=1, description="返回的参考片段数量")
    # temperature 上限 2.0 对齐 OpenAI 规范；某些国产模型上限不同，
    # 由 Provider 自行截断处理。
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0, description="LLM 生成温度 (0.0-2.0)")
    chunk_size: Optional[int] = Field(default=None, ge=1, description="上下文组装的片段大小")
    skill_id: Optional[int] = Field(default=None, description="技能场景 ID，使用对应技能的 system_prompt")
    # 单次问答覆盖检索参数；不传则用 DB 全局默认。
    retrieval: Optional[RetrievalParams] = Field(
        default=None, description="混合检索调参；None 则使用全局默认"
    )


class SourceDocument(BaseModel):
    """单个被检索到的参考片段（用于 QueryResponse.sources）。

    ``index`` 与 LLM 答案正文中的 ``[n]`` 角标对齐（1-based）；
    默认 0 是为了兼容老接口/老前端解析路径——不显式赋值时保持 0
    表示「未编号」，前端可据此回退到旧的卡片式展示。
    """
    index: int = 0
    content: str
    source: str
    score: float
    chunk_id: Optional[str] = None
    page: Optional[int] = None


class QueryResponse(BaseModel):
    """``POST /query`` 响应。``rule_matched`` 不为 None 说明走了规则引擎直答。"""
    answer: str
    sources: List[SourceDocument]
    rule_matched: Optional[str] = None
    citation_validation: Optional[dict] = None
    usage: Optional[dict] = None


class UploadResponse(BaseModel):
    """``POST /upload`` 响应。"""
    filename: str
    status: str        # "success" / "failed"
    message: str       # 人读说明，失败时给出原因


class AuthRequest(BaseModel):
    """登录 / 注册共用入参。min_length=6 是最低安全门槛（明文密码长度限制）。"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., min_length=6, description="密码（至少6位）")


class AuthResponse(BaseModel):
    """登录成功后下发 JWT。token_type 固定 ``bearer``，符合 RFC 6750。"""
    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")


# ==========================================
# 对话相关 Schema
# ==========================================


class ConversationCreate(BaseModel):
    """新建对话入参。所有字段可选 —— 前端可仅传空对象创建匿名对话，
    系统按时间生成默认标题。"""
    title: Optional[str] = Field(default=None, description="对话标题（可选）")
    skill_id: Optional[int] = Field(default=None, description="技能场景 ID（可选）")
    provider_id: Optional[int] = Field(default=None, description="LLM Provider ID（可选）")


class MessageCreate(BaseModel):
    """发消息入参。``use_rag=False`` 用于"纯聊天"场景，跳过向量检索
    （前端"开关知识库"按钮）。"""
    question: str = Field(..., min_length=1, description="用户提出的问题")
    provider_id: Optional[int] = Field(default=None, description="LLM Provider ID（可选，覆盖对话默认值）")
    skill_id: Optional[int] = Field(default=None, description="技能场景 ID（可选，覆盖对话默认值）")
    use_rag: bool = Field(
        default=True,
        description="是否结合知识库检索；关闭则纯对话（忽略向量检索）",
    )
    # 单次消息覆盖检索参数；用于"对同一问题切换检索算法做对比实验"。
    retrieval: Optional[RetrievalParams] = Field(
        default=None, description="混合检索调参；None 则使用全局默认"
    )


class MessageOut(BaseModel):
    """消息出参。``sources`` 用 JSON 字符串而非嵌套结构，是为了和 ORM
    存储格式一致（DB 中 sources 列就是 TEXT JSON）。"""
    id: int
    role: str          # "user" / "assistant"
    content: str
    sources: Optional[str] = None       # JSON 字符串，前端解析后展示来源卡片
    rule_matched: Optional[str] = None  # 命中规则时的规则名
    # 查询重写信息：JSON 字符串，形如 {"original":"","simple":"","hyde":"",
    # "bm25_query":"","vector_query":""}。仅 assistant 消息可能有；
    # 关闭重写或未触发时为 None。
    query_rewrite: Optional[str] = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class ConversationOut(BaseModel):
    """对话列表项。``message_count`` 由 API 层 SQL COUNT 后赋值。"""
    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ConversationDetail(BaseModel):
    """对话详情：列表项 + 全部消息。"""
    id: int
    title: str
    created_at: str
    updated_at: str
    messages: List[MessageOut] = []

    model_config = ConfigDict(from_attributes=True)


# ==========================================
# 文档管理相关 Schema
# ==========================================


class DocumentInfo(BaseModel):
    """文档列表中的单项。chunk_count 来自 database.list_document_sources。"""
    name: str = Field(..., description="文档文件名")
    chunk_count: int = Field(..., description="文档对应的向量片段数量")
    status: str = Field(default="ready", description="queued/processing/ready/failed")
    error_message: Optional[str] = Field(default=None, description="最近一次入库失败原因")
    content_sha256: Optional[str] = Field(default=None, description="文件内容 SHA-256")
    updated_at: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: List[DocumentInfo] = Field(default_factory=list, description="文档列表")


class DocumentDeleteResponse(BaseModel):
    name: str = Field(..., description="被删除的文档名")
    deleted_chunks: int = Field(..., description="删除的片段数量")


class DocumentReingestResponse(BaseModel):
    name: str = Field(..., description="重新入库的文档名")
    chunk_count: int = Field(..., description="新生成的片段数量")
    status: str = Field(default="queued", description="后台入库任务状态")


# ==========================================
# 向量片段 CRUD 相关 Schema
# ==========================================


class ChunkOut(BaseModel):
    """单个向量片段。id 为 ChromaDB UUID 字符串。"""
    id: str = Field(..., description="片段 ID")
    content: str = Field(..., description="片段文本内容")
    metadata: dict = Field(default_factory=dict, description="片段元数据")


class ChunkListResponse(BaseModel):
    chunks: List[ChunkOut] = Field(default_factory=list, description="片段列表")
    total: int = Field(..., description="符合条件的片段总数")


class ChunkSearchResult(BaseModel):
    """向量检索单条结果，比 ChunkOut 多 ``distance`` 字段。"""
    id: str = Field(..., description="片段 ID")
    content: str = Field(..., description="片段文本内容")
    metadata: dict = Field(default_factory=dict, description="片段元数据")
    distance: float = Field(..., description="与查询的距离（越小越相似）")


class ChunkSearchResponse(BaseModel):
    results: List[ChunkSearchResult] = Field(default_factory=list, description="搜索结果")


class ChunkUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="新的片段文本内容")


class ChunkDeleteResponse(BaseModel):
    deleted: bool = Field(..., description="是否成功删除")


# ==========================================
# 反馈相关 Schema
# ==========================================


class FeedbackCreate(BaseModel):
    """点赞 / 点踩入参。rating 严格限定为 'up'/'down'，由路由层校验。"""
    message_id: int = Field(..., description="消息 ID")
    rating: str = Field(..., description="评分：'up' 或 'down'")
    comment: Optional[str] = Field(default=None, description="可选评论")


class FeedbackOut(BaseModel):
    id: int
    message_id: int
    rating: str
    comment: Optional[str] = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class FeedbackListResponse(BaseModel):
    feedbacks: List[FeedbackOut] = Field(default_factory=list, description="反馈列表")


# ==========================================
# 文档预览相关 Schema
# ==========================================


class DocumentPreviewResponse(BaseModel):
    """``GET /api/documents/{name}/preview`` 响应。

    PDF 文档可能极大，预览只截取前 N 页 / 前 N 字符，因此区分
    ``total_*`` 与 ``previewed_*`` 让前端可显示"已加载 X / 总 Y"。
    """
    filename: str = Field(..., description="文件名")
    content: str = Field(..., description="预览内容")
    total_pages: Optional[int] = Field(default=None, description="PDF 总页数")
    previewed_pages: Optional[int] = Field(default=None, description="预览的页数")
    total_chars: int = Field(..., description="文件总字符数")
    previewed_chars: int = Field(..., description="预览的字符数")


# ==========================================
# Admin 管理相关 Schema
# ==========================================


class AdminUserOut(BaseModel):
    id: int
    username: str
    role: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class AdminUserListResponse(BaseModel):
    users: List[AdminUserOut] = Field(default_factory=list, description="用户列表")


class AdminStatsResponse(BaseModel):
    """管理后台首页 KPI。"""
    user_count: int = Field(..., description="用户总数")
    conversation_count: int = Field(..., description="对话总数")
    document_count: int = Field(..., description="文档总数")


class AdminFeedbackOut(BaseModel):
    """管理员看到的反馈列表多了 user_id —— 前台用户只能看自己的，不需要。"""
    id: int
    message_id: int
    user_id: int
    rating: str
    comment: Optional[str] = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class AdminFeedbackListResponse(BaseModel):
    feedbacks: List[AdminFeedbackOut] = Field(default_factory=list, description="反馈列表")


# ==========================================
# 技能场景相关 Schema
# ==========================================


class SkillCreate(BaseModel):
    """创建技能。``auto_detect_patterns`` 在 DB 中存为 JSON 字符串，
    在 ORM 的 ``to_dict()`` 中解析为 list，本 schema 直接接 list 提交。"""
    name: str = Field(..., min_length=1, description="技能名称")
    description: Optional[str] = Field(default="", description="技能描述")
    system_prompt: str = Field(..., min_length=1, description="系统提示词")
    icon: Optional[str] = Field(default=None, description="技能图标")
    auto_detect_patterns: Optional[List[str]] = Field(default_factory=list, description="自动检测模式列表")


class SkillUpdate(BaseModel):
    """部分更新：所有字段 Optional，None 表示不改。"""
    name: Optional[str] = Field(default=None, min_length=1, description="技能名称")
    description: Optional[str] = Field(default=None, description="技能描述")
    system_prompt: Optional[str] = Field(default=None, min_length=1, description="系统提示词")
    icon: Optional[str] = Field(default=None, description="技能图标")
    auto_detect_patterns: Optional[List[str]] = Field(default=None, description="自动检测模式列表")


class SkillOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = ""
    system_prompt: str
    icon: Optional[str] = None
    auto_detect_patterns: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class SkillImportRequest(BaseModel):
    """``POST /api/admin/skills/import`` 入参：从带 frontmatter 的
    markdown 文本一键导入技能。frontmatter 解析仅支持简单 ``key: value``
    单行格式，不支持嵌套 YAML / list。"""

    markdown: str = Field(..., min_length=1, description="完整的 SKILL.md 文本")
    name_override: Optional[str] = Field(
        default=None, description="覆盖 frontmatter 的 name；不传则用 frontmatter"
    )
    icon: Optional[str] = Field(default=None, description="技能图标，默认 📚")
    auto_detect_patterns: Optional[List[str]] = Field(
        default=None, description="关键词列表，默认空"
    )
    overwrite: bool = Field(
        default=False, description="同名已存在时是否更新；False 则返回 409"
    )


class SkillListResponse(BaseModel):
    skills: List[SkillOut] = Field(default_factory=list, description="技能列表")


class SkillDetectRequest(BaseModel):
    """技能自动识别入参。filename 与 content 可任意组合，越多越准确。"""
    filename: Optional[str] = Field(default=None, description="文件名")
    content: Optional[str] = Field(default=None, description="文档内容")


class SkillDetectResponse(BaseModel):
    """识别失败（无技能命中）时两个字段都为 None。"""
    suggested_skill_id: Optional[int] = Field(default=None, description="建议的技能 ID")
    skill_name: Optional[str] = Field(default=None, description="建议的技能名称")


# ==========================================
# LLM Provider 相关 Schema
# ==========================================


class ProviderCreate(BaseModel):
    """新增模型 Provider 入参（管理员）。

    ``model_type`` 三种取值决定该 Provider 在何处被消费：
        - llm        → rag_engine 调用 chat.completions
        - embedding  → database.get_embeddings 用作向量化
        - ocr        → ocr_engine 走 vision API
    """
    name: str = Field(..., min_length=1, description="显示名称")
    model_type: str = Field(default="llm", description="模型类型：llm / embedding / ocr")
    provider_type: str = Field(default="remote", description="部署形态：remote / local（仅展示用）")
    base_url: str = Field(..., min_length=1, description="API 基址")
    api_key: str = Field(..., description="API Key")
    model_name: str = Field(..., min_length=1, description="模型标识")
    max_tokens: int = Field(default=4096, ge=1, description="最大生成 token 数")
    timeout_seconds: int = Field(default=120, ge=1, description="超时时间（秒）")
    is_default: bool = Field(default=False, description="是否为同类型的默认 provider")


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    model_type: Optional[str] = Field(default=None, description="llm / embedding / ocr")
    provider_type: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None, min_length=1)
    # 空字符串语义专门设计为"不修改"：管理后台展示时 api_key 是脱敏的，
    # 用户不改 key 时表单提交空串，避免覆写真实值。
    api_key: Optional[str] = Field(default=None, description="空字符串表示不修改")
    model_name: Optional[str] = Field(default=None, min_length=1)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    is_default: Optional[bool] = Field(default=None)
    is_active: Optional[bool] = Field(default=None)


class ProviderOut(BaseModel):
    """Provider 出参 —— 永远不返回真实 api_key，只返回脱敏 hint
    （形如 ``sk-***abc``）以保护凭据。"""
    id: int
    name: str
    model_type: str
    provider_type: str
    base_url: str
    api_key_hint: str = Field(description="脱敏后的 API Key")
    model_name: str
    max_tokens: int
    timeout_seconds: int
    is_default: bool
    is_active: bool
    created_at: str


class ProviderListResponse(BaseModel):
    providers: List[ProviderOut] = Field(default_factory=list)


class ProviderTestResponse(BaseModel):
    """测试 Provider 连通性的响应。``latency_ms`` 仅在成功时有值。"""
    success: bool
    message: str
    latency_ms: Optional[float] = None
