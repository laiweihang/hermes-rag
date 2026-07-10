"""查询重写（Query Rewriting）。

定位：在 ``hybrid_search`` 之前，对用户原始查询做 LLM 增强，提升检索召
回率。两个相互独立的开关：

1. **简单重写 (simple)**：把口语化、模糊的查询重写为含关键术语、
   句式更接近文档语言的检索查询。
   *示例*：「加班费咋算」→「员工加班费计算方法与倍率」。
2. **HyDE (Hypothetical Document Embeddings)**：让 LLM 写一段
   假设性答案，用其语义向量做检索。即使是幻觉，HyDE 文本和真实答案
   的语义距离也通常比原问题短得多——对短问句、专有名词查询尤其管用。

通道分流：
    - 仅 simple：BM25 / 向量都用 simple
    - 仅 HyDE：向量用 HyDE，BM25 用原查询（HyDE 长文本 BM25 表现差）
    - 都开：BM25 用 simple，向量用 HyDE（最佳组合）
    - 都关：返回 None，调用方应当 short-circuit 走原查询

LLM Provider：与 ``contextual_chunking`` / rerank 同模式：
    ``RetrievalSettings.query_rewrite_provider_id`` 显式指定，
    None / 0 则回退到默认 LLM Provider。

失败降级：任何一路 LLM 调用失败 → 该路通道回退原查询，并 log warning，
不向上抛错——保证开关偶发抽风时主问答仍可继续。
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate

from config import QUERY_REWRITE_HYDE_MAX_CHARS

logger = logging.getLogger(__name__)


# ============================================================
# Prompt 模板
# ============================================================

# 简单重写：要求 LLM 仅输出重写后的查询本身，不解释、不加引号。
# 单行约束让前端展示更紧凑，且降低后续 BM25 分词被「中文标点」误伤
# 的概率。
_SIMPLE_REWRITE_PROMPT = """你是一个检索查询改写助手。把用户的口语化、模糊或简短查询改写为更适合在中文文档中做关键词与语义检索的查询。

要求：
- 保留所有专有名词、数字、人名、术语原样不动
- 补充隐含的关键术语（如把「咋算」补为「计算方法」「计算公式」）
- 不要添加用户未提及的限定条件，避免过度收窄
- 输出单行查询，不超过 50 字，不加引号、不加解释

【原始查询】
{query}

【改写后查询】"""


# HyDE：让 LLM 写一段简短的「假设性答案」。提示中允许"如不确定可合理推
# 测"——HyDE 的关键不在于答对，而在于让生成的文本在主题词与表达风格上
# 接近真实文档，从而在向量空间里更容易召回真实答案。
_HYDE_PROMPT = """请用 1 段约 100-200 字的中文写一段「假设性答案」回答下面的问题。这段文字会被用来做向量检索，**不要标注是假设、也不要表达不确定**——直接像一份正式文档那样陈述要点。

要求：
- 包含问题中的所有专有名词与关键术语
- 句式接近正式制度 / 政策 / 手册的写法
- 避免 Markdown、列表、引号、省略号
- 不超过 {max_chars} 字

【问题】
{query}

