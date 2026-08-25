"""
全库重嵌入：换嵌入模型（Qwen3-0.6B → BAAI/bge-m3）后同步知识库向量

背景（代码审查批次③ / 实验 8）：跨语言检索是系统短板，业界标准解法是换
强多语言嵌入模型。检索两侧必须同模型——模型切换后全库向量必须重算，
否则查询向量与库内向量不在同一语义空间，检索完全失效。

幂等：重跑 = 用当前 EMBED_MODEL 全量覆盖（无破坏性）。维度 1024 不变，
表结构/索引无需变动。
"""

import sys
import time
from pathlib import Path

import psycopg2

PROJECT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / "backend"))

from app.core.config import API_BASE_URL, API_KEY, EMBED_MODEL, PG_DSN
from langchain_openai import OpenAIEmbeddings

BATCH = 32                 # 嵌入批量（与入库管线一致）


def main() -> None:
    embeddings = OpenAIEmbeddings(
        model=EMBED_MODEL, api_key=API_KEY, base_url=API_BASE_URL,
        check_embedding_ctx_length=False, timeout=30, max_retries=2)   # pitfalls 第 1 条

    # ==== 步骤 1：取全库待重嵌入块 ====
    conn = psycopg2.connect(PG_DSN)
    with conn.cursor() as cur:
        cur.execute("SELECT id, content FROM knowledge_chunks ORDER BY id;")
        rows = cur.fetchall()
    print(f"[1] 待重嵌入 {len(rows)} 条（模型 {EMBED_MODEL}）")

    # ==== 步骤 2：分批嵌入 → 批量 UPDATE（每 320 条打进度） ====
    t0 = time.time()
    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            vectors = embeddings.embed_documents([c for _id, c in batch])
            # executemany 参数绑定（向量 → pgvector 字面量）
            cur.executemany(
                "UPDATE knowledge_chunks SET embedding = %s::vector WHERE id = %s;",
                [("[" + ",".join(f"{x:.6f}" for x in v) + "]", rid)
                 for (rid, _c), v in zip(batch, vectors)])
            done = min(i + BATCH, len(rows))
            if done % 320 == 0 or done == len(rows):
                print(f"[2] 进度 {done}/{len(rows)}（{(time.time()-t0)/60:.1f}min）", flush=True)
    conn.commit()
    conn.close()
    print(f"[3] 重嵌入完成，检索两侧已同源（{EMBED_MODEL}）")


if __name__ == "__main__":
    main()
