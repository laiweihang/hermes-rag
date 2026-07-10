"""LLM-as-reranker：对混合检索的候选片段做精排。

在 ``hybrid_search`` 已经返回 top_k 候选后，这里再调一次 LLM 让它对
每条候选打 0-10 分，按分数重排取 ``top_n``。设计目标：

- **零依赖**：不引入 BGE / Cohere 等额外模型，复用现有 LLM Provider；
- **失败降级**：LLM 报错 / 解析失败时直接退回原排序，不阻塞主问答；
- **可观察**：debug_dict 里写入 ``original_rank`` 与 ``rerank_score``，
  便于 ``/api/admin/retrieval/preview`` 与评测脚本对比前后差异。
"""
from __future__ import annotations

import logging
import re

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

# 截断单条候选片段，避免 prompt 爆炸：20 条 × 300 字 ≈ 6k 字符输入。
_CANDIDATE_PREVIEW_CHARS = 300

# 解析 LLM 输出的每行评分：兼容 `#1: 8`、`1: 8.5`、`#1 8` 等形式。
_SCORE_LINE_RE = re.compile(r"^\s*#?(\d+)\s*[:：\s]\s*(\d+(?:\.\d+)?)")

_RERANK_PROMPT = """你是一个精确的相关性评分助手。
给定用户问题和一组候选片段，对每个片段打 0-10 分（越高越相关）。
评分准则：
- 完全无关：0 分；
- 部分提及主题但回答不到点：4-6 分；
- 直接命中、可作为答案依据：8-10 分；
- 只看片段内容是否能回答该问题，不评价表达质量。

**严格按格式输出，每行一个**：
#<id>: <score>

不要解释、不要总结、不要 Markdown。

【问题】
{question}

【候选片段】
{candidates}

【打分】
"""


def _format_candidates(hits: list[tuple[Document, float, dict]]) -> str:
    """把候选片段拼成 LLM 易读的编号列表。"""
    parts = []
    for i, (doc, _score, _debug) in enumerate(hits, 1):
        text = (doc.page_content or "")[:_CANDIDATE_PREVIEW_CHARS].replace("\n", " ")
        parts.append(f"#{i}: {text}")
    return "\n".join(parts)


def _parse_scores(raw: str, n_candidates: int) -> dict[int, float]:
    """从 LLM 输出里抽 ``{candidate_index: score}``，编号 1-based。

    缺漏的编号会被调用方按 0 分对待。任何无法解析的行直接跳过。
    """
    out: dict[int, float] = {}
    for line in (raw or "").splitlines():
        m = _SCORE_LINE_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        if 1 <= idx <= n_candidates:
            try:
                out[idx] = float(m.group(2))
            except ValueError:
                continue
    return out


def rerank(
    question: str,
    hits: list[tuple[Document, float, dict]],
    *,
    top_n: int = 5,
    provider_id: int | None = None,
) -> list[tuple[Document, float, dict]]:
    """对 ``hybrid_search`` 的候选做 LLM 精排，返回 top_n。

    Args:
        question: 用户原始查询。
        hits: ``hybrid_search`` 的输出 ``(Document, score, debug)``。
        top_n: 精排后保留的最终条数。
        provider_id: 指定 reranker 用的 LLM Provider；None 走默认。

    Returns:
        精排后的 ``[(Document, fused_score_unchanged, debug_with_rerank), ...]``。
        ``debug`` 增加 ``original_rank`` 与 ``rerank_score`` 两个键。

    失败降级：LLM 报错或所有评分解析失败时，直接返回 ``hits[:top_n]``。
    """
    if not hits:
        return []
    # 候选数 ≤ top_n 时无需精排，直接返回（仍补 debug 字段保持结构一致）。
    if len(hits) <= top_n:
        return [
            (doc, score, {**(debug or {}), "original_rank": i + 1, "rerank_score": None})
            for i, (doc, score, debug) in enumerate(hits)
        ]

    # 延迟导入避免循环依赖：reranker 被 rag_engine 调用，不能反向 import 它。
    from rag_engine import _build_llm_from_provider, _resolve_provider

    candidates_text = _format_candidates(hits)
    prompt_messages = [("human", _RERANK_PROMPT)]
    prompt = ChatPromptTemplate.from_messages(prompt_messages)

    try:
        provider = _resolve_provider(provider_id)
        llm = _build_llm_from_provider(
            provider, streaming=False, temperature=0.0, apply_generation_params=False
        )
        chain = prompt | llm
        response = chain.invoke({"question": question, "candidates": candidates_text})
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning(f"⚠️ rerank LLM 调用失败，回退原排序: {e}")
        return [
            (doc, score, {**(debug or {}), "original_rank": i + 1, "rerank_score": None})
            for i, (doc, score, debug) in enumerate(hits[:top_n])
        ]

    scores = _parse_scores(raw, len(hits))
    if not scores:
        logger.warning("⚠️ rerank 无可解析评分，回退原排序")
        return [
            (doc, score, {**(debug or {}), "original_rank": i + 1, "rerank_score": None})
            for i, (doc, score, debug) in enumerate(hits[:top_n])
        ]

    enriched: list[tuple[int, float, tuple[Document, float, dict]]] = []
    for i, (doc, score, debug) in enumerate(hits, 1):
        rscore = scores.get(i, 0.0)
        new_debug = {**(debug or {}), "original_rank": i, "rerank_score": rscore}
        enriched.append((i, rscore, (doc, score, new_debug)))

    # 按 rerank 分数降序；同分按原排名升序（稳定）。
    enriched.sort(key=lambda x: (-x[1], x[0]))
    return [item for _, _, item in enriched[:top_n]]
