"""
气象 RAG 智能问答助手 · FastAPI 服务入口

启动（backend/ 目录下）：
    uvicorn app.main:app --reload --port 8000
接口文档（自动生成）：
    http://localhost:8000/docs
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ask, auth
from app.core.config import API_KEY, CORS_ORIGINS, JWT_SECRET, NEO4J_PASSWORD

# 日志：级别可由环境变量 LOG_LEVEL 控制（默认 INFO；代码审查 P1-4）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

app = FastAPI(
    title="气象 RAG 智能问答助手",
    description="气象知识问答（RAG）+ 多轮对话 + SSE 流式输出 + 引用溯源 + 注册登录",
    version="0.1.0",
)

# CORS：允许第 2 期 Vue 前端（5173）跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ask.router)


@app.get("/health")
def health() -> dict:
    """健康检查（部署探活用，不碰数据库）。"""
    return {"status": "ok"}


# 启动即校验必填配置：缺失直接拒绝启动（fail-fast），
# 而不是带空密钥运行到出问题时才暴露（代码审查 P0-4）
_REQUIRED = [("API_KEY", API_KEY), ("JWT_SECRET", JWT_SECRET), ("NEO4J_PASSWORD", NEO4J_PASSWORD)]


@app.on_event("startup")
def check_config() -> None:
    missing = [name for name, value in _REQUIRED if not value]
    if missing:
        raise RuntimeError(f"缺少必填配置 {missing}：检查 backend/.env（对照 .env.example）")
