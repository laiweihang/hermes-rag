# auth.py
"""JWT 认证模块 — 密码哈希、令牌生成/验证、FastAPI 依赖。

本模块对外暴露三类能力：

1. 密码哈希：``hash_password`` / ``verify_password``，使用 bcrypt 自带 salt。
2. JWT 令牌：``create_access_token`` / ``decode_access_token``，HS256 对称签名。
3. FastAPI 依赖：``get_current_user`` / ``get_admin_user``，挂在路由的
   ``Depends`` 上完成"必须登录"或"必须管理员"的鉴权。

为什么不用 OAuth2PasswordBearer：
    项目仅需简单的"登录拿 Token + 后续 Bearer 头"模式，HTTPBearer 更
    轻量；前端用同一个 token 即可访问所有受保护接口。
"""

import logging
from datetime import datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import JWT_EXPIRY_MINUTES, JWT_SECRET_KEY

logger = logging.getLogger(__name__)

# auto_error=False：允许"未带 token"时由我们自己抛 401 文案，而不是 FastAPI
# 默认的英文 "Not authenticated"，便于前端统一处理跳转登录页。
_bearer_scheme = HTTPBearer(auto_error=False)

# HS256：对称签名足够本项目本地化部署的安全等级；如未来要做多服务共享
# 公钥校验，可改 RS256 + JWKS。
JWT_ALGORITHM = "HS256"


# ==========================================
# 密码哈希
# ==========================================


def hash_password(password: str) -> str:
    """使用 bcrypt 对明文密码进行哈希。

    bcrypt 自动生成 salt 并嵌入结果字符串，不需要额外存储 salt 列。
    返回值是 ``$2b$...`` 形式的 ASCII 字符串，可直接落 SQLite TEXT 列。
    """
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码是否与 bcrypt 哈希匹配。

    ``bcrypt.checkpw`` 自身是恒定时间比较，可抵御 timing attack；不要
    自行 == 比较。
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


# ==========================================
# JWT 令牌
# ==========================================


def create_access_token(subject: str, role: str = "user") -> str:
    """创建 JWT 访问令牌，包含 role 声明。

    Args:
        subject: 通常传用户名，写入 ``sub`` 标准字段。
        role: 自定义字段，仅 "user" / "admin" 两值；"admin" 用于绕过
            ``get_admin_user`` 的角色检查。

    令牌过期时间由 ``config.JWT_EXPIRY_MINUTES`` 控制（默认 30 分钟），
    UTC 时间戳；前端拿到 401 后应跳登录页重登。
    """
    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRY_MINUTES)
    payload = {"sub": subject, "role": role, "exp": expire}
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    logger.info(f"🔑 已为用户 '{subject}' 创建访问令牌 (role={role})")
    return token


def decode_access_token(token: str) -> dict:
    """解码并验证 JWT 令牌，返回 payload 字典。

    异常处理：
        - ``ExpiredSignatureError`` → 401 "令牌已过期"。
        - 其他 ``InvalidTokenError`` 子类（签名错、格式坏等）→ 401 "无效的令牌"。

    上游 API 把这两类都映射为 401，让前端走统一的"清 token + 跳登录"
    分支。
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已过期",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的令牌",
        )


# ==========================================
# FastAPI 依赖
# ==========================================


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """FastAPI 依赖：从 Bearer 令牌中提取当前用户名。

    用法：``user = Depends(get_current_user)``。返回字符串型用户名而不是
    User ORM 对象，是为了让单元测试无需准备 DB session 即可 mock；如需
    完整用户信息，调用方自行 ``db.query(User).filter_by(username=user)``。
    """
    if credentials is None:
        # 未携带 Authorization 头 —— 显式 401 而不是默认的 403。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
        )
    payload = decode_access_token(credentials.credentials)
    username: str = payload.get("sub")
    if username is None:
        # token 合法但缺 sub 字段，理论上不该出现，做防御。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的令牌",
        )
    return username


def get_admin_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """FastAPI 依赖：验证当前用户为 admin 角色，否则返回 403。

    与 ``get_current_user`` 的区别仅在于多一道 role 校验。重复整段逻辑
    而不是 ``Depends(get_current_user)`` 包一层，是因为这里需要拿到完整
    payload（含 role），二次解码不划算。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
        )
    payload = decode_access_token(credentials.credentials)
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的令牌",
        )
    # 缺省按 "user" 处理，避免历史 token（无 role 字段）被误判为 admin。
    role: str = payload.get("role", "user")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足，需要管理员权限",
        )
    return username
