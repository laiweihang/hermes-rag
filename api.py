# api.py
"""FastAPI 后端 API — 赫尔墨斯 Hermes 的所有 HTTP 端点。

本模块结构（按章节组织）：

1. **基础设施**：CORS、请求日志、全局异常、404 处理。
2. **认证中间件**：白名单外的所有路径要求 Bearer JWT；
   ``/api/admin/*`` 额外要求 admin role。
3. **公开端点**：``/`` / ``/health`` / ``/auth/*``。
4. **核心问答**：``/query``（同步）+ ``/api/conversations/{id}/messages/stream``（SSE 流式）。
5. **文件上传与文档管理**：``/upload`` / ``/api/documents/*``。
6. **向量片段 CRUD**：``/api/chunks/*``。
7. **对话与消息**：``/api/conversations/*``。
8. **反馈与导出**：``/api/feedback`` / ``/api/export/*``。
9. **管理员后台**：``/api/admin/*`` —— 用户管理、Provider 管理、统计、向量库重建。
10. **技能场景**：``/api/skills/*`` + ``/api/admin/skills/*``。

每个路由的鉴权策略：
- 公开（无需 token）：见 ``_PUBLIC_PATHS`` / ``_PUBLIC_PREFIXES``。
- 普通用户（``Depends(get_current_user)``）：所有 ``/api/*`` 默认要求登录。
- 管理员（``Depends(get_admin_user)`` 或 ``/api/admin/*`` 路径前缀）：
  双重防护，中间件按路径前缀拦截 + 依赖注入再校验。
"""

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    decode_access_token,
    get_admin_user,
    get_current_user,
    hash_password,
    verify_password,
)
from config import (
    UPLOAD_DIR, MAX_HISTORY_MESSAGES, CORS_ORIGINS,
    MAX_UPLOAD_SIZE_BYTES, ALLOWED_UPLOAD_EXTENSIONS,
)
from database import (
    clear_vector_store,
    delete_chunk_by_id,
    delete_documents_by_source,
    document_count,
    get_chunk_by_id,
    list_chunks,
    list_document_sources,
    search_chunks,
    update_chunk_content,
)
from ingest import file_sha256, ingest_files, process_document_job
from models import (
    Conversation,
    Feedback,
    LlmProvider,
    KnowledgeDocument,
    Message,
    RetrievalSettings,
    SessionLocal,
    Skill,
    User,
    init_db,
)
from database import invalidate_embedding_cache
from ocr_engine import invalidate_ocr_client_cache
from rag_engine import (
    generate_answer,
    generate_answer_stream,
    invalidate_provider_cache,
    invalidate_retrieval_settings_cache,
)
from retrieval import BM25Index, hybrid_search
from schemas import (
    AdminFeedbackListResponse,
    AdminStatsResponse,
    AdminUserListResponse,
    AuthRequest,
    AuthResponse,
    ChunkDeleteResponse,
    ChunkListResponse,
    ChunkSearchResponse,
    ChunkUpdateRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentPreviewResponse,
    DocumentReingestResponse,
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackOut,
    MessageCreate,
    MessageOut,
    ProviderCreate,
    ProviderListResponse,
    ProviderOut,
    ProviderTestResponse,
    ProviderUpdate,
    QueryRequest,
    QueryResponse,
    RetrievalPreviewItem,
    RetrievalPreviewRequest,
    RetrievalPreviewResponse,
    RetrievalSettingsOut,
    RetrievalSettingsUpdate,
    SkillCreate,
    SkillDetectRequest,
    SkillDetectResponse,
    SkillImportRequest,
    SkillListResponse,
    SkillOut,
    SkillUpdate,
    UploadResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="SmartPolicyAgent API")

# 进程启动即建表 + 写 seed 数据。这里没用 startup event 是为了让单元
# 测试也能通过 import 触发初始化（无需手动调用）。重复调用是幂等的。
init_db()

# ==========================================
# CORS 中间件
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# 请求日志中间件
# ==========================================


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """请求日志中间件：记录 HTTP 方法、路径、状态码和耗时。"""
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        f"{request.method} {request.url.path} {response.status_code} {duration_ms:.1f}ms"
    )
    return response


# ==========================================
# 404 处理器：未知路由返回 JSON
# ==========================================


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """未知路由返回 JSON 格式的 404 错误。"""
    return JSONResponse(
        status_code=404,
        content={"detail": "Not Found"},
    )


# ==========================================
# 全局异常处理器：未捕获异常返回 JSON（不泄露堆栈）
# ==========================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器：将未捕获的异常转换为 JSON 500 响应，不暴露堆栈信息。"""
    logger.error(f"❌ 未处理异常: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


# ==========================================
# 文件名安全处理函数
# ==========================================


def _secure_filename(filename: str) -> str:
    """清理文件名：移除路径遍历序列和不安全字符，仅保留安全的文件名。

    安全意图：用户上传的 ``UploadFile.filename`` 完全由客户端控制，
    可能包含 ``../etc/passwd`` 等路径遍历串。本函数充当"白名单过滤器"
    把非法字符替换为下划线，强制把文件落到 ``UPLOAD_DIR`` 之内。

    - 去除路径分隔符（/ 和 \\）
    - 去除路径遍历序列（../ 和 ..\\）
    - 仅保留字母、数字、下划线、连字符、点号和中文字符
    - 空文件名返回 'unnamed'
    """
    if not filename:
        return "unnamed"

    # 去除路径遍历序列
    filename = filename.replace("../", "").replace("..\\", "")

    # 只取最后一个路径组件（去除目录部分）
    filename = filename.replace("\\", "/")
    filename = filename.split("/")[-1]

    # 仅保留安全字符：字母、数字、下划线、连字符、点号、中文字符
    filename = re.sub(r"[^\w\-. \u4e00-\u9fff]", "_", filename)

    # 去除前导点号（防止隐藏文件）
    filename = filename.lstrip(".")

    # 去除首尾空白
    filename = filename.strip()

    if not filename:
        return "unnamed"

    return filename

# ==========================================
# 公开路径白名单（不需要认证）
# ==========================================

_PUBLIC_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}
_PUBLIC_PREFIXES = ("/auth/",)


# ==========================================
# 认证中间件
# ==========================================


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """认证中间件：保护除公开路径外的所有端点。Admin 路径需要 admin 角色。

    设计要点：
        - 用中间件而非路由依赖统一拦截，避免每个路由都写 ``Depends(...)``。
        - 路由层仍可声明 ``Depends(get_current_user)`` 来拿到 username
          字符串（中间件只验 token，不传值）。
        - 中间件层异常需手工组装 CORS 响应头，否则浏览器看到 401 时
          会被 CORS 策略二次拦截，前端拿不到具体错误。
    """
    path = request.url.path

    if request.method == "OPTIONS":
        # 预检请求直接放行 —— CORS 头由 CORSMiddleware 自动加上。
        return await call_next(request)

    # 公开路径直接放行
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    origin = request.headers.get("origin", "")
    cors_headers: dict[str, str] = {}
    if origin:
        cors_headers = {
            "access-control-allow-origin": origin,
            "access-control-allow-credentials": "true",
        }

    # 检查 Authorization 头
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "未提供认证令牌"},
            headers=cors_headers,
        )

    token = auth_header.split("Bearer ", 1)[1]
    try:
        payload = decode_access_token(token)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=cors_headers,
        )

    # Admin 路径需要 admin 角色
    if path.startswith("/api/admin"):
        role = payload.get("role", "user")
        if role != "admin":
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "权限不足，需要管理员权限"},
                headers=cors_headers,
            )

    return await call_next(request)


# ==========================================
# 数据库会话依赖
# ==========================================


