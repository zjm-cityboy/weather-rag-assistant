"""
冒烟测试：验证和风天气 JWT 认证与实况天气链路连通性（不经 FastAPI/LangGraph）

链路：backend/.env 五要素 → EdDSA 签名 JWT → GeoAPI 城市查找 → 实况天气。
任一环节失败会在此暴露具体异常，用于区分"凭据配置问题"与"服务编排问题"。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.weather.client import _JWT_READY, _make_token, fetch_now

# ==== 步骤 1：JWT 模式就绪检查（五要素：kid/iss/sub/私钥/host）====
print(f"JWT 模式就绪: {_JWT_READY}")
if not _JWT_READY:
    sys.exit("五要素不全，检查 backend/.env")

# ==== 步骤 2：生成 token（私钥读取 + EdDSA 签名自检）====
token = _make_token()
print(f"token 生成成功（{len(token)} 字符，Bearer 头可直接使用）")

# ==== 步骤 3：完整链路实测（GeoAPI 城市查找 → 实况天气）====
result = fetch_now("北京")
print(json.dumps(result, ensure_ascii=False, indent=2))
