"""上下文感知分块（Contextual Retrieval, Anthropic 风格）。

定位：在切片入库前为每个 split 调一次 LLM 生成 50-100 字「该片段在整篇
文档中的位置 / 角色」上下文摘要，前置到 ``page_content`` 后再送入嵌入。
显示给用户的原文保留在 ``metadata.original_text``，避免上下文摘要污染
``/vectors`` 编辑页与检索结果展示。

接入点：
    - ``ingest.py``      批量入库
    - ``api.py``         单文件 reingest
    都在 ``text_splitter.split_documents(docs)`` 之后调用
    ``apply_contextual_chunking_if_enabled``，由 ``RetrievalSettings``
    决定是否真正走 LLM。

失败降级：单个切片 LLM 调用失败时，该切片回退为「不带上下文」，仅打
warning，不阻塞整批入库——保证开关偶发抽风时仍能成功入库。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from config import (
    CONTEXTUAL_CHUNKING_MAX_DOC_CHARS,
    CONTEXTUAL_CHUNKING_PARALLELISM,
)

logger = logging.getLogger(__name__)


# Anthropic 官方 prompt 的中文化版本。要求 LLM 仅输出上下文本身，不要
# 解释、不要重复原文，避免后续嵌入时引入噪声。
_CONTEXT_PROMPT = """<document>
{full_doc}
</document>

下面是要做检索定位的片段：
<chunk>
{chunk}
</chunk>