def get_db():
    """获取数据库会话（FastAPI Depends 风格）。

    yield 模式确保异常情况下 session 也能被关闭，是 FastAPI 处理资源
    生命周期的推荐写法。每个请求独立 session，避免跨请求污染。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# 公开端点
# ==========================================


@app.get("/")
def read_root():
    """根路径。"""
    return {"message": "智策办后端服务已启动！"}


@app.get("/health")
def health_check():
    """健康检查。"""
    return {"status": "ok"}


@app.get("/api/knowledge/status")
def knowledge_status(current_user: str = Depends(get_current_user)):
    """返回知识库规模，不向普通用户暴露文件名或切片内容。"""
    sources = list_document_sources()
    return {"documents": len(sources), "chunks": document_count()}


# ==========================================
# 认证端点
# ==========================================


@app.post("/auth/register", response_model=AuthResponse)
def register(request: AuthRequest, db: Session = Depends(get_db)):
    """用户注册 — 返回 JWT 令牌。

    新用户默认 role="user"。注册即登录：直接下发 token 省去前端再调
    /auth/login 的来回。用户名冲突返回 409。
    """
    # 检查用户名是否已存在
    existing = db.query(User).filter(User.username == request.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        )

    # 创建用户
    user = User(
        username=request.username,
        hashed_password=hash_password(request.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(f"📝 新用户注册: {request.username}")

    token = create_access_token(subject=request.username, role=user.role)
    return AuthResponse(access_token=token, token_type="bearer")


@app.post("/auth/login", response_model=AuthResponse)
def login(request: AuthRequest, db: Session = Depends(get_db)):
    """用户登录 — 返回 JWT 令牌。

    刻意把"用户不存在"和"密码错误"合并为同一错误信息，防止账号枚举攻击。
    """
    user = db.query(User).filter(User.username == request.username).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    logger.info(f"🔓 用户登录: {request.username}")

    token = create_access_token(subject=request.username, role=user.role)
    return AuthResponse(access_token=token, token_type="bearer")


# ==========================================
# 受保护端点
# ==========================================


@app.post("/query", response_model=QueryResponse)
async def query_policy(
    request: QueryRequest,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """策略查询（需要认证）。支持可选的 temperature、top_k、chunk_size、skill_id 参数。"""
    # 如果指定了 skill_id，获取对应技能的 system_prompt
    skill_system_prompt = None
    if request.skill_id is not None:
        skill = db.query(Skill).filter(Skill.id == request.skill_id).first()
        if skill:
            skill_system_prompt = skill.system_prompt

    result = generate_answer(
        request.question,
        temperature=request.temperature,
        top_k=request.top_k,
        chunk_size=request.chunk_size,
        skill_system_prompt=skill_system_prompt,
        retrieval_params=request.retrieval,
    )
    return QueryResponse(**result)


@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """保存待处理文件并创建后台入库任务；旧索引在新任务成功前继续可用。"""
    try:
        # 1. 文件名安全处理
        original_filename = file.filename or ""
        safe_filename = _secure_filename(original_filename)

        # 2. 扩展名验证
        ext = os.path.splitext(safe_filename)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不允许的文件类型: '{ext}'。允许的类型: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
            )

        # 3. 文件大小检查（读取内容并检查大小）
        contents = await file.read()
        if len(contents) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件大小超过限制: {len(contents)} 字节 > {MAX_UPLOAD_SIZE_BYTES} 字节 (50MB)",
            )

        content_hash = hashlib.sha256(contents).hexdigest()
        existing = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.filename == safe_filename
        ).first()
        if (
            existing is not None
            and existing.content_sha256 == content_hash
            and existing.status == "ready"
        ):
            return UploadResponse(
                filename=safe_filename,
                status="ready",
                message="相同内容已入库，无需重复处理",
            )

        # 写入独立 pending 文件；后台成功后再替换正式原文件。
        pending_dir = os.path.join(UPLOAD_DIR, ".pending")
        os.makedirs(pending_dir, exist_ok=True)
        pending_path = os.path.join(pending_dir, f"{content_hash}_{safe_filename}")
        with open(pending_path, "wb") as buffer:
            buffer.write(contents)

        if existing is None:
            existing = KnowledgeDocument(filename=safe_filename)
            db.add(existing)
        existing.content_sha256 = content_hash
        existing.status = "queued"
        existing.error_message = None
        existing.uploaded_by = current_user
        db.commit()

        final_path = os.path.join(UPLOAD_DIR, safe_filename)
        background_tasks.add_task(
            process_document_job,
            pending_path,
            safe_filename,
            content_sha256=content_hash,
            uploaded_by=current_user,
            promote_to=final_path,
        )

        return UploadResponse(
            filename=safe_filename,
            status="queued",
            message="文件已上传，正在后台解析和建立索引",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)[:200]}")


# ==========================================
# 文档管理端点
# ==========================================


@app.get("/api/documents", response_model=DocumentListResponse)
def list_documents(
    current_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """列出文件级入库状态，并兼容尚未写入状态表的旧向量数据。"""
    sources = list_document_sources()
    records = db.query(KnowledgeDocument).order_by(
        KnowledgeDocument.updated_at.desc()
    ).all()
    documents = []
    known = set()
    for row in records:
        known.add(row.filename)
        documents.append({
            "name": row.filename,
            "chunk_count": row.chunk_count or sources.get(row.filename, 0),
            "status": row.status,
            "error_message": row.error_message,
            "content_sha256": row.content_sha256,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    for name, count in sources.items():
        if name not in known:
            documents.append({"name": name, "chunk_count": count, "status": "ready"})
    logger.info(f"📄 用户 '{current_user}' 查询文档列表: {len(documents)} 个文档")
    return {"documents": documents}


@app.delete("/api/documents/{name}", response_model=DocumentDeleteResponse)
def delete_document(
    name: str,
    current_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """删除指定文档及其所有向量片段。文档不存在时返回 404。"""
    sources = list_document_sources()
    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.filename == name
    ).first()
    if name not in sources and record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文档 '{name}' 不存在",
        )
    deleted_count = delete_documents_by_source(name)
    file_path = os.path.join(UPLOAD_DIR, name)
    if os.path.isfile(file_path):
        os.remove(file_path)
    if record is not None:
        db.delete(record)
        db.commit()
    BM25Index.instance().mark_stale()
    logger.info(f"🗑️ 用户 '{current_user}' 删除文档 '{name}': 删除 {deleted_count} 个片段")
    return {"name": name, "deleted_chunks": deleted_count}


@app.post("/api/documents/{name}/reingest", response_model=DocumentReingestResponse)
def reingest_document(
    name: str,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """把重新入库放入后台；失败时保留旧向量切片。"""
    file_path = os.path.join(UPLOAD_DIR, name)
    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文件 '{name}' 不存在于上传目录",
        )

    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.filename == name
    ).first()
    if record is None:
        record = KnowledgeDocument(filename=name)
        db.add(record)
    record.status = "queued"
    record.error_message = None
    record.content_sha256 = file_sha256(file_path)
    record.uploaded_by = current_user
    current_count = record.chunk_count or list_document_sources().get(name, 0)
    db.commit()
    background_tasks.add_task(
        process_document_job,
        file_path,
        name,
        content_sha256=record.content_sha256,
        uploaded_by=current_user,
    )
    return {"name": name, "chunk_count": current_count, "status": "queued"}


@app.get("/api/documents/{filename}/preview", response_model=DocumentPreviewResponse)
def preview_document(
    filename: str,
    max_chars: int = Query(default=500, ge=1, description="最大预览字符数"),
    max_pages: int = Query(default=3, ge=1, description="PDF 最大预览页数"),
    current_user: str = Depends(get_admin_user),
):
    """预览文档内容：返回文件的前 N 个字符或前 N 页。支持 PDF 和 TXT 文件。"""
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文件 '{filename}' 不存在于上传目录",
        )

    ext = os.path.splitext(filename)[1].lower()

    try:
        if ext == ".pdf":
            from langchain_community.document_loaders import PyPDFLoader
            docs = PyPDFLoader(file_path).load()
            total_pages = len(docs)
            preview_pages = min(max_pages, total_pages)
            full_text = "\n".join(doc.page_content for doc in docs[:preview_pages])
            total_text = "\n".join(doc.page_content for doc in docs)
            content = full_text[:max_chars]
            return DocumentPreviewResponse(
                filename=filename,
                content=content,
                total_pages=total_pages,
                previewed_pages=preview_pages,
                total_chars=len(total_text),
                previewed_chars=len(content),
            )
        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                full_text = f.read()
            content = full_text[:max_chars]
            return DocumentPreviewResponse(
                filename=filename,
                content=content,
                total_pages=None,
                previewed_pages=None,
                total_chars=len(full_text),
                previewed_chars=len(content),
            )
        else:
            # 尝试通用文本读取
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                content = full_text[:max_chars]
                return DocumentPreviewResponse(
                    filename=filename,
                    content=content,
                    total_pages=None,
                    previewed_pages=None,
                    total_chars=len(full_text),
                    previewed_chars=len(content),
                )
            except (UnicodeDecodeError, Exception):
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"不支持预览的文件格式: {ext}",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 预览文档 '{filename}' 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"预览失败: {str(e)}",
        )


# ==========================================
# 向量片段 CRUD 端点
# ==========================================


@app.get("/api/chunks", response_model=ChunkListResponse)
def list_chunks_endpoint(
    source: str = None,
    offset: int = 0,
    limit: int = 20,
    current_user: str = Depends(get_admin_user),
):
    """分页列出向量片段。可按 source 过滤。"""
    result = list_chunks(source_name=source, offset=offset, limit=limit)
    logger.info(
        f"📋 用户 '{current_user}' 查询片段列表: source={source}, offset={offset}, limit={limit}, total={result['total']}"
    )
    return result


@app.get("/api/chunks/search", response_model=ChunkSearchResponse)
def search_chunks_endpoint(
    q: str = None,
    top_k: int = 10,
    current_user: str = Depends(get_admin_user),
):
    """按相似度搜索向量片段。"""
    if not q or not q.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="查询参数 q 不能为空",
        )
    results = search_chunks(query_text=q.strip(), top_k=top_k)
    logger.info(
        f"🔍 用户 '{current_user}' 搜索片段: q='{q}', top_k={top_k}, 返回 {len(results)} 条"
    )
    return {"results": results}


@app.put("/api/chunks/{chunk_id}")
def update_chunk_endpoint(
    chunk_id: str,
    body: ChunkUpdateRequest,
    current_user: str = Depends(get_admin_user),
):
    """更新片段内容（重新嵌入）。"""
    existing = get_chunk_by_id(chunk_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"片段 '{chunk_id}' 不存在",
        )
    success = update_chunk_content(chunk_id, body.content)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新片段失败",
        )
    BM25Index.instance().mark_stale()
    logger.info(f"✏️ 用户 '{current_user}' 更新片段: id={chunk_id}")
    return {"detail": "片段已更新", "old_id": chunk_id}


@app.delete("/api/chunks/{chunk_id}", response_model=ChunkDeleteResponse)
def delete_chunk_endpoint(
    chunk_id: str,
    current_user: str = Depends(get_admin_user),
):
    """删除单个向量片段。"""
    success = delete_chunk_by_id(chunk_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"片段 '{chunk_id}' 不存在",
        )
    BM25Index.instance().mark_stale()
    logger.info(f"🗑️ 用户 '{current_user}' 删除片段: id={chunk_id}")
    return {"deleted": True}


# ==========================================
# 辅助函数：获取当前用户 ID
# ==========================================


def _get_user_id(db: Session, username: str) -> int:
    """根据用户名获取用户 ID，不存在则抛出 401。"""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    return user.id


# ==========================================
# 对话管理端点（用户隔离）
# ==========================================


@app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    body: ConversationCreate = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建新对话（需要认证）。"""
    user_id = _get_user_id(db, current_user)
    title = (body.title if body and body.title else "新对话")
    skill_id = (body.skill_id if body and body.skill_id else None)
    provider_id = (body.provider_id if body and body.provider_id else None)
    conv = Conversation(user_id=user_id, title=title, skill_id=skill_id, provider_id=provider_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    logger.info(f"💬 用户 '{current_user}' 创建对话: id={conv.id}, title='{conv.title}'")
    return {
        "id": conv.id,
        "title": conv.title,
        "skill_id": conv.skill_id,
        "provider_id": conv.provider_id,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
    }


@app.get("/api/conversations")
def list_conversations(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户的所有对话（按创建时间降序）。"""
    user_id = _get_user_id(db, current_user)
    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
        .all()
    )
    result = []
    for c in convs:
        msg_count = db.query(Message).filter(Message.conversation_id == c.id).count()
        result.append({
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
            "message_count": msg_count,
        })
    return {"conversations": result}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取对话详情及消息列表（仅限当前用户的对话）。"""
    user_id = _get_user_id(db, current_user)
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在",
        )
    if conv.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此对话",
        )
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return {
        "id": conv.id,
        "title": conv.title,
        "skill_id": conv.skill_id,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources,
                "rule_matched": m.rule_matched,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除对话及其所有消息（仅限当前用户的对话）。"""
    user_id = _get_user_id(db, current_user)
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在",
        )
    if conv.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此对话",
        )
    db.delete(conv)
    db.commit()
    logger.info(f"🗑️ 用户 '{current_user}' 删除对话: id={conversation_id}")
    return {"detail": "对话已删除"}


# ==========================================
# 消息端点
# ==========================================


@app.post("/api/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: int,
    body: MessageCreate,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发送消息：存储用户消息，调用 generate_answer，存储助手回复，首条消息自动设置标题。

    与 ``/messages/stream`` 的区别：本端点同步等待完整回答后一次返回，
    适合非交互式集成（如脚本批量提问）。前端聊天界面统一走流式版本。
    """
    import json as _json

    user_id = _get_user_id(db, current_user)
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在",
        )
    if conv.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此对话",
        )

    # 存储用户消息
    user_msg = Message(
        conversation_id=conv.id,
        role="user",
        content=body.question,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # 首条消息自动设置标题（在调用 generate_answer 之前检查）
    existing_msg_count = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .count()
    )
    is_first_message = existing_msg_count <= 1  # 只有刚添加的用户消息

    # 加载对话历史（不含刚添加的用户消息，取最近 N 条）
    previous_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .filter(Message.id != user_msg.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    conversation_history = [
        {"role": m.role, "content": m.content}
        for m in previous_messages[-MAX_HISTORY_MESSAGES:]
    ] if previous_messages else None

    # 解析有效 skill：请求体 skill_id > conv.skill_id；查不到则忽略。
    effective_skill_id = body.skill_id if body.skill_id else conv.skill_id
    effective_provider_id = body.provider_id if body.provider_id else conv.provider_id
    skill_system_prompt = None
    if effective_skill_id is not None:
        _skill = db.query(Skill).filter(Skill.id == effective_skill_id).first()
        if _skill:
            skill_system_prompt = _skill.system_prompt

    # 调用 generate_answer 获取回复（传入对话历史）
    try:
        result = generate_answer(
            body.question,
            conversation_history=conversation_history,
            provider_id=effective_provider_id,
            use_rag=body.use_rag,
            retrieval_params=body.retrieval,
            skill_system_prompt=skill_system_prompt,
        )
    except Exception as e:
        logger.error(f"❌ 生成回答失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="生成回答失败",
        )

    # 存储助手回复
    assistant_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=result.get("answer", ""),
        rule_matched=result.get("rule_matched"),
    )
    sources = result.get("sources")
    if sources:
        assistant_msg.sources = _json.dumps(sources, ensure_ascii=False)
    qr_info = result.get("query_rewrite")
    if qr_info:
        # 同步路径下也持久化查询重写信息：与流式路径行为一致，保证刷新
        # 后老消息仍能在 UI 上展示「LLM 已把查询优化为 XXX」。
        assistant_msg.query_rewrite = _json.dumps(qr_info, ensure_ascii=False)
    db.add(assistant_msg)

    # 首条消息自动设置标题（截取问题前30个字符）
    if is_first_message:
        auto_title = body.question[:30].strip()
        if len(body.question) > 30:
            auto_title += "..."
        conv.title = auto_title
        logger.info(f"📝 自动设置对话标题: '{auto_title}'")

    db.commit()
    db.refresh(assistant_msg)

    logger.info(
        f"💬 用户 '{current_user}' 在对话 {conversation_id} 中发送消息"
    )

    return {
        "user_message": {
            "id": user_msg.id,
            "role": user_msg.role,
            "content": user_msg.content,
            "created_at": user_msg.created_at.isoformat(),
        },
        "assistant_message": {
            "id": assistant_msg.id,
            "role": assistant_msg.role,
            "content": assistant_msg.content,
            "sources": result.get("sources", []),
            "rule_matched": result.get("rule_matched"),
            "citation_validation": result.get("citation_validation"),
            "usage": result.get("usage"),
            "created_at": assistant_msg.created_at.isoformat(),
        },
    }


