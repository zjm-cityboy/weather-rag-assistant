"""
/auth 路由：注册登录（bcrypt 密码哈希 + JWT 签发）

安全设计：
    - 密码只存 bcrypt 哈希（自带盐，抗彩虹表），绝不存明文
    - 登录成功签发 JWT（HS256，24h 有效），前端存 localStorage 并带 Authorization 头
    - verify_token 作为 FastAPI 依赖供受保护路由（如 /ask）复用
"""

import datetime
import time
import uuid

import bcrypt
import jwt
import psycopg2
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import JWT_SECRET
from app.core.db import pg_conn

router = APIRouter(prefix="/auth", tags=["auth"])

TOKEN_TTL_HOURS = 24            # 登录态有效期

# 登录失败限流（内存版）：同一用户名 10 分钟内失败 5 次锁定——
# 防暴力破解（代码审查 P1-3；单机演示够用，多副本部署换 Redis 计数）
_login_fails: dict[str, list[float]] = {}
MAX_FAILS = 5
LOCK_SECONDS = 600


def _is_locked(username: str) -> bool:
    """清理过期记录并判断是否达到锁定阈值。"""
    now = time.time()
    fails = [t for t in _login_fails.get(username, []) if now - t < LOCK_SECONDS]
    _login_fails[username] = fails
    return len(fails) >= MAX_FAILS


class Credentials(BaseModel):
    """注册/登录请求体（Pydantic 校验）。"""
    username: str = Field(min_length=3, max_length=20, description="用户名")
    password: str = Field(min_length=6, max_length=64, description="密码")


def _hash_password(password: str) -> str:
    """bcrypt 哈希（gensalt 自带随机盐，同一密码每次哈希结果不同——校验用 checkpw）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def make_token(username: str) -> str:
    """签发 JWT：sub=用户名，exp=签发后 24 小时。"""
    now = datetime.datetime.now(datetime.timezone.utc)   # timezone.utc 兼容 3.10-
    return jwt.encode({"sub": username, "iat": now,
                       "exp": now + datetime.timedelta(hours=TOKEN_TTL_HOURS)},
                      JWT_SECRET, algorithm="HS256")


def verify_token(authorization: str = Header("")) -> str:
    """FastAPI 依赖：校验 Authorization Bearer 头，返回用户名（无效则 401）。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from None


@router.post("/register", status_code=201)
def register(cred: Credentials) -> dict:
    """注册：用户名唯一冲突返回 409；成功即自动登录（返回 token）。"""
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id;",
                (cred.username, _hash_password(cred.password)))
            user_id = cur.fetchone()[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="用户名已被注册") from None
    return {"id": user_id, "username": cred.username, "token": make_token(cred.username)}


@router.post("/login")
def login(cred: Credentials) -> dict:
    """登录：bcrypt.checkpw 校验哈希；用户不存在与密码错误返回同一提示（不泄露可枚举信息）。"""
    if _is_locked(cred.username):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请 10 分钟后再试")
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, password_hash FROM users WHERE username = %s;",
                    (cred.username,))
        row = cur.fetchone()

    ok = row is not None and bcrypt.checkpw(cred.password.encode("utf-8"),
                                            row[1].encode("utf-8"))
    if not ok:
        _login_fails.setdefault(cred.username, []).append(time.time())
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _login_fails.pop(cred.username, None)          # 成功清空失败记录
    return {"id": row[0], "username": cred.username,
            "token": make_token(cred.username), "session": f"u-{cred.username}-{uuid.uuid4().hex[:8]}"}
