"""远程 OCR 引擎：通过 OpenAI 兼容 Vision API（如 GLM-4V / Qwen-VL / GPT-4o）识别图像文字。

不再加载任何本地模型；所需模型由数据库中 ``model_type='ocr'`` 的
Provider 提供。如需切换 OCR 服务，只需在管理后台 → 模型管理中修改
对应 Provider 的 base_url / api_key / model_name，无需重启后端。

对外 API：
    - ``ocr_pdf(pdf_path)``  → 逐页渲染图片再识别，返回每页一个 Document。
    - ``ocr_image_file(path)`` → 单图识别，返回单元素列表。
    - ``invalidate_ocr_client_cache()`` → 配置变更后清空 OpenAI 客户端缓存。

线程安全：``_client_cache`` 字典在 CPython 下读写本身是原子操作；
即使并发场景下偶尔重复构造一个 client 也无副作用，因此未加锁。
"""

import base64
import logging
import os
import tempfile
from typing import Optional

import fitz  # PyMuPDF：仅用于把 PDF 渲染为图片
from langchain_core.documents import Document
from openai import OpenAI

from config import OCR_PDF_RENDER_DPI

logger = logging.getLogger(__name__)


# 提示词刻意要求"纯文本，不加 markdown" —— 否则部分模型会在多列文档处
# 自动加 ``---`` 分隔线，污染下游切片。temperature=0 保证确定性输出。
_OCR_PROMPT = (
    "你是一个 OCR 引擎。请识别图片中的所有文字，按从上到下、从左到右的"
    "原始阅读顺序输出纯文本，不要添加任何解释、标题或 markdown 格式；"
    "若图中没有可识别的文字，请输出空字符串。"
)


# 以 provider.id 为键缓存 OpenAI 客户端：每个 OpenAI() 内部维护连接池，
# 复用可避免每次 OCR 都重建 TCP/TLS 连接。
_client_cache: dict[int, OpenAI] = {}


def _get_ocr_provider():
    """从数据库读取 model_type='ocr' 的活跃 Provider，优先默认。

    选择策略：
        1. 优先返回 ``is_default=True`` 的 Provider（管理员显式指定）。
        2. 其次返回任意 ``is_active=True`` 的 OCR Provider。
        3. 都没有返回 ``None``，由调用方抛出友好错误。
    """
    # lazy import 防止 models.py 与本模块产生循环导入。
    from models import LlmProvider, SessionLocal

    db = SessionLocal()
    try:
        provider = (
            db.query(LlmProvider)
            .filter(
                LlmProvider.model_type == "ocr",
                LlmProvider.is_active == True,
                LlmProvider.is_default == True,
            )
            .first()
        )
        if not provider:
            # 没有"默认 OCR"时降级到任一 active OCR。
            provider = (
                db.query(LlmProvider)
                .filter(
                    LlmProvider.model_type == "ocr",
                    LlmProvider.is_active == True,
                )
                .first()
            )
        return provider
    finally:
        db.close()


def _get_client(provider) -> OpenAI:
    """为指定 Provider 返回一个共享的 OpenAI 客户端实例。"""
    cached = _client_cache.get(provider.id)
    if cached is not None:
        return cached
    client = OpenAI(
        base_url=provider.base_url,
        api_key=provider.api_key,
        timeout=provider.timeout_seconds,
    )
    _client_cache[provider.id] = client
    return client


def invalidate_ocr_client_cache():
    """OCR Provider 配置发生变更时清空客户端缓存。

    管理后台编辑 Provider（如改 base_url）后，必须调用此函数清缓存，
    否则后续 OCR 请求仍走旧地址。api.py 的 Provider 编辑接口里调用。
    """
    _client_cache.clear()


def _ocr_image_bytes(image_bytes: bytes, mime: str = "image/png") -> str:
    """对单张图片字节流执行远程 OCR，返回识别到的文字（失败返回空串）。

    错误处理：
        - Provider 缺失 / 未配 API Key → RuntimeError，向上传递给路由层
          转 500，前端可看到具体原因。
        - 网络/接口错误 → 捕获后返回空串，避免单页失败拖垮整篇 OCR。
    """
    provider = _get_ocr_provider()
    if not provider:
        raise RuntimeError(
            "未配置 OCR 模型 Provider。请在管理后台 → 模型管理 中新增"
            " model_type='ocr' 的远程 Vision API（如 GLM-4V、Qwen-VL 等）。"
        )
    if not (provider.api_key or "").strip():
        raise RuntimeError(
            f"OCR Provider「{provider.name}」尚未配置 API Key，"
            f"请在管理后台 → 模型管理 中编辑并填写。"
        )

    client = _get_client(provider)
    # OpenAI Vision 接口要求图片以 data URI 形式内联在 message 里，
    # base64 是唯一兼容方式。
    b64 = base64.b64encode(image_bytes).decode("ascii")

    try:
        resp = client.chat.completions.create(
            model=provider.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
            max_tokens=provider.max_tokens,
            temperature=0.0,   # 确定性输出 —— OCR 不需要创造性
        )
        text = (resp.choices[0].message.content or "").strip()
        return text
    except Exception as e:
        # 单页失败不抛出：保证整篇 PDF 还能继续往后识别。
        logger.error(f"[OCR] 远程识别失败 ({provider.name}/{provider.model_name}): {e}")
        return ""


