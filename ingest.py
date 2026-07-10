"""文档入库管道（批量模式）。

定位：扫描 ``uploads/`` 目录下所有用户文件，对未入库的新文件执行
"解析 → 切片 → 向量化 → 写入 ChromaDB" 的完整链路。

使用场景：
- 命令行直接运行：``python ingest.py``，适合首次部署或批量补录。
- API 上传走的是 ``api.py`` 中的同步入库路径（单文件即时处理），不通过
  本脚本。

依赖：
- ``utils.load_document``：根据扩展名分派到 PDF/Word/OCR 解析器。
- ``database.get_vector_store``：返回 LangChain Chroma 包装器。
- ``database.list_ingested_filenames``：基于 metadata.source 字段去重。
- ``langchain_text_splitters.RecursiveCharacterTextSplitter``：按句号、
  换行等递归切分，尽量保留语义完整性。
"""

# ingest.py
import hashlib
import logging
import os

# 镜像加速：langchain/transformers 默认走 huggingface.co，国内访问不稳定，
# 这里在导入相关库之前先把 HF_ENDPOINT 指向 hf-mirror，避免后续模型/分词
# 资源下载超时。setdefault 保证不覆盖用户自定义环境变量。
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

from langchain_text_splitters import RecursiveCharacterTextSplitter
from database import (
    list_ingested_filenames,
    replace_documents_by_source_atomic,
)
from utils import load_document
from config import CHUNK_SIZE, CHUNK_OVERLAP, SPLITTER_DEFAULT_STRATEGY, UPLOAD_DIR

logger = logging.getLogger(__name__)


def _load_chunk_settings() -> dict:
    """从 ``RetrievalSettings`` 读取分块参数；失败时退回 config 常量。

    入库期每份文档调用一次，读一行 SQLite 成本可忽略；这样管理员在后台
    修改切片大小 / 策略后，新入库 / 重新入库的文档即时生效，无需重启。
    """
    fallback = {
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "splitter_strategy": SPLITTER_DEFAULT_STRATEGY,
        "chunk_separators": None,
    }
    try:
        import json as _json
        from models import RetrievalSettings, SessionLocal

        db = SessionLocal()
        try:
            row = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
            if not row:
                return fallback
            seps_raw = getattr(row, "chunk_separators", None)
            seps = None
            if seps_raw:
                try:
                    parsed = _json.loads(seps_raw)
                    seps = parsed if isinstance(parsed, list) and parsed else None
                except (ValueError, TypeError):
                    seps = None
            return {
                "chunk_size": int(getattr(row, "chunk_size", CHUNK_SIZE) or CHUNK_SIZE),
                "chunk_overlap": int(
                    getattr(row, "chunk_overlap", CHUNK_OVERLAP) or CHUNK_OVERLAP
                ),
                "splitter_strategy": getattr(row, "splitter_strategy", None)
                or SPLITTER_DEFAULT_STRATEGY,
                "chunk_separators": seps,
            }
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — 任何异常都不应阻塞入库
        logger.warning(f"⚠️ 读取分块设置失败，使用 config 默认: {e}")
        return fallback


def build_text_splitter(settings: dict | None = None):
    """按设置构造文本切分器。

    支持四种策略：
        - ``recursive``：递归字符切分（默认，对中文段落友好）。
        - ``markdown``：标题 / 列表感知，适合结构化 Markdown / 文档。
        - ``character``：按单一分隔符切，最简单可控。
        - ``token``：按 token 计数切，贴合模型上下文窗口；依赖 tiktoken，
          不可用时自动回退 recursive。

    ``chunk_separators`` 非空时覆盖策略内置分隔符（recursive/character 生效）。
    """
    s = settings or _load_chunk_settings()
    size = int(s.get("chunk_size") or CHUNK_SIZE)
    overlap = int(s.get("chunk_overlap") or CHUNK_OVERLAP)
    strategy = (s.get("splitter_strategy") or "recursive").lower()
    separators = s.get("chunk_separators") or None

    if strategy == "token":
        try:
            from langchain_text_splitters import TokenTextSplitter

            return TokenTextSplitter(chunk_size=size, chunk_overlap=overlap)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️ token 切分不可用（{e}），回退 recursive")
            strategy = "recursive"

    if strategy == "markdown":
        try:
            from langchain_text_splitters import MarkdownTextSplitter

            return MarkdownTextSplitter(chunk_size=size, chunk_overlap=overlap)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️ markdown 切分不可用（{e}），回退 recursive")
            strategy = "recursive"

    if strategy == "character":
        from langchain_text_splitters import CharacterTextSplitter

        sep = separators[0] if separators else "\n\n"
        return CharacterTextSplitter(
            separator=sep, chunk_size=size, chunk_overlap=overlap
        )

    # recursive（默认）
    kwargs = {"chunk_size": size, "chunk_overlap": overlap}
    if separators:
        kwargs["separators"] = separators
    return RecursiveCharacterTextSplitter(**kwargs)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_document_chunks(file_path: str, source_name: str):
    """解析并切片单个文件，不修改现有向量索引。"""
    docs = load_document(file_path)
    if not docs:
        raise ValueError("未从文件中提取到有效内容")
    for doc in docs:
        doc.metadata["source"] = source_name

    chunk_settings = _load_chunk_settings()
    text_splitter = build_text_splitter(chunk_settings)
    logger.info(
        f"✂️ 切分策略={chunk_settings['splitter_strategy']} "
        f"size={chunk_settings['chunk_size']} overlap={chunk_settings['chunk_overlap']}"
    )
    split_docs = text_splitter.split_documents(docs)
    from contextual_chunking import apply_contextual_chunking_if_enabled
    split_docs = apply_contextual_chunking_if_enabled(docs, split_docs)
    if not split_docs:
        raise ValueError("文档切片结果为空")
    return split_docs