请用 50-100 字给出该片段在整篇文档中的定位上下文（如所属章节、上下文角色、关键主题），仅输出该上下文，不要解释也不要重复原文。"""


# ============================================================
# Provider 解析
# ============================================================


def _resolve_ctx_provider(provider_id: Optional[int]):
    """解析上下文生成 LLM Provider：
    1. 显式 provider_id（>0）优先按 ID 取活跃 LLM Provider；
    2. 未指定或失效 → 回退到默认 LLM Provider（与主问答同 Provider）。

    返回 ``LlmProvider`` ORM 对象；上层负责进一步构建 ChatOpenAI 客户端。
    """
    # 延迟导入：避免 contextual_chunking 在 rag_engine 之前被加载时的循环。
    from rag_engine import _get_default_provider, _get_provider_by_id

    if provider_id and provider_id > 0:
        provider = _get_provider_by_id(provider_id)
        if provider:
            return provider
        logger.warning(
            f"⚠️ 上下文生成 Provider id={provider_id} 不可用，回退默认 LLM"
        )
    return _get_default_provider()


# ============================================================
# 单切片上下文生成
# ============================================================


def _generate_chunk_context(llm, full_doc: str, chunk_text: str) -> str:
    """调一次 LLM 生成单个切片的定位上下文。

    截断策略：``full_doc`` 已在外层按 ``MAX_DOC_CHARS`` 截过；这里不再
    做二次截断。``chunk_text`` 一般 <= CHUNK_SIZE（500 字符），无需截断。

    返回去掉首尾空白的文本；空字符串视为生成失败由调用方处理。
    """
    prompt = ChatPromptTemplate.from_messages([("human", _CONTEXT_PROMPT)])
    chain = prompt | llm
    response = chain.invoke({"full_doc": full_doc, "chunk": chunk_text})
    raw = response.content if hasattr(response, "content") else str(response)
    return (raw or "").strip()


# ============================================================
# 主入口
# ============================================================


def _build_source_doc_map(docs: list[Document]) -> dict[str, str]:
    """按 ``metadata.source`` 把同源 Document 的 page_content 拼成完整原文。

    单文档可能被解析器拆成多页（PDF）或多块（HTML）；这里按 source 聚合
    后再截断到 ``MAX_DOC_CHARS``，作为 prompt 中的 ``<document>`` 内容。
    截断从尾部进行——绝大多数文档前部包含目录 / 章节标题等定位信息，
    丢弃尾部对上下文生成质量影响最小。
    """
    buckets: dict[str, list[str]] = {}
    for d in docs:
        src = (d.metadata or {}).get("source", "")
        if not src:
            continue
        buckets.setdefault(src, []).append(d.page_content or "")

    out: dict[str, str] = {}
    for src, parts in buckets.items():
        full = "\n\n".join(parts)
        if len(full) > CONTEXTUAL_CHUNKING_MAX_DOC_CHARS:
            full = full[:CONTEXTUAL_CHUNKING_MAX_DOC_CHARS]
        out[src] = full
    return out


def apply_contextual_chunking(
    docs: list[Document],
    split_docs: list[Document],
    *,
    provider_id: Optional[int] = None,
) -> list[Document]:
    """为每个 split 生成上下文摘要并前置到 ``page_content``。

    Args:
        docs: 切片前的原始 Document 列表（用来组装整篇 full_doc）。
        split_docs: text_splitter 切完的片段列表（in-place 修改后返回新列表）。
        provider_id: 指定上下文生成 Provider；None / 0 走默认 LLM。

    Returns:
        新的 Document 列表，长度与 ``split_docs`` 一致，顺序保持。
        每个返回的 Document：
            - ``page_content`` = ``"<chunk_context>\\n\\n<original_text>"``
            - ``metadata.chunk_context`` = LLM 生成的上下文（可能为空）
            - ``metadata.original_text`` = 切片原文（供 ``/vectors`` 显示）
            - ``metadata.contextual_chunked`` = True
        其余 metadata 字段（source、page 等）原样保留。

    失败降级：单切片失败时退回原 ``page_content``，metadata 仍写入
    ``contextual_chunked=False`` 与 ``chunk_context=""``，保证字段稳定。
    """
    if not split_docs:
        return split_docs

    # 延迟导入：避免循环依赖（rag_engine → reranker → ... → contextual_chunking）。
    from rag_engine import _build_llm_from_provider

    provider = _resolve_ctx_provider(provider_id)
    if not provider:
        logger.warning("⚠️ 未找到可用 LLM Provider，跳过上下文感知分块")
        return split_docs

    try:
        # temperature=0 让上下文生成尽量可复现；同一切片多次入库结果稳定。
        # apply_generation_params=False：上下文摘要不应受面向最终答案的 stop/penalty 影响。
        llm = _build_llm_from_provider(
            provider, streaming=False, temperature=0.0, apply_generation_params=False
        )
    except Exception as e:
        logger.warning(f"⚠️ 构造 LLM 客户端失败，跳过上下文感知分块: {e}")
        return split_docs

    source_to_full = _build_source_doc_map(docs)

    logger.info(
        f"🧠 启动上下文感知分块: {len(split_docs)} 个切片 / "
        f"{len(source_to_full)} 个文档 / 并发 {CONTEXTUAL_CHUNKING_PARALLELISM} 路"
    )

    # results[i] = (chunk_context, ok)；按 split_docs 顺序回填，保证稳定性。
    results: list[tuple[str, bool]] = [("", False)] * len(split_docs)

    def _task(idx: int) -> tuple[int, str, bool]:
        split = split_docs[idx]
        src = (split.metadata or {}).get("source", "")
        full_doc = source_to_full.get(src, "")
        if not full_doc:
            # source 缺失：通常是 ingest 流程未写 metadata。直接跳过。
            return idx, "", False
        try:
            ctx = _generate_chunk_context(llm, full_doc, split.page_content or "")
            return idx, ctx, bool(ctx)
        except Exception as e:
            logger.warning(
                f"⚠️ 切片 #{idx} ({src}) 上下文生成失败，回退无上下文: {e}"
            )
            return idx, "", False

    with ThreadPoolExecutor(max_workers=CONTEXTUAL_CHUNKING_PARALLELISM) as pool:
        futures = [pool.submit(_task, i) for i in range(len(split_docs))]
        done = 0
        for fut in as_completed(futures):
            idx, ctx, ok = fut.result()
            results[idx] = (ctx, ok)
            done += 1
            if done % 20 == 0 or done == len(split_docs):
                logger.info(f"   -> 上下文进度 {done}/{len(split_docs)}")

    enriched: list[Document] = []
    success_count = 0
    for split, (ctx, ok) in zip(split_docs, results):
        original = split.page_content or ""
        # 切片本身的所有 metadata（source、page、chunk_id 等）整体复制后再补字段。
        new_meta = dict(split.metadata or {})
        new_meta["original_text"] = original
        new_meta["chunk_context"] = ctx
        new_meta["contextual_chunked"] = ok

        if ok:
            new_content = f"{ctx}\n\n{original}"
            success_count += 1
        else:
            new_content = original

        enriched.append(Document(page_content=new_content, metadata=new_meta))

    logger.info(
        f"✅ 上下文感知分块完成: {success_count}/{len(split_docs)} 成功"
    )
    return enriched


def apply_contextual_chunking_if_enabled(
    docs: list[Document],
    split_docs: list[Document],
) -> list[Document]:
    """读取 ``RetrievalSettings``，按开关决定是否走上下文增强。

    设计成单参函数包装 + 内部读 DB 是为了给 ``ingest.py`` /
    ``api.py`` 提供「调一行」的接入点——上层无需感知开关位置，
    也无需在每次入库时重复读一次 DB 模板。

    关闭时直接原样返回（零开销）。
    """
    # 延迟导入：避免顶层 import 触发 SQLAlchemy 在 config 加载阶段连库。
    from models import RetrievalSettings, SessionLocal

    db = SessionLocal()
    try:
        row = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
        if not row or not getattr(row, "contextual_chunking_enabled", False):
            return split_docs
        provider_id = getattr(row, "contextual_chunking_provider_id", None)
    finally:
        db.close()

    return apply_contextual_chunking(
        docs, split_docs, provider_id=provider_id
    )