def _read_image_file(image_path: str) -> tuple[bytes, str]:
    """读取图片字节并按扩展名推断 MIME 类型。

    MIME 必须正确传给 vision 接口，否则部分模型会以二进制形式拒绝。
    未知扩展名兜底为 image/png（对绝大多数 vision 模型最兼容）。
    """
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "bmp": "image/bmp",
        "tif": "image/tiff",
        "tiff": "image/tiff",
        "webp": "image/webp",
    }
    mime = mime_map.get(ext, "image/png")
    with open(image_path, "rb") as f:
        return f.read(), mime


def ocr_pdf(pdf_path: str, dpi: Optional[int] = None) -> list[Document]:
    """将 PDF 逐页渲染为 PNG 后通过远程 Vision API 识别。

    返回值：每成功识别出文本的页面对应一个 Document，metadata 含
    ``source``（文件名）、``page``（0-based 索引）、``page_label``（人读
    用 1-based）、``ocr=True`` 标记。空页直接跳过不入库，避免污染向量库。

    Args:
        pdf_path: PDF 绝对路径。
        dpi: 渲染分辨率，None 时使用 ``config.OCR_PDF_RENDER_DPI``。
             调高 DPI 可提升小字识别率但单页 base64 体积线性增长，
             vision API 也可能拒绝过大图片。
    """
    render_dpi = dpi or OCR_PDF_RENDER_DPI
    doc = fitz.open(pdf_path)
    filename = os.path.basename(pdf_path)
    total_pages = len(doc)

    documents: list[Document] = []

    # 临时目录会在 with 块结束时自动删除，避免长时间占用磁盘。
    with tempfile.TemporaryDirectory() as tmp_dir:
        for page_idx in range(total_pages):
            page = doc[page_idx]
            # PDF 默认 72 DPI，按 render_dpi/72 缩放矩阵得到目标 DPI 的位图。
            pix = page.get_pixmap(matrix=fitz.Matrix(render_dpi / 72, render_dpi / 72))
            img_path = os.path.join(tmp_dir, f"page_{page_idx}.png")
            pix.save(img_path)

            logger.info(f"[OCR] 正在识别第 {page_idx + 1}/{total_pages} 页: {filename}")
            try:
                with open(img_path, "rb") as f:
                    img_bytes = f.read()
                text = _ocr_image_bytes(img_bytes, mime="image/png")
            except Exception as e:
                # 这里 catch 主要是兜底 _ocr_image_bytes 抛 RuntimeError 的情况
                # （例如运行中 Provider 被禁用），让其它页继续处理。
                logger.error(f"[OCR] 第 {page_idx + 1} 页处理失败: {e}")
                text = ""

            logger.info(f"[OCR] 第 {page_idx + 1} 页完成, 识别 {len(text)} 字符")
            if text:
                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": filename,
                            "page": page_idx,
                            "page_label": str(page_idx + 1),
                            "ocr": True,
                        },
                    )
                )

    doc.close()
    return documents


def ocr_image_file(image_path: str) -> list[Document]:
    """对单个图片文件执行远程 OCR。

    与 ocr_pdf 不同，单图无 page 概念，metadata 只保留 source 与 ocr 标记。
    """
    filename = os.path.basename(image_path)
    logger.info(f"[OCR] 正在识别图片: {filename}")
    try:
        img_bytes, mime = _read_image_file(image_path)
        text = _ocr_image_bytes(img_bytes, mime=mime)
    except Exception as e:
        logger.error(f"[OCR] 图片识别失败: {e}")
        return []
    if not text:
        # 图片完全没识别出文字（比如纯装饰图），返回空让上层提示用户。
        return []
    return [
        Document(
            page_content=text,
            metadata={"source": filename, "ocr": True},
        )
    ]
