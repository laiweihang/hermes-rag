# config.py
"""集中配置模块。

定位：
    - 单一事实来源（Single Source of Truth）—— 所有调参变量集中此处，
      其它模块只 import 不重复读取 ``os.getenv``。
    - 副作用：``import config`` 时即创建 data/、uploads/ 等运行时目录，
      并初始化 root logger 配置。

读取顺序：
    1. 项目根目录的 ``.env`` 文件（由 python-dotenv 自动加载）。
    2. 进程环境变量。
    3. 代码内置默认值（``os.getenv("X", "default")`` 的第二参数）。

⚠️ 生产部署务必覆盖：``JWT_SECRET_KEY``、``CORS_ORIGINS``。
"""

import os
import logging

from dotenv import load_dotenv

# 显式调用一次：确保后续 os.getenv 能读到 .env 中的键。
# python-dotenv 找不到 .env 时静默不报错，本地开发可不放 .env。
load_dotenv()

# 全局日志配置：所有模块共享同一格式 + INFO 级别。
# 单独模块要 DEBUG 时可在自身 logger 上 setLevel 覆盖。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ==========================================
# 1. 路径配置
# ==========================================

# 以本文件所在目录作为项目根，避免依赖 cwd —— 任意目录下启动均能找到资源。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")        # 运行时数据：DB + 向量库
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")   # 用户上传的原始文件
DB_DIR = os.path.join(DATA_DIR, "chroma_db")     # ChromaDB 持久化目录

# 关系数据库：默认 SQLite 单文件，方便零依赖本地部署；如需切到 Postgres
# 等可通过环境变量覆盖整个 SQLALCHEMY_DATABASE_URL。
SQLITE_DB_PATH = os.path.join(DATA_DIR, "smartpolicy.db")
SQLALCHEMY_DATABASE_URL = os.getenv(
    "SQLALCHEMY_DATABASE_URL",
    f"sqlite:///{SQLITE_DB_PATH}",
)

# 必要目录预创建：避免首次写文件时 FileNotFoundError。
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

# ==========================================
# 2. 检索 / 切片配置
# ==========================================

# 检索 Top-K 相关片段（基础值，小知识库会自动上调）
TOP_K = int(os.getenv("TOP_K", "5"))
# 切片大小：保留足够上下文。500 字符是 langchain RecursiveCharacterTextSplitter
# 的常见经验值，对中文段落友好。
# 注意：这些常量仅作为首次启动写入 RetrievalSettings 表的 seed —— 启动后实际
# 生效的是数据库中的设置，管理员通过 /api/admin/retrieval 修改（无需重启）。
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
# 重叠部分：防止把同一句话切到两个片段中导致检索时缺上下文。
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))
# 切分策略 seed：recursive / markdown / character / token。
SPLITTER_DEFAULT_STRATEGY = os.getenv("SPLITTER_DEFAULT_STRATEGY", "recursive")
# 发送给 LLM 的参考资料最大字符数，防止超出上下文窗口
MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", "8000"))
# 当总切片数 <= 此值时，自动检索全部文档（小知识库返回全集更合理）。
SMALL_KB_THRESHOLD = 50

# RAG 相关度阈值（LangChain 归一化分数 0~1，越大越相似；低于此值的片段不计入上下文）
# 可通过环境变量 RAG_RELEVANCE_THRESHOLD 调整；调高则更多问题走纯对话，调低则更依赖文档
RAG_RELEVANCE_THRESHOLD = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.5"))

# ==========================================
# 3. OCR 行为配置（不再加载本地模型）
# ==========================================

# PDF 每页文本少于此字符数时判定为扫描件，回退到 OCR
OCR_MIN_TEXT_THRESHOLD = 50
# OCR Vision API 请求里的图片渲染 DPI。150 是 OCR 通用甜点：清晰度足够
# 又不会让单张图过大拖慢 vision 接口。
OCR_PDF_RENDER_DPI = int(os.getenv("OCR_PDF_RENDER_DPI", "150"))
# 支持的图片扩展名（小写带点），用于 utils.load_document 分派 OCR 路径。
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp'}