# ==========================================
# 流式消息端点 (SSE)
# ==========================================


@app.post("/api/conversations/{conversation_id}/messages/stream")
def send_message_stream(
    conversation_id: int,
    body: MessageCreate,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """流式发送消息：SSE 格式逐块返回助手回复，流结束后存储完整回答到数据库。

    SSE 协议要点：
        - ``data: <token>\\n\\n`` 普通 token 帧。
        - ``data: [SOURCES]<json>\\n\\n`` 流末尾发引用来源 JSON，让前端
          展示参考资料卡片。
        - ``data: [DONE]\\n\\n`` 结束标记，前端收到后关闭连接。
        - HTTP 头 ``X-Accel-Buffering: no`` 防止 Nginx/Cloudflare 缓冲。

    错误兜底：准备阶段（如 Provider 未配 API Key）失败时，把错误提示
    包成单 chunk 流返回 + 落库为 assistant 消息，避免对话只剩孤立的用户消息。

    数据库 session 隔离：原 ``db`` 在 StreamingResponse 生命周期内可能
    已关闭，generator 内部用 ``SessionLocal()`` 单独创建写库 session。
    """
    import json as _json

    user_id = _get_user_id(db, current_user)
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在",
        )
    if conv.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此对话",
        )

    # 存储用户消息
    user_msg = Message(
        conversation_id=conv.id,
        role="user",
        content=body.question,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # 首条消息自动设置标题
    existing_msg_count = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .count()
    )
    is_first_message = existing_msg_count <= 1

    if is_first_message:
        auto_title = body.question[:30].strip()
        if len(body.question) > 30:
            auto_title += "..."
        conv.title = auto_title
        db.commit()
        logger.info(f"📝 自动设置对话标题: '{auto_title}'")

    # 加载对话历史
    previous_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .filter(Message.id != user_msg.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    conversation_history = [
        {"role": m.role, "content": m.content}
        for m in previous_messages[-MAX_HISTORY_MESSAGES:]
    ] if previous_messages else None

    # 解析 provider_id：优先请求体中的，其次对话绑定的
    effective_provider_id = body.provider_id if body.provider_id else conv.provider_id

    # 解析有效 skill：请求体 skill_id > conv.skill_id；查不到则忽略。
    effective_skill_id = body.skill_id if body.skill_id else conv.skill_id
    skill_system_prompt = None
    if effective_skill_id is not None:
        _skill = db.query(Skill).filter(Skill.id == effective_skill_id).first()
        if _skill:
            skill_system_prompt = _skill.system_prompt

    # 获取流式生成器和元数据。即使准备阶段失败（如 Provider 未配置 API Key），
    # 也不直接 500——退化为一个直接吐错误信息的伪流式生成器，
    # 这样错误也会被作为 assistant 消息持久化，避免对话只剩用户消息。
    try:
        chunk_gen, metadata = generate_answer_stream(
            body.question,
            conversation_history=conversation_history,
            provider_id=effective_provider_id,
            use_rag=body.use_rag,
            retrieval_params=body.retrieval,
            skill_system_prompt=skill_system_prompt,
        )
    except Exception as e:
        logger.error(f"❌ 创建流式生成器失败: {e}")
        err_text = f"⚠️ 模型调用失败：{e}\n\n请到「管理后台 → 模型管理」检查 LLM Provider 配置。"

        def _err_gen(msg=err_text):
            yield msg

        chunk_gen = _err_gen()
        metadata = {"sources": [], "rule_matched": None, "full_answer": err_text}

    # 需要在流结束后保存到数据库，使用闭包捕获所需变量
    # 注意：db session 在 StreamingResponse 的生命周期内需要保持打开
    # 因此我们在 event_generator 内部创建新的 session 来保存

    def event_generator():
        """SSE 事件生成器：逐块发送 data 事件，最后发送 [DONE]。

        事件顺序：
            1. ``[REWRITE]<json>``（仅当查询重写已开启且产生结果）—— 在
               token 流之前发送，让前端"思考中"动画结束前就能展示重写后
               的查询，符合"让用户立即看到 LLM 增强后的提示词"的需求。
            2. 逐 token ``data: <chunk>``
            3. ``[SOURCES]<json>``（finally 阶段，确保即使失败也会发送）
            4. ``[DONE]``
        """
        # 1) 先把查询重写信息推下去（如果有的话）。
        rewrite_info = metadata.get("query_rewrite")
        if rewrite_info:
            yield f"data: [REWRITE]{_json.dumps(rewrite_info, ensure_ascii=False)}\n\n"
        try:
            for chunk in chunk_gen:
                yield f"data: {chunk}\n\n"
        except Exception as e:
            logger.error(f"❌ 流式生成出错: {e}")
            err_text = f"⚠️ 模型调用失败：{e}"
            yield f"data: {err_text}\n\n"
            # 让错误内容也作为 assistant 消息保存，避免持久化"半截"
            current = metadata.get("full_answer", "") or ""
            metadata["full_answer"] = (current + ("\n\n" if current else "") + err_text)
        finally:
            # 流结束后，存储完整回答到数据库
            try:
                save_db = SessionLocal()
                try:
                    full_answer = metadata.get("full_answer", "")
                    sources = metadata.get("sources", [])
                    rule_matched = metadata.get("rule_matched")
                    qr_info = metadata.get("query_rewrite")

                    assistant_msg = Message(
                        conversation_id=conv.id,
                        role="assistant",
                        content=full_answer,
                        rule_matched=rule_matched,
                    )
                    if sources:
                        assistant_msg.sources = _json.dumps(
                            sources, ensure_ascii=False
                        )
                    if qr_info:
                        # 持久化查询重写信息，刷新页面后旧消息也能继续展示。
                        assistant_msg.query_rewrite = _json.dumps(
                            qr_info, ensure_ascii=False
                        )
                    save_db.add(assistant_msg)
                    save_db.commit()
                    logger.info(
                        f"💾 流式回答已保存到对话 {conversation_id}"
                    )
                finally:
                    save_db.close()
            except Exception as save_err:
                logger.error(f"❌ 保存流式回答失败: {save_err}")

            # 发送引用来源（让前端立即显示 SourcesPanel）
            sources_list = metadata.get("sources", [])
            if sources_list:
                yield f"data: [SOURCES]{_json.dumps(sources_list, ensure_ascii=False)}\n\n"

            # 发送结束标记
            yield "data: [DONE]\n\n"

    logger.info(
        f"🌊 用户 '{current_user}' 在对话 {conversation_id} 中开始流式消息"
    )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==========================================
# 反馈端点
# ==========================================

_VALID_RATINGS = {"up", "down"}


@app.post("/api/feedback", status_code=status.HTTP_201_CREATED)
def submit_feedback(
    body: FeedbackCreate,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交消息反馈（点赞/点踩 + 可选评论）。rating 必须为 'up' 或 'down'。"""
    # 验证 rating 值
    if body.rating not in _VALID_RATINGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"无效的评分值 '{body.rating}'，必须为 'up' 或 'down'",
        )

    # 验证 message_id 存在
    message = db.query(Message).filter(Message.id == body.message_id).first()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"消息 ID {body.message_id} 不存在",
        )

    user_id = _get_user_id(db, current_user)

    feedback = Feedback(
        message_id=body.message_id,
        user_id=user_id,
        rating=body.rating,
        comment=body.comment,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    logger.info(
        f"👍 用户 '{current_user}' 提交反馈: message_id={body.message_id}, rating={body.rating}"
    )

    return {
        "id": feedback.id,
        "message_id": feedback.message_id,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "created_at": feedback.created_at.isoformat(),
    }


@app.get("/api/feedback/{message_id}", response_model=FeedbackListResponse)
def get_feedback(
    message_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取指定消息的所有反馈条目。"""
    feedbacks = (
        db.query(Feedback)
        .filter(Feedback.message_id == message_id)
        .order_by(Feedback.created_at.asc())
        .all()
    )

    logger.info(
        f"📋 用户 '{current_user}' 查询消息 {message_id} 的反馈: {len(feedbacks)} 条"
    )

    return {
        "feedbacks": [
            {
                "id": f.id,
                "message_id": f.message_id,
                "rating": f.rating,
                "comment": f.comment,
                "created_at": f.created_at.isoformat(),
            }
            for f in feedbacks
        ]
    }


# ==========================================
# 导出端点
# ==========================================

_SUPPORTED_EXPORT_FORMATS = {"json", "csv"}


@app.get("/api/export/conversations/{conversation_id}")
def export_conversation(
    conversation_id: int,
    format: str = Query("json", description="导出格式：json 或 csv"),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导出对话记录（JSON 或 CSV 格式）。"""
    # 验证格式
    if format not in _SUPPORTED_EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的导出格式 '{format}'，支持: json, csv",
        )

    # 验证对话存在且属于当前用户
    user_id = _get_user_id(db, current_user)
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在",
        )
    if conv.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此对话",
        )

    # 获取消息列表
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .all()
    )

    logger.info(
        f"📤 用户 '{current_user}' 导出对话 {conversation_id}: format={format}, {len(messages)} 条消息"
    )

    if format == "json":
        data = {
            "conversation_id": conv.id,
            "title": conv.title,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }
        return JSONResponse(content=data, media_type="application/json")

    # CSV 格式
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["role", "content", "timestamp"])
    for m in messages:
        writer.writerow([m.role, m.content, m.created_at.isoformat()])
    csv_content = output.getvalue()
    output.close()

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=conversation_{conversation_id}.csv",
        },
    )


# ==========================================
# Admin 管理端点
# ==========================================


@app.get("/api/admin/users", response_model=AdminUserListResponse)
def admin_list_users(
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：列出所有用户（包含 id, username, role, created_at）。"""
    users = db.query(User).order_by(User.created_at.asc()).all()
    logger.info(f"👤 管理员 '{admin_user}' 查询用户列表: {len(users)} 个用户")
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ]
    }


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：删除指定用户。不允许删除自己。"""
    # 获取目标用户
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    # 不允许删除自己
    admin = db.query(User).filter(User.username == admin_user).first()
    if admin and admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不允许删除自己的账户",
        )

    db.delete(target)
    db.commit()
    logger.info(f"🗑️ 管理员 '{admin_user}' 删除用户: id={user_id}, username='{target.username}'")
    return {"detail": f"用户 '{target.username}' 已删除"}


@app.get("/api/admin/users/{user_id}/conversations")
def admin_get_user_conversations(
    user_id: int,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：查看指定用户的所有对话。"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
        .all()
    )
    result = []
    for c in convs:
        msg_count = db.query(Message).filter(Message.conversation_id == c.id).count()
        result.append({
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
            "message_count": msg_count,
        })
    logger.info(
        f"👤 管理员 '{admin_user}' 查看用户 {user_id} 的对话: {len(result)} 个"
    )
    return {"conversations": result}


@app.get("/api/admin/feedback", response_model=AdminFeedbackListResponse)
def admin_list_feedback(
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：列出所有反馈记录。"""
    feedbacks = (
        db.query(Feedback)
        .order_by(Feedback.created_at.desc())
        .all()
    )
    logger.info(f"📋 管理员 '{admin_user}' 查询所有反馈: {len(feedbacks)} 条")
    return {
        "feedbacks": [
            {
                "id": f.id,
                "message_id": f.message_id,
                "user_id": f.user_id,
                "rating": f.rating,
                "comment": f.comment,
                "created_at": f.created_at.isoformat(),
            }
            for f in feedbacks
        ]
    }


@app.get("/api/admin/stats", response_model=AdminStatsResponse)
def admin_stats(
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：获取系统统计信息。"""
    user_count = db.query(User).count()
    conversation_count = db.query(Conversation).count()

    # 文档数量从向量库获取
    try:
        doc_sources = list_document_sources()
        document_count = len(doc_sources)
    except Exception:
        document_count = 0

    logger.info(
        f"📊 管理员 '{admin_user}' 查询统计: users={user_count}, convs={conversation_count}, docs={document_count}"
    )
    return {
        "user_count": user_count,
        "conversation_count": conversation_count,
        "document_count": document_count,
    }


# ==========================================
# LLM Provider 端点
# ==========================================


def _mask_api_key(key: str) -> str:
    """脱敏 API Key 用于前端展示：仅留前 3 位 + 后 4 位。

    长度 ≤ 8 时返回纯 ``****``，避免短 key 被还原推测。前端不应能
    看到任何完整 key 字符串。
    """
    if not key or len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]


def _provider_to_dict(p: LlmProvider) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "model_type": p.model_type,
        "provider_type": p.provider_type,
        "base_url": p.base_url,
        "api_key_hint": _mask_api_key(p.api_key),
        "model_name": p.model_name,
        "max_tokens": p.max_tokens,
        "timeout_seconds": p.timeout_seconds,
        "is_default": p.is_default,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat(),
    }


_VALID_MODEL_TYPES = {"llm", "embedding", "ocr"}
_VALID_PROVIDER_TYPES = {"local", "remote"}


def _invalidate_caches_by_type(model_type: str, provider_id: int = None):
    """按 model_type 清空相应客户端缓存。

    管理员编辑 / 删除 Provider 后必须调用，否则后续业务请求仍会命中
    旧的内存缓存（导致使用旧 base_url / api_key）。
    """
    if model_type == "llm":
        invalidate_provider_cache(provider_id)
    elif model_type == "embedding":
        invalidate_embedding_cache()
    elif model_type == "ocr":
        invalidate_ocr_client_cache()


@app.get("/api/providers", response_model=ProviderListResponse)
def list_providers(
    model_type: str = Query(default=None, description="按模型类型过滤：llm / embedding / ocr"),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出所有活跃的 Provider（API Key 脱敏）。可按 model_type 过滤。"""
    q = db.query(LlmProvider).filter(LlmProvider.is_active == True)
    if model_type:
        if model_type not in _VALID_MODEL_TYPES:
            raise HTTPException(status_code=400, detail=f"无效的 model_type: {model_type}")
        q = q.filter(LlmProvider.model_type == model_type)
    providers = q.order_by(LlmProvider.id.asc()).all()
    logger.info(
        f"🤖 用户 '{current_user}' 查询 Provider 列表 (type={model_type or '*'}): {len(providers)} 个"
    )
    return {"providers": [_provider_to_dict(p) for p in providers]}


@app.post("/api/admin/providers", status_code=status.HTTP_201_CREATED, response_model=ProviderOut)
def admin_create_provider(
    body: ProviderCreate,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：创建新 Provider。"""
    if body.model_type not in _VALID_MODEL_TYPES:
        raise HTTPException(status_code=400, detail="model_type 必须为 'llm' / 'embedding' / 'ocr'")
    if body.provider_type not in _VALID_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail="provider_type 必须为 'local' 或 'remote'")

    # 默认标志按 model_type 维度互斥
    if body.is_default:
        db.query(LlmProvider).filter(
            LlmProvider.model_type == body.model_type,
            LlmProvider.is_default == True,
        ).update({"is_default": False})

    provider = LlmProvider(
        name=body.name,
        model_type=body.model_type,
        provider_type=body.provider_type,
        base_url=body.base_url,
        api_key=body.api_key,
        model_name=body.model_name,
        max_tokens=body.max_tokens,
        timeout_seconds=body.timeout_seconds,
        is_default=body.is_default,
        is_active=True,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    _invalidate_caches_by_type(provider.model_type)
    logger.info(
        f"🤖 管理员 '{admin_user}' 创建 Provider: '{provider.name}' (type={provider.model_type})"
    )
    return _provider_to_dict(provider)


@app.get("/api/admin/providers", response_model=ProviderListResponse)
def admin_list_all_providers(
    model_type: str = Query(default=None, description="按模型类型过滤：llm / embedding / ocr"),
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：列出所有 Provider（含不活跃的）。可按 model_type 过滤。"""
    q = db.query(LlmProvider)
    if model_type:
        if model_type not in _VALID_MODEL_TYPES:
            raise HTTPException(status_code=400, detail=f"无效的 model_type: {model_type}")
        q = q.filter(LlmProvider.model_type == model_type)
    providers = q.order_by(LlmProvider.model_type.asc(), LlmProvider.id.asc()).all()
    return {"providers": [_provider_to_dict(p) for p in providers]}


@app.put("/api/admin/providers/{provider_id}", response_model=ProviderOut)
def admin_update_provider(
    provider_id: int,
    body: ProviderUpdate,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：更新 Provider。api_key 为空字符串表示不修改。"""
    provider = db.query(LlmProvider).filter(LlmProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    if body.name is not None:
        provider.name = body.name
    if body.model_type is not None:
        if body.model_type not in _VALID_MODEL_TYPES:
            raise HTTPException(status_code=400, detail="model_type 必须为 'llm' / 'embedding' / 'ocr'")
        provider.model_type = body.model_type
    if body.provider_type is not None:
        if body.provider_type not in _VALID_PROVIDER_TYPES:
            raise HTTPException(status_code=400, detail="provider_type 必须为 'local' 或 'remote'")
        provider.provider_type = body.provider_type
    if body.base_url is not None:
        provider.base_url = body.base_url
    if body.api_key is not None and body.api_key != "":
        provider.api_key = body.api_key
    if body.model_name is not None:
        provider.model_name = body.model_name
    if body.max_tokens is not None:
        provider.max_tokens = body.max_tokens
    if body.timeout_seconds is not None:
        provider.timeout_seconds = body.timeout_seconds
    if body.is_active is not None:
        provider.is_active = body.is_active
    if body.is_default is not None and body.is_default:
        db.query(LlmProvider).filter(
            LlmProvider.id != provider_id,
            LlmProvider.model_type == provider.model_type,
            LlmProvider.is_default == True,
        ).update({"is_default": False})
        provider.is_default = True
    elif body.is_default is not None and not body.is_default:
        provider.is_default = False

    db.commit()
    db.refresh(provider)
    _invalidate_caches_by_type(provider.model_type, provider_id)
    logger.info(f"✏️ 管理员 '{admin_user}' 更新 Provider: '{provider.name}'")
    return _provider_to_dict(provider)


@app.delete("/api/admin/providers/{provider_id}")
def admin_delete_provider(
    provider_id: int,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：删除 Provider。禁止删除该类型下的默认 provider。"""
    provider = db.query(LlmProvider).filter(LlmProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    if provider.is_default:
        raise HTTPException(
            status_code=400,
            detail=f"不能删除该类型({provider.model_type})的默认 Provider，请先把另一个 Provider 设为默认",
        )

    model_type = provider.model_type
    name = provider.name
    db.delete(provider)
    db.commit()
    _invalidate_caches_by_type(model_type, provider_id)
    logger.info(f"🗑️ 管理员 '{admin_user}' 删除 Provider: '{name}'")
    return {"detail": f"Provider '{name}' 已删除"}


@app.post("/api/admin/providers/{provider_id}/test", response_model=ProviderTestResponse)
def admin_test_provider(
    provider_id: int,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：按 model_type 测试 Provider 连通性。

    三种测试方式：
        - llm:        发一条极短 prompt，期望返回任意文本。
        - embedding:  embed 一句 "hello world"，校验向量维度。
        - ocr:        生成 64x64 白色 PNG 调 vision 接口，校验响应体。
                       64×64 的尺寸是为了满足 DeepSeek-OCR 等模型的
                       "宽高 ≥ 28" 限制。

    错误诊断：根据响应错误关键词推断常见配置问题（base_url 缺 /v1、
    API Key 无效、model_name 不匹配等），返回带"排查建议"的人读消息。
    """
    provider = db.query(LlmProvider).filter(LlmProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    if not (provider.api_key or "").strip():
        return {
            "success": False,
            "message": "尚未配置 API Key，请先在「编辑」中填写后再测试。",
            "latency_ms": None,
        }

    try:
        t0 = time.time()
        if provider.model_type == "llm":
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key,
                model=provider.model_name,
                temperature=0,
                max_tokens=32,
                streaming=False,
                request_timeout=min(provider.timeout_seconds, 30),
            )
            result = llm.invoke("你好，请回复 OK")
            content = result.content if hasattr(result, "content") else str(result)
            preview = content[:100]
        elif provider.model_type == "embedding":
            from langchain_openai import OpenAIEmbeddings
            emb = OpenAIEmbeddings(
                base_url=provider.base_url,
                api_key=provider.api_key,
                model=provider.model_name,
                timeout=min(provider.timeout_seconds, 30),
                check_embedding_ctx_length=False,
            )
            vec = emb.embed_query("hello world")
            preview = f"返回 {len(vec)} 维向量"
        elif provider.model_type == "ocr":
            # 用一张 64x64 白色 PNG 测试可达性
            # （DeepSeek-OCR 要求宽高 ≥ 28；其他 vision 模型也接受）
            import base64 as _b64
            try:
                from PIL import Image
                import io as _io
                _img = Image.new("RGB", (64, 64), color="white")
                _buf = _io.BytesIO()
                _img.save(_buf, format="PNG")
                tiny_png_b64 = _b64.b64encode(_buf.getvalue()).decode("ascii")
            except Exception:
                # 兜底：32x32 全白 PNG（同样满足 ≥28 限制）
                tiny_png_b64 = (
                    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAAA1BMVEX///"
                    "+nxBvIAAAAC0lEQVR4AWMYBQAAAAEAAaJgmKEAAAAASUVORK5CYII="
                )
            from openai import OpenAI
            client = OpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key,
                timeout=min(provider.timeout_seconds, 30),
            )
            resp = client.chat.completions.create(
                model=provider.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "回复 OK"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{tiny_png_b64}"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=32,
                temperature=0,
            )
            content = (resp.choices[0].message.content or "").strip()
            preview = content[:100] or "（模型未返回文本，但请求已成功）"
        else:
            return {
                "success": False,
                "message": f"未知的 model_type: {provider.model_type}",
                "latency_ms": None,
            }

        latency = (time.time() - t0) * 1000
        return {
            "success": True,
            "message": f"连通成功 [{provider.model_type}]，{preview}",
            "latency_ms": round(latency, 1),
        }
    except Exception as e:
        logger.error(f"❌ Provider 测试失败: {e}")
        msg = str(e)
        low = msg.lower()
        base_url = (provider.base_url or "").rstrip("/")
        hints: list[str] = []

        # base_url 缺 /v1 是最常见错误（硅基流动 / OpenAI / 智谱 部分端点要求 /v1）
        if (
            ("not found" in low or "404" in low)
            and base_url
            and not base_url.endswith("/v1")
            and "/v1/" not in (provider.base_url or "")
        ):
            hints.append(
                f"base_url 末尾缺 /v1，请改为 `{base_url}/v1` 后重试 "
                "（硅基流动 / OpenAI 等 OpenAI 兼容服务的嵌入 / 视觉端点必须带 /v1）"
            )

        if (
            provider.model_type == "embedding"
            and ("not found" in low or "404" in low)
            and not hints
        ):
            hints.append(
                "该 base_url 不存在 /embeddings 端点。请确认所选服务支持 Embeddings —— "
                "DeepSeek 不提供；硅基流动用 BAAI/bge-m3、智谱用 embedding-3、"
                "OpenAI 用 text-embedding-3-small。"
            )
        elif provider.model_type == "ocr" and (
            "must be larger than 28" in low
            or ("height" in low and "width" in low)
        ):
            hints.append(
                "该 OCR 模型对图片尺寸有最小限制；后端测试图已升级到 64×64，请重启后端再试。"
            )

        if "401" in low or "unauthorized" in low or "invalid api key" in low or "invalid token" in low or "认证" in msg:
            hints.append(
                "API Key 无效或不被该 base_url 接受。请检查："
                "1) 复制 KEY 时头尾无空格；"
                "2) 不要带 `Bearer ` 前缀；"
                "3) 该 KEY 已开通对应模型权限（如硅基流动需在控制台开通付费/免费配额）。"
            )

        if provider.model_type == "llm" and ("model" in low and ("not" in low or "找不到" in msg)):
            hints.append("model_name 与服务端不匹配，请到服务商控制台核对模型 ID（注意大小写和斜杠）。")

        hint_block = ""
        if hints:
            hint_block = "\n\n[排查建议]\n" + "\n".join(f"• {h}" for h in hints)

        return {
            "success": False,
            "message": f"连接失败: {msg[:300]}{hint_block}",
            "latency_ms": None,
        }


# ==========================================
# 技能场景端点（认证用户可读）
# ==========================================


def _skill_to_dict(skill: Skill) -> dict:
    """将 Skill ORM 对象转换为 API 响应字典。"""
    import json as _json
    patterns = []
    if skill.auto_detect_patterns:
        try:
            patterns = _json.loads(skill.auto_detect_patterns)
        except (ValueError, TypeError):
            patterns = []
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description or "",
        "system_prompt": skill.system_prompt,
        "icon": skill.icon,
        "auto_detect_patterns": patterns,
        "created_at": skill.created_at.isoformat(),
        "updated_at": skill.updated_at.isoformat(),
    }


@app.get("/api/skills", response_model=SkillListResponse)
def list_skills(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出所有技能场景（认证用户可访问）。"""
    skills = db.query(Skill).order_by(Skill.id.asc()).all()
    logger.info(f"🎯 用户 '{current_user}' 查询技能列表: {len(skills)} 个技能")
    return {"skills": [_skill_to_dict(s) for s in skills]}


@app.get("/api/skills/{skill_id}", response_model=SkillOut)
def get_skill(
    skill_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取单个技能详情（认证用户可访问）。"""
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="技能不存在",
        )
    return _skill_to_dict(skill)


@app.post("/api/skills/detect", response_model=SkillDetectResponse)
def detect_skill(
    body: SkillDetectRequest,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """根据文件名和内容自动检测匹配的技能场景。

    匹配算法极简：对每个技能数关键词在 (filename + content) 中的命中数，
    取得分最高者。打平时按 Skill.id 升序（先注册的优先）。
    没有命中返回 None，前端不弹出推荐横幅。
    """
    import json as _json

    text_to_match = ""
    if body.filename:
        text_to_match += body.filename
    if body.content:
        text_to_match += " " + body.content

    if not text_to_match.strip():
        return {"suggested_skill_id": None, "skill_name": None}

    skills = db.query(Skill).all()
    best_skill = None
    best_score = 0

    for skill in skills:
        patterns = []
        if skill.auto_detect_patterns:
            try:
                patterns = _json.loads(skill.auto_detect_patterns)
            except (ValueError, TypeError):
                patterns = []
        score = sum(1 for p in patterns if p in text_to_match)
        if score > best_score:
            best_score = score
            best_skill = skill

    if best_skill:
        logger.info(f"🔍 自动检测技能: '{best_skill.name}' (匹配 {best_score} 个模式)")
        return {
            "suggested_skill_id": best_skill.id,
            "skill_name": best_skill.name,
        }

    return {"suggested_skill_id": None, "skill_name": None}


# ==========================================
# Admin 混合检索调参端点
# ==========================================


def _parse_json_list(raw) -> Optional[list]:
    """把 DB 中的 JSON 文本数组解析为 list；空 / 非法 → None。"""
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) and val else None
    except (json.JSONDecodeError, TypeError):
        return None


def _retrieval_settings_to_out(row: RetrievalSettings) -> RetrievalSettingsOut:
    """ORM 行 → Pydantic 出参，统一处理 datetime → str、JSON 文本 → list。"""
    return RetrievalSettingsOut(
        mode=row.mode,
        alpha=float(row.alpha),
        rrf_k=int(row.rrf_k),
        bm25_top_k=int(row.bm25_top_k),
        vector_top_k=int(row.vector_top_k),
        final_top_k=int(row.final_top_k),
        semantic_threshold=float(row.semantic_threshold),
        enable_bm25=bool(row.enable_bm25),
        rerank_enabled=bool(getattr(row, "rerank_enabled", True)),
        rerank_top_n=int(getattr(row, "rerank_top_n", 5)),
        rerank_provider_id=getattr(row, "rerank_provider_id", None),
        contextual_chunking_enabled=bool(
            getattr(row, "contextual_chunking_enabled", False)
        ),
        contextual_chunking_provider_id=getattr(
            row, "contextual_chunking_provider_id", None
        ),
        query_rewrite_simple_enabled=bool(
            getattr(row, "query_rewrite_simple_enabled", False)
        ),
        query_rewrite_hyde_enabled=bool(
            getattr(row, "query_rewrite_hyde_enabled", False)
        ),
        query_rewrite_provider_id=getattr(row, "query_rewrite_provider_id", None),
        chunk_size=int(getattr(row, "chunk_size", 500) or 500),
        chunk_overlap=int(getattr(row, "chunk_overlap", 100) or 100),
        splitter_strategy=getattr(row, "splitter_strategy", "recursive") or "recursive",
        chunk_separators=_parse_json_list(getattr(row, "chunk_separators", None)),
        gen_temperature=float(getattr(row, "gen_temperature", 0.7) or 0.7),
        gen_top_p=getattr(row, "gen_top_p", None),
        gen_max_tokens=getattr(row, "gen_max_tokens", None),
        gen_presence_penalty=float(getattr(row, "gen_presence_penalty", 0.0) or 0.0),
        gen_frequency_penalty=float(getattr(row, "gen_frequency_penalty", 0.0) or 0.0),
        gen_stop=_parse_json_list(getattr(row, "gen_stop", None)),
        max_context_length=int(getattr(row, "max_context_length", 8000) or 8000),
        max_history_messages=int(getattr(row, "max_history_messages", 10) or 10),
        system_prompt_rag=getattr(row, "system_prompt_rag", None) or None,
        system_prompt_direct=getattr(row, "system_prompt_direct", None) or None,
        no_answer_text=getattr(row, "no_answer_text", None) or None,
        allow_fallback_to_direct=bool(getattr(row, "allow_fallback_to_direct", False)),
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


@app.get("/api/admin/retrieval", response_model=RetrievalSettingsOut)
def admin_get_retrieval_settings(
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """读取当前全局检索参数。"""
    row = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RetrievalSettings 默认行未初始化，请重启后端触发 init_db",
        )
    return _retrieval_settings_to_out(row)


@app.put("/api/admin/retrieval", response_model=RetrievalSettingsOut)
def admin_update_retrieval_settings(
    body: RetrievalSettingsUpdate,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """局部更新全局检索参数（仅写非 None 字段），并清缓存。"""
    row = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RetrievalSettings 默认行未初始化，请重启后端触发 init_db",
        )
    # exclude_unset：只写客户端真正传了的字段（含显式 null），实现 PATCH 语义。
    patch = body.model_dump(exclude_unset=True)

    # 文本字段：空字符串 / 仅空白 → None（重置为内置默认）。
    _text_reset_fields = {"system_prompt_rag", "system_prompt_direct", "no_answer_text"}
    # 列表字段：序列化为 JSON 文本；空数组 → None（重置 / 清空）。
    _json_list_fields = {"chunk_separators", "gen_stop"}

    for k, v in patch.items():
        if k in _text_reset_fields:
            v = v.strip() if isinstance(v, str) else v
            setattr(row, k, v or None)
        elif k in _json_list_fields:
            cleaned = [s for s in (v or []) if str(s).strip()] if isinstance(v, list) else None
            setattr(row, k, json.dumps(cleaned, ensure_ascii=False) if cleaned else None)
        else:
            setattr(row, k, v)
    from datetime import datetime
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    invalidate_retrieval_settings_cache()
    # 生成参数（temperature/top_p/penalty 等）变化需要重建 LLM 客户端缓存。
    invalidate_provider_cache()
    logger.info(f"⚙️ 管理员 '{admin_user}' 更新检索参数: {list(patch.keys())}")
    return _retrieval_settings_to_out(row)


@app.post("/api/admin/retrieval/preview", response_model=RetrievalPreviewResponse)
def admin_preview_retrieval(
    body: RetrievalPreviewRequest,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """跑一次混合检索，返回带 ranking debug 信息的候选列表（不调 LLM）。

    这是"验证检索算法如何设计"的核心入口：同一 query 用不同 mode/参数
    分别打一次，看哪些片段被召回、它们在两路的 rank 与归一化分数。
    """
    row = db.query(RetrievalSettings).filter(RetrievalSettings.id == 1).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RetrievalSettings 默认行未初始化",
        )

    # 合并：DB 默认 ←—— request.params（仅非 None 字段覆盖）
    merged = {
        "mode": row.mode,
        "alpha": float(row.alpha),
        "rrf_k": int(row.rrf_k),
        "bm25_top_k": int(row.bm25_top_k),
        "vector_top_k": int(row.vector_top_k),
        "final_top_k": int(row.final_top_k),
        "semantic_threshold": float(row.semantic_threshold),
        "enable_bm25": bool(row.enable_bm25),
        "rerank_enabled": bool(getattr(row, "rerank_enabled", True)),
        "rerank_top_n": int(getattr(row, "rerank_top_n", 5)),
        "rerank_provider_id": getattr(row, "rerank_provider_id", None),
    }
    if body.params is not None:
        for k, v in body.params.model_dump(exclude_none=True).items():
            merged[k] = v

    # hybrid_search 不认识 rerank_* 字段，先剥离再调；rerank 单独跑。
    hybrid_kw = {k: v for k, v in merged.items() if not k.startswith("rerank")}
    final_limit = int(hybrid_kw["final_top_k"])
    if merged["rerank_enabled"]:
        hybrid_kw["final_top_k"] = max(
            final_limit,
            int(hybrid_kw["bm25_top_k"]),
            int(hybrid_kw["vector_top_k"]),
            int(merged["rerank_top_n"]),
        )
    triples = hybrid_search(body.query, **hybrid_kw)
    if merged["rerank_enabled"]:
        from reranker import rerank as _rerank
        triples = _rerank(
            body.query, triples,
            top_n=merged["rerank_top_n"],
            provider_id=merged.get("rerank_provider_id"),
        )
    else:
        triples = triples[:final_limit]

    items: list[RetrievalPreviewItem] = []
    for doc, fused_score, debug in triples:
        meta = doc.metadata or {}
        cid = (
            meta.get("_id")
            or f"{meta.get('source', '?')}#{abs(hash(doc.page_content)) % (10**8)}"
        )
        items.append(
            RetrievalPreviewItem(
                id=str(cid),
                source=str(meta.get("source", "?")),
                content=(doc.page_content or "")[:200],
                bm25_rank=debug.get("bm25_rank"),
                bm25_norm=debug.get("bm25_norm"),
                sem_rank=debug.get("sem_rank"),
                sem_norm=debug.get("sem_norm"),
                fused_score=float(fused_score),
                original_rank=debug.get("original_rank"),
                rerank_score=debug.get("rerank_score"),
            )
        )

    used_out = _retrieval_settings_to_out(row)
    # 把生效后的参数（含 request 覆盖）回写到响应里，便于前端展示真正用的是啥
    for k, v in merged.items():
        if hasattr(used_out, k):
            setattr(used_out, k, v)

    logger.info(
        f"🔬 管理员 '{admin_user}' 检索 preview: query='{body.query[:30]}', "
        f"mode={merged['mode']}, 命中 {len(items)} 条"
    )
    return RetrievalPreviewResponse(
        mode=merged["mode"],
        used_params=used_out,
        items=items,
    )


# ==========================================
# Admin 向量库维护端点
# ==========================================


@app.post("/api/admin/vectorstore/rebuild")
def admin_rebuild_vectorstore(
    admin_user: str = Depends(get_admin_user),
):
    """管理员：清空并按当前嵌入模型重建整个向量库。

    场景：切换 embedding Provider 后维度对不上时使用。
    会扫描 uploads/ 目录下的所有文件重新切片入库。

    重建是破坏性操作 —— 旧的向量切片全部丢失，依赖手工编辑过的
    chunk content 也会被覆盖回原始解析结果。前端应弹确认框。
    """
    logger.info(f"🧨 管理员 '{admin_user}' 触发向量库重建")
    try:
        # 1) 清空当前 collection
        clear_vector_store()
        # 2) 让嵌入客户端缓存失效（保险：用户可能刚改了 embedding Provider）
        invalidate_embedding_cache()
        # 3) 扫描 uploads 重新入库（ingest_files 会按当前默认 embedding 重建集合）
        ingest_files()
        # 4) BM25 索引同步失效（旧 chunk_id 全部作废）
        BM25Index.instance().mark_stale()
        from database import document_count, list_document_sources
        sources = list_document_sources()
        return {
            "success": True,
            "message": f"已重建向量库，入库 {len(sources)} 个文档、{document_count()} 个片段",
            "documents": len(sources),
            "chunks": document_count(),
        }
    except Exception as e:
        logger.error(f"❌ 向量库重建失败: {e}")
        return {
            "success": False,
            "message": f"重建失败: {str(e)[:300]}",
            "documents": 0,
            "chunks": 0,
        }


# ==========================================
# Admin 技能管理端点
# ==========================================


@app.post("/api/admin/skills", status_code=status.HTTP_201_CREATED, response_model=SkillOut)
def admin_create_skill(
    body: SkillCreate,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：创建新技能。"""
    import json as _json

    # 检查名称是否已存在
    existing = db.query(Skill).filter(Skill.name == body.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"技能名称 '{body.name}' 已存在",
        )

    skill = Skill(
        name=body.name,
        description=body.description or "",
        system_prompt=body.system_prompt,
        icon=body.icon,
        auto_detect_patterns=_json.dumps(body.auto_detect_patterns or [], ensure_ascii=False),
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)

    logger.info(f"🎯 管理员 '{admin_user}' 创建技能: '{skill.name}'")
    return _skill_to_dict(skill)


# 简单 frontmatter 解析：兼容 `---\nkey: value\n---\n<body>` 的常见
# SKILL.md 写法。**只支持单行 KV**，不支持嵌套 YAML / list / 多行串；
# 这样可以零依赖（不引 pyyaml）地完成 80% 的真实导入场景。
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_markdown(md: str) -> tuple[dict, str]:
    """返回 ``(meta_dict, body_text)``。无 frontmatter 时 meta 为空。"""
    m = _FRONTMATTER_RE.match(md or "")
    if not m:
        return {}, (md or "").strip()
    meta_raw = m.group(1)
    body = md[m.end():].lstrip("\n")
    meta: dict = {}
    for line in meta_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, body.strip()


@app.post(
    "/api/admin/skills/import",
    status_code=status.HTTP_201_CREATED,
    response_model=SkillOut,
)
def admin_import_skill(
    body: SkillImportRequest,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：从带 frontmatter 的 markdown 一键导入技能。

    解析规则：
        - frontmatter 的 ``name`` / ``description`` 自动写入对应字段；
        - 正文（``---`` 之后的所有内容）作为 ``system_prompt``；
        - frontmatter 缺失 ``name`` 时必须传 ``name_override``。

    冲突处理：同名已存在且 ``overwrite=False`` → 409；
    ``overwrite=True`` → 按 id 更新 system_prompt / description。
    """
    import json as _json

    meta, body_text = _parse_skill_markdown(body.markdown)
    if not body_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="markdown 正文为空，无法作为 system_prompt",
        )

    name = body.name_override or meta.get("name")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="未提供 name：frontmatter 无 name 字段且未传 name_override",
        )

    description = meta.get("description", "")
    icon = body.icon or "📚"
    patterns_json = _json.dumps(body.auto_detect_patterns or [], ensure_ascii=False)

    existing = db.query(Skill).filter(Skill.name == name).first()
    if existing:
        if not body.overwrite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"技能名称 '{name}' 已存在；如需覆盖请设 overwrite=true",
            )
        existing.description = description or existing.description
        existing.system_prompt = body_text
        if body.icon is not None:
            existing.icon = icon
        if body.auto_detect_patterns is not None:
            existing.auto_detect_patterns = patterns_json
        db.commit()
        db.refresh(existing)
        logger.info(f"♻️ 管理员 '{admin_user}' 覆盖导入技能: '{name}'")
        return _skill_to_dict(existing)

    skill = Skill(
        name=name,
        description=description,
        system_prompt=body_text,
        icon=icon,
        auto_detect_patterns=patterns_json,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    logger.info(
        f"📥 管理员 '{admin_user}' 导入技能: '{name}' "
        f"(prompt {len(body_text)} 字符)"
    )
    return _skill_to_dict(skill)


@app.put("/api/admin/skills/{skill_id}", response_model=SkillOut)
def admin_update_skill(
    skill_id: int,
    body: SkillUpdate,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：更新技能。"""
    import json as _json

    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="技能不存在",
        )

    # 如果更新名称，检查是否与其他技能冲突
    if body.name is not None and body.name != skill.name:
        existing = db.query(Skill).filter(Skill.name == body.name).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"技能名称 '{body.name}' 已存在",
            )
        skill.name = body.name

    if body.description is not None:
        skill.description = body.description
    if body.system_prompt is not None:
        skill.system_prompt = body.system_prompt
    if body.icon is not None:
        skill.icon = body.icon
    if body.auto_detect_patterns is not None:
        skill.auto_detect_patterns = _json.dumps(body.auto_detect_patterns, ensure_ascii=False)

    db.commit()
    db.refresh(skill)

    logger.info(f"✏️ 管理员 '{admin_user}' 更新技能: '{skill.name}'")
    return _skill_to_dict(skill)


@app.delete("/api/admin/skills/{skill_id}")
def admin_delete_skill(
    skill_id: int,
    admin_user: str = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员：删除技能。"""
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="技能不存在",
        )

    skill_name = skill.name
    db.delete(skill)
    db.commit()

    logger.info(f"🗑️ 管理员 '{admin_user}' 删除技能: '{skill_name}'")
    return {"detail": f"技能 '{skill_name}' 已删除"}
