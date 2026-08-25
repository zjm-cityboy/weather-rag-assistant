"""
冒烟测试：验证硅基流动 rerank API 可用性与跨语言重排能力

实验设计（相关性梯度 + 跨语言两组）：
    A. 中文组：4 个文档相关性递减（台风定义 > 预警信号 > 冷锋 > 无关）
       → 验证 relevance_score 是否单调对应人工判断的相关性
    B. 跨语组：中文 query 对英文文档（typhoon 定义 vs cold front vs 无关）
       → 决定本项目（英文教材语料）能否用中文问题直接精排英文 chunk
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")

API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")


def rerank(model: str, query: str, documents: list) -> list:
    """调用 rerank 接口，返回 [(index, relevance_score), ...] 按 score 降序。"""
    r = requests.post(
        f"{BASE_URL}/rerank",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": model, "query": query, "documents": documents, "top_n": len(documents)},
        timeout=30,
    )
    r.raise_for_status()
    return [(item["index"], item["relevance_score"]) for item in r.json()["results"]]


# ==== A. 中文组：相关性梯度验证 ====
MODEL = "Qwen/Qwen3-Reranker-0.6B"
docs_zh = [
    "台风是发生在热带海洋上的强烈气旋性涡旋，中心附近最大风力达12级以上。",     # 强相关
    "台风预警信号分为蓝色、黄色、橙色、红色四级，红色表示6小时内可能受强台风影响。",  # 中相关（同主题不同侧）
    "冷锋是冷气团主动推向暖气团时形成的锋面，过境时常出现大风降温天气。",           # 弱相关（气象但主题不同）
    "今天推荐三款适合办公室的咖啡豆，浅烘焙带有花果香气。",                       # 无关
]
print("=== A. 中文组（query：台风是什么）===")
for idx, score in rerank(MODEL, "台风是什么", docs_zh):
    print(f"  score={score:.4f}  {docs_zh[idx][:30]}")

# ==== B. 跨语组：中文 query 对英文文档 ====
docs_en = [
    "A tropical cyclone is a rapidly rotating storm system characterized by a low-pressure center.",  # 强相关
    "A cold front is the leading edge of a cooler air mass replacing warmer air at ground level.",    # 弱相关
    "The best coffee beans for espresso are dark roasted with chocolate notes.",                      # 无关
]
print("=== B. 跨语组（query：什么是台风）===")
for idx, score in rerank(MODEL, "什么是台风", docs_en):
    print(f"  score={score:.4f}  {docs_en[idx][:40]}")
