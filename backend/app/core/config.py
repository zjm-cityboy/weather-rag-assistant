"""
应用配置：集中管理所有常量与环境变量（.env 外置密钥，禁止硬编码）
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env（本项目独立配置，gitignore 不入库；缺失即报错，不降级借外部文件）
BACKEND_DIR = Path(__file__).parent.parent.parent     # backend/
load_dotenv(BACKEND_DIR / ".env")

# ============================================================
# 模型（硅基流动 OpenAI 兼容接口）
# ============================================================
API_KEY = os.getenv("API_KEY", "")
API_BASE_URL = os.getenv("API_BASE_URL", "")
CHAT_MODEL = os.getenv("MODEL_NAME", "Qwen/Qwen3.5-35B")   # 问答模型（优先读 .env 的 MODEL_NAME）
EMBED_MODEL = "BAAI/bge-m3"   # 多语嵌入（批次③升级：跨语对齐强于 Qwen3-0.6B，见实验 8；1024 维与库兼容）

# ============================================================
# 检索
# ============================================================
TOP_K = 5                # 每次检索取 top-5 知识块
# 默认本机直连；Docker Compose 里通过环境变量覆盖为服务名（host=db）
PG_DSN = os.getenv(
    "PG_DSN",
    "host=localhost port=5432 dbname=weather user=postgres password=weather_dev_2026",
)

# ============================================================
# 会话
# ============================================================
MAX_HISTORY_ROUNDS = 5    # 每个会话保留的历史轮数（防 prompt 无限膨胀）

# ============================================================
# 和风天气（免费开发版；申请：dev.qweather.com → 控制台 → 创建项目）
# 认证优先级：JWT（推荐，Ed25519）→ API KEY → mock 演示数据
# ============================================================
# JWT 四要素（控制台"项目管理→凭据"页可查 kid；"设置"页可查开发者ID iss）
QWEATHER_PRIVATE_KEY_PATH = os.getenv("QWEATHER_PRIVATE_KEY_PATH", "")  # 私钥文件路径
QWEATHER_CREDENTIAL_ID = os.getenv("QWEATHER_CREDENTIAL_ID", "")        # 凭据 ID（kid）
QWEATHER_DEV_ID = os.getenv("QWEATHER_DEV_ID", "")                      # 开发者 ID（iss，Q 开头）
QWEATHER_PROJECT_ID = os.getenv("QWEATHER_PROJECT_ID", "")              # 项目 ID（sub）
# API KEY（降级方案，2027 年起限流）与专属 API Host
QWEATHER_KEY = os.getenv("QWEATHER_API_KEY", "")
QWEATHER_HOST = os.getenv("QWEATHER_HOST", "")   # 账户专属 host，形如 abcxyz.qweatherapi.com
QWEATHER_TIMEOUT = 15     # 和风 API 超时（秒）

# ============================================================
# Neo4j 图数据库（第 5 期知识图谱；容器 weather-neo4j，见 README）
# ============================================================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# JWT 登录密钥（第 6 期注册登录；只存 .env，泄露即可伪造登录态）
JWT_SECRET = os.getenv("JWT_SECRET", "")

# ============================================================
# 服务
# ============================================================
CORS_ORIGINS = [         # 允许跨域的前端来源（Vue dev server；走 Vite 代理时不触发，直连时生效）
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]