# ==========================================
# 4. JWT 认证配置
# ==========================================

# ⚠️ 生产环境必须通过环境变量覆盖此默认值，否则任何人都能伪造 token。
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "smartpolicy-dev-secret-key-change-in-production")
JWT_EXPIRY_MINUTES = int(os.getenv("JWT_EXPIRY_MINUTES", "30"))

# ==========================================
# 5. 多轮对话配置
# ==========================================

# 拼装 LLM 上下文时回溯的最近消息条数。条数过多易超 token 上限，过少会
# 丢失追问语境；10 条覆盖大多数对话深度。
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "10"))

# ==========================================
# 6. LLM 调用可靠性配置（适用于所有远程 Provider）
# ==========================================

# LLM API 调用超时时间（秒），仅在 Provider 未单独指定时作为兜底
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

# LLM API 调用最大重试次数（tenacity 指数退避，参见 rag_engine）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# ==========================================
# 6.5 LLM 生成参数默认值（seed 到 RetrievalSettings）
# ==========================================
# 这些常量仅作为首次启动写入 RetrievalSettings 表的 seed；启动后实际生效的是
# 数据库中的设置，管理员通过 /api/admin/retrieval 修改（无需重启）。
# 请求级 temperature 仍可覆盖全局默认。


def _optional_float(name: str):
    """读取可选浮点环境变量；空字符串 / 未设置 → None（表示不显式传给模型）。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _optional_int(name: str):
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# 默认采样温度，兼顾事实性与可读性。
GEN_DEFAULT_TEMPERATURE = float(os.getenv("GEN_TEMPERATURE", "0.7"))
# top_p / max_tokens 默认 None：不显式传，沿用模型 / Provider 既有行为。
GEN_DEFAULT_TOP_P = _optional_float("GEN_TOP_P")
GEN_DEFAULT_MAX_TOKENS = _optional_int("GEN_MAX_TOKENS")
# 存在惩罚 / 频率惩罚，默认 0（不惩罚）。
GEN_DEFAULT_PRESENCE_PENALTY = float(os.getenv("GEN_PRESENCE_PENALTY", "0.0"))
GEN_DEFAULT_FREQUENCY_PENALTY = float(os.getenv("GEN_FREQUENCY_PENALTY", "0.0"))

# ==========================================
# 7. 文件上传安全配置
# ==========================================

# 最大上传文件大小（字节），默认 100MB —— 比 README 中 50MB 更宽松，因为
# 单页扫描件经 OCR 渲染后可能较大。
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(100 * 1024 * 1024)))

# 允许上传的文件扩展名 —— 白名单，反向阻挡 .exe/.sh 等危险类型。
ALLOWED_UPLOAD_EXTENSIONS = {
    # 文档
    ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown",
    ".html", ".htm",
    # 表格 / 演示 / 数据
    ".xlsx", ".pptx", ".csv", ".json",
    # 图片（走 OCR）
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp",
}

# ==========================================
# 8. 混合检索（BM25 + 语义向量）默认值
# ==========================================
# 这里的常量仅作为首次启动时写入 RetrievalSettings 表的 seed —— 启动后
# 实际生效的是数据库中的设置，管理员通过 /api/admin/retrieval 修改。
# 目的是让"调参验证"既支持持久化默认，又能被请求级参数动态覆盖。

# 融合策略：weighted（α 加权）/ rrf（倒数排名融合）/ semantic（仅语义）
# / bm25（仅关键词）。默认 weighted+α=0.5 与原纯语义行为差异最小。
HYBRID_DEFAULT_MODE = os.getenv("HYBRID_DEFAULT_MODE", "weighted")
# weighted 模式下 semantic 通道的权重，bm25 通道权重 = 1 - alpha。
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.5"))
# RRF 公式 1/(k+rank) 中的常数 k —— 业界常用 60。
HYBRID_RRF_K = int(os.getenv("HYBRID_RRF_K", "60"))
# 召回阶段两路各取多少候选，再做融合排序后取 final_top_k。
HYBRID_BM25_TOP_K = int(os.getenv("HYBRID_BM25_TOP_K", "20"))
HYBRID_VECTOR_TOP_K = int(os.getenv("HYBRID_VECTOR_TOP_K", "20"))

# Rerank（LLM-as-reranker）默认值。运行期以 RetrievalSettings 表为准，
# 这里仅作首次启动 seed 用。
RERANK_DEFAULT_ENABLED = os.getenv("RERANK_DEFAULT_ENABLED", "true").lower() == "true"
RERANK_DEFAULT_TOP_N = int(os.getenv("RERANK_DEFAULT_TOP_N", "5"))
# Provider id；0 / 空 表示走默认 Provider（与主问答同一个）。
_rerank_provider_raw = os.getenv("RERANK_DEFAULT_PROVIDER_ID", "")
RERANK_DEFAULT_PROVIDER_ID = (
    int(_rerank_provider_raw)
    if _rerank_provider_raw.isdigit() and int(_rerank_provider_raw) > 0
    else None
)

# Contextual Retrieval（Anthropic 风格上下文感知分块）默认值。运行期以
# RetrievalSettings 表为准，这里仅作首次启动 seed 用。
CONTEXTUAL_CHUNKING_DEFAULT_ENABLED = os.getenv(
    "CTX_CHUNK_ENABLED", "false"
).lower() == "true"
# 整篇文档拼接后送入 prompt 的最大字符数；超出会从尾部截断。30000 字符
# 对应中文 ~15-20K tokens，留足生成空间且兼容大多数 8K-32K 上下文模型。
CONTEXTUAL_CHUNKING_MAX_DOC_CHARS = int(
    os.getenv("CTX_CHUNK_MAX_DOC_CHARS", "30000")
)
# 同时并发跑多少路 LLM 调用生成上下文。本地 LM Studio 通常单机单卡，
# 4 路并发已能榨干吞吐；远程 API 可酌情调高。
CONTEXTUAL_CHUNKING_PARALLELISM = int(
    os.getenv("CTX_CHUNK_PARALLELISM", "4")
)

# Query Rewriting（查询重写）默认值。运行期以 RetrievalSettings 表为准，
# 这里仅作首次启动 seed 用。两个开关相互独立：
#   - simple：把口语化查询改写为含关键术语的检索查询，BM25 / 向量都用它
#   - hyde：让 LLM 写假设性答案，向量通道改用此假答案做语义检索
# 都关：行为完全等同改造前；都开：BM25 用 simple，向量用 hyde。
QUERY_REWRITE_SIMPLE_DEFAULT_ENABLED = os.getenv(
    "QUERY_REWRITE_SIMPLE_ENABLED", "false"
).lower() == "true"
QUERY_REWRITE_HYDE_DEFAULT_ENABLED = os.getenv(
    "QUERY_REWRITE_HYDE_ENABLED", "false"
).lower() == "true"
# HyDE 假设性答案最大长度（字符）；过长会拖慢嵌入与检索。300 字够覆盖
# 一段完整结论，又不会让向量退化到「整段平均后失去主题焦点」。
QUERY_REWRITE_HYDE_MAX_CHARS = int(
    os.getenv("QUERY_REWRITE_HYDE_MAX_CHARS", "300")
)

# ==========================================
# 9. CORS 配置
# ==========================================

# 允许跨域来源；逗号分隔；默认 "*" 仅适合开发环境。生产应明确列出
# 前端域名，避免接口被任意站点调用。
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# ==========================================
# 10. 启动日志
# ==========================================

# import 时立即输出关键参数，便于在 uvicorn 启动日志中肉眼确认
# .env 是否生效。
logger.info("✅ 已切换为远程 API 模式：LLM / 嵌入 / OCR 全部走在线 OpenAI 兼容接口")
logger.info(f"📂 数据目录: {DATA_DIR}")
logger.info(
    f"⚙️ 检索参数: TOP_K={TOP_K}, CHUNK_SIZE={CHUNK_SIZE}, "
    f"RAG_RELEVANCE_THRESHOLD={RAG_RELEVANCE_THRESHOLD}"
)