【假设性答案】"""


# ============================================================
# Provider 解析
# ============================================================


def _resolve_qr_provider(provider_id: Optional[int]):
    """与 contextual_chunking._resolve_ctx_provider 对称的解析逻辑。

    1. provider_id > 0 优先按 ID 取活跃 LLM Provider；
    2. 取不到 → 回退默认 LLM Provider。
    """
    # 延迟导入：避免在 rag_engine 加载完成前触发循环。
    from rag_engine import _get_default_provider, _get_provider_by_id

    if provider_id and provider_id > 0:
        provider = _get_provider_by_id(provider_id)
        if provider:
            return provider
        logger.warning(
            f"⚠️ 查询重写 Provider id={provider_id} 不可用，回退默认 LLM"
        )
    return _get_default_provider()


def _build_qr_llm(provider_id: Optional[int]):
    """构造一个低温度 ChatOpenAI 实例。

    返回 None 表示无可用 Provider 或客户端构造失败——调用方据此 fallback。
    """
    provider = _resolve_qr_provider(provider_id)
    if not provider:
        logger.warning("⚠️ 未找到可用 LLM Provider，跳过查询重写")
        return None
    try:
        from rag_engine import _build_llm_from_provider
        # 改写 / HyDE 都希望尽量稳定可复现，温度拉到 0；不套用面向最终答案的生成参数。
        return _build_llm_from_provider(
            provider, streaming=False, temperature=0.0, apply_generation_params=False
        )
    except Exception as e:
        logger.warning(f"⚠️ 构造查询重写 LLM 客户端失败: {e}")
        return None


# ============================================================
# 单功能函数
# ============================================================


def simple_rewrite_query(query: str, llm) -> Optional[str]:
    """让 LLM 把 query 重写为更结构化的检索查询。

    返回值规则：
        - 成功：剥去引号 / 多余空白后的字符串（非空）
        - 失败 / 输出空：返回 None，调用方应回退原查询
    """
    if not (query or "").strip():
        return None
    try:
        prompt = ChatPromptTemplate.from_messages([("human", _SIMPLE_REWRITE_PROMPT)])
        chain = prompt | llm
        resp = chain.invoke({"query": query})
        raw = resp.content if hasattr(resp, "content") else str(resp)
        out = (raw or "").strip().strip("「」\"'`""''")
        # 截到首行：模型偶尔会画蛇添足追加解释行。
        out = out.split("\n", 1)[0].strip()
        return out or None
    except Exception as e:
        logger.warning(f"⚠️ 简单查询重写失败: {e}")
        return None


def hyde_query(query: str, llm) -> Optional[str]:
    """让 LLM 写一段假设性答案作为 HyDE 检索文本。

    输出长度受 ``QUERY_REWRITE_HYDE_MAX_CHARS`` 控制；超出会硬截断。
    """
    if not (query or "").strip():
        return None
    try:
        prompt = ChatPromptTemplate.from_messages([("human", _HYDE_PROMPT)])
        chain = prompt | llm
        resp = chain.invoke({
            "query": query,
            "max_chars": QUERY_REWRITE_HYDE_MAX_CHARS,
        })
        raw = resp.content if hasattr(resp, "content") else str(resp)
        out = (raw or "").strip()
        if not out:
            return None
        if len(out) > QUERY_REWRITE_HYDE_MAX_CHARS:
            out = out[:QUERY_REWRITE_HYDE_MAX_CHARS]
        return out
    except Exception as e:
        logger.warning(f"⚠️ HyDE 生成失败: {e}")
        return None


# ============================================================
# 主入口：统一返回双查询路由
# ============================================================


def apply_query_rewrite_if_enabled(query: str) -> Optional[dict]:
    """读取 ``RetrievalSettings``，按开关执行查询重写。

    Returns:
        - 两个开关都关：``None``——上层 short-circuit 走原查询。
        - 其他情况：dict，含字段
            * ``original``      原始查询
            * ``simple``        简单重写结果（开关关 / 失败时 None）
            * ``hyde``          HyDE 结果（开关关 / 失败时 None）
            * ``bm25_query``    实际用于 BM25 通道的查询字符串
            * ``vector_query``  实际用于向量通道的查询字符串
            * ``simple_enabled`` / ``hyde_enabled`` 开关的真实状态

    通道分流约定（也是 docstring 顶部说明里描述的策略）：
        - 仅 simple：BM25 / 向量都用 simple；如 simple 失败则两路都退原。
        - 仅 HyDE：向量用 hyde，BM25 用原；如 hyde 失败则向量退原。
        - 都开：BM25 用 simple（失败退原），向量用 hyde（失败退原）。
    """
    # 延迟导入：避免在 SQLAlchemy 初始化完成前触发循环。
    from models import RetrievalSettings, SessionLocal

    db = SessionLocal()
    try:
        row = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
        if not row:
            return None
        simple_on = bool(getattr(row, "query_rewrite_simple_enabled", False))
        hyde_on = bool(getattr(row, "query_rewrite_hyde_enabled", False))
        provider_id = getattr(row, "query_rewrite_provider_id", None)
    finally:
        db.close()

    if not simple_on and not hyde_on:
        return None

    llm = _build_qr_llm(provider_id)
    if llm is None:
        # Provider 不可用时，按"开关开但生成失败"语义返回，前端依然能看到
        # original，知道开了重写但本次未生效。
        return {
            "original": query,
            "simple": None,
            "hyde": None,
            "bm25_query": query,
            "vector_query": query,
            "simple_enabled": simple_on,
            "hyde_enabled": hyde_on,
        }

    simple_text: Optional[str] = None
    hyde_text: Optional[str] = None

    if simple_on:
        simple_text = simple_rewrite_query(query, llm)
        if simple_text:
            logger.info(f"🔁 simple 重写: '{query}' → '{simple_text}'")
    if hyde_on:
        hyde_text = hyde_query(query, llm)
        if hyde_text:
            preview = hyde_text[:60].replace("\n", " ")
            logger.info(f"🔁 HyDE 假答案前 60 字: '{preview}...'")

    # 通道分流
    if simple_on and hyde_on:
        bm25_q = simple_text or query
        vec_q = hyde_text or query
    elif simple_on:
        bm25_q = simple_text or query
        vec_q = simple_text or query
    elif hyde_on:
        bm25_q = query  # HyDE 长文不适合 BM25
        vec_q = hyde_text or query
    else:
        # 上面已经过滤；保留 else 分支防御性。
        return None

    return {
        "original": query,
        "simple": simple_text,
        "hyde": hyde_text,
        "bm25_query": bm25_q,
        "vector_query": vec_q,
        "simple_enabled": simple_on,
        "hyde_enabled": hyde_on,
    }