def process_document_job(
    file_path: str,
    source_name: str,
    *,
    content_sha256: str = "",
    uploaded_by: str | None = None,
    promote_to: str | None = None,
) -> None:
    """后台处理单份文档，并把状态写入关系数据库。"""
    from models import KnowledgeDocument, SessionLocal
    from retrieval import BM25Index

    db = SessionLocal()
    row = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.filename == source_name
    ).first()
    if row is None:
        row = KnowledgeDocument(filename=source_name)
        db.add(row)
    row.status = "processing"
    row.error_message = None
    row.uploaded_by = uploaded_by or row.uploaded_by
    if content_sha256:
        row.content_sha256 = content_sha256
    db.commit()

    try:
        split_docs = prepare_document_chunks(file_path, source_name)
        chunk_count = replace_documents_by_source_atomic(source_name, split_docs)
        if promote_to and os.path.abspath(file_path) != os.path.abspath(promote_to):
            os.replace(file_path, promote_to)
        row.status = "ready"
        row.chunk_count = chunk_count
        row.error_message = None
        db.commit()
        BM25Index.instance().mark_stale()
        logger.info(f"✅ 文档 '{source_name}' 后台入库完成: {chunk_count} 个片段")
    except Exception as e:
        db.rollback()
        row = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.filename == source_name
        ).first()
        if row is not None:
            row.status = "failed"
            row.error_message = str(e)[:1000]
            db.commit()
        if promote_to and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        logger.exception(f"❌ 文档 '{source_name}' 后台入库失败")
    finally:
        db.close()


def ingest_files():
    """遍历 uploads 文件夹，跳过已入库文档，将新文档入库。

    流程：
        1. 列出 ``UPLOAD_DIR`` 下所有非隐藏文件。
        2. 通过 ChromaDB metadata 查询出"已入库文件名"集合，做差集得到
           真正需要处理的新文件。
        3. 初始化向量库连接 + 文本切片器。
        4. 逐文件解析 → 写入 source metadata → 切片 → 累计到 all_docs。
        5. 一次性 ``add_documents`` 批量写入，减少嵌入 API 调用开销。

    幂等性：同名文件不会重复入库；如需更新内容，应先在 API 层调用
    "重新入库"接口（``api.py`` 中的 ``/api/documents/{name}/reingest``），
    它会先删除旧片段再走单文件入库流程。

    本函数无返回值；所有异常被吞并写日志，避免单文件失败阻塞整批。
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # 过滤掉 .DS_Store 等隐藏文件，避免被当成业务文档处理。
    files = [f for f in os.listdir(UPLOAD_DIR) if not f.startswith('.')]
    if not files:
        logger.warning("⚠️ 上传文件夹为空，无需入库。")
        return

    # 已入库列表用于去重；返回的是 metadata.source 的去重集合。
    already_ingested = list_ingested_filenames()
    new_files = [f for f in files if f not in already_ingested]

    if not new_files:
        logger.info("✅ 所有文件均已入库，无需重复处理。")
        return

    logger.info(f"🔍 发现 {len(new_files)} 个新文件（跳过 {len(files) - len(new_files)} 个已入库），开始处理...")

    for file in new_files:
        file_path = os.path.join(UPLOAD_DIR, file)
        # 防御：os.listdir 罕见情况下返回名字不对应实体文件（符号链接断裂等）
        if not os.path.isfile(file_path):
            continue
        logger.info(f"📄 正在处理: {file}")
        try:
            process_document_job(
                file_path,
                file,
                content_sha256=file_sha256(file_path),
                uploaded_by="batch",
            )
        except Exception as e:
            # 单文件异常不打断整批，只记录后继续处理下一个文件。
            logger.error(f"   -> ❌ 处理文件 {file} 时出错: {e}")
if __name__ == "__main__":
    # 直接运行时启用最简日志，便于命令行查看进度。
    ingest_files()
