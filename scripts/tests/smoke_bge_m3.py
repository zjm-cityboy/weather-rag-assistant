"""
冒烟测试：BGE-M3 嵌入模型可用性 + 跨语言对齐对照（BGE-M3 vs Qwen3-Embedding）

背景（代码审查批次③）：实验 7 显示 context 指标低的主因是跨语言检索错位，
业界标准解法是换强多语言嵌入模型。本冒烟测试先验证硅基流动 BGE-M3 可用、
维度兼容（需 1024），并对比两模型"中文问题 vs 英文教材段"的余弦距离——
距离显著更小即跨语对齐更强的直接证据（重嵌入全库前的依据）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.core.config import API_BASE_URL, API_KEY
from langchain_openai import OpenAIEmbeddings

# 库内真实语料（Stull Ch16 台风章的表述 + 一段无关文本作对照）
ZH_QUERY = "台风是怎么形成的"
EN_TARGET = "A tropical cyclone is a warm-core low pressure system that forms over warm tropical ocean water, driven by latent heat release and the Coriolis effect."
EN_IRRELEVANT = "The best coffee beans for espresso are dark roasted with chocolate notes."

MODELS = ["BAAI/bge-m3", "Qwen/Qwen3-Embedding-0.6B"]


def cosine_sim(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb)


for model in MODELS:
    emb = OpenAIEmbeddings(model=model, api_key=API_KEY, base_url=API_BASE_URL,
                           check_embedding_ctx_length=False, timeout=30, max_retries=2)
    zh, en_t, en_i = emb.embed_documents([ZH_QUERY, EN_TARGET, EN_IRRELEVANT])
    dim = len(zh)
    print(f"{model}（维度 {dim}）")
    print(f"  中文问题 ↔ 英文台风段   相似度 {cosine_sim(zh, en_t):.4f}")
    print(f"  中文问题 ↔ 无关英文段   相似度 {cosine_sim(zh, en_i):.4f}")
    assert dim == 1024, f"{model} 维度 {dim} ≠ 1024，pgvector 表不兼容！"
