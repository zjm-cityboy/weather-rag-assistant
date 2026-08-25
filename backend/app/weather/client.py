"""
和风天气客户端：城市查找 → 实况天气

认证优先级：JWT（Ed25519 签名，推荐）→ API KEY → mock 演示数据。
三项 JWT 配置齐备即走 JWT；全部未配置走 mock（结果带 mock=True 标记）。

新版 API v1 说明：host 为账户专属（形如 abcxyz.qweatherapi.com），
实况接口按经纬度查询（/weather/v1/current/{lat}/{lon}），需先经 GeoAPI
城市查找（/geo/v2/city/lookup）拿坐标。响应结构直接整体透传给播报
prompt（对结构变化免疫）。
"""

import time
from pathlib import Path

import jwt
import requests

from app.core.config import (
    QWEATHER_CREDENTIAL_ID,
    QWEATHER_DEV_ID,
    QWEATHER_HOST,
    QWEATHER_KEY,
    QWEATHER_PRIVATE_KEY_PATH,
    QWEATHER_PROJECT_ID,
    QWEATHER_TIMEOUT,
)

# mock 演示数据（仅未配置任何凭据时使用；数值参考华北秋季典型实况）
MOCK_NOW = {
    "北京": {"temp": "22", "feelsLike": "21", "text": "晴", "windDir": "东北风",
             "windScale": "3", "humidity": "45", "vis": "24", "precip": "0.0"},
    "上海": {"temp": "26", "feelsLike": "27", "text": "多云", "windDir": "东南风",
             "windScale": "2", "humidity": "68", "vis": "18", "precip": "0.0"},
    "广州": {"temp": "31", "feelsLike": "36", "text": "阵雨", "windDir": "南风",
             "windScale": "3", "humidity": "82", "vis": "12", "precip": "1.2"},
}
MOCK_DEFAULT = {"temp": "24", "feelsLike": "24", "text": "多云", "windDir": "微风",
                "windScale": "1", "humidity": "60", "vis": "20", "precip": "0.0"}

# JWT 模式可用性：kid/iss/sub/私钥/host 五要素齐备
_JWT_READY = all([QWEATHER_CREDENTIAL_ID, QWEATHER_DEV_ID,
                  QWEATHER_PROJECT_ID, QWEATHER_PRIVATE_KEY_PATH, QWEATHER_HOST])

_token_cache = {"token": "", "exp": 0}     # JWT 缓存（签名有成本，有效期内复用）


def _make_token() -> str:
    """生成 EdDSA 签名的 JWT；有效期内直接复用缓存。

    Header: {alg: EdDSA, kid: 凭据ID}；Payload: {iss: 开发者ID, sub: 项目ID, iat, exp}。
    iat 提前 30s（和风要求，防时钟误差）；有效期 15 分钟。
    """
    now = int(time.time())
    if _token_cache["token"] and now < _token_cache["exp"] - 60:   # 剩余>60s 复用
        return _token_cache["token"]

    private_key = Path(QWEATHER_PRIVATE_KEY_PATH).read_text(encoding="utf-8")
    token = jwt.encode(
        {"iss": QWEATHER_DEV_ID, "sub": QWEATHER_PROJECT_ID,
         "iat": now - 30, "exp": now + 900},
        private_key, algorithm="EdDSA",
        headers={"kid": QWEATHER_CREDENTIAL_ID},
    )
    _token_cache.update(token=token, exp=now + 900)
    return token


def _headers() -> dict:
    """认证请求头：优先 JWT Bearer，降级用 API KEY。"""
    if _JWT_READY:
        return {"Authorization": f"Bearer {_make_token()}"}
    return {"X-QW-Api-Key": QWEATHER_KEY}


def _get(path: str, params: dict) -> dict:
    """GET 并做基础校验（HTTP 非 2xx 或业务 error 字段视为失败）。"""
    r = requests.get(f"https://{QWEATHER_HOST}{path}",
                     params=params, headers=_headers(), timeout=QWEATHER_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_now(city: str) -> dict:
    """取城市实况天气。返回 {city, now, mock}；失败返回 {city, error}。

    now 字段直接放 API 原始 JSON（播报 prompt 自行读取，对响应结构变化免疫）。
    """
    if not _JWT_READY and not QWEATHER_KEY:
        # mock 模式：链路演示用，播报会标注"演示数据"
        return {"city": city, "now": MOCK_NOW.get(city, MOCK_DEFAULT), "mock": True}

    try:
        # ① GeoAPI 城市查找：城市名 → 坐标（v1 实况按经纬度查询）
        geo = _get("/geo/v2/city/lookup", {"location": city, "lang": "zh"})
        first = (geo.get("location") or [{}])[0]
        lat, lon = first.get("lat"), first.get("lon")
        resolved = first.get("name", city)
        if not (lat and lon):
            raise RuntimeError(f"未找到城市「{city}」")

        # ② 实况天气（坐标 → 原始 JSON 整体透传）
        now = _get(f"/weather/v1/current/{lat}/{lon}", {"lang": "zh"})
        return {"city": resolved, "now": now, "mock": False}
    except Exception as e:  # noqa: BLE001 —— 天气获取失败不阻断回答，转提示
        return {"city": city, "error": f"天气数据获取失败：{e}", "mock": False}
